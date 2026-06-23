USE [Digital_Impact_Reportes];
GO

CREATE OR ALTER VIEW dbo.vw_clarity_metric_rows AS
SELECT
    id,
    run_id,
    project_name,
    token_name,
    report_name,
    num_of_days,
    metric_name,
    extraction_date_utc,
    extracted_at,
    window_start_utc,
    window_end_utc,
    dimension1_name,
    dimension1_value,
    dimension2_name,
    dimension2_value,
    dimension3_name,
    dimension3_value,
    COALESCE(JSON_VALUE(measures_json, '$.Url'), dimension1_value) AS url,
    TRY_CONVERT(float, JSON_VALUE(measures_json, '$.sessionsCount')) AS sessions_count,
    TRY_CONVERT(float, JSON_VALUE(measures_json, '$.sessionsWithMetricPercentage')) AS sessions_with_metric_pct,
    TRY_CONVERT(float, JSON_VALUE(measures_json, '$.sessionsWithoutMetricPercentage')) AS sessions_without_metric_pct,
    TRY_CONVERT(float, JSON_VALUE(measures_json, '$.pagesViews')) AS pages_views,
    TRY_CONVERT(float, JSON_VALUE(measures_json, '$.subTotal')) AS subtotal,
    TRY_CONVERT(float, JSON_VALUE(measures_json, '$.totalSessionCount')) AS total_session_count,
    TRY_CONVERT(float, JSON_VALUE(measures_json, '$.totalBotSessionCount')) AS total_bot_session_count,
    TRY_CONVERT(float, JSON_VALUE(measures_json, '$.distinctUserCount')) AS distinct_user_count,
    TRY_CONVERT(float, JSON_VALUE(measures_json, '$.pagesPerSessionPercentage')) AS pages_per_session,
    TRY_CONVERT(float, JSON_VALUE(measures_json, '$.averageScrollDepth')) AS average_scroll_depth,
    TRY_CONVERT(float, JSON_VALUE(measures_json, '$.totalTime')) AS total_time,
    TRY_CONVERT(float, JSON_VALUE(measures_json, '$.activeTime')) AS active_time
FROM dbo.clarity_live_insights;
GO

