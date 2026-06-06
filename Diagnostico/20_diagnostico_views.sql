USE [Digital_Impact_Reportes];
GO
/* =========================================================
   DIAGNOSTICO — staging + primeras vistas accionables
     vw_clarity_url_norm   -> Clarity by_url_device con URL normalizada
                              (sin scheme+dominio, sin querystring) + tenant
     vw_ga4_item_funnel    -> embudo por producto vs mediana del catalogo
   ========================================================= */

/* ---- 20.1 Staging: Clarity URL normalizada -------------------------
   Resuelve la fragmentacion por querystring (~6x) y el mismatch de
   formato con GA4 (GA4 = path puro; Clarity = URL completa).
   page_path resultante calza con ga4_pages_12m.page_path. */
CREATE OR ALTER VIEW dbo.vw_clarity_url_norm AS
SELECT
    ci.project_name,
    t.property_name,                         -- crosswalk (UmbroPE -> Umbro)
    ci.num_of_days,
    ci.extraction_date_utc,
    ci.metric_name,
    ci.dimension2_value AS device,
    LOWER(
        CASE WHEN LEN(p.path_only) > 1 AND RIGHT(p.path_only,1) = '/'
             THEN LEFT(p.path_only, LEN(p.path_only)-1)
             ELSE p.path_only END
    ) AS page_path,
    TRY_CONVERT(float, JSON_VALUE(ci.measures_json,'$.totalSessionCount'))            AS sessions,
    TRY_CONVERT(float, JSON_VALUE(ci.measures_json,'$.distinctUserCount'))            AS users,
    TRY_CONVERT(float, JSON_VALUE(ci.measures_json,'$.subTotal'))                     AS subtotal,
    TRY_CONVERT(float, JSON_VALUE(ci.measures_json,'$.sessionsWithMetricPercentage')) AS sess_with_metric_pct,
    TRY_CONVERT(float, JSON_VALUE(ci.measures_json,'$.averageScrollDepth'))           AS avg_scroll_depth
FROM dbo.clarity_live_insights ci
LEFT JOIN dbo.dim_tenant t ON t.clarity_project = ci.project_name
CROSS APPLY (SELECT u = JSON_VALUE(ci.measures_json,'$.Url')) j
CROSS APPLY (SELECT noq = CASE WHEN CHARINDEX('?', j.u) > 0
                              THEN LEFT(j.u, CHARINDEX('?', j.u)-1) ELSE j.u END) q
CROSS APPLY (SELECT path_only =
    CASE
        WHEN CHARINDEX('//', q.noq) = 0 THEN q.noq                                  -- ya es path
        WHEN CHARINDEX('/', SUBSTRING(q.noq, CHARINDEX('//', q.noq)+2, 8000)) = 0
            THEN '/'                                                                -- solo dominio (home)
        ELSE SUBSTRING(q.noq,
                 CHARINDEX('//', q.noq) + 1
                 + CHARINDEX('/', SUBSTRING(q.noq, CHARINDEX('//', q.noq)+2, 8000)),
                 8000)
    END) p
WHERE ci.report_name = 'by_url_device'
  AND JSON_VALUE(ci.measures_json,'$.Url') IS NOT NULL;
GO

/* ---- 20.2 Embudo por producto (GA4 puro, 12m) ----------------------
   Detecta productos con trafico alto pero conversion vista->carrito
   anomala vs la mediana del catalogo de SU MISMA web.
   brecha_vs_mediana negativo = fuga estructural del producto.
   Piso de 50 vistas para que la tasa sea significativa. */
CREATE OR ALTER VIEW dbo.vw_ga4_item_funnel AS
WITH latest AS (
    SELECT property_id, MAX(end_date) AS end_date
    FROM dbo.ga4_items_12m GROUP BY property_id
),
base AS (
    SELECT i.property_id, i.property_name, i.item_id, i.item_name,
           i.items_viewed, i.items_added_to_cart, i.items_purchased, i.item_revenue
    FROM dbo.ga4_items_12m i
    JOIN latest l ON l.property_id = i.property_id AND l.end_date = i.end_date
    WHERE i.items_viewed >= 50
),
rates AS (
    SELECT *,
        CAST(items_added_to_cart AS float)/NULLIF(items_viewed,0) AS view_to_cart,
        CAST(items_purchased     AS float)/NULLIF(items_viewed,0) AS view_to_purchase
    FROM base
),
med AS (
    SELECT DISTINCT property_id,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY view_to_cart)
            OVER (PARTITION BY property_id) AS med_view_to_cart
    FROM rates
)
SELECT
    r.property_name, r.item_id, r.item_name,
    r.items_viewed, r.items_added_to_cart, r.items_purchased, r.item_revenue,
    CAST(r.view_to_cart        AS decimal(6,4)) AS view_to_cart,
    CAST(m.med_view_to_cart    AS decimal(6,4)) AS med_view_to_cart,
    CAST(r.view_to_cart - m.med_view_to_cart AS decimal(6,4)) AS brecha_vs_mediana
FROM rates r
JOIN med m ON m.property_id = r.property_id;
GO
