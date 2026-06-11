-- 00_create_magento_orders_tables.sql
-- Tablas destino del consumidor del API de export de órdenes
-- (etl_magento_orders.py <- https://api.novedadescoliseum.com.pe/api/export/orders).
-- Grano: línea de ítem por source (PK id+sku+source_id), espejo sin PII de
-- order_magento_master del droplet. Las Ventas_Solidez_Magento_2024/2025
-- quedan como histórico congelado (esquema viejo, ingesta manual descontinuada).
-- Idempotente. Aplicar:
--   sqlcmd -S localhost -E -C -d Digital_Impact_Reportes -i 00_create_magento_orders_tables.sql

IF OBJECT_ID('dbo.Magento_Orders', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.Magento_Orders (
        id                                  varchar(40)     NOT NULL,
        sku                                 varchar(120)    NOT NULL,
        source_id                           varchar(40)     NOT NULL,
        order_id                            bigint          NULL,
        created                             datetime2       NULL,
        updated                             datetime2       NULL,
        order_state                         nvarchar(50)    NULL,
        order_status                        varchar(30)     NULL,
        store_name                          nvarchar(200)   NULL,
        store_id                            int             NULL,
        shipping_and_handling_information   nvarchar(2000)  NULL,
        courrier                            varchar(120)    NULL,
        customer_id                         bigint          NULL,
        departamento                        varchar(120)    NULL,
        provincia                           varchar(120)    NULL,
        distrito                            varchar(120)    NULL,
        promo_id                            varchar(200)    NULL,
        coupon_code                         varchar(120)    NULL,
        discount_name                       nvarchar(2000)  NULL,
        discount_description                nvarchar(2000)  NULL,
        payment_method                      varchar(80)     NULL,
        pago_confirmado                     bit             NULL,
        cuotas                              int             NULL,
        tarjeta_de_credito_o_debito         varchar(60)     NULL,
        invoice_date                        datetime2       NULL,
        saleschannel                        varchar(80)     NULL,
        payment_value                       decimal(18,2)   NULL,
        grand_total_purchased               decimal(18,2)   NULL,
        qty_confirmed                       decimal(18,2)   NULL,
        product_name                        nvarchar(400)   NULL,
        source_name                         nvarchar(200)   NULL,
        original_price                      decimal(18,2)   NULL,
        price                               decimal(18,2)   NULL,
        row_total                           decimal(18,2)   NULL,
        total_shipping_charges              decimal(18,2)   NULL,
        coupon_discount                     decimal(18,2)   NULL,
        price_discount                      decimal(18,2)   NULL,
        qty_ordered                         decimal(18,2)   NULL,
        grand_total_item                    decimal(18,2)   NULL,
        _ingested_at                        datetime2       NULL,
        utm_source                          nvarchar(1024)  NULL,
        utm_medium                          nvarchar(1024)  NULL,
        utm_campaign                        nvarchar(1024)  NULL,
        session_type                        varchar(128)    NULL,
        referrer                            nvarchar(max)   NULL,
        purchaseorigin_created_at           datetime2       NULL,
        extracted_at                        datetime2       NOT NULL CONSTRAINT DF_mo_extracted DEFAULT SYSUTCDATETIME(),
        CONSTRAINT PK_Magento_Orders PRIMARY KEY (id, sku, source_id)
    );
    CREATE INDEX IX_mo_created  ON dbo.Magento_Orders (created);
    CREATE INDEX IX_mo_updated  ON dbo.Magento_Orders (updated);
    CREATE INDEX IX_mo_customer ON dbo.Magento_Orders (customer_id);
END;

IF OBJECT_ID('dbo.magento_etl_runs', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.magento_etl_runs (
        run_id          int IDENTITY(1,1) NOT NULL CONSTRAINT PK_magento_etl_runs PRIMARY KEY,
        started_at      datetime2       NOT NULL CONSTRAINT DF_mer_started DEFAULT SYSUTCDATETIME(),
        finished_at     datetime2       NULL,
        since_param     datetime2       NULL,
        until_param     datetime2       NULL,
        watermark_after datetime2       NULL,
        pages           int             NULL,
        rows_fetched    int             NULL,
        rows_upserted   int             NULL,
        rows_skipped    int             NULL,
        status          varchar(30)     NOT NULL,
        error_message   nvarchar(max)   NULL
    );
END;
