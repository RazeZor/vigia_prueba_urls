#!/usr/bin/env python3
"""Convierte los JSON de ingesta de VIGÍA en archivos CSV compatibles con Excel."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


ENCABEZADOS = {
    "t_real": "Fecha_hora_UTC",
    "satelite": "Numero_satelite_GOES",
    "energia": "Umbral_energia_MeV",
    "flujo": "Flujo_protones_[protons/(cm2*s*sr)]",
    "unidad": "Unidad_flujo",
    "sep_id": "ID_evento_SEP",
    "hora_real_utc": "Fecha_hora_evento_UTC",
    "instrumentos": "Instrumentos",
    "actividades_ligadas": "Eventos_relacionados",
    "enlace": "Enlace",
    "t_real": "Fecha_hora_UTC",
    "instrumento": "ID_instrumento",
    "dosis_absorbida_uGy_h": "Dosis_absorbida_[uGy/h]",
    "dosis_equivalente_uSv_h": "Dosis_equivalente_[uSv/h]",
    "timestamp": "Fecha_hora_UTC",
    "instrument_id": "ID_instrumento",
    "absorbed_dose_rate": "Tasa_dosis_absorbida_[uGy/h]",
    "dose_equivalent_rate": "Tasa_dosis_equivalente_[uSv/h]",
}

ENCABEZADOS_SIN_FILAS = {
    "donki_sep": [
        "sep_id",
        "hora_real_utc",
        "instrumentos",
        "actividades_ligadas",
        "enlace",
    ],
}


def valor_csv(valor: Any) -> Any:
    if valor is None:
        return ""
    if isinstance(valor, (dict, list)):
        return json.dumps(valor, ensure_ascii=False, separators=(",", ":"))
    return valor


def convertir_archivo(origen: Path, destino: Path) -> int | None:
    contenido = json.loads(origen.read_text(encoding="utf-8"))
    if isinstance(contenido, list):
        filas = contenido
    elif isinstance(contenido, dict) and isinstance(contenido.get("datos"), list):
        filas = contenido["datos"]
    else:
        print(f"Omitido {origen.name}: no contiene una lista de registros.")
        return None

    if any(not isinstance(fila, dict) for fila in filas):
        raise ValueError(f"{origen}: cada registro debe ser un objeto JSON.")

    campos = list(dict.fromkeys(campo for fila in filas for campo in fila))
    if not campos:
        campos = list(contenido.get("columnas", [])) if isinstance(contenido, dict) else []
    if not campos:
        campos = ENCABEZADOS_SIN_FILAS.get(origen.stem, [])

    nombres = [ENCABEZADOS.get(campo, campo) for campo in campos]
    destino.parent.mkdir(parents=True, exist_ok=True)
    with destino.open("w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=nombres, extrasaction="ignore")
        escritor.writeheader()
        for fila in filas:
            escritor.writerow({
                ENCABEZADOS.get(campo, campo): valor_csv(fila.get(campo))
                for campo in campos
            })

    return len(filas)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convierte los JSON de VIGÍA en CSV para Excel y análisis."
    )
    parser.add_argument("--in", dest="entrada", default="data", help="carpeta con JSON")
    parser.add_argument("--out", dest="salida", default="csv", help="carpeta para CSV")
    args = parser.parse_args()

    carpeta_entrada = Path(args.entrada)
    if not carpeta_entrada.is_dir():
        parser.error(f"no existe la carpeta de entrada: {carpeta_entrada}")

    total = 0
    for origen in sorted(carpeta_entrada.glob("*.json")):
        destino = Path(args.salida) / f"{origen.stem}.csv"
        filas = convertir_archivo(origen, destino)
        if filas is not None:
            print(f"{origen.name} -> {destino} ({filas} filas)")
            total += 1

    if total == 0:
        print(f"No se encontraron JSON con registros en {carpeta_entrada}.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
