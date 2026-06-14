-- ============================================================================
-- 02_create_search_terms_tables.sql — búsqueda interna del sitio (GA4 searchTerm)
--
-- Alimenta la Q3 del GPM (qué busca la gente dentro de la web) y, más adelante,
-- el scraper de search in-site (cruce demanda × surtido). El extractor descarta
-- el bucket vacío de searchTerm (eventos sin búsqueda, que dominan el volumen) y
-- guarda el término CRUDO + su hash (igual que page_path); la normalización
-- (trim/lower, dedup de 'chuck 70' vs ' Chuck 70') vive en vw_ga4_search_terms_*.
--
-- delete_existing del extractor hace DELETE antes del primer INSERT → la tabla
-- debe existir de antemano (este script). Mismo patrón/tipos que
-- ga4_landing_pages_monthly.
--
-- Solo grano MENSUAL: la ventana 12m es demasiado amplia para search terms; 1m /
-- 3m y el mismo periodo LY se derivan agregando meses desde esta tabla.
--
-- Ejecutar: sqlcmd -S localhost -E -C -d Digital_Impact_Reportes -i 02_create_search_terms_tables.sql
-- ============================================================================

-- Grano MENSUAL (meses cerrados)
IF OBJECT_ID('dbo.ga4_search_terms_monthly', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ga4_search_terms_monthly (
        property_id      varchar(30)   NOT NULL,
        property_name    varchar(100)  NOT NULL,
        year_month       char(6)       NOT NULL,
        search_term_hash char(64)      NOT NULL,
        search_term      nvarchar(2000) NOT NULL,
        event_count      float          NULL,
        sessions         float          NULL,
        total_users      float          NULL,
        run_id           int            NULL,
        extracted_at     datetime2      NOT NULL,
        CONSTRAINT pk_ga4_search_terms_monthly
            PRIMARY KEY (property_id, year_month, search_term_hash)
    );
    CREATE INDEX ix_ga4_search_terms_monthly_ym
        ON dbo.ga4_search_terms_monthly (year_month);
END;
GO
