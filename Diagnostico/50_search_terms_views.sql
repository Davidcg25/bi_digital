-- ============================================================================
-- 50_search_terms_views.sql — normalización de búsqueda interna (GA4 searchTerm)
--
-- El extractor guarda el término CRUDO (con mayúsculas/espacios: 'chuck 70',
-- ' Chuck 70', 'CHUCK 70' son filas distintas). Estas vistas normalizan
-- (trim + lower + colapso de espacios) y consolidan, igual que vw_clarity_url_norm
-- hace con las URLs. Alimentan la Q3 del GPM (qué busca la gente en la web) y el
-- futuro scraper de search in-site (cruce demanda × surtido).
--
-- term_norm = búsqueda canónica. rango_pos = ranking por sesiones dentro de la
-- property+mes. Para 1m usar el último year_month; para 3m / LY agregar varios
-- meses desde esta vista (la ventana 12m se descartó por demasiado amplia).
--
-- Ejecutar: sqlcmd -S localhost -E -C -d Digital_Impact_Reportes -i 50_search_terms_views.sql
-- ============================================================================

CREATE OR ALTER VIEW dbo.vw_ga4_search_terms_monthly_norm AS
WITH norm AS (
    SELECT property_id, property_name, year_month,
           LTRIM(RTRIM(LOWER(
               REPLACE(REPLACE(REPLACE(search_term, CHAR(9), ' '), CHAR(13), ' '), CHAR(10), ' ')
           ))) AS term_norm,
           event_count, sessions, total_users
    FROM dbo.ga4_search_terms_monthly
),
agg AS (
    SELECT property_id, property_name, year_month, term_norm,
           SUM(event_count) AS event_count,
           SUM(sessions)    AS sessions,
           SUM(total_users) AS total_users
    FROM norm
    WHERE term_norm <> ''
    GROUP BY property_id, property_name, year_month, term_norm
)
SELECT *,
       ROW_NUMBER() OVER (PARTITION BY property_id, year_month ORDER BY sessions DESC) AS rango_pos
FROM agg;
GO
