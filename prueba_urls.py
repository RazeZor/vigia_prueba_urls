#!/usr/bin/env python3
"""
vigia_ingesta.py

Prueba las fuentes abiertas de VIGÍA, valida el código HTTP y la estructura,
limpia los datos y guarda JSON estáticos. Cada archivo queda registrado en
manifest.json con URL, hora de consulta y huella SHA-256. La aplicación lee
esos archivos, así que la demo no depende de la red.

Uso:
    python vigia_ingesta.py                       # guarda en ./data
    python vigia_ingesta.py --out datos --eda-dir eda_manual

Requiere: pip install requests

Estados posibles por fuente:
    OK                        datos válidos guardados
    SIN_EVENTOS               la fuente respondió bien y no hay eventos (no es fallo)
    SIN_DATOS_UTILES          estructura correcta, pero ninguna fila pasó la limpieza
    SIN_PARAMETROS            falta radlab_params.json (configuración pendiente)
    SIN_ARCHIVOS              no hay CSV de EDA en la carpeta manual (no es fallo)
    ESTRUCTURA_INESPERADA     la respuesta no tiene la forma esperada; no se guarda
    ERROR_HTTP                código distinto de 200
    ERROR_RED                 no hubo conexión tras los reintentos
    ERROR_TODAS_LAS_DIRECCIONES  DONKI no respondió en ninguna dirección
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import platform
import statistics
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

TIMEOUT_S = 30
REINTENTOS = 3
USER_AGENT = "VIGIA-ingesta/0.1 (NASA Space Apps 2026)"
ESTADOS_NO_FALLO = {"OK", "SIN_EVENTOS", "SIN_PARAMETROS", "SIN_ARCHIVOS", "SIN_DATOS_UTILES"}

# --- GOES (NOAA SWPC): flujo integral de protones -------------------------
# Ruta del archivo de 1 día: verificar al ejecutar. Si cambia, el script
# reporta ESTRUCTURA_INESPERADA o ERROR_HTTP en lugar de guardar basura.
GOES_URL = "https://services.swpc.noaa.gov/json/goes/primary/integral-protons-1-day.json"
GOES_CAMPOS = {"time_tag", "satellite", "flux", "energy"}
GOES_ENERGIAS = {">=10 MeV", ">=50 MeV", ">=100 MeV"}

# --- DONKI (NASA CCMC): eventos SEP. Orden: dirección nueva, anterior, espejo.
DONKI_BASES = [
    "https://ccmc.gsfc.nasa.gov/DONKI/WS/get/SEP",
    "https://kauai.ccmc.gsfc.nasa.gov/DONKI/WS/get/SEP",
    "https://api.nasa.gov/DONKI/SEP",
]

# --- RadLab (NASA OSDR): dosis de radiación ------------------------------
# Los parámetros exactos van en radlab_params.json, copiados de la guía de la
# Data API. No se adivinan aquí: el script no consulta RadLab sin ese archivo.
RADLAB_URL = "https://visualization.osdr.nasa.gov/radlab/api/"
RADLAB_CAMPOS = {"timestamp", "instrument_id", "absorbed_dose_rate", "dose_equivalent_rate"}

# --- EDA (NASA OSDR): sin API documentada; se leen CSV descargados a mano --
EDA_URL = "https://visualization.osdr.nasa.gov/eda"


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def pedir(url: str, params: dict | None = None) -> requests.Response:
    """GET con reintentos y espera exponencial. Lanza la última excepción de red."""
    ultimo_error: Exception | None = None
    for intento in range(REINTENTOS):
        try:
            return requests.get(
                url,
                params=params,
                timeout=TIMEOUT_S,
                headers={"User-Agent": USER_AGENT},
            )
        except requests.RequestException as e:
            ultimo_error = e
            time.sleep(2 ** intento)
    raise ultimo_error  # type: ignore[misc]


def sha256_de(ruta: Path) -> str:
    h = hashlib.sha256()
    with ruta.open("rb") as f:
        for bloque in iter(lambda: f.read(65536), b""):
            h.update(bloque)
    return h.hexdigest()


def guardar_json(carpeta: Path, nombre: str, contenido: dict) -> tuple[str, str]:
    ruta = carpeta / f"{nombre}.json"
    ruta.write_text(json.dumps(contenido, ensure_ascii=False, indent=2), encoding="utf-8")
    return ruta.name, sha256_de(ruta)


def registro(
    fuente: str,
    estado: str,
    url: str,
    consultado: str,
    http: int | None = None,
    filas: int = 0,
    archivos: dict | None = None,
    nota: str = "",
) -> dict:
    return {
        "fuente": fuente,
        "estado": estado,
        "url": url,
        "consultado_utc": consultado,
        "http": http,
        "filas": filas,
        "archivos": archivos or {},
        "nota": nota,
    }


def _num(valor: str | None) -> float | None:
    if valor is None:
        return None
    valor = str(valor).strip()
    if valor == "":
        return None
    try:
        return float(valor)
    except ValueError:
        return None


def _hora_iso(texto: str | None) -> str | None:
    """DONKI entrega 'AAAA-MM-DDThh:mmZ'; se normaliza a segundos: 'AAAA-MM-DDThh:mm:ssZ'."""
    if texto and len(texto) == 17 and texto.endswith("Z"):
        return texto[:-1] + ":00Z"
    return texto


# ---------------------------------------------------------------------------
# GOES (NOAA SWPC)
# ---------------------------------------------------------------------------

def ingestar_goes(carpeta: Path) -> dict:
    t0 = iso_utc(ahora_utc())
    try:
        r = pedir(GOES_URL)
    except requests.RequestException as e:
        return registro("GOES", "ERROR_RED", GOES_URL, t0, nota=type(e).__name__)

    if r.status_code != 200:
        return registro("GOES", "ERROR_HTTP", r.url, t0, http=r.status_code)

    try:
        datos = r.json()
    except ValueError:
        return registro("GOES", "ESTRUCTURA_INESPERADA", r.url, t0, http=200,
                        nota="La respuesta no es JSON")

    if not isinstance(datos, list) or not datos or not all(
        isinstance(x, dict) and GOES_CAMPOS <= x.keys() for x in datos
    ):
        return registro("GOES", "ESTRUCTURA_INESPERADA", r.url, t0, http=200,
                        nota=f"Se esperaba una lista con los campos {sorted(GOES_CAMPOS)}")

    limpios: list[dict] = []
    vistos: set[tuple] = set()
    descartados = 0
    energias_vistas: set[str] = set()

    for x in datos:
        energias_vistas.add(str(x.get("energy")))
        flujo = _num(str(x.get("flux")))
        if flujo is None or flujo < 0:          # valores de relleno o inválidos
            descartados += 1
            continue
        if x["energy"] not in GOES_ENERGIAS:
            descartados += 1
            continue
        clave = (x["time_tag"], x["satellite"], x["energy"])
        if clave in vistos:
            descartados += 1
            continue
        vistos.add(clave)
        limpios.append({
            "t_real": x["time_tag"],
            "satelite": x["satellite"],
            "energia": x["energy"],
            "flujo": flujo,
            "unidad": "protons/(cm2 s sr)",
        })

    if not limpios:
        return registro("GOES", "SIN_DATOS_UTILES", r.url, t0, http=200,
                        nota=f"Energías encontradas: {sorted(energias_vistas)}")

    archivo, sha = guardar_json(carpeta, "goes_protones", {
        "fuente": r.url,
        "consultado_utc": t0,
        "datos": limpios,
    })
    return registro("GOES", "OK", r.url, t0, http=200, filas=len(limpios),
                    archivos={archivo: sha},
                    nota=f"{descartados} filas descartadas")


# ---------------------------------------------------------------------------
# DONKI (NASA CCMC)
# ---------------------------------------------------------------------------

def _evento_donki(x: dict) -> dict:
    return {
        "sep_id": x["sepID"],
        "hora_real_utc": _hora_iso(x.get("eventTime")),
        "instrumentos": [i.get("displayName") for i in (x.get("instruments") or [])],
        "actividades_ligadas": [l.get("activityID") for l in (x.get("linkedEvents") or [])],
        "enlace": x.get("link"),
    }


def ingestar_donki(carpeta: Path) -> dict:
    t0_dt = ahora_utc()
    t0 = iso_utc(t0_dt)
    hoy = t0_dt.date()
    # Por defecto DONKI devuelve 30 días; se piden fechas explícitas.
    params_base = {
        "startDate": (hoy - timedelta(days=30)).isoformat(),
        "endDate": hoy.isoformat(),
    }
    intentos: list[str] = []

    for base in DONKI_BASES:
        params = dict(params_base)
        if base.startswith("https://api.nasa.gov"):
            params["api_key"] = os.environ.get("NASA_API_KEY", "DEMO_KEY")

        try:
            r = pedir(base, params)
        except requests.RequestException as e:
            intentos.append(f"{base}: red ({type(e).__name__})")
            continue
        if r.status_code != 200:
            intentos.append(f"{base}: HTTP {r.status_code}")
            continue
        try:
            datos = r.json()
        except ValueError:
            intentos.append(f"{base}: no es JSON")
            continue
        if not isinstance(datos, list) or not all(
            isinstance(x, dict) and "sepID" in x and "eventTime" in x for x in datos
        ):
            intentos.append(f"{base}: estructura inesperada")
            continue

        # Lista vacía = sin eventos en el rango; es una respuesta válida.
        limpios = [_evento_donki(x) for x in datos]
        limpios.sort(key=lambda e: e["hora_real_utc"] or "")
        archivo, sha = guardar_json(carpeta, "donki_sep", {
            "fuente": r.url.split("api_key=")[0].rstrip("&?"),
            "rango": params_base,
            "consultado_utc": t0,
            "datos": limpios,
        })
        estado = "SIN_EVENTOS" if not limpios else "OK"
        return registro("DONKI", estado, r.url.split("api_key=")[0].rstrip("&?"),
                        t0, http=200, filas=len(limpios), archivos={archivo: sha},
                        nota=f"Dirección usada: {base}")

    return registro("DONKI", "ERROR_TODAS_LAS_DIRECCIONES", DONKI_BASES[0], t0,
                    nota=" | ".join(intentos))


# ---------------------------------------------------------------------------
# RadLab (NASA OSDR)
# ---------------------------------------------------------------------------

def ingestar_radlab(carpeta: Path) -> dict:
    t0 = iso_utc(ahora_utc())
    ruta_params = Path("radlab_params.json")
    if not ruta_params.exists():
        return registro("RadLab", "SIN_PARAMETROS", RADLAB_URL, t0,
                        nota="Crear radlab_params.json con los parámetros de la guía de la Data API")

    try:
        params = json.loads(ruta_params.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return registro("RadLab", "ESTRUCTURA_INESPERADA", RADLAB_URL, t0,
                        nota=f"radlab_params.json no es JSON válido: {e}")

    try:
        r = pedir(RADLAB_URL, params)
    except requests.RequestException as e:
        return registro("RadLab", "ERROR_RED", RADLAB_URL, t0, nota=type(e).__name__)

    if r.status_code != 200:
        return registro("RadLab", "ERROR_HTTP", r.url, t0, http=r.status_code)

    lector = csv.DictReader(io.StringIO(r.text))
    campos = set(lector.fieldnames or [])
    if not RADLAB_CAMPOS <= campos:
        return registro("RadLab", "ESTRUCTURA_INESPERADA", r.url, t0, http=200,
                        nota=f"Faltan columnas; llegaron: {sorted(campos)}")

    limpios: list[dict] = []
    descartados = 0
    for fila in lector:
        abs_ = _num(fila.get("absorbed_dose_rate"))
        eq = _num(fila.get("dose_equivalent_rate"))
        if abs_ is None and eq is None:          # sin dosis: no sirve
            descartados += 1
            continue
        limpios.append({
            "t_real": fila["timestamp"],
            "instrumento": fila["instrument_id"],
            "dosis_absorbida_uGy_h": abs_,
            "dosis_equivalente_uSv_h": eq,
        })

    if not limpios:
        return registro("RadLab", "SIN_DATOS_UTILES", r.url, t0, http=200,
                        nota="Ninguna fila con dosis")

    archivo, sha = guardar_json(carpeta, "radlab_dosis", {
        "fuente": r.url,
        "consultado_utc": t0,
        "unidades": {
            "dosis_absorbida_uGy_h": "uGy/h",
            "dosis_equivalente_uSv_h": "uSv/h",
        },
        "aviso": "El límite de carrera está en mSv: usar dosis_equivalente cuando exista; "
                 "con dosis absorbida la comparación es solo orientativa.",
        "datos": limpios,
    })
    return registro("RadLab", "OK", r.url, t0, http=200, filas=len(limpios),
                    archivos={archivo: sha}, nota=f"{descartados} filas sin dosis descartadas")


# ---------------------------------------------------------------------------
# EDA (NASA OSDR): CSV descargados a mano
# ---------------------------------------------------------------------------

def ingestar_eda(carpeta: Path, eda_dir: Path) -> dict:
    t0 = iso_utc(ahora_utc())
    csvs = sorted(eda_dir.glob("*.csv")) if eda_dir.exists() else []
    if not csvs:
        return registro("EDA", "SIN_ARCHIVOS", EDA_URL, t0,
                        nota=f"Descargar tablas desde EDA a {eda_dir}/")

    archivos: dict[str, str] = {}
    total = 0
    notas: list[str] = []

    for ruta_csv in csvs:
        with ruta_csv.open(encoding="utf-8-sig", newline="") as f:
            lector = csv.DictReader(f)
            campos = lector.fieldnames or []
            col_t = next((c for c in campos if "time" in c.lower() or "date" in c.lower()), None)
            if col_t is None:
                notas.append(f"{ruta_csv.name}: sin columna de tiempo")
                continue
            filas = []
            for fila in lector:
                limpia = {}
                for c, v in fila.items():
                    if c is None:
                        continue
                    num = _num(v)
                    limpia[c] = num if num is not None else (v.strip() if v else None)
                filas.append(limpia)

        marcas = []
        for fila in filas:
            try:
                marcas.append(datetime.fromisoformat(str(fila[col_t]).replace("Z", "+00:00")))
            except (TypeError, ValueError):
                pass
        marcas.sort()
        cadencia = None
        deltas = [(b - a).total_seconds() for a, b in zip(marcas, marcas[1:])]
        deltas = [d for d in deltas if d > 0]
        if deltas:
            cadencia = statistics.median(deltas)

        nombre, sha = guardar_json(carpeta, f"eda_{ruta_csv.stem}", {
            "fuente": EDA_URL,
            "archivo_origen": ruta_csv.name,
            "consultado_utc": t0,
            "cadencia_mediana_s": cadencia,
            "columnas": campos,
            "datos": filas,
        })
        archivos[nombre] = sha
        total += len(filas)

    if not archivos:
        return registro("EDA", "ESTRUCTURA_INESPERADA", EDA_URL, t0, nota="; ".join(notas))
    return registro("EDA", "OK", EDA_URL, t0, filas=total, archivos=archivos,
                    nota="; ".join(notas) if notas else f"{len(archivos)} archivo(s) convertidos")


# ---------------------------------------------------------------------------
# Principal
# ---------------------------------------------------------------------------

def imprimir_tabla(resultados: list[dict]) -> None:
    cols = ("FUENTE", "ESTADO", "HTTP", "FILAS", "NOTA")
    filas = [
        (r["fuente"], r["estado"], str(r["http"] or "-"), str(r["filas"]), r["nota"][:70])
        for r in resultados
    ]
    ancho = [max(len(c), *(len(f[i]) for f in filas)) for i, c in enumerate(cols)]
    linea = "  ".join(c.ljust(ancho[i]) for i, c in enumerate(cols))
    print(linea)
    print("-" * len(linea))
    for f in filas:
        print("  ".join(v.ljust(ancho[i]) for i, v in enumerate(f)))


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingesta de datos abiertos para VIGÍA")
    ap.add_argument("--out", default="data", help="carpeta de salida (por defecto: data)")
    ap.add_argument("--eda-dir", default="eda_manual", help="carpeta con CSV de EDA")
    args = ap.parse_args()

    carpeta = Path(args.out)
    carpeta.mkdir(parents=True, exist_ok=True)

    print(f"VIGÍA · ingesta · {iso_utc(ahora_utc())}")
    resultados = [
        ingestar_goes(carpeta),
        ingestar_donki(carpeta),
        ingestar_radlab(carpeta),
        ingestar_eda(carpeta, Path(args.eda_dir)),
    ]

    manifest = {
        "generado_utc": iso_utc(ahora_utc()),
        "python": platform.python_version(),
        "fuentes": resultados,
    }
    (carpeta / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    imprimir_tabla(resultados)
    print(f"\nManifest: {carpeta / 'manifest.json'}")

    fallos = [r for r in resultados if r["estado"] not in ESTADOS_NO_FALLO]
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())