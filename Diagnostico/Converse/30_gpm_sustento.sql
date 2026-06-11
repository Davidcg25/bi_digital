-- ============================================================================
-- 30_gpm_sustento.sql — Sustento numérico del GPM Digital Insights (Converse DTC PE)
--
-- Reproduce los números detrás de cada respuesta de la hoja "Digital Insights"
-- del template GPM_DTC_PER_YYYYMMDD.xlsx. Calibrado contra el GPM de abril-2026
-- (GPM_DTC_PER_20260508.xlsx en esta carpeta). Cambiar @mes para otro cierre.
--
-- Definiciones (ver README_SUSTENTO.md):
--   * Venta = RMH neto (rpt.v_ventas_base, Tienda_final='Converse', es_ecom=1).
--     David reporta sobre RMH, NO sobre GA4. Magento ventas congelado feb-2026.
--   * Core (Q8)        = Coleccion LIKE '%CORE%'
--   * Elevation (Q9)   = Descripcion LIKE '%PLAT%' (Platform)
--   * Q10: base UNIDADES, dscto = 1 - Precio/FullPrecio redondeado a 0.05
--     (MROUND); FullPrecio>0; sobreprecios (bucket negativo) cuentan como full
--     price; el grupo "full + <20%" INCLUYE el bucket 0.20.
--   * Stock web = Stock_Solidez_RMH con Integrada LIKE 'S%', ÚLTIMO snapshot
--     del mes (jamás SUM entre fechas: son ~20 snapshots diarios).
-- ============================================================================

DECLARE @mes      char(7) = '2026-04';   -- mes reportado (cerrado)
DECLARE @mes_prev char(7) = '2026-03';   -- mes anterior (vs prior month)
DECLARE @mes_ly   char(7) = '2025-04';   -- mismo mes año anterior (vs LY)

-- ---------------------------------------------------------------------------
-- Q4 + Q6 — Revenue / unidades / AUR / margen (mes vs prev vs LY)
-- ---------------------------------------------------------------------------
SELECT 'Q4_Q6_venta_rmh' AS bloque, AnioMes,
       ROUND(SUM(TotalNeto), 0)                                   AS revenue_neto,
       SUM(Cantidad)                                              AS unidades,
       ROUND(SUM(TotalNeto) / NULLIF(SUM(Cantidad), 0), 2)        AS aur,
       ROUND(100.0 * (1.0 - SUM(TotalCosto) / NULLIF(SUM(TotalNeto), 0)), 1) AS gm_pct
FROM rpt.v_ventas_base
WHERE Tienda_final = 'Converse' AND es_ecom = 1
  AND AnioMes IN (@mes, @mes_prev, @mes_ly)
GROUP BY AnioMes
ORDER BY AnioMes;

-- ---------------------------------------------------------------------------
-- Q5 — Tráfico y conversión (GA4 mensual, property Converse)
-- ---------------------------------------------------------------------------
SELECT 'Q5_trafico_ga4' AS bloque, year_month,
       CAST(sessions AS int)                                       AS sesiones,
       CAST(ecommerce_purchases AS int)                            AS compras,
       ROUND(100.0 * ecommerce_purchases / NULLIF(sessions, 0), 2) AS cr_pct
FROM dbo.ga4_monthly_core
WHERE property_name = 'Converse'
  AND year_month IN (REPLACE(@mes,'-',''), REPLACE(@mes_prev,'-',''), REPLACE(@mes_ly,'-',''))
ORDER BY year_month;

-- ---------------------------------------------------------------------------
-- Q8 — Core (Coleccion CORE): venta, share y margen del segmento
-- ---------------------------------------------------------------------------
SELECT 'Q8_core' AS bloque, AnioMes,
       ROUND(SUM(CASE WHEN UPPER(Coleccion) LIKE '%CORE%' THEN TotalNeto ELSE 0 END), 0) AS revenue_core,
       SUM(CASE WHEN UPPER(Coleccion) LIKE '%CORE%' THEN Cantidad ELSE 0 END)            AS unidades_core,
       ROUND(100.0 * SUM(CASE WHEN UPPER(Coleccion) LIKE '%CORE%' THEN TotalNeto ELSE 0 END)
             / NULLIF(SUM(TotalNeto), 0), 1)                                             AS share_revenue_pct,
       ROUND(100.0 * (1.0 - SUM(CASE WHEN UPPER(Coleccion) LIKE '%CORE%' THEN TotalCosto ELSE 0 END)
             / NULLIF(SUM(CASE WHEN UPPER(Coleccion) LIKE '%CORE%' THEN TotalNeto ELSE 0 END), 0)), 1) AS gm_core_pct
FROM rpt.v_ventas_base
WHERE Tienda_final = 'Converse' AND es_ecom = 1
  AND AnioMes IN (@mes, @mes_prev, @mes_ly)
GROUP BY AnioMes
ORDER BY AnioMes;

