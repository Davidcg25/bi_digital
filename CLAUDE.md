# Digital Impact — Plataforma BI de diagnóstico ecommerce (`4_BI_Ecom`)

Plataforma de datos que ingiere señales de varias fuentes (GA4, Microsoft Clarity,
Google Search Console, Lighthouse/CrUX, ventas/stock RMH, órdenes Magento, libro de
reclamos), las normaliza/certifica en **SQL Server** y produce entregables accionables
(digest HTML, scorecard mensual, webapp Flask). Es la capa que sostiene la jugada de
**agencia: diagnóstico primero, sistema portable después**.

Multi-tenant: cada **web = property** (Coliseum, New Balance, Caterpillar, Converse,
Merrell, Umbro, Steve Madden…). Las dimensiones de tenant viven en `dim_tenant`.

---

## Stack y cómo conectarse

| Item | Valor |
|------|-------|
| DB | **SQL Server** local `Digital_Impact_Reportes` (Windows auth) |
| Conexión CLI | `sqlcmd -S localhost -E -C -d Digital_Impact_Reportes` |
| Conexión Python | SQLAlchemy `mssql+pyodbc`, `trusted_connection=yes`, **ODBC Driver 17 for SQL Server** (ver `Diagnostico/digest.py::get_engine`) |
| Entorno Python | `venv/` en la raíz; deps en `requirements.txt` (pandas, httpx, sqlalchemy, pyodbc, tqdm) |
| Orquestación | **Windows Task Scheduler** — `instalar_tareas_bi.ps1` registra las tareas; cada pipeline tiene su `*.bat` |
| Disco | SQL Server **siempre en C** (`C:\BD_SQL`). El disco **D no sirve** para SQL Server (SN7100, sector físico 32K) |

> Para aplicar/recrear vistas: `sqlcmd -S localhost -E -C -d Digital_Impact_Reportes -i <archivo>.sql -b`
> Las vistas usan `CREATE OR ALTER`, son idempotentes.

---

## Flujo de datos

```
Fuentes externas                 Extractores (Python)              SQL Server                 Entregables
─────────────────                ────────────────────              ──────────                 ───────────
GA4 Data API          ───►  medios_campanias_sql.py, GA4/*   ─┐
Microsoft Clarity     ───►  Clarity/clarity_extractor_to_sql ─┤
Search Console        ───►  seo_performance-web_sql.py       ─┤
Lighthouse / CrUX     ───►  (GA4/ , Onsite_Audit/)           ─┼─►  Digital_Impact_Reportes ─┬─► Diagnostico/digest.py  → HTML
RMH ventas / stock    ───►  ventas_solidez-rmh.py, stock_*   ─┤    (tablas + vistas vw_*)    ├─► build_scorecard.py     → scorecard mensual
Órdenes Magento       ───►  Magento_Orders/ , Order_Magento  ─┤                              └─► webapp/ (Flask)        → tablero
Libro de reclamos     ───►  complaint_books-extract.py       ─┘
```

Dirección general: los datos productivos viven en el servidor/MySQL; se **traen al local**
(SQL Server `Digital_Impact_Reportes`) para reporting y diagnóstico.

---

## Estructura del repo

| Carpeta / archivo | Qué es |
|---|---|
| `Clarity/` | Extractor de Microsoft Clarity → SQL + sus tablas/vistas (ver sección dedicada) |
| `GA4/` | Extractores y backfills de GA4 (mensual + diario) |
| `GSC/` , `seo_performance-web_sql.py` | Search Console (performance por web/term) |
| `Diagnostico/` | **Capa de salida**: `digest.py` (digest accionable), `build_scorecard.py`, vistas SQL (`00_dim_tenant`, `10_certificacion_ga4`, `20_diagnostico_views`, `40_ops_views`, `50_search_terms_views`), `webapp/` Flask, carpetas por marca |
| `Magento_Orders/` , `Order_Magento/` | ETL de órdenes Magento (réplica droplet→local, lead times de entrega) |
| `Onsite_Audit/` | Auditor on-site (barrido multi-PDP de las webs, reporte HTML para agencia) |
| `Promociones Magento/` , `magento_map_promos.bat` | Mapeo de promos Magento |
| `Vistas_RMH/` | Vistas/exports hacia RMH / Google Sheets |
| `*.bat` , `instalar_tareas_bi.ps1` | Tareas programadas (Windows Task Scheduler) |
| `tabla_*.csv` | Catálogos de apoyo (marcas, familias, locales, temporadas, origin, ecommerce) |
| `Logs/` | Logs rotados por día de los pipelines |

---

## Clarity (UX) — pipeline y **gotcha crítico**

### Pipeline
- `Clarity/clarity_extractor_to_sql.py` → API **`project-live-insights`** (`https://www.clarity.ms/export-data/api/v1/...`).
  - `numOfDays` ∈ {1,2,3} (config `CLARITY_NUM_OF_DAYS`, default 3). **La API solo cubre los últimos 1–3 días**, no más.
  - 6 reportes (`REPORTS` en `clarity_config.py`): `overview`, `by_device`, `by_channel`,
    `by_source_medium_campaign`, **`by_url_device`**, `by_country_device`.
  - El extractor guarda el payload **genérico** (`measures_json`, `row_json`) — NO interpreta métricas.
