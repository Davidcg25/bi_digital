# GA4 Multi-Property -> SQL Server

## 1. Crear tablas
Ejecuta en SQL Server Management Studio:

```sql
USE [Digital_Impact_Reportes];
GO
-- luego pega/ejecuta 00_create_ga4_tables.sql
```

> El error anterior de `ga4_landing_pages` se corrige separando:
> - `ga4_landing_pages_12m`
> - `ga4_landing_pages_monthly`

Así `year_month` ya no queda nullable dentro de un primary key.

## 2. Instalar dependencias

```bash
pip install -r requirements.txt
```

## 3. Configurar `.env`

Copia:

```bash
copy .env.example .env
```

o en PowerShell:

```powershell
Copy-Item .env.example .env
```

Edita `.env` si tu servidor SQL no es `localhost`.

## 4. Colocar credenciales GA4

Coloca `credenciales.json` en la misma carpeta de los scripts, o cambia:

```env
GA4_CREDENTIALS_FILE=credenciales.json
```

## 5. Primera prueba recomendada

Deja en `.env` solo 2 properties:

```env
PROPERTY_IDS_TO_RUN=407838284,427321367
```

Ejecuta:

```bash
python ga4_extractor_to_sql.py
```

## 6. Activar todas las marcas

Cuando valide bien, elimina o comenta:

```env
PROPERTY_IDS_TO_RUN=407838284,427321367
```

El script correrá todas las properties definidas en `ga4_config.py`.

## 7. Ventana mensual alineada y backfills

Los reportes **mensuales** (`monthly_*`, `landing_pages_monthly`) NO usan el `START_DATE`
crudo: usan `MONTHLY_START_DATE` = día 1 del mes de `START_DATE` (ver `ga4_config.py`).
Motivo: con ventana rodante (`LOOKBACK_DAYS=62` en el .bat), un start a mitad de mes
traía el mes de borde parcial y el upsert pisaba meses completos ya cargados
(pasó con 202505 y 202604 — reparado con backfill el 2026-06-10).
El mes en curso se carga y se asume **parcial** hasta que cierre.
Los reportes de rango (`*_12m`) sí siguen con la ventana rodante (su llave es
`start_date`+`end_date` exactos, no sufren el problema).

### Backfill de meses históricos

Correr solo los reportes mensuales con ventana de meses completos:

```powershell
$env:START_DATE='2025-01-01'          # siempre día 1
$env:END_DATE='2025-03-31'            # idealmente fin de mes (u hoy)
$env:REPORT_NAMES_TO_RUN='monthly_core,monthly_rates,monthly_events,monthly_channels,monthly_devices,landing_pages_monthly'
& D:\Proyectos\4_BI_Ecom\venv\Scripts\python.exe ga4_extractor_to_sql.py
```

`REPORT_NAMES_TO_RUN` evita ensuciar las tablas `*_12m` con ventanas atípicas.
El upsert borra `year_month BETWEEN START_YM AND END_YM` por property antes de insertar,
así que la ventana debe cubrir completos todos los meses que toca.

## 8. Validación rápida SQL

```sql
SELECT TOP 20 * FROM dbo.ga4_etl_runs ORDER BY run_id DESC;
SELECT TOP 20 * FROM dbo.ga4_etl_report_loads ORDER BY id DESC;

SELECT property_name, year_month, sessions, purchase_revenue, ecommerce_purchases
FROM dbo.ga4_monthly_core
ORDER BY year_month DESC, property_name;
```
