-- ============================================================================
-- 40_ops_views.sql — Capa certificada sobre dbo.Magento_Orders + embudo macro
--
-- Tres vistas:
--   vw_magento_orders_linea  : grano LÍNEA (id+sku) — colapsa el split por
--                              almacén (source_id) en una fila por ítem.
--   vw_magento_orders_pedido : grano PEDIDO (1 fila por orden).
--   vw_ops_diaria            : embudo macro por web y día —
--                              GA4 (demanda) × Magento (pedido) × RMH (facturado).
--
-- Convenciones:
--   * web_key usa el nombre de property GA4 / Tienda_ecom RMH ('Converse',
--     'Caterpillar', 'Coliseum', ...). Chile y Marketplaces solo existen en
--     Magento (GA4 local no tiene properties Chile; RMH local es Perú).
--   * Filtro de confianza created >= '2026-02-01' INCORPORADO: el master del
--     droplet solo es completo desde su go-live (2026-01-26); antes de eso
--     trae casi solo cancelaciones tardías. 2025 vive en el histórico
--     congelado Ventas_Solidez_Magento_*.
--   * qty_confirmed es por SOURCE (se suma); qty_ordered viene repetido por
--     source (se toma MAX por línea). Lección qty_confirmed vs qty_ordered.
--   * mc = sku sin el último segmento '-talla'; matchea item_id de GA4 y MC
--     del stock RMH.
--
-- Ejecutar: sqlcmd -S localhost -E -C -d Digital_Impact_Reportes -i 40_ops_views.sql
-- ============================================================================

-- ----------------------------------------------------------------------------
-- Grano LÍNEA (id + sku)
-- ----------------------------------------------------------------------------
CREATE OR ALTER VIEW dbo.vw_magento_orders_linea AS
SELECT
    o.id,
    MAX(o.order_id)                              AS order_id,
    CONVERT(date, MIN(o.created))                AS fecha,
    MIN(o.created)                               AS created,
    -- web_key por store_id (estable, inmune a encoding); convención GA4/RMH
    CASE MAX(o.store_id)
        WHEN 4  THEN 'Converse'
        WHEN 19 THEN 'Caterpillar'
        WHEN 25 THEN 'Coliseum'
        WHEN 11 THEN 'New Balance'
        WHEN 13 THEN 'Merrell'
        WHEN 16 THEN 'Steve Madden'
        WHEN 30 THEN 'Umbro'
        WHEN 39 THEN 'Marketplaces Peru'
        WHEN 10 THEN 'Converse Chile'
        WHEN 22 THEN 'Coliseum Chile'
        WHEN 27 THEN 'Fila Chile'
        WHEN 31 THEN 'Umbro Chile'
        ELSE CONCAT('store_', MAX(o.store_id))
    END                                          AS web_key,
    MAX(o.order_status)                          AS estado,
    CASE
        WHEN MAX(o.order_status) IN ('canceled', 'fraud')                  THEN 'cancelada'
        WHEN MAX(o.order_status) IN ('pending', 'payment_review', 'holded') THEN 'pendiente_pago'
        WHEN MAX(o.order_status) = 'closed'                                THEN 'devuelta'
        WHEN MAX(o.order_status) IN ('complete', 'pedido_entregado',
                                     'package_delivered', 'delivered')     THEN 'entregada'
        ELSE 'en_proceso'
    END                                          AS estado_grupo,
    o.sku,
    CASE WHEN o.sku LIKE '%-%'
         THEN LEFT(o.sku, LEN(o.sku) - CHARINDEX('-', REVERSE(o.sku)))
         ELSE o.sku END                          AS mc,
    CASE WHEN o.sku LIKE '%-%'
         THEN RIGHT(o.sku, CHARINDEX('-', REVERSE(o.sku)) - 1)
         ELSE NULL END                           AS talla,
    MAX(o.product_name)                          AS product_name,
    MAX(o.qty_ordered)                           AS qty_ordered,    -- repetida por source
    SUM(o.qty_confirmed)                         AS qty_confirmed,  -- por source: se suma
    COUNT(DISTINCT o.source_id)                  AS n_sources,
    MAX(CAST(o.pago_confirmado AS tinyint))      AS pago_confirmado,
    MAX(o.original_price)                        AS original_price,
    MAX(o.price)                                 AS price,
    MAX(o.row_total)                             AS row_total,
    MAX(o.grand_total_item)                      AS grand_total_item,
    MAX(o.coupon_code)                           AS coupon_code,
    MAX(o.payment_method)                        AS payment_method,
    MAX(o.saleschannel)                          AS saleschannel,
    MAX(o.customer_id)                           AS customer_id,
    MAX(o.utm_source)                            AS utm_source,
    MAX(o.utm_medium)                            AS utm_medium,
    MAX(o.utm_campaign)                          AS utm_campaign
