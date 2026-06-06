"""
GA4 Ecommerce daily channel detail -> Google Sheets.

Sheet target: Ecommerce-GA4

Usage:
    python ga4_ecommerce_to_sheets.py
"""

from __future__ import annotations

import math
import os
import time
from datetime import date, datetime
from typing import Dict, Iterable, List

import gspread
import pandas as pd
from sqlalchemy import text
from gspread.exceptions import APIError
from google.oauth2 import service_account
from gspread.exceptions import SpreadsheetNotFound, WorksheetNotFound
from gspread.utils import rowcol_to_a1

import ga4_config as config
from ga4_db import get_engine


SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SPREADSHEET_NAME = os.getenv("GA4_ECOMMERCE_SPREADSHEET_NAME", "Ecommerce-GA4")
SPREADSHEET_ID = os.getenv("GA4_ECOMMERCE_SPREADSHEET_ID", "").strip()
CHANNEL_EQUIVALENCES_TABLE = os.getenv("GA4_CHANNEL_EQUIVALENCES_TABLE", "dbo.Equivalencias_Canales")
MONTHLY_CHANNELS_TABLE = os.getenv("GA4_MONTHLY_CHANNELS_TABLE", "dbo.ga4_monthly_channels")
RMH_GA4_CHANNEL_VIEW = os.getenv("GA4_RMH_CHANNEL_VIEW", "dbo.vw_ga4_rmh_mensual_canal")
RMH_GA4_OWNER_VIEW = os.getenv("GA4_RMH_OWNER_VIEW", "dbo.vw_ga4_rmh_mensual_responsable")
WRITE_CHUNK_ROWS = int(os.getenv("GA4_SHEETS_WRITE_CHUNK_ROWS", "5000"))
SHEETS_RETRY_BASE_SECONDS = int(os.getenv("GA4_SHEETS_RETRY_BASE_SECONDS", "20"))
SHEETS_MAX_RETRIES = int(os.getenv("GA4_SHEETS_MAX_RETRIES", "4"))
REPORT_START_DATE = os.getenv("GA4_ECOMMERCE_START_DATE", "2025-01-01")
REPORT_END_DATE = os.getenv("GA4_ECOMMERCE_END_DATE", config.END_DATE)

INTEGER_COLUMNS = {
    "sessions",
    "transacciones",
    "total_sessions_mes",
    "total_transacciones_mes",
    "transactions",
    "sessions_total_mes",
    "transactions_total_mes",
    "ordenes_rmh_ecommerce_total",
}
AMOUNT_COLUMNS = {
    "ingresos",
    "ingreso_por_sesion",
    "ticket_promedio",
    "total_ingresos_mes",
    "ga4_revenue",
    "ga4_revenue_total_mes",
    "ingresos_rmh_ecommerce_total",
    "ingresos_rmh_prorrateados",
    "unidades_rmh_ecommerce_total",
    "unidades_rmh_prorrateadas",
    "contribucion_rmh_prorrateada",
}
PERCENT_COLUMNS = {
    "conversion_rate",
    "share_ingresos_propiedad",
    "share_sesiones_propiedad",
    "share_ingresos_mes",
    "participacion_sesiones_ga4",
    "participacion_ingresos_ga4",
}
DATE_COLUMNS = {"fecha", "start_date", "end_date", "mes", "mes_fecha"}
TOTAL_ROW_LABEL = "TOTAL MES"
OBSOLETE_TABS = {"Tendencia_Diaria_Propiedad", "Detalle_Diario_Canal", "Resumen_Diario"}

FALLBACK_CHANNEL_EQUIVALENCES = [
    ("Cross-network", "Google Ads", "Mkt Digital"),
    ("Direct", "Direct", "Branding"),
    ("Display", "Google Ads", "Mkt Digital"),
    ("Email", "Automation", "Patagonia"),
    ("Mobile Push Notifications", "Automation", "Patagonia"),
    ("Organic Search", "Organic", "Pereda"),
    ("Organic Shopping", "Organic", "Pereda"),
    ("Organic Social", "Organic Social", "Marketing Team"),
    ("Organic Video", "Organic", "Pereda"),
    ("Paid Other", "Others", "Mkt Digital"),
    ("Paid Search", "Google Ads", "Mkt Digital"),
    ("Paid Shopping", "Google Ads", "Mkt Digital"),
    ("Paid Social", "Social Ads", "Mkt Digital"),
    ("Paid Video", "Google Ads", "Mkt Digital"),
    ("Referral", "Referral", "Branding"),
    ("Unassigned", "Others", "Others"),
]


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}")


