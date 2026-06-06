USE [Digital_Impact_Reportes];
GO

/* =========================================================
   GA4 ANALYTICS LAYER - MULTI PROPERTY
   Fix aplicado: landing pages total y mensual van en tablas separadas.
   No se define PK sobre year_month nullable.
   ========================================================= */

IF OBJECT_ID('dbo.ga4_etl_report_loads', 'U') IS NOT NULL DROP TABLE dbo.ga4_etl_report_loads;
IF OBJECT_ID('dbo.ga4_landing_pages_monthly', 'U') IS NOT NULL DROP TABLE dbo.ga4_landing_pages_monthly;
IF OBJECT_ID('dbo.ga4_landing_pages_12m', 'U') IS NOT NULL DROP TABLE dbo.ga4_landing_pages_12m;
IF OBJECT_ID('dbo.ga4_pages_12m', 'U') IS NOT NULL DROP TABLE dbo.ga4_pages_12m;
IF OBJECT_ID('dbo.ga4_categories_12m', 'U') IS NOT NULL DROP TABLE dbo.ga4_categories_12m;
IF OBJECT_ID('dbo.ga4_items_12m', 'U') IS NOT NULL DROP TABLE dbo.ga4_items_12m;
IF OBJECT_ID('dbo.ga4_total_devices_12m', 'U') IS NOT NULL DROP TABLE dbo.ga4_total_devices_12m;
IF OBJECT_ID('dbo.ga4_total_channels_12m', 'U') IS NOT NULL DROP TABLE dbo.ga4_total_channels_12m;
IF OBJECT_ID('dbo.ga4_total_events_12m', 'U') IS NOT NULL DROP TABLE dbo.ga4_total_events_12m;
IF OBJECT_ID('dbo.ga4_total_rates_12m', 'U') IS NOT NULL DROP TABLE dbo.ga4_total_rates_12m;
IF OBJECT_ID('dbo.ga4_total_core_12m', 'U') IS NOT NULL DROP TABLE dbo.ga4_total_core_12m;
IF OBJECT_ID('dbo.ga4_monthly_devices', 'U') IS NOT NULL DROP TABLE dbo.ga4_monthly_devices;
IF OBJECT_ID('dbo.ga4_monthly_channels', 'U') IS NOT NULL DROP TABLE dbo.ga4_monthly_channels;
IF OBJECT_ID('dbo.ga4_monthly_events', 'U') IS NOT NULL DROP TABLE dbo.ga4_monthly_events;
IF OBJECT_ID('dbo.ga4_monthly_rates', 'U') IS NOT NULL DROP TABLE dbo.ga4_monthly_rates;
IF OBJECT_ID('dbo.ga4_monthly_core', 'U') IS NOT NULL DROP TABLE dbo.ga4_monthly_core;
IF OBJECT_ID('dbo.ga4_etl_runs', 'U') IS NOT NULL DROP TABLE dbo.ga4_etl_runs;
IF OBJECT_ID('dbo.ga4_properties', 'U') IS NOT NULL DROP TABLE dbo.ga4_properties;
GO

CREATE TABLE dbo.ga4_properties (
    property_id VARCHAR(30) NOT NULL PRIMARY KEY,
    property_name VARCHAR(100) NOT NULL,
    brand_name VARCHAR(100) NOT NULL,
    country VARCHAR(50) NULL,
    is_active BIT NOT NULL DEFAULT 1,
    created_at DATETIME2 NOT NULL DEFAULT SYSDATETIME()
);
GO

CREATE TABLE dbo.ga4_etl_runs (
    run_id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    started_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    finished_at DATETIME2 NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status VARCHAR(30) NOT NULL,
    total_properties INT NULL,
    successful_properties INT NULL,
    failed_properties INT NULL,
    error_message NVARCHAR(MAX) NULL
);
GO

CREATE TABLE dbo.ga4_etl_report_loads (
    id INT IDENTITY(1,1) NOT NULL PRIMARY KEY,
    run_id INT NOT NULL,
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    report_name VARCHAR(100) NOT NULL,
    table_name VARCHAR(128) NOT NULL,
    rows_loaded INT NOT NULL DEFAULT 0,
    status VARCHAR(30) NOT NULL,
    error_message NVARCHAR(MAX) NULL,
    loaded_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT fk_ga4_report_loads_run FOREIGN KEY (run_id) REFERENCES dbo.ga4_etl_runs(run_id)
);
GO

CREATE TABLE dbo.ga4_monthly_core (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    year_month CHAR(6) NOT NULL,
    sessions FLOAT NULL,
    total_users FLOAT NULL,
    active_users FLOAT NULL,
    purchase_revenue FLOAT NULL,
    ecommerce_purchases FLOAT NULL,
    average_purchase_revenue FLOAT NULL,
    items_purchased FLOAT NULL,
    engagement_rate FLOAT NULL,
    screen_page_views_per_session FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_monthly_core PRIMARY KEY (property_id, year_month)
);
GO

CREATE TABLE dbo.ga4_monthly_rates (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    year_month CHAR(6) NOT NULL,
    cart_to_view_rate FLOAT NULL,
    purchase_to_view_rate FLOAT NULL,
    session_purchase_key_event_rate FLOAT NULL,
    event_count FLOAT NULL,
    items_viewed FLOAT NULL,
    items_added_to_cart FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_monthly_rates PRIMARY KEY (property_id, year_month)
);
GO

