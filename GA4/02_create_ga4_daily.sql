-- =====================================================================
-- GA4 grano DIARIO: tablas + cadena de vistas espejo del flujo mensual.
-- Las tablas daily guardan historia continua (upsert por fecha, sin
-- problema de bordes). El mes en curso vive aquí; las tablas monthly
-- quedan solo para meses cerrados (cierre los primeros días del mes).
-- Ejecutar: sqlcmd -S localhost -E -C -d Digital_Impact_Reportes -i 02_create_ga4_daily.sql
-- =====================================================================

IF OBJECT_ID('dbo.ga4_daily_core', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ga4_daily_core (
        property_id VARCHAR(30) NOT NULL,
        property_name VARCHAR(100) NOT NULL,
        [date] DATE NOT NULL,
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
        CONSTRAINT pk_ga4_daily_core PRIMARY KEY (property_id, [date])
    );
END;
GO

IF OBJECT_ID('dbo.ga4_daily_channels', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.ga4_daily_channels (
        property_id VARCHAR(30) NOT NULL,
        property_name VARCHAR(100) NOT NULL,
        [date] DATE NOT NULL,
        session_default_channel_group VARCHAR(150) NOT NULL,
        sessions FLOAT NULL,
        purchase_revenue FLOAT NULL,
        ecommerce_purchases FLOAT NULL,
        session_purchase_key_event_rate FLOAT NULL,
        run_id INT NULL,
        extracted_at DATETIME2 NOT NULL DEFAULT SYSDATETIME(),
        CONSTRAINT pk_ga4_daily_channels PRIMARY KEY (property_id, [date], session_default_channel_group)
    );
END;
GO

-- Espejo diario de vw_ga4_mensual_canales_agrupados
CREATE OR ALTER VIEW dbo.vw_ga4_diario_canales_agrupados AS
WITH src AS (
    SELECT
        CAST(d.property_id AS varchar(30)) AS property_id,
        LTRIM(RTRIM(d.property_name)) AS Tienda_ecom,
        d.[date] AS fecha,
        LTRIM(RTRIM(d.session_default_channel_group)) AS session_default_channel_group,
        COALESCE(NULLIF(LTRIM(RTRIM(e.canal)), ''), LTRIM(RTRIM(d.session_default_channel_group)), 'Sin canal') AS canal,
        COALESCE(NULLIF(LTRIM(RTRIM(e.responsable)), ''), 'Sin responsable') AS responsable,
        CAST(COALESCE(d.sessions, 0) AS decimal(18,4)) AS sessions,
        CAST(COALESCE(d.ecommerce_purchases, 0) AS decimal(18,4)) AS transactions,
        CAST(COALESCE(d.purchase_revenue, 0) AS decimal(18,4)) AS ga4_revenue
    FROM dbo.ga4_daily_channels d
    LEFT JOIN dbo.Equivalencias_Canales e
        ON e.session_default_channel_group = d.session_default_channel_group
)
SELECT
    property_id,
    Tienda_ecom,
    fecha,
    CONVERT(char(6), fecha, 112) AS year_month,
    canal,
    responsable,
    SUM(sessions) AS sessions,
    SUM(transactions) AS transactions,
    SUM(ga4_revenue) AS ga4_revenue
FROM src
GROUP BY
    property_id,
    Tienda_ecom,
    fecha,
    canal,
    responsable;
GO

-- Espejo diario de vw_rmh_ecommerce_mensual_tienda
CREATE OR ALTER VIEW dbo.vw_rmh_ecommerce_diario_tienda AS
SELECT
    LTRIM(RTRIM(Tienda_ecom)) AS Tienda_ecom,
    CAST([Time] AS date) AS fecha,
    COUNT(DISTINCT NULLIF(LTRIM(RTRIM(Documento)), '')) AS ordenes_rmh,
    CAST(SUM(COALESCE(Cantidad, 0)) AS decimal(18,4)) AS unidades_rmh,
    CAST(SUM(COALESCE(TotalNeto, 0)) AS decimal(18,4)) AS ingresos_rmh_ecommerce,
    CAST(SUM(COALESCE(Contribucion, 0)) AS decimal(18,4)) AS contribucion_rmh_ecommerce
FROM dbo.Ventas_Solidez_RMH
WHERE
    LTRIM(RTRIM(UPPER(Tipo))) = 'ECOMMERCE'
    AND NULLIF(LTRIM(RTRIM(Tienda_ecom)), '') IS NOT NULL
    AND COALESCE(NULLIF(LTRIM(RTRIM(UPPER(Marca_Limpia))), ''), NULLIF(LTRIM(RTRIM(UPPER(Marca))), '')) <> 'UNICO'
GROUP BY
    LTRIM(RTRIM(Tienda_ecom)),
    CAST([Time] AS date);
GO

-- Espejo diario de vw_ga4_rmh_mensual_canal: shares y prorrateo POR DÍA
CREATE OR ALTER VIEW dbo.vw_ga4_rmh_diario_canal AS
WITH ga4 AS (
    SELECT property_id, Tienda_ecom, fecha, year_month, canal, MIN(responsable) AS responsable,
           SUM(sessions) AS sessions, SUM(transactions) AS transactions, SUM(ga4_revenue) AS ga4_revenue
    FROM dbo.vw_ga4_diario_canales_agrupados
    GROUP BY property_id, Tienda_ecom, fecha, year_month, canal
), scored AS (
    SELECT g.*,
           SUM(g.ga4_revenue) OVER (PARTITION BY g.property_id, g.Tienda_ecom, g.fecha) AS ga4_revenue_total_dia,
           SUM(g.sessions) OVER (PARTITION BY g.property_id, g.Tienda_ecom, g.fecha) AS sessions_total_dia,
           SUM(g.transactions) OVER (PARTITION BY g.property_id, g.Tienda_ecom, g.fecha) AS transactions_total_dia
    FROM ga4 g
), shares AS (
    SELECT s.*,
           CONVERT(float, s.sessions) / NULLIF(CONVERT(float, s.sessions_total_dia), 0) AS share_sesiones_calc,
           CONVERT(float, s.ga4_revenue) / NULLIF(CONVERT(float, s.ga4_revenue_total_dia), 0) AS share_ingresos_calc
    FROM scored s
)
SELECT
    s.property_id, s.Tienda_ecom, s.fecha, s.year_month,
    YEAR(s.fecha) AS anio, MONTH(s.fecha) AS mes, DAY(s.fecha) AS dia,
    s.canal, s.responsable,
    s.sessions, s.transactions, s.ga4_revenue,
    CAST(s.share_sesiones_calc AS decimal(18,8)) AS participacion_sesiones_ga4,
    CAST(s.share_ingresos_calc AS decimal(18,8)) AS participacion_ingresos_ga4,
    s.sessions_total_dia, s.transactions_total_dia, s.ga4_revenue_total_dia,
    COALESCE(r.ordenes_rmh, 0) AS ordenes_rmh_ecommerce_total,
    COALESCE(r.unidades_rmh, 0) AS unidades_rmh_ecommerce_total,
    COALESCE(r.ingresos_rmh_ecommerce, 0) AS ingresos_rmh_ecommerce_total,
    CAST(COALESCE(r.ingresos_rmh_ecommerce, 0) * COALESCE(s.share_ingresos_calc, 0) AS decimal(18,4)) AS ingresos_rmh_prorrateados,
    CAST(COALESCE(r.unidades_rmh, 0) * COALESCE(s.share_ingresos_calc, 0) AS decimal(18,4)) AS unidades_rmh_prorrateadas,
    CAST(COALESCE(r.contribucion_rmh_ecommerce, 0) * COALESCE(s.share_ingresos_calc, 0) AS decimal(18,4)) AS contribucion_rmh_prorrateada,
    CASE WHEN r.Tienda_ecom IS NULL THEN 0 ELSE 1 END AS tiene_venta_rmh_ecommerce
FROM shares s
LEFT JOIN dbo.vw_rmh_ecommerce_diario_tienda r
    ON r.Tienda_ecom = s.Tienda_ecom AND r.fecha = s.fecha;
GO
