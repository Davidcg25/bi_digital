# Magento_Orders — réplica local de órdenes (API export del droplet)

Consumidor de `GET /api/export/orders` (Flask del droplet, Bearer token) que
mantiene `dbo.Magento_Orders` en `Digital_Impact_Reportes`. Grano línea de
ítem por source (PK `id+sku+source_id`), **sin PII** (sin nombres, DNI,
email, teléfono ni dirección exacta; sí distrito/provincia/departamento,
`customer_id` y atribución UTM del sidecar purchaseorigin).

Reemplaza la ingesta manual vieja de `Order_Magento/` (Magento API → NDJSON →
`Ventas_Solidez_Magento_*`, congelada en feb-2026; esas tablas quedan como
histórico).

## Setup (una vez)

```powershell
# 1. Tablas destino
sqlcmd -S localhost -E -C -d Digital_Impact_Reportes -i 00_create_magento_orders_tables.sql

# 2. Token: copiar .env.example a .env y pegar EXPORT_API_TOKEN (mismo del droplet)

# 3. Prueba segura (no escribe)
..\venv\Scripts\python.exe etl_magento_orders.py --since 2026-06-01 --limit 20 --dry-run

# 4. Backfill inicial (desde 2025 para tener vs LY de geografía/pago/courier)
..\venv\Scripts\python.exe etl_magento_orders.py --since 2025-01-01

# 5. Validar idempotencia: repetir el backfill de un día y verificar que no duplica
```

## Operación

- **Incremental diario** (tarea `magento_orders_solidez`, batch
  `sincronizacion_magento_orders.bat`): sin argumentos, arranca desde el último
  `watermark_after` exitoso de `dbo.magento_etl_runs` menos `EXPORT_OVERLAP_DAYS` (2).
- El backfill itera **ventanas de 30 días** (`EXPORT_WINDOW_DAYS`) para no
  castigar el droplet con OFFSET profundo.
- `--dry-run` descarga y valida sin escribir; `--limit N` corta tras N filas.
- Errores 401/403 = token mal configurado (exit 1 inmediato). 429/5xx se
  reintentan con backoff (5 intentos).

## Completitud de la fuente (leer antes de reportar)

`order_magento_master` en el droplet existe desde el **2026-01-26** (primer
`_ingested_at`). Órdenes creadas antes solo aparecen si fueron tocadas después
(ej. cancelaciones masivas de pendientes 2025 → oct-dic 2025 son ~100% canceled).
**Usar la tabla para reporting solo desde 2026-02 (primer mes completo).**
Para 2025 el histórico sigue siendo `Ventas_Solidez_Magento_2025` (congelado).

## Nota técnica: sin fast_executemany

El INSERT al `#stage` va SIN `fast_executemany`: pyodbc no puede describir
parámetros contra tablas temporales (SQLDescribeParam) y cae a un buffer de
255 chars → `String data, right truncation ... buffer 510` con cualquier
string >255 (referrers), sin importar el ancho declarado. Verificado con repro
mínimo el 2026-06-11. Costo: ~5 min para el backfill completo, segundos en el
incremental diario. `diag_lengths.py` mide longitudes por campo de una ventana
del API si vuelve a aparecer un error de truncamiento.

## Validaciones post-backfill

```sql
-- Volumen por mes vs realidad conocida
SELECT FORMAT(created,'yyyyMM') ym, COUNT(*) filas, COUNT(DISTINCT order_id) ordenes
FROM dbo.Magento_Orders GROUP BY FORMAT(created,'yyyyMM') ORDER BY ym;

-- Cuadre de un mes contra el histórico congelado (orden de magnitud)
SELECT COUNT(DISTINCT order_id) FROM dbo.Magento_Orders
WHERE created >= '2025-08-01' AND created < '2025-09-01';
SELECT COUNT(DISTINCT order_id) FROM dbo.Ventas_Solidez_Magento_2025
WHERE created >= '2025-08-01' AND created < '2025-09-01';

-- Runs
SELECT TOP 10 * FROM dbo.magento_etl_runs ORDER BY run_id DESC;
```