CREATE TABLE dbo.ga4_monthly_events (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    year_month CHAR(6) NOT NULL,
    event_name VARCHAR(150) NOT NULL,
    event_count FLOAT NULL,
    total_users FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_monthly_events PRIMARY KEY (property_id, year_month, event_name)
);
GO

CREATE TABLE dbo.ga4_monthly_channels (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    year_month CHAR(6) NOT NULL,
    session_default_channel_group VARCHAR(150) NOT NULL,
    sessions FLOAT NULL,
    purchase_revenue FLOAT NULL,
    ecommerce_purchases FLOAT NULL,
    session_purchase_key_event_rate FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_monthly_channels PRIMARY KEY (property_id, year_month, session_default_channel_group)
);
GO

CREATE TABLE dbo.ga4_monthly_devices (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    year_month CHAR(6) NOT NULL,
    device_category VARCHAR(50) NOT NULL,
    sessions FLOAT NULL,
    purchase_revenue FLOAT NULL,
    ecommerce_purchases FLOAT NULL,
    session_purchase_key_event_rate FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_monthly_devices PRIMARY KEY (property_id, year_month, device_category)
);
GO

CREATE TABLE dbo.ga4_total_core_12m (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    sessions FLOAT NULL,
    total_users FLOAT NULL,
    active_users FLOAT NULL,
    purchase_revenue FLOAT NULL,
    ecommerce_purchases FLOAT NULL,
    average_purchase_revenue FLOAT NULL,
    items_purchased FLOAT NULL,
    engagement_rate FLOAT NULL,
    screen_page_views_per_session FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_total_core_12m PRIMARY KEY (property_id, start_date, end_date)
);
GO

CREATE TABLE dbo.ga4_total_rates_12m (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    cart_to_view_rate FLOAT NULL,
    purchase_to_view_rate FLOAT NULL,
    session_purchase_key_event_rate FLOAT NULL,
    event_count FLOAT NULL,
    items_viewed FLOAT NULL,
    items_added_to_cart FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_total_rates_12m PRIMARY KEY (property_id, start_date, end_date)
);
GO

CREATE TABLE dbo.ga4_total_events_12m (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    event_name VARCHAR(150) NOT NULL,
    event_count FLOAT NULL,
    total_users FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_total_events_12m PRIMARY KEY (property_id, start_date, end_date, event_name)
);
GO

CREATE TABLE dbo.ga4_total_channels_12m (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    session_default_channel_group VARCHAR(150) NOT NULL,
    sessions FLOAT NULL,
    purchase_revenue FLOAT NULL,
    ecommerce_purchases FLOAT NULL,
    session_purchase_key_event_rate FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_total_channels_12m PRIMARY KEY (property_id, start_date, end_date, session_default_channel_group)
);
GO

CREATE TABLE dbo.ga4_total_devices_12m (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    device_category VARCHAR(50) NOT NULL,
    sessions FLOAT NULL,
    purchase_revenue FLOAT NULL,
    ecommerce_purchases FLOAT NULL,
    session_purchase_key_event_rate FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_total_devices_12m PRIMARY KEY (property_id, start_date, end_date, device_category)
);
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
    page_path NVARCHAR(1000) NOT NULL,
    page_title NVARCHAR(1000) NULL,
    screen_page_views FLOAT NULL,
    sessions FLOAT NULL,
    purchase_revenue FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_pages_12m PRIMARY KEY (property_id, start_date, end_date, page_path)
);
GO

CREATE TABLE dbo.ga4_landing_pages_12m (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    landing_page NVARCHAR(1000) NOT NULL,
    sessions FLOAT NULL,
    purchase_revenue FLOAT NULL,
    ecommerce_purchases FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_landing_pages_12m PRIMARY KEY (property_id, start_date, end_date, landing_page)
);
GO

CREATE TABLE dbo.ga4_landing_pages_monthly (
    property_id VARCHAR(30) NOT NULL,
    property_name VARCHAR(100) NOT NULL,
    year_month CHAR(6) NOT NULL,
    landing_page NVARCHAR(1000) NOT NULL,
    sessions FLOAT NULL,
    purchase_revenue FLOAT NULL,
    ecommerce_purchases FLOAT NULL,
    run_id INT NULL,
    extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
    CONSTRAINT pk_ga4_landing_pages_monthly PRIMARY KEY (property_id, year_month, landing_page)
);
GO

CREATE INDEX ix_ga4_monthly_core_ym ON dbo.ga4_monthly_core(year_month);
CREATE INDEX ix_ga4_monthly_channels_ym_channel ON dbo.ga4_monthly_channels(year_month, session_default_channel_group);
CREATE INDEX ix_ga4_monthly_devices_ym_device ON dbo.ga4_monthly_devices(year_month, device_category);
CREATE INDEX ix_ga4_monthly_events_ym_event ON dbo.ga4_monthly_events(year_month, event_name);
CREATE INDEX ix_ga4_pages_12m_revenue ON dbo.ga4_pages_12m(purchase_revenue DESC);
CREATE INDEX ix_ga4_items_12m_revenue ON dbo.ga4_items_12m(item_revenue DESC);
GO
