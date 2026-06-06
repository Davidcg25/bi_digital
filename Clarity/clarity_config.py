from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
ROOT_ENV_FILE = BASE_DIR.parent / ".env"
load_dotenv(ROOT_ENV_FILE)
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR / ".env.example")

API_URL = "https://www.clarity.ms/export-data/api/v1/project-live-insights"
CREDENTIALS_FILE = Path(os.getenv("CLARITY_CREDENTIALS_FILE", BASE_DIR / "credenciales.xlsx"))
if not CREDENTIALS_FILE.is_absolute():
    CREDENTIALS_FILE = BASE_DIR / CREDENTIALS_FILE

NUM_OF_DAYS = int(os.getenv("CLARITY_NUM_OF_DAYS", "3"))
if NUM_OF_DAYS not in (1, 2, 3):
    raise ValueError("CLARITY_NUM_OF_DAYS debe ser 1, 2 o 3")

MAX_WORKERS = int(os.getenv("CLARITY_MAX_WORKERS", "4"))
MAX_RETRIES = int(os.getenv("CLARITY_MAX_RETRIES", "3"))
RETRY_BASE_SECONDS = int(os.getenv("CLARITY_RETRY_BASE_SECONDS", "10"))

_projects_env = os.getenv("CLARITY_PROJECTS_TO_RUN", "").strip()
PROJECTS_TO_RUN = [x.strip() for x in _projects_env.split(",") if x.strip()] if _projects_env else []

_project_keys_env = os.getenv("CLARITY_PROJECT_KEYS", "").strip()
PROJECT_KEYS = [x.strip() for x in _project_keys_env.split(",") if x.strip()] if _project_keys_env else []

_reports_env = os.getenv("CLARITY_REPORTS_TO_RUN", "").strip()
REPORTS_TO_RUN = [x.strip() for x in _reports_env.split(",") if x.strip()] if _reports_env else []

SQL_SERVER = os.getenv("SQL_SERVER", "localhost")
SQL_DATABASE = os.getenv("SQL_DATABASE", "Digital_Impact_Reportes")
SQL_DRIVER = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
SQL_TRUSTED_CONNECTION = os.getenv("SQL_TRUSTED_CONNECTION", "yes").lower() in ("1", "true", "yes", "y")
SQL_USERNAME = os.getenv("SQL_USERNAME", "")
SQL_PASSWORD = os.getenv("SQL_PASSWORD", "")

LOAD_TO_SQL = os.getenv("LOAD_TO_SQL", "true").lower() in ("1", "true", "yes", "y")
SAVE_JSON_BACKUP = os.getenv("SAVE_JSON_BACKUP", "true").lower() in ("1", "true", "yes", "y")
OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", BASE_DIR / "outputs_clarity_sql_backup"))
if not OUTPUT_DIR.is_absolute():
    OUTPUT_DIR = BASE_DIR / OUTPUT_DIR
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

REPORTS = [
    {"name": "overview", "dimensions": []},
    {"name": "by_device", "dimensions": ["Device"]},
    {"name": "by_channel", "dimensions": ["Channel"]},
    {"name": "by_source_medium_campaign", "dimensions": ["Source", "Medium", "Campaign"]},
    {"name": "by_url_device", "dimensions": ["URL", "Device"]},
    {"name": "by_country_device", "dimensions": ["Country/Region", "Device"]},
]

if REPORTS_TO_RUN:
    _wanted_reports = {x.lower() for x in REPORTS_TO_RUN}
    REPORTS = [report for report in REPORTS if report["name"].lower() in _wanted_reports]
    if not REPORTS:
        raise ValueError("CLARITY_REPORTS_TO_RUN no coincide con ningun reporte definido")
