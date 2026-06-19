-- Eventos mensuales cortados por DEVICE (mobile/desktop/tablet).
-- Motivo: ga4_monthly_events no separa device, y el CR muere en mobile (2-3.5x
-- peor que desktop, 83-90% del trafico). Esta tabla permite reconstruir el embudo
-- de checkout por device y ver en QUE paso pierde el mobile vs desktop.
-- Espeja ga4_monthly_events + la dimension deviceCategory de ga4_monthly_devices.
-- Idempotente: solo crea si no existe.

IF OBJECT_ID('dbo.ga4_monthly_events_device', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ga4_monthly_events_device (
        property_id VARCHAR(30) NOT NULL,
        property_name VARCHAR(100) NOT NULL,
        year_month CHAR(6) NOT NULL,
        device_category VARCHAR(50) NOT NULL,
        event_name VARCHAR(150) NOT NULL,
        event_count FLOAT NULL,
        total_users FLOAT NULL,
        run_id INT NULL,
        extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
        CONSTRAINT pk_ga4_monthly_events_device
            PRIMARY KEY (property_id, year_month, device_category, event_name)
    );
    CREATE INDEX ix_ga4_events_device_ym_ev
        ON dbo.ga4_monthly_events_device(year_month, event_name);
END
GO

-- Embudo de checkout por web x mes x device. Pivotea los eventos del flujo de
-- compra y calcula la tasa de paso a paso. La fuga real esta en
-- add_shipping_info -> add_payment_info (shock de envio); pay -> purchase ~100%.
IF OBJECT_ID('dbo.vw_ga4_checkout_funnel_device', 'V') IS NOT NULL
    DROP VIEW dbo.vw_ga4_checkout_funnel_device;
GO

CREATE VIEW dbo.vw_ga4_checkout_funnel_device AS
WITH piv AS (
    SELECT
        property_id, property_name, year_month, device_category,
        SUM(CASE WHEN event_name = 'view_item'         THEN event_count END) AS view_item,
        SUM(CASE WHEN event_name = 'add_to_cart'       THEN event_count END) AS add_to_cart,
        SUM(CASE WHEN event_name = 'begin_checkout'    THEN event_count END) AS begin_checkout,
        SUM(CASE WHEN event_name = 'add_shipping_info' THEN event_count END) AS add_shipping_info,
        SUM(CASE WHEN event_name = 'add_payment_info'  THEN event_count END) AS add_payment_info,
        SUM(CASE WHEN event_name = 'purchase'          THEN event_count END) AS purchase
    FROM dbo.ga4_monthly_events_device
    WHERE event_name IN ('view_item','add_to_cart','begin_checkout',
                         'add_shipping_info','add_payment_info','purchase')
    GROUP BY property_id, property_name, year_month, device_category
)
SELECT
    property_name, year_month, device_category,
    CAST(view_item         AS INT) AS view_item,
    CAST(add_to_cart       AS INT) AS add_to_cart,
    CAST(begin_checkout    AS INT) AS begin_checkout,
    CAST(add_shipping_info AS INT) AS add_shipping_info,
    CAST(add_payment_info  AS INT) AS add_payment_info,
    CAST(purchase          AS INT) AS purchase,
    -- tasas de paso (%)
    CAST(100.0 * add_to_cart       / NULLIF(view_item, 0)         AS DECIMAL(5,1)) AS view_to_cart_pct,
    CAST(100.0 * begin_checkout    / NULLIF(add_to_cart, 0)       AS DECIMAL(5,1)) AS cart_to_checkout_pct,
    CAST(100.0 * add_shipping_info / NULLIF(begin_checkout, 0)    AS DECIMAL(5,1)) AS checkout_to_shipping_pct,
    CAST(100.0 * add_payment_info  / NULLIF(add_shipping_info, 0) AS DECIMAL(5,1)) AS shipping_to_payment_pct,
    CAST(100.0 * purchase          / NULLIF(add_payment_info, 0)  AS DECIMAL(5,1)) AS payment_to_purchase_pct,
    -- conversion global del embudo de producto
    CAST(100.0 * purchase          / NULLIF(view_item, 0)         AS DECIMAL(6,3)) AS view_item_to_purchase_pct
FROM piv;
GO
