"""
etl_catalogo_productos.py — Réplica local del catálogo del hub (droplet).

GET /api/export/catalog (Bearer EXPORT_API_TOKEN) -> dbo.Catalogo_Productos.
Full refresh por corrida (DELETE + INSERT en una transacción): el catálogo es
chico y así no se arrastran borrados. Sin fast_executemany (lección del
backfill de órdenes: SQLDescribeParam + buffer 510).

Uso:
  venv\\Scripts\\python.exe etl_catalogo_productos.py
  venv\\Scripts\\python.exe etl_catalogo_productos.py --dry-run

Tabla: aplicar antes 01_create_catalogo_productos.sql.
"""
from __future__ import annotations

import argparse
import sys
import time

from sqlalchemy import text

from etl_magento_orders import (
    API_TOKEN, API_URL, get_engine, build_session, logger, HTTP_TIMEOUT,
)

CATALOG_URL = API_URL.rsplit("/", 1)[0] + "/catalog"

COLS = (
    "id", "magento_id", "sku_tipo", "mc", "codigo_gp",
    "marca", "linea", "genero", "descripcion", "coleccion", "temporada",
    "name_web", "url_key", "base_image", "small_image",
    "price_web", "special_price_web",
    "producto_vigente", "web_segmentado",
    "updated_at",
)
_NUM = {"id", "magento_id", "price_web", "special_price_web",
        "producto_vigente", "web_segmentado"}
_INSERT = (
    f"INSERT INTO dbo.Catalogo_Productos ({', '.join(f'[{c}]' for c in COLS)}) "
    f"VALUES ({', '.join(f':{c}' for c in COLS)})"
)


def sanitize(raw: dict) -> dict | None:
    row = {}
    for c in COLS:
        v = raw.get(c)
        if isinstance(v, str):
            v = v.strip()
            if v == "" and (c in _NUM or c == "updated_at"):
                v = None
        row[c] = v
    if row["id"] is None or not str(row["mc"] or "").strip():
        return None
    return row


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--page-size", type=int, default=2000)
    args = p.parse_args()

    if not API_TOKEN:
        logger.critical("EXPORT_API_TOKEN no configurado (.env)")
        return 1

    session = build_session()
    t0 = time.time()
    rows, skipped, page = [], 0, 1
    while True:
        resp = session.get(CATALOG_URL, params={"page": page, "page_size": args.page_size},
                           timeout=HTTP_TIMEOUT)
        if resp.status_code == 404:
            logger.critical("404 en %s — el endpoint /api/export/catalog aún no está "
                            "desplegado en el droplet (commit 03dc0a3: git pull + restart).",
                            CATALOG_URL)
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

    if not rows:
        logger.critical("0 filas del API — no se toca la tabla local.")
        return 1
    if args.dry_run:
        logger.info("[DRY-RUN] %d filas listas (skipped=%d). No se escribe.", len(rows), skipped)
        return 0

    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM dbo.Catalogo_Productos"))
        conn.execute(text(_INSERT), rows)
    logger.info("Full refresh OK — %d filas (skipped=%d) en %.1fs",
                len(rows), skipped, time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
