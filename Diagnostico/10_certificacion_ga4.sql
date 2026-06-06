USE [Digital_Impact_Reportes];
GO
/* =========================================================
   CERTIFICACION GA4 — "que sanear antes de confiar en la data"
   No transforma la data cruda; solo la clasifica y marca.
   Tres vistas:
     vw_ga4_page_class          -> clasifica cada URL (basura vs contenido)
     vw_ga4_channel_quality     -> banderas de atribucion por canal (Direct inflado, etc.)
     vw_ga4_certificacion_resumen -> 1 fila por web con el veredicto de confianza
   ========================================================= */

/* ---- 10.1 Clasificacion de paginas ---------------------------------
   Detecta URLs utilitarias (sizecharts, busqueda, checkout, cuenta,
   parametrizadas) que inflan sesiones y no son contenido real.
   es_utilitaria = 1  -> NO usar como contenido/landing en diagnostico. */
CREATE OR ALTER VIEW dbo.vw_ga4_page_class AS
WITH c AS (
    SELECT
        property_id, property_name, page_path, page_title,
        screen_page_views, sessions, purchase_revenue,
        CASE
            WHEN page_path LIKE '%sizechart%' OR page_path LIKE '%size-chart%'      THEN 'sizechart'
            WHEN page_path LIKE '/catalogsearch%' OR page_path LIKE '%?q=%'         THEN 'busqueda'
            WHEN page_path LIKE '/checkout%'                                        THEN 'checkout'
            WHEN page_path LIKE '%/cart%' OR page_path LIKE '%/carrito%'            THEN 'carrito'
            WHEN page_path LIKE '/customer%' OR page_path LIKE '%/account%'
                 OR page_path LIKE '%login%' OR page_path LIKE '%wishlist%'         THEN 'cuenta'
            WHEN page_path = '/' OR page_path = ''                                  THEN 'home'
            WHEN page_path LIKE '%?%'                                               THEN 'parametrizada'
            ELSE 'contenido'
        END AS page_type
    FROM dbo.ga4_pages_12m
)
SELECT
    c.*,
    CASE WHEN c.page_type IN ('sizechart','busqueda','checkout','carrito','cuenta','parametrizada')
         THEN 1 ELSE 0 END AS es_utilitaria
FROM c;
GO

/* ---- 10.2 Calidad de canal (atribucion) ----------------------------
   Banderas sobre share de sesiones / revenue por canal.
   Umbrales iniciales (ajustables): Direct > 35% sesiones o > 40% revenue;
   Unassigned > 5% sesiones. */
CREATE OR ALTER VIEW dbo.vw_ga4_channel_quality AS
WITH tot AS (
    SELECT property_id, property_name,
           SUM(sessions)         AS tot_ses,
           SUM(purchase_revenue) AS tot_rev
    FROM dbo.ga4_total_channels_12m
    GROUP BY property_id, property_name
)
SELECT
    c.property_id, c.property_name,
    c.session_default_channel_group                          AS canal,
    c.sessions, c.purchase_revenue, c.ecommerce_purchases,
    CAST(CAST(c.purchase_revenue AS float)/NULLIF(c.sessions,0) AS decimal(12,2)) AS rev_por_sesion,
    CAST(CAST(c.sessions         AS float)/NULLIF(t.tot_ses,0)  AS decimal(5,3))  AS share_sesiones,
    CAST(CAST(c.purchase_revenue AS float)/NULLIF(t.tot_rev,0)  AS decimal(5,3))  AS share_revenue,
    CASE
        WHEN c.session_default_channel_group='Direct'
             AND CAST(c.sessions AS float)/NULLIF(t.tot_ses,0) > 0.35
            THEN 'DIRECT_SESIONES_INFLADO'
        WHEN c.session_default_channel_group='Direct'
             AND CAST(c.purchase_revenue AS float)/NULLIF(t.tot_rev,0) > 0.40
            THEN 'DIRECT_REVENUE_DOMINANTE'
        WHEN c.session_default_channel_group='Unassigned'
             AND CAST(c.sessions AS float)/NULLIF(t.tot_ses,0) > 0.05
            THEN 'UNASSIGNED_ALTO'
        ELSE NULL
    END AS bandera
FROM dbo.ga4_total_channels_12m c
JOIN tot t ON t.property_id = c.property_id;
GO

/* ---- 10.3 Resumen de certificacion (1 fila por web) ----------------
   OJO: ses_total (de paginas) y ses_canal_total (de canales) vienen de
   reportes GA4 distintos y NO reconcilian (agregaciones/sampling/(other)).
   Por eso cada % es intra-fuente, no se cruzan magnitudes. */
CREATE OR ALTER VIEW dbo.vw_ga4_certificacion_resumen AS
WITH pg AS (
    SELECT property_id, property_name,
           SUM(sessions) AS ses_total_paginas,
           SUM(CASE WHEN es_utilitaria=1 THEN sessions ELSE 0 END) AS ses_utilitarias,
           SUM(CASE WHEN page_type='sizechart' THEN sessions ELSE 0 END) AS ses_sizechart
    FROM dbo.vw_ga4_page_class
    GROUP BY property_id, property_name
),
ch AS (
    SELECT property_id,
           SUM(sessions) AS ses_total_canales,
           SUM(CASE WHEN session_default_channel_group='Direct' THEN sessions ELSE 0 END)         AS ses_direct,
           SUM(CASE WHEN session_default_channel_group='Direct' THEN purchase_revenue ELSE 0 END) AS rev_direct,
           SUM(purchase_revenue) AS rev_total
    FROM dbo.ga4_total_channels_12m
    GROUP BY property_id
)
SELECT
    p.property_name,
    p.ses_total_paginas,
    CAST(1.0*p.ses_utilitarias/NULLIF(p.ses_total_paginas,0) AS decimal(5,3)) AS pct_ses_utilitarias,
    CAST(1.0*p.ses_sizechart  /NULLIF(p.ses_total_paginas,0) AS decimal(5,3)) AS pct_ses_sizechart,
    CAST(1.0*c.ses_direct     /NULLIF(c.ses_total_canales,0) AS decimal(5,3)) AS pct_ses_direct,
    CAST(1.0*c.rev_direct     /NULLIF(c.rev_total,0)         AS decimal(5,3)) AS pct_rev_direct
FROM pg p
LEFT JOIN ch c ON c.property_id = p.property_id;
GO
