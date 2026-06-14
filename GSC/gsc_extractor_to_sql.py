# -*- coding: utf-8 -*-
"""
GSC (Google Search Console) → SQL Server. Búsqueda orgánica por mes.

Reusa el service account de GA4 (GA4/credenciales.json), que ya tiene acceso a
todas las propiedades GSC. Grano mensual: por cada sitio/mes pega a la Search
Analytics API una vez para los totales (core) y otra para el detalle por query.

Tablas: gsc_monthly_core, gsc_monthly_queries (DDL en 00_create_gsc_tables.sql).

Grano (env GRAIN): 'monthly' (default, mes cerrado → scorecard) o 'weekly'
(rolling últimas WEEKS semanas → monitoreo intra-mes). Tablas gsc_monthly_* y
gsc_weekly_* (DDL en 00_create_gsc_tables.sql).

Correr:
  venv\\Scripts\\python.exe GSC\\gsc_extractor_to_sql.py                  (mensual, últimos 16m)
  GRAIN=weekly venv\\Scripts\\python.exe GSC\\gsc_extractor_to_sql.py     (semanal, últimas 12 sem)
  PROPERTIES=Coliseum START_YM=202605 END_YM=202605 ... gsc_extractor...   (mensual acotado)
"""
from __future__ import annotations

import calendar
import datetime as dt
import os
import time
from pathlib import Path

import pandas as pd
from google.oauth2 import service_account
from googleapiclient.discovery import build
from sqlalchemy import create_engine, text

BASE = Path(__file__).resolve().parent
CREDENTIALS_FILE = Path(os.getenv("GA4_CREDENTIALS_FILE", BASE.parent / "GA4" / "credenciales.json"))
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]
ENGINE = create_engine(
    "mssql+pyodbc://@localhost/Digital_Impact_Reportes"
    "?trusted_connection=yes&driver=ODBC+Driver+17+for+SQL+Server"
)

# marca (= property_name de GA4 / Tienda_final RMH) → siteUrl de Search Console.
# Resuelto vía sites().list() del SA (jun-2026). Fila no tiene sitio en GSC.
GSC_SITES = {
    "Coliseum": "https://www.coliseum.com.pe/",
    "Caterpillar": "https://www.catlifestyle.pe/",
    "Converse": "https://www.converse.com.pe/",
    "Merrell": "https://www.merrell.com.pe/",
    "New Balance": "https://newbalance.com.pe/",
    "Steve Madden": "https://www.stevemadden.com.pe/",
    "Umbro": "https://umbro.com.pe/",
}

ROW_LIMIT = 25000
MAX_RETRIES = 4
RETRY_BASE = 4
QUERY_TOP_N = int(os.getenv("GSC_QUERY_TOP_N", "1000"))   # guardar top-N queries/periodo

# Grano: 'monthly' (mes cerrado, scorecard) o 'weekly' (rolling, monitoreo).
GRAIN = os.getenv("GRAIN", "monthly").strip().lower()
TABLES = {
    "monthly": {"core": "gsc_monthly_core", "queries": "gsc_monthly_queries", "keycol": "year_month"},
    "weekly":  {"core": "gsc_weekly_core",  "queries": "gsc_weekly_queries",  "keycol": "week_start"},
}


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}")


def closed_month() -> str:
    first = dt.date.today().replace(day=1)
    last_closed = first - dt.timedelta(days=1)
    return last_closed.strftime("%Y%m")


def shift_ym(ym: str, months: int) -> str:
    y, m = int(ym[:4]), int(ym[4:])
    total = y * 12 + (m - 1) + months
    return f"{total // 12:04d}{total % 12 + 1:02d}"


def months_between(start_ym: str, end_ym: str) -> list[str]:
    out, cur = [], start_ym
    while cur <= end_ym:
        out.append(cur)
        cur = shift_ym(cur, 1)
    return out


def month_bounds(ym: str) -> tuple[str, str]:
    y, m = int(ym[:4]), int(ym[4:])
    return f"{y}-{m:02d}-01", f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}"


def week_periods(n: int) -> list[tuple[str, tuple[str, str]]]:
    """Últimas n semanas (lunes ISO). Cada periodo: (week_start, (start, end))."""
    today = dt.date.today()
    this_monday = today - dt.timedelta(days=today.weekday())
    out = []
    for i in range(n - 1, -1, -1):
        mon = this_monday - dt.timedelta(weeks=i)
        end = min(mon + dt.timedelta(days=6), today)   # semana en curso se recorta a hoy
        out.append((mon.isoformat(), (mon.isoformat(), end.isoformat())))
    return out


def build_service():
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError(f"No encontré credenciales en: {CREDENTIALS_FILE}")
    creds = service_account.Credentials.from_service_account_file(str(CREDENTIALS_FILE), scopes=SCOPES)
    return build("searchconsole", "v1", credentials=creds, cache_discovery=False)