def normalize_channel(value: object) -> str:
    if value is None:
        return "(not set)"
    text = str(value).strip()
    return text if text else "(not set)"


def coalesce_number(value: object) -> float:
    if value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(number) or math.isinf(number):
        return 0.0
    return number


def load_channel_equivalences() -> pd.DataFrame:
    query = f"""
        SELECT
            session_default_channel_group,
            canal,
            responsable
        FROM {CHANNEL_EQUIVALENCES_TABLE};
    """
    try:
        engine = get_engine()
        df = pd.read_sql(query, engine)
        log("SQL", f"Equivalencias cargadas desde {CHANNEL_EQUIVALENCES_TABLE}: {len(df)} filas")
    except Exception as exc:
        log("WARN", f"No pude leer {CHANNEL_EQUIVALENCES_TABLE}; uso equivalencias embebidas. Detalle: {exc}")
        df = pd.DataFrame(
            FALLBACK_CHANNEL_EQUIVALENCES,
            columns=["session_default_channel_group", "canal", "responsable"],
        )

    df["session_default_channel_group"] = df["session_default_channel_group"].map(normalize_channel)
    df["canal"] = df["canal"].fillna("Others").astype(str).str.strip().replace("", "Others")
    df["responsable"] = df["responsable"].fillna("Others").astype(str).str.strip().replace("", "Others")
    return df.drop_duplicates(subset=["session_default_channel_group"], keep="last").reset_index(drop=True)


def load_monthly_channel_detail() -> pd.DataFrame:
    property_ids = ", ".join(f"'{property_id}'" for property_id in config.PROPERTY_IDS_TO_RUN)
    start_ym = REPORT_START_DATE[:7].replace("-", "")
    end_ym = REPORT_END_DATE[:7].replace("-", "")
    query = text(f"""
        SELECT
            property_id,
            property_name,
            year_month,
            CONVERT(char(7), CONVERT(date, CONCAT(year_month, '01'), 112), 120) AS mes,
            session_default_channel_group,
            sessions,
            ecommerce_purchases AS transacciones,
            purchase_revenue AS ingresos
        FROM {MONTHLY_CHANNELS_TABLE}
        WHERE property_id IN ({property_ids})
          AND year_month BETWEEN :start_ym AND :end_ym;
    """)
    engine = get_engine()
    detail = pd.read_sql(query, engine, params={"start_ym": start_ym, "end_ym": end_ym})
    log("SQL", f"{MONTHLY_CHANNELS_TABLE}: {len(detail)} filas")

    if detail.empty:
        return pd.DataFrame(
            columns=[
                "property_id",
                "property_name",
                "year_month",
                "mes",
                "session_default_channel_group",
                "sessions",
                "transacciones",
                "ingresos",
            ]
        )

    detail["session_default_channel_group"] = detail["session_default_channel_group"].map(normalize_channel)
    detail["sessions"] = detail["sessions"].map(coalesce_number)
    detail["transacciones"] = detail["transacciones"].map(coalesce_number)
    detail["ingresos"] = detail["ingresos"].map(coalesce_number)
    return detail.groupby(
        ["property_id", "property_name", "year_month", "mes", "session_default_channel_group"],
        as_index=False,
        dropna=False,
    )[["sessions", "transacciones", "ingresos"]].sum()


def apply_channel_equivalences(detail: pd.DataFrame, equivalences: pd.DataFrame) -> pd.DataFrame:
    out = detail.merge(equivalences, how="left", on="session_default_channel_group")
    out["canal"] = out["canal"].fillna("Sin mapeo")
    out["responsable"] = out["responsable"].fillna("Sin mapeo")
    return out[
        [
            "mes",
            "year_month",
            "property_name",
            "session_default_channel_group",
            "canal",
            "responsable",
            "sessions",
            "transacciones",
            "ingresos",
        ]
    ].sort_values(["year_month", "property_name", "canal", "session_default_channel_group"], ascending=[False, True, True, True])


