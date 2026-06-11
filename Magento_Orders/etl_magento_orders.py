"""
etl_magento_orders.py — Consumidor del API de export de órdenes del droplet.

Replica order_magento_master (sin PII) en SQL Server local:
  GET /api/export/orders (Bearer EXPORT_API_TOKEN, paginado)
  -> MERGE dbo.Magento_Orders (PK id+sku+source_id)

Incremental por watermark `updated`: el watermark se persiste en
dbo.magento_etl_runs (watermark_after) y cada corrida arranca desde
watermark - OVERLAP_DAYS. El backfill itera ventanas de WINDOW_DAYS para
no profundizar el OFFSET en el servidor (1.9 GB RAM).

Uso:
  python etl_magento_orders.py                       # incremental (watermark)
  python etl_magento_orders.py --since 2025-01-01    # backfill inicial
  python etl_magento_orders.py --since 2026-06-01 --until 2026-06-10 --dry-run
  python etl_magento_orders.py --limit 50 --dry-run  # prueba segura

Tablas: aplicar antes 00_create_magento_orders_tables.sql (ver README_EJECUCION.md).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from tqdm import tqdm
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
# .env de la carpeta tiene prioridad; el de la raíz del repo es fallback
load_dotenv(BASE_DIR / ".env")
load_dotenv(BASE_DIR.parent / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("etl_magento_orders")

# =========================
# CONFIG
# =========================
API_URL = os.getenv("EXPORT_API_URL", "https://api.novedadescoliseum.com.pe/api/export/orders")
API_TOKEN = os.getenv("EXPORT_API_TOKEN", "")
PAGE_SIZE = int(os.getenv("EXPORT_PAGE_SIZE", "1000"))
OVERLAP_DAYS = int(os.getenv("EXPORT_OVERLAP_DAYS", "2"))
WINDOW_DAYS = int(os.getenv("EXPORT_WINDOW_DAYS", "30"))
HTTP_TIMEOUT = int(os.getenv("EXPORT_HTTP_TIMEOUT", "90"))
DEFAULT_SINCE = os.getenv("EXPORT_DEFAULT_SINCE", "2025-01-01")

SQL_SERVER = os.getenv("SQL_SERVER", "localhost")
SQL_DATABASE = os.getenv("SQL_DATABASE", "Digital_Impact_Reportes")
SQL_DRIVER = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server")
SQL_TRUSTED_CONNECTION = os.getenv("SQL_TRUSTED_CONNECTION", "yes").lower() in ("1", "true", "yes", "y")
SQL_USERNAME = os.getenv("SQL_USERNAME", "")
SQL_PASSWORD = os.getenv("SQL_PASSWORD", "")

# Mismo orden que la whitelist _EXPORT_COLS del blueprint (export_api.py)
COLS = (
    "id", "order_id", "created", "updated", "order_state", "order_status",
    "store_name", "store_id", "shipping_and_handling_information", "courrier",
    "customer_id", "departamento", "provincia", "distrito",
    "promo_id", "coupon_code", "discount_name", "discount_description",
    "payment_method", "pago_confirmado", "cuotas", "tarjeta_de_credito_o_debito",
    "invoice_date", "saleschannel",
    "payment_value", "grand_total_purchased",
    "qty_confirmed", "sku", "product_name", "source_id", "source_name",
    "original_price", "price", "row_total", "total_shipping_charges",
    "coupon_discount", "price_discount", "qty_ordered", "grand_total_item",
    "_ingested_at",
    "utm_source", "utm_medium", "utm_campaign", "session_type", "referrer",
    "purchaseorigin_created_at",
)
KEY_COLS = ("id", "sku", "source_id")
# Columnas tipadas: '' del API se convierte a NULL antes del MERGE
_DATE_COLS = {"created", "updated", "invoice_date", "_ingested_at", "purchaseorigin_created_at"}
_NUM_COLS = {
    "order_id", "store_id", "customer_id", "pago_confirmado", "cuotas",
    "payment_value", "grand_total_purchased", "qty_confirmed",
    "original_price", "price", "row_total", "total_shipping_charges",
    "coupon_discount", "price_discount", "qty_ordered", "grand_total_item",
}
# Anchos de las columnas string del destino: truncar en vez de tumbar el run.
# referrer se limita a 4000 para que el #stage no necesite nvarchar(max).
_MAXLEN = {
    "referrer": 4000,
    "id": 40, "sku": 120, "source_id": 40, "order_state": 50, "order_status": 30,
    "store_name": 200, "shipping_and_handling_information": 2000, "courrier": 120,
    "departamento": 120, "provincia": 120, "distrito": 120, "promo_id": 200,
    "coupon_code": 120, "discount_name": 2000, "discount_description": 2000,
    "payment_method": 80, "tarjeta_de_credito_o_debito": 60, "saleschannel": 80,
    "product_name": 400, "source_name": 200, "utm_source": 1024, "utm_medium": 1024,
    "utm_campaign": 1024, "session_type": 128,
}


# =========================
# SQL SERVER (mismo patrón que GA4/ga4_db.py)
# =========================
def get_engine() -> Engine:
    if SQL_TRUSTED_CONNECTION:
        conn_str = (
            f"DRIVER={{{SQL_DRIVER}}};SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};"
            "Trusted_Connection=yes;TrustServerCertificate=yes;"
        )
    else:
        conn_str = (
            f"DRIVER={{{SQL_DRIVER}}};SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};"
            f"UID={SQL_USERNAME};PWD={SQL_PASSWORD};TrustServerCertificate=yes;"
        )
    params = urllib.parse.quote_plus(conn_str)
    # SIN fast_executemany: SQLDescribeParam no puede describir parámetros de un
    # INSERT a tabla temporal (#stage) y pyodbc cae a un buffer de 255 chars ->
    # 'String data, right truncation: ... buffer 510' con cualquier string >255
    # (referrers de Facebook), sin importar el ancho declarado de la columna.
    return create_engine(f"mssql+pyodbc:///?odbc_connect={params}", future=True)


def ensure_tables(engine: Engine) -> None:
    with engine.connect() as conn:
        ok = conn.execute(text(
            "SELECT CASE WHEN OBJECT_ID('dbo.Magento_Orders','U') IS NOT NULL "
            "AND OBJECT_ID('dbo.magento_etl_runs','U') IS NOT NULL THEN 1 ELSE 0 END"
        )).scalar()
    if not ok:
        logger.critical(
            "Faltan tablas destino. Aplicar primero:\n"
            "  sqlcmd -S localhost -E -C -d %s -i %s",
            SQL_DATABASE, BASE_DIR / "00_create_magento_orders_tables.sql",
        )
        sys.exit(1)


def get_watermark(engine: Engine) -> datetime | None:
    with engine.connect() as conn:
        wm = conn.execute(text(
            "SELECT MAX(watermark_after) FROM dbo.magento_etl_runs WHERE status = 'success'"
        )).scalar()
    return wm


def run_start(engine: Engine, since: datetime, until: datetime) -> int:
    with engine.begin() as conn:
        return conn.execute(
            text(
                "INSERT INTO dbo.magento_etl_runs (since_param, until_param, status) "
                "OUTPUT INSERTED.run_id VALUES (:s, :u, 'running')"
            ),
            {"s": since, "u": until},
        ).scalar()


def run_finish(engine: Engine, run_id: int, status: str, watermark_after, pages, fetched, upserted, skipped, error=None):
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE dbo.magento_etl_runs SET finished_at = SYSUTCDATETIME(), status = :st, "
                "watermark_after = :wm, pages = :pg, rows_fetched = :rf, rows_upserted = :ru, "
                "rows_skipped = :rs, error_message = :em WHERE run_id = :rid"
            ),
            {"st": status, "wm": watermark_after, "pg": pages, "rf": fetched,
             "ru": upserted, "rs": skipped, "em": (str(error)[:4000] if error else None), "rid": run_id},
        )


# MERGE vía tabla de staging por lote (fast_executemany) — una transacción por página
_COL_LIST = ", ".join(f"[{c}]" for c in COLS)
_PARAM_LIST = ", ".join(f":{c}" for c in COLS)
_UPDATE_SET = ", ".join(f"t.[{c}] = s.[{c}]" for c in COLS if c not in KEY_COLS)
_STAGE_DDL = """
IF OBJECT_ID('tempdb..#stage') IS NOT NULL DROP TABLE #stage;
CREATE TABLE #stage (
    id varchar(40) NOT NULL, sku varchar(120) NOT NULL, source_id varchar(40) NOT NULL,
    order_id bigint NULL, created datetime2 NULL, updated datetime2 NULL,
    order_state nvarchar(50) NULL, order_status varchar(30) NULL,
    store_name nvarchar(200) NULL, store_id int NULL,
    shipping_and_handling_information nvarchar(2000) NULL, courrier varchar(120) NULL,
    customer_id bigint NULL, departamento varchar(120) NULL, provincia varchar(120) NULL,
    distrito varchar(120) NULL, promo_id varchar(200) NULL, coupon_code varchar(120) NULL,
    discount_name nvarchar(2000) NULL, discount_description nvarchar(2000) NULL,
    payment_method varchar(80) NULL, pago_confirmado bit NULL, cuotas int NULL,
    tarjeta_de_credito_o_debito varchar(60) NULL, invoice_date datetime2 NULL,
    saleschannel varchar(80) NULL, payment_value decimal(18,2) NULL,
    grand_total_purchased decimal(18,2) NULL, qty_confirmed decimal(18,2) NULL,
    product_name nvarchar(400) NULL, source_name nvarchar(200) NULL,
    original_price decimal(18,2) NULL, price decimal(18,2) NULL, row_total decimal(18,2) NULL,
    total_shipping_charges decimal(18,2) NULL, coupon_discount decimal(18,2) NULL,
    price_discount decimal(18,2) NULL, qty_ordered decimal(18,2) NULL,
    grand_total_item decimal(18,2) NULL, _ingested_at datetime2 NULL,
    utm_source nvarchar(1024) NULL, utm_medium nvarchar(1024) NULL,
    utm_campaign nvarchar(1024) NULL, session_type varchar(128) NULL,
    referrer nvarchar(4000) NULL, purchaseorigin_created_at datetime2 NULL
);
"""
_MERGE_SQL = f"""
MERGE dbo.Magento_Orders AS t
USING #stage AS s
   ON t.id = s.id AND t.sku = s.sku AND t.source_id = s.source_id