def gsc_query(svc, site: str, start: str, end: str, dimensions: list[str], row_limit: int = ROW_LIMIT) -> list[dict]:
    """Search Analytics con paginación + retry exponencial."""
    rows, start_row = [], 0
    while True:
        body = {"startDate": start, "endDate": end, "dimensions": dimensions,
                "rowLimit": row_limit, "startRow": start_row, "dataState": "final"}
        for attempt in range(MAX_RETRIES):
            try:
                resp = svc.searchanalytics().query(siteUrl=site, body=body).execute()
                break
            except Exception as e:
                if attempt == MAX_RETRIES - 1:
                    raise
                wait = RETRY_BASE * (attempt + 1)
                log("RETRY", f"{site} | {dimensions} | intento {attempt+1}: {type(e).__name__} -> espera {wait}s")
                time.sleep(wait)
        batch = resp.get("rows", [])
        rows.extend(batch)
        if len(batch) < row_limit:
            break
        start_row += row_limit
    return rows


def delete_existing(table: str, keycol: str, property_name: str, keyval: str) -> None:
    with ENGINE.begin() as conn:
        conn.execute(text(f"DELETE FROM dbo.{table} WHERE property_name=:p AND {keycol}=:k"),
                     {"p": property_name, "k": keyval})


def load_df(df: pd.DataFrame, table: str) -> int:
    if df.empty:
        return 0
    df.to_sql(table, ENGINE, schema="dbo", if_exists="append", index=False, chunksize=1000)
    return len(df)


def run_period(svc, marca: str, site: str, grain: str, keyval: str, start: str, end: str) -> tuple[int, int]:
    t = TABLES[grain]
    keycol = t["keycol"]
    # core (totales del periodo)
    core = gsc_query(svc, site, start, end, dimensions=[], row_limit=1)
    core_rows = 0
    if core:
        r = core[0]
        df_core = pd.DataFrame([{
            "property_name": marca, "site_url": site, keycol: keyval,
            "clicks": int(r.get("clicks", 0)), "impressions": int(r.get("impressions", 0)),
            "ctr": float(r.get("ctr", 0.0)), "position": float(r.get("position", 0.0)),
        }])
        delete_existing(t["core"], keycol, marca, keyval)
        core_rows = load_df(df_core, t["core"])
    # queries (detalle)
    qrows = gsc_query(svc, site, start, end, dimensions=["query"])
    q_loaded = 0
    if qrows:
        dfq = pd.DataFrame([{
            "property_name": marca, keycol: keyval,
            "query": (row["keys"][0] or "")[:500],
            "clicks": int(row.get("clicks", 0)), "impressions": int(row.get("impressions", 0)),
            "ctr": float(row.get("ctr", 0.0)), "position": float(row.get("position", 0.0)),
        } for row in qrows])
        dfq = dfq.sort_values("clicks", ascending=False).head(QUERY_TOP_N)
        delete_existing(t["queries"], keycol, marca, keyval)
        q_loaded = load_df(dfq, t["queries"])
    return core_rows, q_loaded


def main() -> None:
    grain = GRAIN if GRAIN in TABLES else "monthly"
    if grain == "weekly":
        n = int(os.getenv("WEEKS", "12"))
        periods = week_periods(n)              # [(week_start, (start, end)), ...]
        desc = f"semanal · últimas {n} semanas"
    else:
        end_ym = os.getenv("END_YM", "").strip() or closed_month()
        start_ym = os.getenv("START_YM", "").strip() or shift_ym(end_ym, -15)  # GSC ~16m
        periods = [(ym, month_bounds(ym)) for ym in months_between(start_ym, end_ym)]
        desc = f"mensual · {start_ym}..{end_ym}"

    props_env = os.getenv("PROPERTIES", "").strip()
    marcas = [m.strip() for m in props_env.split(",") if m.strip()] if props_env else list(GSC_SITES)
    log("START", f"GSC -> SQL | {desc} ({len(periods)} periodos) | marcas: {', '.join(marcas)}")
    svc = build_service()

    ok = fail = 0
    for marca in marcas:
        site = GSC_SITES.get(marca)
        if not site:
            log("SKIP", f"{marca}: sin sitio GSC mapeado")
            continue
        for keyval, (start, end) in periods:
            try:
                c, qn = run_period(svc, marca, site, grain, keyval, start, end)
                log("LOAD", f"{marca} | {keyval} | core={c} queries={qn}")
                ok += 1
            except Exception as e:
                log("ERROR", f"{marca} | {keyval} | {type(e).__name__}: {e}")
                fail += 1
    log("DONE", f"Finalizado. grain={grain} ok={ok} fail={fail}")


if __name__ == "__main__":
    main()