FROM dbo.Magento_Orders o
WHERE o.created >= '2026-02-01'   -- filtro de confianza (go-live ingest droplet)
GROUP BY o.id, o.sku;
GO

-- ----------------------------------------------------------------------------
-- Grano PEDIDO (1 fila por orden)
-- ----------------------------------------------------------------------------
CREATE OR ALTER VIEW dbo.vw_magento_orders_pedido AS
WITH lin AS (   -- colapsa primero el split por source para no inflar qty/montos
    SELECT
        o.id, o.sku,
        MAX(o.order_id)          AS order_id,
        MIN(o.created)           AS created,
        MAX(o.store_id)          AS store_id,
        MAX(o.order_status)      AS order_status,
        MAX(o.qty_ordered)       AS qty_ordered,
        SUM(o.qty_confirmed)     AS qty_confirmed,
        MAX(o.row_total)         AS row_total,
        MAX(o.grand_total_item)  AS grand_total_item,
        MAX(o.grand_total_purchased) AS grand_total_purchased,
        MAX(o.total_shipping_charges) AS total_shipping_charges,
        MAX(o.payment_value)     AS payment_value,
        MAX(o.customer_id)       AS customer_id,
        MAX(o.courrier)          AS courrier,
        MAX(o.departamento)      AS departamento,
        MAX(o.provincia)         AS provincia,
        MAX(o.distrito)          AS distrito,
        MAX(o.coupon_code)       AS coupon_code,
        MAX(o.promo_id)          AS promo_id,
        MAX(o.discount_name)     AS discount_name,
        MAX(o.coupon_discount)   AS coupon_discount,
        MAX(o.price_discount)    AS price_discount,
        MAX(o.payment_method)    AS payment_method,
        MAX(CAST(o.pago_confirmado AS tinyint)) AS pago_confirmado,
        MAX(o.cuotas)            AS cuotas,
        MAX(o.tarjeta_de_credito_o_debito) AS tarjeta,
        MAX(o.invoice_date)      AS invoice_date,
        MAX(o.saleschannel)      AS saleschannel,
        MAX(o.utm_source)        AS utm_source,
        MAX(o.utm_medium)        AS utm_medium,
        MAX(o.utm_campaign)      AS utm_campaign,
        MAX(o.session_type)      AS session_type,
        MAX(o.purchaseorigin_created_at) AS purchaseorigin_created_at,
        COUNT(DISTINCT o.source_id)      AS n_sources_linea
    FROM dbo.Magento_Orders o
    WHERE o.created >= '2026-02-01'   -- filtro de confianza
    GROUP BY o.id, o.sku
),
src AS (        -- # de almacenes DISTINTOS que abastecen el pedido completo
    SELECT id, COUNT(DISTINCT source_id) AS n_sources
    FROM dbo.Magento_Orders
    WHERE created >= '2026-02-01'
    GROUP BY id
)
SELECT
    l.id,
    MAX(l.order_id)                              AS order_id,
    CONVERT(date, MIN(l.created))                AS fecha,
    MIN(l.created)                               AS created,
    CASE MAX(l.store_id)
        WHEN 4  THEN 'Converse'
        WHEN 19 THEN 'Caterpillar'
        WHEN 25 THEN 'Coliseum'
        WHEN 11 THEN 'New Balance'
        WHEN 13 THEN 'Merrell'
        WHEN 16 THEN 'Steve Madden'
        WHEN 30 THEN 'Umbro'
        WHEN 39 THEN 'Marketplaces Peru'
        WHEN 10 THEN 'Converse Chile'
        WHEN 22 THEN 'Coliseum Chile'
        WHEN 27 THEN 'Fila Chile'
        WHEN 31 THEN 'Umbro Chile'
        ELSE CONCAT('store_', MAX(l.store_id))
    END                                          AS web_key,
    MAX(l.order_status)                          AS estado,
    CASE
        WHEN MAX(l.order_status) IN ('canceled', 'fraud')                  THEN 'cancelada'
        WHEN MAX(l.order_status) IN ('pending', 'payment_review', 'holded') THEN 'pendiente_pago'
        WHEN MAX(l.order_status) = 'closed'                                THEN 'devuelta'
        WHEN MAX(l.order_status) IN ('complete', 'pedido_entregado',
                                     'package_delivered', 'delivered')     THEN 'entregada'
        ELSE 'en_proceso'
    END                                          AS estado_grupo,
    MAX(l.customer_id)                           AS customer_id,
    MAX(l.courrier)                              AS courrier,
    MAX(l.departamento)                          AS departamento,
    MAX(l.provincia)                             AS provincia,
    MAX(l.distrito)                              AS distrito,
    MAX(l.payment_method)                        AS payment_method,
    MAX(l.pago_confirmado)                       AS pago_confirmado,
    MAX(l.cuotas)                                AS cuotas,
    MAX(l.tarjeta)                               AS tarjeta,
    MAX(l.saleschannel)                          AS saleschannel,
    MAX(l.coupon_code)                           AS coupon_code,
    MAX(l.promo_id)                              AS promo_id,
    MAX(l.discount_name)                         AS discount_name,
    SUM(l.coupon_discount)                       AS coupon_discount,
    SUM(l.price_discount)                        AS price_discount,
    MAX(l.invoice_date)                          AS invoice_date,
    MAX(l.grand_total_purchased)                 AS grand_total,
    -- Venta de ítems (sin shipping, con IGV): la métrica de "ingresos" del
    -- overview de Novedades; /1.18 la hace comparable con TotalNeto RMH.
    SUM(l.grand_total_item)                      AS venta_items,
    MAX(l.total_shipping_charges)                AS envio_cobrado,
    MAX(l.payment_value)                         AS payment_value,
    COUNT(*)                                     AS n_lineas,
    MAX(s.n_sources)                             AS n_sources,
    SUM(l.qty_ordered)                           AS unidades_pedidas,
    SUM(l.qty_confirmed)                         AS unidades_confirmadas,
    MAX(l.utm_source)                            AS utm_source,
    MAX(l.utm_medium)                            AS utm_medium,
    MAX(l.utm_campaign)                          AS utm_campaign,
    MAX(l.session_type)                          AS session_type,
    MAX(l.purchaseorigin_created_at)             AS purchaseorigin_created_at