WHEN MATCHED THEN UPDATE SET {_UPDATE_SET}, t.extracted_at = SYSUTCDATETIME()
WHEN NOT MATCHED THEN INSERT ({_COL_LIST}) VALUES ({", ".join(f"s.[{c}]" for c in COLS)});
"""


def sanitize_row(raw: dict) -> dict | None:
    """Whitelist + '' -> NULL en columnas tipadas. None si falta la clave."""
    row = {}
    for c in COLS:
        v = raw.get(c)
        if isinstance(v, str):
            v = v.strip()
            if v == "" and (c in _DATE_COLS or c in _NUM_COLS):
                v = None
            elif c in _MAXLEN and len(v) > _MAXLEN[c]:
                logger.warning("Truncando %s (%d chars) en id=%s", c, len(v), raw.get("id"))
                v = v[:_MAXLEN[c]]
        row[c] = v
    for k in KEY_COLS:
        if row[k] is None or str(row[k]).strip() == "":
            return None
    row["id"] = str(row["id"]).strip()
    row["sku"] = str(row["sku"]).strip()
    row["source_id"] = str(row["source_id"]).strip()
    return row


_INSERT_SQL = f"INSERT INTO #stage ({_COL_LIST}) VALUES ({_PARAM_LIST})"


def upsert_page(engine: Engine, rows: list[dict]) -> int:
    # dedup por clave dentro de la página (MERGE falla con fuente duplicada)
    by_key = {(r["id"], r["sku"], r["source_id"]): r for r in rows}
    rows = list(by_key.values())
    if not rows:
        return 0
    try:
        with engine.begin() as conn:
            conn.execute(text(_STAGE_DDL))
            conn.execute(text(_INSERT_SQL), rows)
            conn.execute(text(_MERGE_SQL))
        return len(rows)
    except Exception as exc:
        logger.warning("INSERT por lote falló (%s) — reintentando la página fila por fila", exc)
        return _upsert_row_by_row(engine, rows)


def _upsert_row_by_row(engine: Engine, rows: list[dict]) -> int:
    """Fallback blindaje: aísla y loguea la fila culpable sin tumbar el run."""
    ok = 0
    with engine.begin() as conn:
        conn.execute(text(_STAGE_DDL))
        for r in rows:
            try:
                conn.execute(text(_INSERT_SQL), r)
                ok += 1
            except Exception as exc:
                logger.error(
                    "Fila descartada id=%s sku=%s source_id=%s: %s",
                    r["id"], r["sku"], r["source_id"], exc,
                )
        conn.execute(text(_MERGE_SQL))
    return ok


# =========================
# API CLIENT
# =========================
def build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5, backoff_factor=3,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.headers["Authorization"] = f"Bearer {API_TOKEN}"
    return s


def fetch_page(session: requests.Session, since: datetime, until: datetime | None, page: int, page_size: int) -> dict:
    params = {
        "updated_since": since.strftime("%Y-%m-%d %H:%M:%S"),
        "page": page,
        "page_size": page_size,
    }
    if until is not None:
        params["updated_until"] = until.strftime("%Y-%m-%d %H:%M:%S")
    resp = session.get(API_URL, params=params, timeout=HTTP_TIMEOUT)
    if resp.status_code in (401, 403):
        logger.critical("Auth rechazada por el API (%s): revisar EXPORT_API_TOKEN", resp.status_code)
        sys.exit(1)
    resp.raise_for_status()
    return resp.json()


def iter_windows(since: datetime, until: datetime, days: int):
    cur = since
    step = timedelta(days=days)
    while cur < until:
        yield cur, min(cur + step, until)
        cur += step


# =========================
# MAIN
# =========================
def parse_args():
    p = argparse.ArgumentParser(description="Replica órdenes Magento (API export droplet) -> SQL Server local")
    p.add_argument("--since", help="Inicio YYYY-MM-DD (default: watermark - overlap, o EXPORT_DEFAULT_SINCE)")
    p.add_argument("--until", help="Fin YYYY-MM-DD (default: ahora)")
    p.add_argument("--page-size", type=int, default=PAGE_SIZE)
    p.add_argument("--dry-run", action="store_true", help="Descarga y valida sin escribir en SQL")
    p.add_argument("--limit", type=int, default=None, help="Cortar tras N filas (prueba segura)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not API_TOKEN:
        logger.critical("EXPORT_API_TOKEN no configurado (.env)")
        return 1

    engine = get_engine()
    ensure_tables(engine)

    if args.since:
        since = datetime.strptime(args.since, "%Y-%m-%d")
    else:
        wm = get_watermark(engine)
        if wm is not None:
            since = wm - timedelta(days=OVERLAP_DAYS)
            logger.info("Watermark previo: %s -> arrancando desde %s (overlap %sd)", wm, since, OVERLAP_DAYS)
        else:
            since = datetime.strptime(DEFAULT_SINCE, "%Y-%m-%d")
            logger.info("Sin watermark previo -> backfill desde EXPORT_DEFAULT_SINCE=%s", DEFAULT_SINCE)
    until = datetime.strptime(args.until, "%Y-%m-%d") if args.until else datetime.now()

    session = build_session()
    run_id = None if args.dry_run else run_start(engine, since, until)
    t0 = time.time()
    pages = fetched = upserted = skipped = 0
    watermark_after = None
    windows = list(iter_windows(since, until, WINDOW_DAYS))

    try:
        for w_start, w_end in tqdm(windows, desc="Ventanas", unit="ventana"):
            page = 1
            while True:
                data = fetch_page(session, w_start, w_end, page, args.page_size)
                rows_raw = data.get("rows", [])
                pages += 1
                fetched += len(rows_raw)

                rows = []
                for raw in rows_raw:
                    r = sanitize_row(raw)
                    if r is None:
                        skipped += 1
                        logger.warning("Fila sin clave completa descartada: id=%s sku=%s source_id=%s",
                                       raw.get("id"), raw.get("sku"), raw.get("source_id"))
                    else:
                        rows.append(r)

                if rows and not args.dry_run:
                    upserted += upsert_page(engine, rows)
                if data.get("max_updated"):
                    watermark_after = max(watermark_after or data["max_updated"], data["max_updated"])

                if args.limit and fetched >= args.limit:
                    logger.info("--limit %s alcanzado, cortando.", args.limit)
                    raise StopIteration
                if not data.get("has_more"):
                    break
                page += 1
    except StopIteration:
        pass
    except Exception as exc:
        logger.critical("Fallo sistémico: %s", exc, exc_info=True)
        if run_id is not None:
            run_finish(engine, run_id, "error", watermark_after, pages, fetched, upserted, skipped, error=exc)
        return 1

    elapsed = time.time() - t0
    # En dry-run o corrida parcial (--limit/--until) NO avanzar watermark más allá de lo visto
    if run_id is not None:
        run_finish(engine, run_id, "success", watermark_after, pages, fetched, upserted, skipped)
    logger.info(
        "Run completo — pages=%d fetched=%d upserted=%d skipped=%d watermark=%s elapsed=%.1fs%s",
        pages, fetched, upserted, skipped, watermark_after, elapsed,
        " [DRY-RUN]" if args.dry_run else "",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
