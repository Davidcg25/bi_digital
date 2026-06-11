"""
GA4 Multi-Property -> SQL Server config
Proyecto: Digital_Impact_Reportes
"""

from pathlib import Path
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

# =========================
# GA4 PROPERTIES
# =========================
PROPERTY_INFO = {
    "338208380": "Caterpillar",
    "287142051": "Coliseum",
    "407838284": "Converse",
    "304627263": "Merrell",
    "427321367": "New Balance",
    "293692998": "Steve Madden",
    "495902890": "Umbro",
    "513757079": "Fila",
}

# Para pruebas iniciales puedes limitar properties desde .env:
# PROPERTY_IDS_TO_RUN=407838284,427321367
_property_ids_env = os.getenv("PROPERTY_IDS_TO_RUN", "").strip()
if _property_ids_env:
    PROPERTY_IDS_TO_RUN = [x.strip() for x in _property_ids_env.split(",") if x.strip()]
else:
    PROPERTY_IDS_TO_RUN = list(PROPERTY_INFO.keys())

# =========================
# DATE RANGE
# =========================
# Por defecto: últimos 365 días hasta hoy.
# Puedes forzar desde .env:
# START_DATE=2025-05-04
# END_DATE=2026-05-04
_today = datetime.today().date()
_default_start = _today - timedelta(days=int(os.getenv("LOOKBACK_DAYS", "365")))

START_DATE = os.getenv("START_DATE", _default_start.strftime("%Y-%m-%d"))
END_DATE = os.getenv("END_DATE", _today.strftime("%Y-%m-%d"))

# Los reportes mensuales usan un start alineado al día 1 del mes de START_DATE.
# Con ventana rodante (LOOKBACK_DAYS), un start a mitad de mes traía el mes de
# borde parcial y el upsert pisaba meses completos ya cargados en SQL.
MONTHLY_START_DATE = (
    datetime.strptime(START_DATE, "%Y-%m-%d").date().replace(day=1).strftime("%Y-%m-%d")
)

# Grano diario: ventana corta independiente de la rodante de 12m. 35 días cubre
# el mes en curso completo + cola del anterior y sana reprocesos tardíos de GA4.
# El upsert es por fecha, así que no sufre el problema de bordes del mensual.
DAILY_LOOKBACK_DAYS = int(os.getenv("DAILY_LOOKBACK_DAYS", "35"))
DAILY_START_DATE = os.getenv(
    "DAILY_START_DATE", (_today - timedelta(days=DAILY_LOOKBACK_DAYS)).strftime("%Y-%m-%d")
)

# Mensuales solo al cierre de mes: en modo programado (sin START_DATE/END_DATE
# forzados) los reportes monthly corren solo los primeros MONTHLY_CLOSE_DAY_LIMIT
# días del mes, con end recortado al último día del mes anterior — las tablas
# monthly quedan solo con meses cerrados; el mes en curso vive en las daily.
# RUN_MONTHLY=true/false fuerza el comportamiento (backfills, pruebas).
MONTHLY_CLOSE_DAY_LIMIT = int(os.getenv("MONTHLY_CLOSE_DAY_LIMIT", "7"))
_manual_window = bool(os.getenv("START_DATE", "").strip() or os.getenv("END_DATE", "").strip())
_run_monthly_env = os.getenv("RUN_MONTHLY", "").strip().lower()
if _run_monthly_env in ("1", "true", "yes", "y"):
    RUN_MONTHLY = True
elif _run_monthly_env in ("0", "false", "no", "n"):
    RUN_MONTHLY = False
else:
    RUN_MONTHLY = _manual_window or _today.day <= MONTHLY_CLOSE_DAY_LIMIT

if _manual_window:
    MONTHLY_END_DATE = END_DATE
