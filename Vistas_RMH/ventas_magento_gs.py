# export_magento_gs.py
import os
import math
import numpy as np
import pandas as pd
import pyodbc
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from gspread.utils import rowcol_to_a1
from datetime import date, timedelta

# =========================
# CONFIG
# =========================
SQL_DRIVER     = os.getenv("DI_SQL_DRIVER", "ODBC Driver 17 for SQL Server")
SQL_SERVER     = os.getenv("DI_SQL_SERVER", "localhost")
SQL_DATABASE   = os.getenv("DI_SQL_DATABASE", "Digital_Impact_Reportes")
VISTA_SQL      = "dbo.vw_magento_gs_export"

# Filtros
EXCLUIR_CHILE  = True          # excluye Tienda_Web que terminan en " CL"
DAYS_BACK      = 28             # 4 semanas atrás

# Google Sheets
GS_CRED_PATH   = r"D:\Programs\1. Apps\7. Digital Impact\4. BI\Vistas_RMH\di-auth-gsheets.json"
GS_SHEET_NAME  = "Ecom | Venta Magento"
GS_TAB_NAME    = "Data"

# Subida por lotes
MAX_ROWS_BATCH = 10000  # filas por lote

# =========================
# SQL
# =========================
def sql_connect():
    conn_str = (
        f"DRIVER={{{SQL_DRIVER}}};"
        f"SERVER={SQL_SERVER};"
        f"DATABASE={SQL_DATABASE};"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
        "Connection Timeout=15;"
    )
    return pyodbc.connect(conn_str)

def build_query():
    where = []
    if EXCLUIR_CHILE:
        where.append("Tienda_Web NOT LIKE '% CL'")
    if DAYS_BACK:
        desde = (date.today() - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%d")
        where.append(f"fecha_local >= '{desde}'")
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    query = f"SELECT * FROM {VISTA_SQL} {where_sql}"
    print(f"🧠 Ejecutando Query:\n{query}\n")
    return query

# =========================
# Limpieza/normalización
# =========================
def normalize_df(df: pd.DataFrame) -> pd.DataFrame:
    df = df.replace([np.inf, -np.inf], np.nan).fillna("")

    dt_cols = df.select_dtypes(include=["datetime64[ns]", "datetime64[ns, UTC]"]).columns.tolist()
    for col in dt_cols:
        df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")

    obj_cols = df.select_dtypes(include=["object"]).columns.tolist()
    for col in obj_cols:
        sample = df[col].dropna().astype(str).head(100)
        looks_datey = sample.str.contains(
            r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}/\d{1,2}/\d{2,4}", regex=True
        ).any()
        if looks_datey:
            parsed = pd.to_datetime(df[col], errors="coerce", utc=False)
            if parsed.notna().mean() >= 0.05:
                df[col] = parsed.dt.strftime("%Y-%m-%d %H:%M:%S").fillna("")
            else:
                df[col] = df[col].astype(str)
        else:
            df[col] = df[col].astype(str)

    return df

# =========================
# Google Sheets helpers
# =========================
def gs_auth(creds_path: str):
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(creds_path, scope)
    return gspread.authorize(creds)

def gs_get_or_create_worksheet(client, sheet_name: str, tab_name: str):
    try:
        ss = client.open(sheet_name)
    except gspread.SpreadsheetNotFound:
        ss = client.create(sheet_name)
    try:
        ws = ss.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=tab_name, rows=100, cols=26)
    return ss, ws

def ensure_grid(ws, nrows: int, ncols: int):
    cur_rows = ws.row_count
    cur_cols = ws.col_count
    need_rows = max(cur_rows, nrows)
    need_cols = max(cur_cols, ncols)
    if need_rows != cur_rows or need_cols != cur_cols:
        ws.resize(rows=need_rows, cols=need_cols)

def gs_clear_and_update(ws, df: pd.DataFrame):
    ws.clear()

    headers = df.columns.tolist()
    nrows = len(df)
    ncols = len(headers)
    ensure_grid(ws, nrows + 1, ncols)

    ws.update(range_name=rowcol_to_a1(1, 1), values=[headers])

    if nrows == 0:
        return

    batches = math.ceil(nrows / MAX_ROWS_BATCH)
    for i in range(batches):
        start = i * MAX_ROWS_BATCH
        end = min((i + 1) * MAX_ROWS_BATCH, nrows)
        rows_chunk = df.iloc[start:end].values.tolist()
        start_row = start + 2
        ws.update(
            range_name=rowcol_to_a1(start_row, 1),
            values=rows_chunk,
            value_input_option="RAW"
        )

# =========================
# MAIN
# =========================
def main():
    print(f"⏳ Leyendo SQL → {VISTA_SQL} ...")
    query = build_query()
    conn = sql_connect()
    try:
        df = pd.read_sql(query, conn)
    finally:
        conn.close()

    print(f"✔ {len(df):,} filas leídas. Normalizando tipos…")
    df = normalize_df(df)

    print("🔐 Autenticando Google Sheets…")
    client = gs_auth(GS_CRED_PATH)
    ss, ws = gs_get_or_create_worksheet(client, GS_SHEET_NAME, GS_TAB_NAME)

    print(f"🧹 Subiendo a '{GS_SHEET_NAME}' / '{GS_TAB_NAME}' …")
    gs_clear_and_update(ws, df)

    print("✅ Exportación completa.")

if __name__ == "__main__":
    main()