- Tablas: `Clarity/00_create_clarity_tables.sql` (`clarity_live_insights` + tablas de run).
- Vistas: `Clarity/02_create_clarity_views.sql`
  - `vw_clarity_metric_rows` — parsea el JSON a columnas tipadas (scroll, pcts, conteos…).
  - **`vw_clarity_url_device_summary`** — resumen por **página × device** (consumida por el digest).
  - `vw_clarity_device_summary`, `vw_clarity_channel_summary`, `vw_clarity_campaign_summary`.

### ⚠️ Gotcha: Clarity fragmenta una página en decenas de URLs (querystring)
El reporte `by_url_device` devuelve los datos por **URL exacta**. Una misma página aparece
fragmentada en **decenas de URLs** por querystring (`utm_*`, `gclid`, `fbclid`, `gad_*`),
casi todas de **1 sesión**. El **dashboard/heatmap de Clarity normaliza** la URL a su path;
la API no. Por eso **los números de la API no matchean 1:1 el dashboard**.

**Bug que esto causó (jun-2026):** `digest.py::area_ux` hacía `MIN(avg_scroll_depth)` y
`MAX(rage_click_session_pct)` sobre los fragmentos → reportaba **el peor fragmento de 1 sesión**,
no la página. Ejemplo real (New Balance `new-balance-lima-15-k`):

| | Antes (artefacto MIN/MAX) | Real (agregado bien) |
|---|---|---|
| URLs | 86 fragmentos | **1 página** |
| Rage | **100%** (de 1 sesión) | **0%** |
| Scroll | **5%** (de 1 sesión) | **24.8%** |

### Regla de agregación correcta para métricas de Clarity
Al colapsar fragmentos (querystring) o devices, **nunca usar MIN/MAX**. Por tipo de métrica:

| Métrica (JSON) | Tipo | Cómo agregar |
|---|---|---|
| `totalSessionCount`, `distinctUserCount`, `subTotal` (clicks), `totalTime`/`activeTime` | conteo/total | **SUM** |
| `averageScrollDepth` | promedio (sin base de sesiones propia) | **ponderado por las sesiones de Traffic** del fragmento (fallback peso 1) |
| `sessionsWithMetricPercentage` (rage/dead/error/quickback/script…) | % de sesión | **ponderado por la `sessionsCount` PROPIA de esa métrica**: `SUM(sc*pct)/SUM(sc)` — NO por Traffic (puede venir NULL) |

**Normalizar URL = cortar desde `?` y `#` ANTES de agrupar.**

### Cómo quedó el fix
- `vw_clarity_url_device_summary` reescrita en **2 niveles**: `frag` (URL cruda, empareja
  métricas de la misma sesión) → `agg` (URL normalizada × device, aplica la regla de arriba).
- `digest.py::area_ux` colapsa devices **ponderando por sesiones** (no MIN/MAX) y ordena por
  riesgo ponderado.

### Residual esperado (no es bug)
Aun bien agregada, la **API ≠ heatmap** por definición: la API da **scroll promedio** y el
heatmap da **distribución de alcance** (% que llega a cada profundidad); y los conteos
sesión vs visitante difieren. **Usar la API/digest para señales relativas y tendencia**, y
el heatmap de Clarity para la foto exacta de una página.

---

## Digest accionable (`Diagnostico/digest.py`)

Genera `Diagnostico/digest/digest_<fecha>.html` + `digest_latest.html` (lo sirve la webapp Flask).
Estructurado en **6 áreas**, cada una una función `area_*(eng)`:

1. **Conversión y fugas** — embudo, CR, fuga mobile en checkout.
2. **Confianza de datos** — certificación GA4 (Direct inflado, URLs basura).
3. **UX / fricción (Clarity)** — `area_ux` (ver gotcha arriba). `ux_risk_score` = `script_err×3 + dead×2 + error×2 + rage×3 + quickback×1 + (scroll<25 → +10)`.
4. **Operación (ventas / stock)** — ventas/stock RMH, marketplaces (Falabella/MercadoLibre/Ripley).
5. **Tareas para agencia de desarrollo** — briefs (size charts, Direct, etc.).
6. **Decisiones de negocio** — pagos go/no-go (BNPL no se apaga por CR baja = rechazo de crédito), courier / lead time de entrega (umbral abs+rel por zona), productos empujar (stock sano + vende con poco tráfico) vs liquidar (SKU con stock alto y 0 ventas ecom).

---

## Convenciones

- **Commits:** conventional commits con scope — `feat(digest): …`, `fix(clarity): …`, `chore(ops): …`.
- **Git user:** `David CG <davidcg.life@gmail.com>` (este repo lo maneja David directo, no un usuario de automatización).
- **SQL:** consultar con `sqlcmd -E -C` (Windows auth). `mysql`/`sqlcmd` sin flags falla.
- **Vistas:** editar el `.sql` fuente y reaplicarlo con `-i ... -b`; no editar la vista solo en la BD (se pierde en el repo).

---

## Aprendizajes / gotchas transversales

- **Clarity URL fragmentation** → ver sección Clarity. La causa #1 de discrepancias API↔dashboard.
- **GA4 ventana mensual:** `monthly` solo trae meses **cerrados** (cierre días 1–7); el mes en
  curso vive en `ga4_daily_*` (MTD). No mezclar grano mensual con MTD.
- **Certificación de datos:** GA4 trae *Direct* inflado y URLs basura; las vistas de
  `10_certificacion_ga4.sql` / `20_diagnostico_views.sql` normalizan antes de reportar.
- **Análisis accionable = ventanas 1m/3m + LY como contexto**, no 12m.
</content>