else:
    MONTHLY_END_DATE = (_today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m-%d")

START_YM = MONTHLY_START_DATE[:7].replace("-", "")
END_YM = END_DATE[:7].replace("-", "")
MONTHLY_END_YM = MONTHLY_END_DATE[:7].replace("-", "")

# =========================
# GA4 AUTH
# =========================
SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
CREDENTIALS_FILE = Path(os.getenv("GA4_CREDENTIALS_FILE", BASE_DIR / "credenciales.json"))

PAGE_SIZE = int(os.getenv("GA4_PAGE_SIZE", "10000"))
MAX_RETRIES = int(os.getenv("GA4_MAX_RETRIES", "5"))
RETRY_BASE_SECONDS = int(os.getenv("GA4_RETRY_BASE_SECONDS", "5"))

# =========================
# SQL SERVER
# =========================
SQL_SERVER = os.getenv("SQL_SERVER", "localhost")
SQL_DATABASE = os.getenv("SQL_DATABASE", "Digital_Impact_Reportes")
SQL_DRIVER = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
SQL_TRUSTED_CONNECTION = os.getenv("SQL_TRUSTED_CONNECTION", "yes").lower() in ("1", "true", "yes", "y")
SQL_USERNAME = os.getenv("SQL_USERNAME", "")
SQL_PASSWORD = os.getenv("SQL_PASSWORD", "")

# =========================
# OUTPUT / BACKUP
# =========================
LOAD_TO_SQL = os.getenv("LOAD_TO_SQL", "true").lower() in ("1", "true", "yes", "y")
SAVE_CSV_BACKUP = os.getenv("SAVE_CSV_BACKUP", "true").lower() in ("1", "true", "yes", "y")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "outputs_ga4_sql_backup"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# =========================
# REPORT DEFINITIONS
# =========================
REPORTS = [
    {
        "name": "monthly_core",
        "table": "ga4_monthly_core",
        "dimensions": ["yearMonth"],
        "metrics": [
            "sessions", "totalUsers", "activeUsers", "purchaseRevenue",
            "ecommercePurchases", "averagePurchaseRevenue", "itemsPurchased",
            "engagementRate", "screenPageViewsPerSession",
        ],
        "grain": "monthly",
        "order_by_dim": "yearMonth",
    },
    {
        "name": "monthly_rates",
        "table": "ga4_monthly_rates",
        "dimensions": ["yearMonth"],
        "metrics": [
            "cartToViewRate", "purchaseToViewRate", "sessionKeyEventRate:purchase",
            "eventCount", "itemsViewed", "itemsAddedToCart",
        ],
        "grain": "monthly",
        "order_by_dim": "yearMonth",
    },
    {
        "name": "monthly_events",
        "table": "ga4_monthly_events",
        "dimensions": ["yearMonth", "eventName"],
        "metrics": ["eventCount", "totalUsers"],
        "grain": "monthly",
        "order_by_dim": "yearMonth",
    },
    {
        "name": "monthly_channels",
        "table": "ga4_monthly_channels",
        "dimensions": ["yearMonth", "sessionDefaultChannelGroup"],
        "metrics": ["sessions", "purchaseRevenue", "ecommercePurchases", "sessionKeyEventRate:purchase"],
        "grain": "monthly",
        "order_by_dim": "yearMonth",
    },
    {
        "name": "monthly_devices",
        "table": "ga4_monthly_devices",
        "dimensions": ["yearMonth", "deviceCategory"],
        "metrics": ["sessions", "purchaseRevenue", "ecommercePurchases", "sessionKeyEventRate:purchase"],
        "grain": "monthly",
        "order_by_dim": "yearMonth",
    },
    {
        "name": "daily_core",
        "table": "ga4_daily_core",
        "dimensions": ["date"],
        "metrics": [
            "sessions", "totalUsers", "activeUsers", "purchaseRevenue",
            "ecommercePurchases", "averagePurchaseRevenue", "itemsPurchased",
            "engagementRate", "screenPageViewsPerSession",
        ],
        "grain": "daily",
        "order_by_dim": "date",
    },
    {
        "name": "daily_channels",
        "table": "ga4_daily_channels",
        "dimensions": ["date", "sessionDefaultChannelGroup"],
        "metrics": ["sessions", "purchaseRevenue", "ecommercePurchases", "sessionKeyEventRate:purchase"],
        "grain": "daily",
        "order_by_dim": "date",
    },
    {
        "name": "total_core_12m",
        "table": "ga4_total_core_12m",
        "dimensions": [],
        "metrics": [
            "sessions", "totalUsers", "activeUsers", "purchaseRevenue",
            "ecommercePurchases", "averagePurchaseRevenue", "itemsPurchased",
            "engagementRate", "screenPageViewsPerSession",
        ],
        "grain": "range",
    },
    {
        "name": "total_rates_12m",
        "table": "ga4_total_rates_12m",
        "dimensions": [],
        "metrics": [
            "cartToViewRate", "purchaseToViewRate", "sessionKeyEventRate:purchase",
            "eventCount", "itemsViewed", "itemsAddedToCart",
        ],
        "grain": "range",
    },
    {
        "name": "total_events_12m",
        "table": "ga4_total_events_12m",
        "dimensions": ["eventName"],
        "metrics": ["eventCount", "totalUsers"],
        "grain": "range",
    },
    {
        "name": "total_channels_12m",
        "table": "ga4_total_channels_12m",
        "dimensions": ["sessionDefaultChannelGroup"],
        "metrics": ["sessions", "purchaseRevenue", "ecommercePurchases", "sessionKeyEventRate:purchase"],
        "grain": "range",
    },
    {
        "name": "total_devices_12m",
        "table": "ga4_total_devices_12m",
        "dimensions": ["deviceCategory"],
        "metrics": ["sessions", "purchaseRevenue", "ecommercePurchases", "sessionKeyEventRate:purchase"],
        "grain": "range",
    },
    {
        "name": "items_12m",
        "table": "ga4_items_12m",
        "dimensions": ["itemName", "itemId"],
        "metrics": ["itemRevenue", "itemsPurchased", "itemsViewed", "itemsAddedToCart"],
        "grain": "range",
    },
    {
        "name": "categories_12m",
        "table": "ga4_categories_12m",
        "dimensions": ["itemCategory"],
        "metrics": ["itemRevenue", "itemsPurchased", "itemsViewed", "itemsAddedToCart"],
        "grain": "range",
    },
    {
        "name": "pages_12m",
        "table": "ga4_pages_12m",
        "dimensions": ["pagePath", "pageTitle"],
        "metrics": ["screenPageViews", "sessions", "purchaseRevenue"],
        "grain": "range",
    },
    {
        "name": "landing_pages_12m",
        "table": "ga4_landing_pages_12m",
        "dimensions": ["landingPage"],
        "metrics": ["sessions", "purchaseRevenue", "ecommercePurchases"],
        "grain": "range",
    },
    {
        "name": "landing_pages_monthly",
        "table": "ga4_landing_pages_monthly",
        "dimensions": ["yearMonth", "landingPage"],
        "metrics": ["sessions", "purchaseRevenue", "ecommercePurchases"],
        "grain": "monthly",
        "order_by_dim": "yearMonth",
    },
]

# Limitar reportes por nombre desde el entorno (ej. backfill solo mensual):
# REPORT_NAMES_TO_RUN=monthly_core,monthly_rates,monthly_events,monthly_channels,monthly_devices,landing_pages_monthly
_report_names_env = os.getenv("REPORT_NAMES_TO_RUN", "").strip()
REPORT_NAMES_TO_RUN = [x.strip() for x in _report_names_env.split(",") if x.strip()]
if REPORT_NAMES_TO_RUN:
    _unknown = set(REPORT_NAMES_TO_RUN) - {r["name"] for r in REPORTS}
    if _unknown:
        raise ValueError(f"REPORT_NAMES_TO_RUN contiene reportes desconocidos: {sorted(_unknown)}")
    REPORTS = [r for r in REPORTS if r["name"] in set(REPORT_NAMES_TO_RUN)]
