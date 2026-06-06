# Clarity -> SQL Server

Extractor local para Microsoft Clarity Live Insights API.

## 1. Credenciales

El script lee las credenciales desde el `.env` raiz del stack BI:

```text
D:\Proyectos\4_BI_Ecom\.env
```

Formato usado:

```env
CLARITY_PROJECT_KEYS=CONVERSE,COLISEUM
CLARITY_PROJECT_CONVERSE_NAME=Converse
CLARITY_PROJECT_CONVERSE_TOKEN_NAME=depor_reportes
CLARITY_PROJECT_CONVERSE_TOKEN=...
CLARITY_PROJECT_COLISEUM_NAME=Coliseum
CLARITY_PROJECT_COLISEUM_TOKEN_NAME=depor_reportes
CLARITY_PROJECT_COLISEUM_TOKEN=...
```

El Excel `credenciales.xlsx` queda solo como fallback temporal si `CLARITY_PROJECT_KEYS` no existe.

## 2. Configuracion

Edita el `.env` raiz si necesitas cambiar base SQL, ventana, paralelismo o proyectos.

```env
CLARITY_NUM_OF_DAYS=3
CLARITY_MAX_WORKERS=4
```

Clarity solo permite `numOfDays` 1, 2 o 3.

## 3. Instalar dependencias

```powershell
pip install -r requirements.txt
```

## 4. Prueba segura

```powershell
python clarity_extractor_to_sql.py --dry-run --limit-projects 1
```

Para no consumir todos los requests de prueba del mismo proyecto:

```powershell
python clarity_extractor_to_sql.py --dry-run --limit-projects 1 --reports overview,by_device
```

## 5. Ejecucion completa

```powershell
python clarity_extractor_to_sql.py
```

El script crea/valida las tablas de SQL Server usando `00_create_clarity_tables.sql`.

La API de Clarity limita a 10 requests por proyecto por dia. La configuracion inicial usa 6 reportes por proyecto, asi que evita correr el proceso completo varias veces el mismo dia para la misma marca.

## 6. Tablas

- `dbo.clarity_projects`
- `dbo.clarity_etl_runs`
- `dbo.clarity_etl_report_loads`
- `dbo.clarity_live_insights`

`clarity_live_insights` guarda dimensiones normalizadas y `measures_json`/`row_json` para no romperse si Clarity agrega o cambia metricas.
