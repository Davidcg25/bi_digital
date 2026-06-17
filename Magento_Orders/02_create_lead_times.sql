-- magento_lead_times — réplica local de lead times de entrega por orden.
-- Fuente: GET /api/export/leadtimes (droplet). Refresh por ventana rodante de
-- created_at (las entregas finalizan días/semanas después de la orden).
IF OBJECT_ID('dbo.magento_lead_times', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.magento_lead_times (
        order_id             varchar(32)  NOT NULL PRIMARY KEY,  -- = increment id Magento
        logistics_zone       varchar(40)  NULL,                  -- LIMA_CALLAO/NORTE/SUR/CENTRO/SELVA/UNKNOWN
        courier              varchar(60)  NULL,                  -- DINET/YOBEL/SCHARFF (logístico, normalizado)
        created_at           datetime2    NULL,
        logistics_status     varchar(60)  NULL,
        delivery_at          datetime2    NULL,                  -- primer package_delivered (tracking_events)
        dias_lead            int          NULL,                  -- DATEDIFF(logistics_last_event_at, created_at)
        dias_lead_limpio     int          NULL,                  -- DATEDIFF(delivery_at, created_at)
        incluida_en_promedio bit          NULL,                  -- 1 si delivered y dias_lead 0-45
        _ingested_at         datetime2    NOT NULL DEFAULT SYSDATETIME()
    );
    CREATE INDEX ix_lead_times_created ON dbo.magento_lead_times(created_at);
    CREATE INDEX ix_lead_times_zona    ON dbo.magento_lead_times(logistics_zone);
END
GO