def add_business_rates(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["conversion_rate"] = out.apply(
        lambda row: row["transacciones"] / row["sessions"] if row["sessions"] else 0,
        axis=1,
    )
    out["ingreso_por_sesion"] = out.apply(
        lambda row: row["ingresos"] / row["sessions"] if row["sessions"] else 0,
        axis=1,
    )
    out["ticket_promedio"] = out.apply(
        lambda row: row["ingresos"] / row["transacciones"] if row["transacciones"] else 0,
        axis=1,
    )
    return out


def build_summary_by_property(detail: pd.DataFrame) -> pd.DataFrame:
    out = detail.copy()

    summary = out.groupby(["mes", "property_name"], as_index=False)[
        ["sessions", "transacciones", "ingresos"]
    ].sum()
    summary = add_business_rates(summary)

    month_revenue = summary.groupby("mes")["ingresos"].transform("sum")
    summary["share_ingresos_mes"] = summary.apply(
        lambda row: row["ingresos"] / month_revenue.loc[row.name] if month_revenue.loc[row.name] else 0,
        axis=1,
    )
    summary["tipo_fila"] = "Propiedad"

    month_totals = out.groupby("mes", as_index=False)[["sessions", "transacciones", "ingresos"]].sum()
    month_totals.insert(1, "property_name", TOTAL_ROW_LABEL)
    month_totals = add_business_rates(month_totals)
    month_totals["share_ingresos_mes"] = 1.0
    month_totals["tipo_fila"] = "Total"

    columns = [
        "mes",
        "tipo_fila",
        "property_name",
        "sessions",
        "transacciones",
        "ingresos",
        "conversion_rate",
        "ingreso_por_sesion",
        "ticket_promedio",
        "share_ingresos_mes",
    ]
    combined = pd.concat([summary[columns], month_totals[columns]], ignore_index=True)
    combined["orden_tipo"] = combined["tipo_fila"].map({"Total": 0, "Propiedad": 1}).fillna(9)
    combined = combined.sort_values(["mes", "orden_tipo", "ingresos"], ascending=[False, True, False])
    return combined.drop(columns=["orden_tipo"]).reset_index(drop=True)


def build_summary_by_channel(detail: pd.DataFrame) -> pd.DataFrame:
    end_month = REPORT_END_DATE[:7]
    out = detail.copy()
    out = out.loc[out["mes"].eq(end_month)]

    summary = out.groupby(["property_name", "canal", "responsable"], as_index=False)[
        ["sessions", "transacciones", "ingresos"]
    ].sum()
    if summary.empty:
        return pd.DataFrame(
            columns=[
                "mes",
                "property_name",
                "canal",
                "responsable",
                "sessions",
                "transacciones",
                "ingresos",
                "conversion_rate",
                "ingreso_por_sesion",
                "ticket_promedio",
                "share_ingresos_propiedad",
                "share_sesiones_propiedad",
            ]
        )

    summary = add_business_rates(summary)
    summary.insert(0, "mes", end_month)

    property_revenue = summary.groupby("property_name")["ingresos"].transform("sum")
    property_sessions = summary.groupby("property_name")["sessions"].transform("sum")
    summary["share_ingresos_propiedad"] = summary.apply(
        lambda row: row["ingresos"] / property_revenue.loc[row.name] if property_revenue.loc[row.name] else 0,
        axis=1,
    )
    summary["share_sesiones_propiedad"] = summary.apply(
        lambda row: row["sessions"] / property_sessions.loc[row.name] if property_sessions.loc[row.name] else 0,
        axis=1,
    )
    return summary.sort_values(["property_name", "ingresos"], ascending=[True, False])


def build_unmapped_channels(detail: pd.DataFrame) -> pd.DataFrame:
    unmapped = detail.loc[detail["canal"].eq("Sin mapeo")]
    if unmapped.empty:
        return pd.DataFrame(columns=["session_default_channel_group", "sessions", "transacciones", "ingresos"])
    return unmapped.groupby("session_default_channel_group", as_index=False)[
        ["sessions", "transacciones", "ingresos"]
    ].sum().sort_values("sessions", ascending=False)


def load_sql_sheet_view(view_name: str) -> pd.DataFrame:
    query = f"SELECT * FROM {view_name} ORDER BY year_month DESC, Tienda_ecom"
    engine = get_engine()
    df = pd.read_sql(query, engine)
    log("SQL", f"{view_name}: {len(df)} filas")
    return df


def build_metadata() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ("generated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ("start_date", REPORT_START_DATE),
            ("end_date", REPORT_END_DATE),
            ("properties", ", ".join(config.PROPERTY_INFO.get(x, x) for x in config.PROPERTY_IDS_TO_RUN)),
            ("spreadsheet", SPREADSHEET_ID or SPREADSHEET_NAME),
            ("monthly_channels_table", MONTHLY_CHANNELS_TABLE),
            ("channel_equivalences_table", CHANNEL_EQUIVALENCES_TABLE),
            ("rmh_ga4_channel_view", RMH_GA4_CHANNEL_VIEW),
            ("rmh_ga4_owner_view", RMH_GA4_OWNER_VIEW),
        ],
        columns=["campo", "valor"],
    )


