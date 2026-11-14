# Guía para ejecutar la ingesta y exportar CSV

Este proyecto descarga datos públicos de NOAA/NASA a JSON y luego convierte
esos JSON a CSV para abrirlos en Excel o analizarlos con otras herramientas.

## 1. Requisitos

- Windows y Python instalado.
- Conexión a Internet para consultar las fuentes de datos.
- El paquete `requests`. El conversor usa solo la biblioteca estándar de Python.

El entorno actual usa Python 3.15.2. `pandas` y `lxml` no son necesarios para
el flujo actual.

## 2. Preparar el entorno

Abre PowerShell en la carpeta del proyecto:

```powershell
cd "C:\Users\ASUS-08\Desktop\pruebas_pandas"
```

Si todavía no existe la carpeta `.venv`, créala:

```powershell
python -m venv .venv
```

Instala `requests` en ese entorno (si ya está instalado, no hace falta
reinstalarlo):

```powershell
.\.venv\Scripts\python.exe -m pip install requests
```

No es obligatorio activar el entorno; los comandos de esta guía invocan
directamente el Python de `.venv`.

## 3. Descargar y guardar los datos como JSON

Ejecuta el script de ingesta:

```powershell
.\.venv\Scripts\python.exe .\prueba_urls.py
```

Por defecto, los archivos JSON y `manifest.json` se guardan en `data\`.
El manifest registra el estado de cada fuente, cuántas filas se guardaron y
la dirección consultada.

La ingesta consulta GOES y DONKI por Internet. RadLab necesita un archivo
`radlab_params.json` con los parámetros indicados por la guía de su Data API.
EDA no tiene una API documentada en este flujo: si se desea incluirlo, coloca
los CSV descargados manualmente en `eda_manual\`. Si esos insumos opcionales
no están disponibles, el script informa su estado y continúa con las demás
fuentes.

Se puede cambiar la carpeta de salida de la ingesta:

```powershell
.\.venv\Scripts\python.exe .\prueba_urls.py --out datos
```

## 4. Convertir los JSON a CSV

Después de la ingesta, ejecuta:

```powershell
.\.venv\Scripts\python.exe .\json_a_csv.py
```

Por defecto, el conversor lee los JSON de `data\` y guarda un CSV por cada
archivo con registros en `csv\`. Por ejemplo:

- `data\goes_protones.json` → `csv\goes_protones.csv`
- `data\donki_sep.json` → `csv\donki_sep.csv`

`manifest.json` no es una tabla de registros, por lo que se omite. Si DONKI
no tuvo eventos en el período consultado, se genera un CSV con encabezados y
sin filas de datos.

También se pueden indicar otras carpetas:

```powershell
.\.venv\Scripts\python.exe .\json_a_csv.py --in datos --out csv
```

La carpeta indicada en `--in` debe contener JSON con una lista de registros
en la raíz o bajo la propiedad `datos`.

## 5. Ejecutar ambos pasos seguidos

Desde la carpeta del proyecto, ejecuta:

```powershell
.\.venv\Scripts\python.exe .\prueba_urls.py
if ($LASTEXITCODE -ne 0) {
    throw "La ingesta terminó con errores; revisa el estado de las fuentes."
}
.\.venv\Scripts\python.exe .\json_a_csv.py
```

## 6. Abrir y entender los resultados

Abre los archivos de `csv\` con Excel. Se escriben con codificación UTF-8
compatible con Excel y con una fila de encabezados.

El CSV de GOES contiene la hora UTC, el número de satélite, el umbral de
energía, el flujo de protones y su unidad (`protons/(cm2 s sr)`). Las fechas
se conservan en formato ISO 8601 con `Z` para indicar UTC. Los valores
numéricos mantienen el punto decimal.

El CSV de DONKI contiene el identificador y hora UTC del evento, instrumentos,
eventos relacionados y enlace. Las listas de instrumentos o eventos se
mantienen juntas en su celda.

## 7. Revisar errores

- `ERROR_RED`: comprueba la conexión a Internet e inténtalo de nuevo.
- `ERROR_HTTP`: la fuente respondió con un código HTTP distinto de 200.
- `ESTRUCTURA_INESPERADA`: la respuesta de la fuente cambió o no tiene los
  campos esperados.
- `SIN_PARAMETROS` (RadLab): falta `radlab_params.json`.
- `SIN_ARCHIVOS` (EDA): no hay CSV descargados en `eda_manual\`.
- `SIN_EVENTOS` (DONKI): la consulta funcionó, pero no encontró eventos en el
  período solicitado.

Consulta `data\manifest.json` para ver el estado y el detalle de cada fuente.
