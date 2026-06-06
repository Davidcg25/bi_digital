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

## 7. Validación rápida SQL

```sql
SELECT TOP 20 * FROM dbo.ga4_etl_runs ORDER BY run_id DESC;
SELECT TOP 20 * FROM dbo.ga4_etl_report_loads ORDER BY id DESC;

SELECT property_name, year_month, sessions, purchase_revenue, ecommerce_purchases
FROM dbo.ga4_monthly_core
ORDER BY year_month DESC, property_name;
```
