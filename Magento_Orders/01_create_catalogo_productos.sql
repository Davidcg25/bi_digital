-- ============================================================================
-- 01_create_catalogo_productos.sql — réplica local del catálogo del hub
-- Fuente: GET /api/export/catalog (catalogo_ops.ch_catalog_products, droplet).
-- Full refresh por corrida (etl_catalogo_productos.py). Sin PII.
-- Ejecutar: sqlcmd -S localhost -E -C -d Digital_Impact_Reportes -i 01_create_catalogo_productos.sql
-- ============================================================================

IF OBJECT_ID('dbo.Catalogo_Productos', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.Catalogo_Productos (
        id                 int            NOT NULL,
        magento_id         bigint         NULL,
        sku_tipo           varchar(20)    NULL,
        mc                 nvarchar(100)  NOT NULL,
        codigo_gp          nvarchar(100)  NULL,
        marca              nvarchar(100)  NULL,
        linea              nvarchar(100)  NULL,
        genero             nvarchar(60)   NULL,
        descripcion        nvarchar(500)  NULL,
        coleccion          nvarchar(100)  NULL,
        temporada          nvarchar(60)   NULL,
        name_web           nvarchar(500)  NULL,
        url_key            nvarchar(300)  NULL,
        base_image         nvarchar(500)  NULL,
        small_image        nvarchar(500)  NULL,
        price_web          decimal(12,4)  NULL,
        special_price_web  decimal(12,4)  NULL,
        producto_vigente   bit            NULL,
        web_segmentado     bit            NULL,
        updated_at         datetime2      NULL,
        extracted_at       datetime2      NOT NULL DEFAULT SYSUTCDATETIME(),
        CONSTRAINT pk_catalogo_productos PRIMARY KEY (id)
    );
    CREATE INDEX ix_catalogo_mc        ON dbo.Catalogo_Productos (mc);
    CREATE INDEX ix_catalogo_codigo_gp ON dbo.Catalogo_Productos (codigo_gp);
END;