-- Microsoft Clarity entrega el reporte by_url_device por URL EXACTA: una misma
-- pagina aparece fragmentada en decenas de URLs por querystring (utm_*, gclid,
-- fbclid, gad_*...), casi todas de 1 sesion. El dashboard de Clarity normaliza
-- la URL a su path; aqui replicamos eso para que la vista cuadre con el dashboard.
--
-- Agregacion correcta por tipo de metrica:
--   * Conteos/totales (Traffic, *_clicks, EngagementTime)  -> SUM
--   * Promedios (averageScrollDepth)                       -> ponderado por sesiones
--   * Porcentajes de sesion (sessionsWithMetricPercentage) -> ponderado por la
--     PROPIA sessionsCount de cada metrica (no por Traffic: los clicks traen su
--     base de sesiones aparte, y Traffic puede venir NULL en el fragmento).
-- Se hace en dos niveles: fragmento (URL cruda) para emparejar metricas de la
-- misma sesion, y pagina (URL normalizada) para colapsar el querystring.
CREATE OR ALTER VIEW dbo.vw_clarity_url_device_summary AS
WITH rows_norm AS (
    SELECT
        s.project_name,
        s.extraction_date_utc,
        s.num_of_days,
        s.dimension2_value AS device,
        s.metric_name,
        s.raw_url,
        -- path sin querystring ni fragmento (#)
        CASE WHEN CHARINDEX('#', s.q) > 0 THEN LEFT(s.q, CHARINDEX('#', s.q) - 1) ELSE s.q END AS page_url,
        s.sessions_count,
        s.sessions_with_metric_pct,
        s.subtotal,
        s.total_session_count,
        s.total_bot_session_count,
        s.distinct_user_count,
        s.pages_per_session,
        s.average_scroll_depth,
        s.total_time,
        s.active_time
    FROM (
        SELECT
            r.*,
            r.url AS raw_url,
            CASE WHEN CHARINDEX('?', r.url) > 0 THEN LEFT(r.url, CHARINDEX('?', r.url) - 1) ELSE r.url END AS q
        FROM dbo.vw_clarity_metric_rows AS r
        WHERE r.report_name = 'by_url_device'
          AND r.url IS NOT NULL
    ) AS s
),
-- Grano fragmento: 1 fila por (page_url, device, URL cruda); empareja las metricas
-- que vienen en filas distintas pero corresponden a la misma URL exacta.
frag AS (
    SELECT
        project_name, extraction_date_utc, num_of_days, page_url, device, raw_url,
        MAX(CASE WHEN metric_name = 'Traffic'        THEN total_session_count END)     AS f_sessions,
        MAX(CASE WHEN metric_name = 'Traffic'        THEN total_bot_session_count END) AS f_bot,
        MAX(CASE WHEN metric_name = 'Traffic'        THEN distinct_user_count END)     AS f_users,
        MAX(CASE WHEN metric_name = 'Traffic'        THEN pages_per_session END)       AS f_pps,
        MAX(CASE WHEN metric_name = 'ScrollDepth'    THEN average_scroll_depth END)    AS f_scroll,
        MAX(CASE WHEN metric_name = 'EngagementTime' THEN total_time END)              AS f_total_time,
        MAX(CASE WHEN metric_name = 'EngagementTime' THEN active_time END)             AS f_active_time,
        MAX(CASE WHEN metric_name = 'DeadClickCount'   THEN sessions_count END)            AS dead_sc,
        MAX(CASE WHEN metric_name = 'DeadClickCount'   THEN sessions_with_metric_pct END)  AS dead_pct,
        MAX(CASE WHEN metric_name = 'DeadClickCount'   THEN subtotal END)                  AS dead_clicks,
        MAX(CASE WHEN metric_name = 'ErrorClickCount'  THEN sessions_count END)            AS err_sc,
        MAX(CASE WHEN metric_name = 'ErrorClickCount'  THEN sessions_with_metric_pct END)  AS err_pct,
        MAX(CASE WHEN metric_name = 'ErrorClickCount'  THEN subtotal END)                  AS err_clicks,
        MAX(CASE WHEN metric_name = 'QuickbackClick'   THEN sessions_count END)            AS qb_sc,
        MAX(CASE WHEN metric_name = 'QuickbackClick'   THEN sessions_with_metric_pct END)  AS qb_pct,
        MAX(CASE WHEN metric_name = 'QuickbackClick'   THEN subtotal END)                  AS qb_clicks,
        MAX(CASE WHEN metric_name = 'RageClickCount'   THEN sessions_count END)            AS rage_sc,
        MAX(CASE WHEN metric_name = 'RageClickCount'   THEN sessions_with_metric_pct END)  AS rage_pct,
        MAX(CASE WHEN metric_name = 'RageClickCount'   THEN subtotal END)                  AS rage_clicks,
        MAX(CASE WHEN metric_name = 'ScriptErrorCount' THEN sessions_count END)            AS scr_sc,
        MAX(CASE WHEN metric_name = 'ScriptErrorCount' THEN sessions_with_metric_pct END)  AS scr_pct,
        MAX(CASE WHEN metric_name = 'ScriptErrorCount' THEN subtotal END)                  AS scr_clicks,
        MAX(CASE WHEN metric_name = 'ExcessiveScroll'  THEN sessions_count END)            AS exc_sc,
        MAX(CASE WHEN metric_name = 'ExcessiveScroll'  THEN sessions_with_metric_pct END)  AS exc_pct
    FROM rows_norm
    GROUP BY project_name, extraction_date_utc, num_of_days, page_url, device, raw_url
),
-- Grano pagina (URL normalizada x device): colapsa los fragmentos del querystring.
agg AS (
    SELECT
        project_name,
        extraction_date_utc,
        num_of_days,
        page_url AS url,
        device,
        SUM(f_sessions) AS sessions,
        SUM(f_bot)      AS bot_sessions,
        SUM(f_users)    AS users,
        SUM(f_pps * f_sessions) / NULLIF(SUM(CASE WHEN f_pps IS NOT NULL THEN f_sessions END), 0) AS pages_per_session,
        -- scroll no trae base de sesiones propia: se pondera por las sesiones de Traffic
        -- del fragmento (fallback peso 1 cuando Traffic vino NULL).
        SUM(f_scroll * COALESCE(f_sessions, 1)) / NULLIF(SUM(CASE WHEN f_scroll IS NOT NULL THEN COALESCE(f_sessions, 1) END), 0) AS avg_scroll_depth,
        SUM(f_total_time)  AS total_time,
        SUM(f_active_time) AS active_time,
        -- % ponderado por la sessionsCount propia de cada metrica: SUM(sc*pct)/SUM(sc)
        SUM(dead_sc * dead_pct) / NULLIF(SUM(dead_sc), 0) AS dead_click_session_pct,
        SUM(dead_clicks) AS dead_clicks,
        SUM(err_sc * err_pct)   / NULLIF(SUM(err_sc), 0)  AS error_click_session_pct,
        SUM(err_clicks)  AS error_clicks,
        SUM(qb_sc * qb_pct)     / NULLIF(SUM(qb_sc), 0)   AS quickback_session_pct,
        SUM(qb_clicks)   AS quickbacks,
        SUM(rage_sc * rage_pct) / NULLIF(SUM(rage_sc), 0) AS rage_click_session_pct,
        SUM(rage_clicks) AS rage_clicks,
        SUM(scr_sc * scr_pct)   / NULLIF(SUM(scr_sc), 0)  AS script_error_session_pct,
        SUM(scr_clicks)  AS script_errors,
        SUM(exc_sc * exc_pct)   / NULLIF(SUM(exc_sc), 0)  AS excessive_scroll_session_pct
    FROM frag
    GROUP BY project_name, extraction_date_utc, num_of_days, page_url, device
)
SELECT
    *,
    COALESCE(script_error_session_pct, 0) * 3
      + COALESCE(dead_click_session_pct, 0) * 2
      + COALESCE(error_click_session_pct, 0) * 2
      + COALESCE(rage_click_session_pct, 0) * 3
      + COALESCE(quickback_session_pct, 0) * 1
      + CASE WHEN COALESCE(avg_scroll_depth, 0) < 25 THEN 10 ELSE 0 END AS ux_risk_score
FROM agg;
GO

CREATE OR ALTER VIEW dbo.vw_clarity_device_summary AS
WITH p AS (
    SELECT
        project_name,
        extraction_date_utc,
        num_of_days,
        dimension1_value AS device,
        MAX(CASE WHEN metric_name = 'Traffic' THEN total_session_count END) AS sessions,
        MAX(CASE WHEN metric_name = 'Traffic' THEN total_bot_session_count END) AS bot_sessions,
        MAX(CASE WHEN metric_name = 'Traffic' THEN distinct_user_count END) AS users,
        MAX(CASE WHEN metric_name = 'Traffic' THEN pages_per_session END) AS pages_per_session,
        MAX(CASE WHEN metric_name = 'ScrollDepth' THEN average_scroll_depth END) AS avg_scroll_depth,
        MAX(CASE WHEN metric_name = 'EngagementTime' THEN total_time END) AS total_time,
        MAX(CASE WHEN metric_name = 'EngagementTime' THEN active_time END) AS active_time,
        MAX(CASE WHEN metric_name = 'DeadClickCount' THEN sessions_with_metric_pct END) AS dead_click_session_pct,
        MAX(CASE WHEN metric_name = 'DeadClickCount' THEN subtotal END) AS dead_clicks,
        MAX(CASE WHEN metric_name = 'ErrorClickCount' THEN sessions_with_metric_pct END) AS error_click_session_pct,
        MAX(CASE WHEN metric_name = 'ErrorClickCount' THEN subtotal END) AS error_clicks,
        MAX(CASE WHEN metric_name = 'QuickbackClick' THEN sessions_with_metric_pct END) AS quickback_session_pct,
        MAX(CASE WHEN metric_name = 'QuickbackClick' THEN subtotal END) AS quickbacks,
        MAX(CASE WHEN metric_name = 'RageClickCount' THEN sessions_with_metric_pct END) AS rage_click_session_pct,
        MAX(CASE WHEN metric_name = 'RageClickCount' THEN subtotal END) AS rage_clicks,
        MAX(CASE WHEN metric_name = 'ScriptErrorCount' THEN sessions_with_metric_pct END) AS script_error_session_pct,
        MAX(CASE WHEN metric_name = 'ScriptErrorCount' THEN subtotal END) AS script_errors
    FROM dbo.vw_clarity_metric_rows
    WHERE report_name = 'by_device'
    GROUP BY project_name, extraction_date_utc, num_of_days, dimension1_value
)
SELECT
    *,
    COALESCE(script_error_session_pct, 0) * 3
      + COALESCE(dead_click_session_pct, 0) * 2
      + COALESCE(error_click_session_pct, 0) * 2
      + COALESCE(rage_click_session_pct, 0) * 3
      + COALESCE(quickback_session_pct, 0) * 1 AS ux_risk_score
FROM p;
GO

CREATE OR ALTER VIEW dbo.vw_clarity_channel_summary AS
WITH p AS (
    SELECT
        project_name,
        extraction_date_utc,
        num_of_days,
        dimension1_value AS channel,
        MAX(CASE WHEN metric_name = 'Traffic' THEN total_session_count END) AS sessions,
        MAX(CASE WHEN metric_name = 'Traffic' THEN total_bot_session_count END) AS bot_sessions,
        MAX(CASE WHEN metric_name = 'Traffic' THEN distinct_user_count END) AS users,
        MAX(CASE WHEN metric_name = 'Traffic' THEN pages_per_session END) AS pages_per_session,
        MAX(CASE WHEN metric_name = 'ScrollDepth' THEN average_scroll_depth END) AS avg_scroll_depth,
        MAX(CASE WHEN metric_name = 'DeadClickCount' THEN sessions_with_metric_pct END) AS dead_click_session_pct,
        MAX(CASE WHEN metric_name = 'DeadClickCount' THEN subtotal END) AS dead_clicks,
        MAX(CASE WHEN metric_name = 'QuickbackClick' THEN sessions_with_metric_pct END) AS quickback_session_pct,
        MAX(CASE WHEN metric_name = 'QuickbackClick' THEN subtotal END) AS quickbacks,
        MAX(CASE WHEN metric_name = 'ScriptErrorCount' THEN sessions_with_metric_pct END) AS script_error_session_pct,
        MAX(CASE WHEN metric_name = 'ScriptErrorCount' THEN subtotal END) AS script_errors
    FROM dbo.vw_clarity_metric_rows
    WHERE report_name = 'by_channel'
    GROUP BY project_name, extraction_date_utc, num_of_days, dimension1_value
)
SELECT
    *,
    COALESCE(script_error_session_pct, 0) * 3
      + COALESCE(dead_click_session_pct, 0) * 2
      + COALESCE(quickback_session_pct, 0) * 1 AS ux_risk_score
FROM p;
GO

CREATE OR ALTER VIEW dbo.vw_clarity_campaign_summary AS
WITH p AS (
    SELECT
        project_name,
        extraction_date_utc,
        num_of_days,
        dimension1_value AS source,
        dimension2_value AS medium,
        dimension3_value AS campaign,
        MAX(CASE WHEN metric_name = 'Traffic' THEN total_session_count END) AS sessions,
        MAX(CASE WHEN metric_name = 'Traffic' THEN total_bot_session_count END) AS bot_sessions,
        MAX(CASE WHEN metric_name = 'Traffic' THEN distinct_user_count END) AS users,
        MAX(CASE WHEN metric_name = 'Traffic' THEN pages_per_session END) AS pages_per_session,
        MAX(CASE WHEN metric_name = 'ScrollDepth' THEN average_scroll_depth END) AS avg_scroll_depth,
        MAX(CASE WHEN metric_name = 'EngagementTime' THEN active_time END) AS active_time,
        MAX(CASE WHEN metric_name = 'DeadClickCount' THEN sessions_with_metric_pct END) AS dead_click_session_pct,
        MAX(CASE WHEN metric_name = 'DeadClickCount' THEN subtotal END) AS dead_clicks,
        MAX(CASE WHEN metric_name = 'QuickbackClick' THEN sessions_with_metric_pct END) AS quickback_session_pct,
        MAX(CASE WHEN metric_name = 'QuickbackClick' THEN subtotal END) AS quickbacks,
        MAX(CASE WHEN metric_name = 'ScriptErrorCount' THEN sessions_with_metric_pct END) AS script_error_session_pct,
        MAX(CASE WHEN metric_name = 'ScriptErrorCount' THEN subtotal END) AS script_errors
    FROM dbo.vw_clarity_metric_rows
    WHERE report_name = 'by_source_medium_campaign'
    GROUP BY project_name, extraction_date_utc, num_of_days, dimension1_value, dimension2_value, dimension3_value
)
SELECT
    *,
    COALESCE(script_error_session_pct, 0) * 3
      + COALESCE(dead_click_session_pct, 0) * 2
      + COALESCE(quickback_session_pct, 0) * 1 AS ux_risk_score
FROM p;
GO