-- ---------------------------------------------------------------------------
-- Q9 — Elevation / Platform (Descripcion %PLAT%): venta, share y margen
-- ---------------------------------------------------------------------------
SELECT 'Q9_platform' AS bloque, AnioMes,
       ROUND(SUM(CASE WHEN UPPER(Descripcion) LIKE '%PLAT%' THEN TotalNeto ELSE 0 END), 0) AS revenue_plat,
       SUM(CASE WHEN UPPER(Descripcion) LIKE '%PLAT%' THEN Cantidad ELSE 0 END)            AS unidades_plat,
       ROUND(100.0 * SUM(CASE WHEN UPPER(Descripcion) LIKE '%PLAT%' THEN TotalNeto ELSE 0 END)
             / NULLIF(SUM(TotalNeto), 0), 1)                                               AS share_revenue_pct
FROM rpt.v_ventas_base
WHERE Tienda_final = 'Converse' AND es_ecom = 1
  AND AnioMes IN (@mes, @mes_prev, @mes_ly)
GROUP BY AnioMes
ORDER BY AnioMes;

-- ---------------------------------------------------------------------------
-- Q10 — Full price vs discounted (base UNIDADES, buckets de 5%)
--   bucket_pct entero (… -5, 0, 5, 10, 15, 20, 25 …); negativos = sobreprecio
--   A) full = bucket <= 0 | discounted = bucket > 0
--   B) "full + <20%" INCLUYE el bucket 20 | resto = > 20%
-- ---------------------------------------------------------------------------
;WITH v AS (
    SELECT Cantidad,
           CAST(ROUND((1.0 - Precio / NULLIF(FullPrecio, 0)) * 20.0, 0) * 5 AS int) AS bucket_pct
    FROM rpt.v_ventas_base
    WHERE Tienda_final = 'Converse' AND es_ecom = 1
      AND AnioMes = @mes AND FullPrecio > 0
)
SELECT 'Q10_full_vs_dscto' AS bloque,
       ROUND(100.0 * SUM(CASE WHEN bucket_pct <= 0  THEN Cantidad ELSE 0 END) / NULLIF(SUM(Cantidad), 0), 0) AS a_full_price_pct,
       ROUND(100.0 * SUM(CASE WHEN bucket_pct > 0   THEN Cantidad ELSE 0 END) / NULLIF(SUM(Cantidad), 0), 0) AS a_discounted_pct,
       ROUND(100.0 * SUM(CASE WHEN bucket_pct <= 20 THEN Cantidad ELSE 0 END) / NULLIF(SUM(Cantidad), 0), 0) AS b_full_y_hasta20_pct,
       ROUND(100.0 * SUM(CASE WHEN bucket_pct > 20  THEN Cantidad ELSE 0 END) / NULLIF(SUM(Cantidad), 0), 0) AS b_mayor20_pct,
       SUM(Cantidad) AS unidades_base
FROM v;

-- Detalle por bucket (para auditar el corte A/B)
;WITH v AS (
    SELECT Cantidad,
           CAST(ROUND((1.0 - Precio / NULLIF(FullPrecio, 0)) * 20.0, 0) * 5 AS int) AS bucket_pct
    FROM rpt.v_ventas_base
    WHERE Tienda_final = 'Converse' AND es_ecom = 1
      AND AnioMes = @mes AND FullPrecio > 0
)
SELECT 'Q10_buckets' AS bloque, bucket_pct,
       SUM(Cantidad) AS unidades,
       ROUND(100.0 * SUM(Cantidad) / NULLIF(SUM(SUM(Cantidad)) OVER (), 0), 1) AS share_pct
FROM v
GROUP BY bucket_pct
ORDER BY bucket_pct;

-- ---------------------------------------------------------------------------
-- Contexto Q8/Q9 — Stock web disponible (último snapshot del mes, Integrada)
-- ---------------------------------------------------------------------------
;WITH ult AS (
    SELECT MAX(TRY_CONVERT(date, Fecha)) AS f
    FROM dbo.Stock_Solidez_RMH
    WHERE Marca_Limpia = 'Converse'
      AND TRY_CONVERT(date, Fecha) <= EOMONTH(CONVERT(date, @mes + '-01'))
)
SELECT 'stock_web_contexto' AS bloque, s.Fecha,
       SUM(CASE WHEN UPPER(s.Coleccion)  LIKE '%CORE%' THEN s.stock ELSE 0 END) AS stock_core,
       SUM(CASE WHEN UPPER(s.Descripcion) LIKE '%PLAT%' THEN s.stock ELSE 0 END) AS stock_platform,
       SUM(s.stock) AS stock_total_web
FROM dbo.Stock_Solidez_RMH s
CROSS JOIN ult
WHERE s.Marca_Limpia = 'Converse'
  AND s.Integrada LIKE 'S%'
  AND TRY_CONVERT(date, s.Fecha) = ult.f
GROUP BY s.Fecha;