FROM lin l
JOIN src s ON s.id = l.id
GROUP BY l.id;
GO

-- ----------------------------------------------------------------------------
-- Embudo macro diario: GA4 (demanda) × Magento (pedido) × RMH (facturado)
-- Solo webs Perú con GA4/RMH tienen las 3 capas; Chile/Marketplaces traen
-- la capa Magento (y Marketplaces además RMH vía Falabella/Ripley/MeLi).
-- ----------------------------------------------------------------------------
CREATE OR ALTER VIEW dbo.vw_ops_diaria AS
WITH ga4 AS (
    SELECT property_name AS web_key, [date] AS fecha,
           SUM(sessions)             AS sesiones,
           SUM(ecommerce_purchases)  AS compras_ga4,
           SUM(purchase_revenue)     AS revenue_ga4
    FROM dbo.ga4_daily_core
    GROUP BY property_name, [date]
),
mag AS (
    SELECT web_key, fecha,
           COUNT(*)                                                        AS pedidos,
           SUM(CASE WHEN pago_confirmado = 1 THEN 1 ELSE 0 END)            AS pedidos_pagados,
           SUM(CASE WHEN estado_grupo = 'cancelada'      THEN 1 ELSE 0 END) AS pedidos_cancelados,
           SUM(CASE WHEN estado_grupo = 'pendiente_pago' THEN 1 ELSE 0 END) AS pedidos_pendientes,
           SUM(CASE WHEN pago_confirmado = 1 THEN unidades_confirmadas ELSE 0 END) AS unidades_pagadas,
           -- Venta pagada de ítems (sin shipping, con IGV) y su neta sin IGV:
           -- comparable con facturado_rmh (TotalNeto). grand_total_purchased
           -- NO se usa aquí porque incluye el cobro de envío.
           SUM(CASE WHEN pago_confirmado = 1 THEN venta_items ELSE 0 END)  AS venta_pagada,
           SUM(CASE WHEN pago_confirmado = 1 THEN venta_items ELSE 0 END) / 1.18 AS venta_pagada_neta,
           SUM(CASE WHEN estado_grupo = 'cancelada' THEN venta_items ELSE 0 END) AS venta_cancelada
    FROM dbo.vw_magento_orders_pedido
    GROUP BY web_key, fecha
),
rmh AS (
    SELECT CASE WHEN Tienda_ecom IN ('Falabella', 'Ripley', 'MercadoLibre')
                THEN 'Marketplaces Peru' ELSE Tienda_ecom END AS web_key,
           fecha,
           SUM(ordenes_rmh)                AS ordenes_rmh,
           SUM(unidades_rmh)               AS unidades_rmh,
           SUM(ingresos_rmh_ecommerce)     AS facturado_rmh,
           SUM(contribucion_rmh_ecommerce) AS contribucion_rmh
    FROM dbo.vw_rmh_ecommerce_diario_tienda
    GROUP BY CASE WHEN Tienda_ecom IN ('Falabella', 'Ripley', 'MercadoLibre')
                  THEN 'Marketplaces Peru' ELSE Tienda_ecom END, fecha
)
SELECT
    COALESCE(g.web_key, m.web_key, r.web_key) AS web_key,
    COALESCE(g.fecha, m.fecha, r.fecha)       AS fecha,
    -- Demanda (GA4)
    g.sesiones, g.compras_ga4, g.revenue_ga4,
    CAST(1.0 * g.compras_ga4 / NULLIF(g.sesiones, 0) AS decimal(18,6))            AS cr_ga4,
    -- Pedido (Magento)
    m.pedidos, m.pedidos_pagados, m.pedidos_cancelados, m.pedidos_pendientes,
    m.unidades_pagadas, m.venta_pagada, m.venta_pagada_neta, m.venta_cancelada,
    CAST(1.0 * m.pedidos_cancelados / NULLIF(m.pedidos, 0) AS decimal(18,6))      AS tasa_cancelacion,
    -- Facturado (RMH, neto)
    r.ordenes_rmh, r.unidades_rmh, r.facturado_rmh, r.contribucion_rmh,
    CAST(1.0 * r.facturado_rmh / NULLIF(m.venta_pagada_neta, 0)
         AS decimal(18,6))                                                        AS ratio_facturado_vs_pagado
FROM ga4 g
FULL JOIN mag m ON m.web_key = g.web_key AND m.fecha = g.fecha
FULL JOIN rmh r ON r.web_key = COALESCE(g.web_key, m.web_key)
               AND r.fecha   = COALESCE(g.fecha, m.fecha);
GO
