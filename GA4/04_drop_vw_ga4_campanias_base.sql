-- ============================================================================
-- 04_drop_vw_ga4_campanias_base.sql — retira la vista de campañas OBSOLETA
--
-- vw_ga4_campanias_base estaba ROTA: leía de dbo.Campañas_GA4 (tabla del script
-- legacy medios_campanias_sql.py) que ya no existe → "Invalid object name
-- 'dbo.Campañas_GA4'" + binding error 4413 al hacer SELECT.
--
-- Además era REDUNDANTE: solo exponía tráfico por channel_group semanal (fecha,
-- yyyymm, semana_lunes, Tienda_Web, channel_group, sessions, transactions,
-- total_revenue) — NO el nombre de campaña — que ya cubre ga4_monthly_channels.
--
-- Las campañas a nivel NOMBRE (lo que pide Marketing) ahora viven en
-- dbo.ga4_monthly_campaigns (dimensión sessionCampaignName del extractor GA4,
-- ver ga4_config.py → report 'monthly_campaigns' y 03_create_campaigns_items_monthly.sql).
-- El scorecard Marketing las consume en build_scorecard.py::sec_campaigns().
--
-- Ningún objeto del repo referencia esta vista (grep limpio). Se retira.
--
-- Ejecutar: sqlcmd -S localhost -E -C -d Digital_Impact_Reportes -i 04_drop_vw_ga4_campanias_base.sql
-- ============================================================================

IF OBJECT_ID('dbo.vw_ga4_campanias_base', 'V') IS NOT NULL
    DROP VIEW dbo.vw_ga4_campanias_base;
GO
