USE [Digital_Impact_Reportes];
GO

/*
PATCH V2 - GA4 rankings tables

Corrige dos problemas detectados en ejecución real:
1) GA4 puede devolver duplicados para la llave lógica usada inicialmente.
   El extractor V2 los consolida antes de insertar.
2) page_path / landing_page pueden superar el límite de 900 bytes de índices clustered.
   Se usa hash SHA-256 como llave técnica y se conserva el texto completo para análisis.

Este patch elimina SOLO las tablas de rankings/long-tail. Las tablas core mensuales y totales se conservan.
*/

IF OBJECT_ID('dbo.ga4_landing_pages_monthly', 'U') IS NOT NULL DROP TABLE dbo.ga4_landing_pages_monthly;
IF OBJECT_ID('dbo.ga4_landing_pages_12m', 'U') IS NOT NULL DROP TABLE dbo.ga4_landing_pages_12m;
IF OBJECT_ID('dbo.ga4_pages_12m', 'U') IS NOT NULL DROP TABLE dbo.ga4_pages_12m;
IF OBJECT_ID('dbo.ga4_categories_12m', 'U') IS NOT NULL DROP TABLE dbo.ga4_categories_12m;
IF OBJECT_ID('dbo.ga4_items_12m', 'U') IS NOT NULL DROP TABLE dbo.ga4_items_12m;
GO

CREATE TABLE dbo.ga4_items_12m (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    item_id NVARCHAR(500) NOT NULL,
    item_name NVARCHAR(1000) NULL,
    item_revenue FLOAT NULL,
    items_purchased FLOAT NULL,
    items_viewed FLOAT NULL,
    items_added_to_cart FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_items_12m PRIMARY KEY (property_id, start_date, end_date, item_id)
);
GO

CREATE TABLE dbo.ga4_categories_12m (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    item_category NVARCHAR(500) NOT NULL,
    item_revenue FLOAT NULL,
    items_purchased FLOAT NULL,
    items_viewed FLOAT NULL,
    items_added_to_cart FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_categories_12m PRIMARY KEY (property_id, start_date, end_date, item_category)
);
GO

CREATE TABLE dbo.ga4_pages_12m (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    page_path_hash CHAR(64) NOT NULL,
    page_path NVARCHAR(2000) NOT NULL,
    page_title NVARCHAR(1000) NULL,
    screen_page_views FLOAT NULL,
    sessions FLOAT NULL,
    purchase_revenue FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_pages_12m PRIMARY KEY (property_id, start_date, end_date, page_path_hash)
);
GO

CREATE TABLE dbo.ga4_landing_pages_12m (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    landing_page_hash CHAR(64) NOT NULL,
    landing_page NVARCHAR(2000) NOT NULL,
    sessions FLOAT NULL,
    purchase_revenue FLOAT NULL,
    ecommerce_purchases FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_landing_pages_12m PRIMARY KEY (property_id, start_date, end_date, landing_page_hash)
);
GO

CREATE TABLE dbo.ga4_landing_pages_monthly (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    year_month CHAR(6) NOT NULL,
    landing_page_hash CHAR(64) NOT NULL,
    landing_page NVARCHAR(2000) NOT NULL,
    sessions FLOAT NULL,
    purchase_revenue FLOAT NULL,
    ecommerce_purchases FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_landing_pages_monthly PRIMARY KEY (property_id, year_month, landing_page_hash)
);
GO

CREATE INDEX ix_ga4_items_12m_revenue ON dbo.ga4_items_12m(item_revenue DESC);
CREATE INDEX ix_ga4_pages_12m_revenue ON dbo.ga4_pages_12m(purchase_revenue DESC);
CREATE INDEX ix_ga4_landing_pages_12m_revenue ON dbo.ga4_landing_pages_12m(purchase_revenue DESC);
CREATE INDEX ix_ga4_landing_pages_monthly_ym ON dbo.ga4_landing_pages_monthly(year_month);
GO