def clean_for_sheets(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d %H:%M:%S")
        elif out[col].map(lambda value: isinstance(value, (date, datetime))).any():
            out[col] = out[col].map(
                lambda value: value.strftime("%Y-%m-%d") if isinstance(value, date) else value
            )
    out = out.replace([float("inf"), float("-inf")], "")
    out = out.fillna("")
    return out


def authorize_sheets() -> gspread.Client:
    if not config.CREDENTIALS_FILE.exists():
        raise FileNotFoundError(f"No encontre credenciales en: {config.CREDENTIALS_FILE}")
    credentials = service_account.Credentials.from_service_account_file(
        config.CREDENTIALS_FILE,
        scopes=SHEETS_SCOPES,
    )
    return gspread.authorize(credentials)


def open_spreadsheet(client: gspread.Client) -> gspread.Spreadsheet:
    if SPREADSHEET_ID:
        return client.open_by_key(SPREADSHEET_ID)
    try:
        return client.open(SPREADSHEET_NAME)
    except SpreadsheetNotFound as exc:
        visible_files = client.list_spreadsheet_files()
        visible_names = ", ".join(file.get("name", "(sin nombre)") for file in visible_files) or "ninguno"
        raise SpreadsheetNotFound(
            f"No encontre el Google Sheet '{SPREADSHEET_NAME}'. "
            "Verifica el nombre, comparte el archivo con el service account de credenciales.json "
            "o define GA4_ECOMMERCE_SPREADSHEET_ID en .env. "
            f"Sheets visibles para esta cuenta: {visible_names}"
        ) from exc


def get_or_create_worksheet(
    spreadsheet: gspread.Spreadsheet,
    title: str,
    rows: int,
    cols: int,
) -> gspread.Worksheet:
    try:
        worksheet = spreadsheet.worksheet(title)
    except WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=title, rows=max(rows, 100), cols=max(cols, 26))
    worksheet.resize(rows=max(rows, 2), cols=max(cols, 1))
    return worksheet


def chunks(values: List[List[object]], chunk_size: int) -> Iterable[List[List[object]]]:
    for idx in range(0, len(values), chunk_size):
        yield values[idx : idx + chunk_size]


def column_letter(col_index: int) -> str:
    return rowcol_to_a1(1, col_index).rstrip("1")


def run_sheets_request(description: str, func):
    for attempt in range(SHEETS_MAX_RETRIES):
        try:
            return func()
        except APIError as exc:
            if "429" not in str(exc) or attempt == SHEETS_MAX_RETRIES - 1:
                raise
            wait_seconds = SHEETS_RETRY_BASE_SECONDS * (attempt + 1)
            log("WARN", f"Sheets quota en {description}; reintento en {wait_seconds}s")
            time.sleep(wait_seconds)


