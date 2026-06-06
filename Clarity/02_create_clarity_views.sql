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

CREATE OR ALTER VIEW dbo.vw_clarity_url_device_summary AS
WITH p AS (
    SELECT
        project_name,
        extraction_date_utc,
        num_of_days,
        url,
        dimension2_value AS device,
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
        MAX(CASE WHEN metric_name = 'ScriptErrorCount' THEN subtotal END) AS script_errors,
        MAX(CASE WHEN metric_name = 'ExcessiveScroll' THEN sessions_with_metric_pct END) AS excessive_scroll_session_pct
    FROM dbo.vw_clarity_metric_rows
    WHERE report_name = 'by_url_device'
      AND url IS NOT NULL
    GROUP BY project_name, extraction_date_utc, num_of_days, url, dimension2_value
)
SELECT
    *,
    COALESCE(script_error_session_pct, 0) * 3
      + COALESCE(dead_click_session_pct, 0) * 2
      + COALESCE(error_click_session_pct, 0) * 2
      + COALESCE(rage_click_session_pct, 0) * 3
      + COALESCE(quickback_session_pct, 0) * 1
      + CASE WHEN COALESCE(avg_scroll_depth, 0) < 25 THEN 10 ELSE 0 END AS ux_risk_score
FROM p;
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
