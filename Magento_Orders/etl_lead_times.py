"""
etl_lead_times.py — Réplica local de lead times de entrega (droplet).

GET /api/export/leadtimes?since&until (Bearer EXPORT_API_TOKEN) -> dbo.magento_lead_times.
Refresh por VENTANA rodante de created_at (las entregas finalizan días/semanas
después de la orden, así que se re-baja la cola reciente): DELETE de la ventana +
INSERT. Una fila por orden (PK order_id).

Uso:
  venv\\Scripts\\python.exe etl_lead_times.py                      # últimos 120 días
  venv\\Scripts\\python.exe etl_lead_times.py --since 2026-01-01   # backfill histórico
  venv\\Scripts\\python.exe etl_lead_times.py --dry-run

Tabla: aplicar antes 02_create_lead_times.sql.
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
import time

from sqlalchemy import text

from etl_magento_orders import (
    API_TOKEN, API_URL, get_engine, build_session, logger, HTTP_TIMEOUT,
)

LEADTIME_URL = API_URL.rsplit("/", 1)[0] + "/leadtimes"
DEFAULT_WINDOW_DAYS = 120

COLS = (
    "order_id", "logistics_zone", "courier", "created_at", "logistics_status",
    "delivery_at", "dias_lead", "dias_lead_limpio", "incluida_en_promedio",
)
_INT = {"dias_lead", "dias_lead_limpio", "incluida_en_promedio"}
_INSERT = (
    f"INSERT INTO dbo.magento_lead_times ({', '.join(f'[{c}]' for c in COLS)}) "
    f"VALUES ({', '.join(f':{c}' for c in COLS)})"
)


def sanitize(raw: dict) -> dict | None:
    row = {}
    for c in COLS:
        v = raw.get(c)
        if isinstance(v, str):
            v = v.strip()
            if v == "":
                v = None
        if c in _INT and v is not None:
            try:
                v = int(v)
            except (TypeError, ValueError):
                v = None
        row[c] = v
    if not str(row.get("order_id") or "").strip():
        return None
    return row


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", default=None, help="created_at >= (YYYY-MM-DD). Default: hoy-120d")
    p.add_argument("--until", default=None, help="created_at < (YYYY-MM-DD). Default: mañana")
    p.add_argument("--page-size", type=int, default=2000)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    if not API_TOKEN:
        logger.critical("EXPORT_API_TOKEN no configurado (.env)")
        return 1

    since = args.since or (dt.date.today() - dt.timedelta(days=DEFAULT_WINDOW_DAYS)).isoformat()
    until = args.until or (dt.date.today() + dt.timedelta(days=1)).isoformat()

    session = build_session()
    t0 = time.time()
    rows, skipped, page = [], 0, 1
    while True:
        resp = session.get(LEADTIME_URL,
                           params={"since": since, "until": until,
                                   "page": page, "page_size": args.page_size},
                           timeout=HTTP_TIMEOUT)
        if resp.status_code == 404:
            logger.critical("404 en %s — el endpoint /api/export/leadtimes aún no está "
                            "desplegado en el droplet (git pull + restart).", LEADTIME_URL)
            return 1
        if resp.status_code in (401, 403):
            logger.critical("Auth rechazada (%s): revisar EXPORT_API_TOKEN", resp.status_code)
            return 1
        resp.raise_for_status()
        data = resp.json()
        for raw in data.get("rows", []):
            r = sanitize(raw)
            if r is None:
                skipped += 1
            else:
                rows.append(r)
        logger.info("Página %d: %d filas (acum %d)", page, data.get("count", 0), len(rows))
        if not data.get("has_more"):
            break
        page += 1

    if args.dry_run:
        logger.info("[DRY-RUN] %d filas listas (skipped=%d, ventana %s..%s). No se escribe.",
                    len(rows), skipped, since, until)
        return 0

    engine = get_engine()
    with engine.begin() as conn:
        # Refresh de la ventana: borra el rango de created_at y reinserta.
        conn.execute(text("DELETE FROM dbo.magento_lead_times WHERE created_at >= :s AND created_at < :u"),
                     {"s": since, "u": until})
        if rows:
            conn.execute(text(_INSERT), rows)
    logger.info("Ventana %s..%s OK — %d filas (skipped=%d) en %.1fs",
                since, until, len(rows), skipped, time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