def apply_sheet_formatting(worksheet: gspread.Worksheet, df: pd.DataFrame) -> None:
    if df.empty and len(df.columns) == 0:
        return

    last_col = column_letter(len(df.columns))
    last_row = max(len(df) + 1, 1)

    if last_row > 1:
        run_sheets_request("freeze", lambda: worksheet.freeze(rows=1))

    formats = [
        {
            "range": f"A1:{last_col}1",
            "format": {
                "backgroundColor": {"red": 0.08, "green": 0.18, "blue": 0.31},
                "horizontalAlignment": "CENTER",
                "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
            },
        },
    ]

    for idx, column in enumerate(df.columns, start=1):
        col = column_letter(idx)
        range_name = f"{col}2:{col}{last_row}"
        number_format = None
        if column in DATE_COLUMNS:
            number_format = {"type": "DATE", "pattern": "yyyy-mm-dd"}
        elif column in INTEGER_COLUMNS:
            number_format = {"type": "NUMBER", "pattern": "#,##0"}
        elif column in AMOUNT_COLUMNS:
            number_format = {"type": "NUMBER", "pattern": "#,##0.00"}
        elif column in PERCENT_COLUMNS:
            number_format = {"type": "PERCENT", "pattern": "0.00%"}

        if number_format:
            formats.append({"range": range_name, "format": {"numberFormat": number_format}})

    run_sheets_request("formatos", lambda: worksheet.batch_format(formats))

    if "property_name" in df.columns:
        total_rows = df.index[df["property_name"].eq(TOTAL_ROW_LABEL)].tolist()
        if total_rows:
            total_formats = [
                {
                    "range": f"A{row_idx + 2}:{last_col}{row_idx + 2}",
                    "format": {
                        "backgroundColor": {"red": 0.95, "green": 0.88, "blue": 0.68},
                        "textFormat": {"bold": True},
                    },
                }
                for row_idx in total_rows
            ]
            run_sheets_request("filas total", lambda: worksheet.batch_format(total_formats))

    if last_row > 1:
        run_sheets_request("filtro", lambda: worksheet.set_basic_filter(f"A1:{last_col}{last_row}"))

    run_sheets_request("auto resize", lambda: worksheet.columns_auto_resize(0, len(df.columns)))


def upload_dataframe(spreadsheet: gspread.Spreadsheet, title: str, df: pd.DataFrame) -> None:
    clean = clean_for_sheets(df)
    values = [clean.columns.tolist()] + clean.values.tolist()
    rows = max(len(values), 1)
    cols = max(len(clean.columns), 1)
    worksheet = get_or_create_worksheet(spreadsheet, title, rows, cols)
    worksheet.clear()

    current_row = 1
    for chunk in chunks(values, WRITE_CHUNK_ROWS):
        range_name = rowcol_to_a1(current_row, 1)
        run_sheets_request(
            f"carga {title}",
            lambda range_name=range_name, chunk=chunk: worksheet.update(
                values=chunk,
                range_name=range_name,
                value_input_option="RAW",
            ),
        )
        current_row += len(chunk)
        time.sleep(0.2)

    apply_sheet_formatting(worksheet, clean)
    time.sleep(1)
    log("SHEETS", f"{title}: {len(clean)} filas")


def delete_obsolete_tabs(spreadsheet: gspread.Spreadsheet) -> None:
    for title in OBSOLETE_TABS:
        try:
            worksheet = spreadsheet.worksheet(title)
        except WorksheetNotFound:
            continue
        run_sheets_request(f"eliminar {title}", lambda worksheet=worksheet: spreadsheet.del_worksheet(worksheet))
        log("SHEETS", f"{title}: pestaña obsoleta eliminada")


def main() -> None:
    config.START_DATE = REPORT_START_DATE
    config.END_DATE = REPORT_END_DATE

    log("START", "GA4 Ecommerce -> Google Sheets")
    log("WINDOW", f"{REPORT_START_DATE} -> {REPORT_END_DATE}")
    log("PROPERTIES", ", ".join(config.PROPERTY_IDS_TO_RUN))

    equivalences = load_channel_equivalences()
    detail_raw = load_monthly_channel_detail()
    detail = apply_channel_equivalences(detail_raw, equivalences)

    sheets = {
        "Detalle_Mensual_Canal": detail,
        "Resumen_Propiedad": build_summary_by_property(detail),
        "Resumen_Canal": build_summary_by_channel(detail),
        "RMH_GA4_Mensual_Canal": load_sql_sheet_view(RMH_GA4_CHANNEL_VIEW),
        "RMH_GA4_Mensual_Responsable": load_sql_sheet_view(RMH_GA4_OWNER_VIEW),
        "Canales_Sin_Mapeo": build_unmapped_channels(detail),
        "Equivalencias_Canales": equivalences,
        "Metadata": build_metadata(),
    }

    client = authorize_sheets()
    spreadsheet = open_spreadsheet(client)
    delete_obsolete_tabs(spreadsheet)
    for title, df in sheets.items():
        upload_dataframe(spreadsheet, title, df)

    log("DONE", f"Actualizado Google Sheet: {SPREADSHEET_ID or SPREADSHEET_NAME}")


if __name__ == "__main__":
    main()
