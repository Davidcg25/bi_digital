-- Tablas mensuales nuevas: campañas y items (vistas por producto).
-- Idempotente: solo crea si no existen. Espeja el patrón de ga4_monthly_channels
-- (campañas) y ga4_items_12m (items, pero a grano mensual por item_id = CodColor).

IF OBJECT_ID('dbo.ga4_monthly_campaigns', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ga4_monthly_campaigns (
        property_id VARCHAR(30) NOT NULL,
        property_name VARCHAR(100) NOT NULL,
        year_month CHAR(6) NOT NULL,
        session_campaign_name NVARCHAR(420) NOT NULL,   -- PK <900 bytes
        sessions FLOAT NULL,
        purchase_revenue FLOAT NULL,
        ecommerce_purchases FLOAT NULL,
        run_id INT NULL,
        extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME()
        -- Sin PK: la colación CI/AI de SQL Server colisiona variantes de nombre
        -- (case/acentos/espacios finales) que GA4 devuelve distintas. delete_existing
        -- (por property_id+year_month) evita dups entre runs; el consumo agrupa.
    );
    CREATE INDEX ix_ga4_monthly_campaigns_pid_ym ON dbo.ga4_monthly_campaigns(property_id, year_month);
END
GO

IF OBJECT_ID('dbo.ga4_monthly_items', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ga4_monthly_items (
        property_id VARCHAR(30) NOT NULL,
        property_name VARCHAR(100) NOT NULL,
        year_month CHAR(6) NOT NULL,
        item_id NVARCHAR(400) NOT NULL,          -- = CodColor (ej. 08335C-001)
        item_name NVARCHAR(1000) NULL,
        item_revenue FLOAT NULL,
        items_purchased FLOAT NULL,
        items_viewed FLOAT NULL,
        items_added_to_cart FLOAT NULL,
        run_id INT NULL,
        extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
        CONSTRAINT pk_ga4_monthly_items PRIMARY KEY (property_id, year_month, item_id)
    );
    CREATE INDEX ix_ga4_monthly_items_ym ON dbo.ga4_monthly_items(year_month, items_viewed DESC);
END
GO
