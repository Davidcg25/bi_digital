"""
GA4 Multi-Property Extractor -> SQL Server
DB: Digital_Impact_Reportes

Usage:
    python ga4_extractor_to_sql.py
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import pandas as pd
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Metric, Dimension
from google.oauth2 import service_account
from google.api_core.exceptions import (
    PermissionDenied,
    ResourceExhausted,
    ServiceUnavailable,
    InternalServerError,
    BadRequest,
)

import ga4_config as config
from ga4_db import (
    get_engine,
    test_connection,
    upsert_properties,
    start_run,
    finish_run,
    register_report_load,
    delete_existing,
    load_dataframe,
)


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}")


def to_snake(name: str) -> str:
    explicit = {
        "yearMonth": "year_month",
        "eventName": "event_name",
        "sessionDefaultChannelGroup": "session_default_channel_group",
        "deviceCategory": "device_category",
        "totalUsers": "total_users",
        "activeUsers": "active_users",
        "purchaseRevenue": "purchase_revenue",
        "ecommercePurchases": "ecommerce_purchases",
        "averagePurchaseRevenue": "average_purchase_revenue",
        "itemsPurchased": "items_purchased",
        "engagementRate": "engagement_rate",
        "screenPageViewsPerSession": "screen_page_views_per_session",
        "cartToViewRate": "cart_to_view_rate",
        "purchaseToViewRate": "purchase_to_view_rate",
        "sessionKeyEventRate:purchase": "session_purchase_key_event_rate",
        "eventCount": "event_count",
        "itemsViewed": "items_viewed",
        "itemsAddedToCart": "items_added_to_cart",
        "itemRevenue": "item_revenue",
        "itemName": "item_name",
        "itemId": "item_id",
        "itemCategory": "item_category",
        "pagePath": "page_path",
        "pageTitle": "page_title",
        "screenPageViews": "screen_page_views",
        "landingPage": "landing_page",
    }
    if name in explicit:
        return explicit[name]
    s = name.replace(":", "_")
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    return s.strip("_")



NUMERIC_SUM_COLS = [
    "sessions", "total_users", "active_users", "purchase_revenue",
    "ecommerce_purchases", "average_purchase_revenue", "items_purchased",
    "engagement_rate", "screen_page_views_per_session", "cart_to_view_rate",
    "purchase_to_view_rate", "session_purchase_key_event_rate", "event_count",
    "items_viewed", "items_added_to_cart", "item_revenue", "screen_page_views",
]


def stable_sha256(value: str) -> str:
    """Hash estable para usar textos largos como llave SQL sin romper límite de 900 bytes."""
    value = "(not set)" if value is None else str(value)
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()


def pick_best_text(series: pd.Series) -> str:
    """Devuelve un texto representativo, evitando nulos/vacíos cuando sea posible."""
    cleaned = series.dropna().astype(str)
    cleaned = cleaned[cleaned.str.strip() != ""]
    if cleaned.empty:
        return "(not set)"
    return cleaned.iloc[0]


def aggregate_for_sql(df: pd.DataFrame, report_name: str) -> pd.DataFrame:
    """
    GA4 puede devolver varias filas con la misma llave SQL cuando una dimensión auxiliar cambia
    o cuando existen valores '(not set)'. Antes de insertar, consolidamos por la granularidad
    real de la tabla para evitar violaciones de PK y conservar la suma de métricas.
    """
    if df.empty:
        return df

    # Tablas/rankings donde vimos duplicados por dimensiones auxiliares.
    group_keys_by_report = {
        "items_12m": ["property_id", "start_date", "end_date", "item_id"],
        "categories_12m": ["property_id", "start_date", "end_date", "item_category"],
        "pages_12m": ["property_id", "start_date", "end_date", "page_path_hash"],
        "landing_pages_12m": ["property_id", "start_date", "end_date", "landing_page_hash"],
        "landing_pages_monthly": ["property_id", "year_month", "landing_page_hash"],
    }

    group_keys = group_keys_by_report.get(report_name)
    if not group_keys:
        return df.drop_duplicates().reset_index(drop=True)

    numeric_cols = [c for c in NUMERIC_SUM_COLS if c in df.columns]
    text_cols = [c for c in df.columns if c not in set(group_keys + numeric_cols)]

    agg = {c: "sum" for c in numeric_cols}
    for c in text_cols:
        if c in ("extracted_at",):
            agg[c] = "max"
        elif c == "run_id":
            agg[c] = "max"
        else:
            agg[c] = pick_best_text

    before = len(df)
    out = df.groupby(group_keys, as_index=False, dropna=False).agg(agg)
    after = len(out)

    if after < before:
        log("DEDUP", f"{report_name} | rows={before} -> {after} | consolidadas={before-after}")

    return out.reset_index(drop=True)

def build_client() -> BetaAnalyticsDataClient:
    if not config.CREDENTIALS_FILE.exists():
        raise FileNotFoundError(f"No encontré credenciales GA4 en: {config.CREDENTIALS_FILE}")
    credentials = service_account.Credentials.from_service_account_file(
        config.CREDENTIALS_FILE,
        scopes=config.SCOPES,
    )
    return BetaAnalyticsDataClient(credentials=credentials)


def execute_report(
    client: BetaAnalyticsDataClient,
    property_id: str,
    report_name: str,
    dimensions: List[str],
    metrics: List[str],
    start_date: str | None = None,
    end_date: str | None = None,
    order_by_dim: str | None = None,
) -> pd.DataFrame:
    start_date = start_date or config.START_DATE
    end_date = end_date or config.END_DATE
    log("REPORT", f"{property_id} | {report_name}")
    all_rows: List[Dict[str, str]] = []
    offset = 0

    for attempt in range(config.MAX_RETRIES):
        try:
            while True:
                request = RunReportRequest(
                    property=f"properties/{property_id}",
                    dimensions=[Dimension(name=d) for d in dimensions],
                    metrics=[Metric(name=m) for m in metrics],
                    date_ranges=[DateRange(start_date=start_date, end_date=end_date)],
                    limit=config.PAGE_SIZE,
                    offset=offset,
                )

                response = client.run_report(request)
                rows = response.rows
                if not rows:
                    break

                for row in rows:
                    record = {}
                    for idx, dim in enumerate(dimensions):
                        record[dim] = row.dimension_values[idx].value
                    for idx, met in enumerate(metrics):
                        record[met] = row.metric_values[idx].value
                    all_rows.append(record)

                log("OK", f"{property_id} | {report_name} | offset={offset} | fetched={len(rows)}")
                if len(rows) < config.PAGE_SIZE:
                    break
                offset += config.PAGE_SIZE

            df = pd.DataFrame(all_rows)
            if order_by_dim and order_by_dim in df.columns and not df.empty:
                df = df.sort_values(by=order_by_dim).reset_index(drop=True)
            return df

        except PermissionDenied as e:
            log("CRITICAL", f"{property_id} | PERMISSION_DENIED: {e}")
            raise
        except BadRequest as e:
            log("CRITICAL", f"{property_id} | BAD_REQUEST: {e}")
            raise
        except (ResourceExhausted, ServiceUnavailable, InternalServerError) as e:
            wait_time = (attempt + 1) * config.RETRY_BASE_SECONDS
            log("WARN", f"{property_id} | retry {attempt+1}/{config.MAX_RETRIES} | wait={wait_time}s | {e}")
            time.sleep(wait_time)

    raise RuntimeError(f"No se pudo completar reporte {report_name} para property {property_id}")


def normalize_dataframe(df: pd.DataFrame, report: Dict, property_id: str, property_name: str, run_id: int) -> pd.DataFrame:
    if df.empty:
        # Crear columnas mínimas para que el flujo no reviente.
        return df

    df = df.rename(columns={c: to_snake(c) for c in df.columns})

    # Convertir métricas a numérico.
    for metric in report["metrics"]:
        col = to_snake(metric)
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Metadata común.
    df.insert(0, "property_name", property_name)
    df.insert(0, "property_id", property_id)
    df["run_id"] = run_id
    df["extracted_at"] = datetime.now()

    if report["grain"] == "monthly":
        if "year_month" not in df.columns:
            raise ValueError(f"Reporte mensual sin year_month: {report['name']}")
        df["year_month"] = df["year_month"].astype(str).str.slice(0, 6)
    elif report["grain"] == "daily":
        if "date" not in df.columns:
            raise ValueError(f"Reporte diario sin date: {report['name']}")
        df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
    else:
        df["start_date"] = config.START_DATE
        df["end_date"] = config.END_DATE

    # Evita nulos en campos de PK textual. GA4 a veces devuelve (not set), vacío o None.
    text_pk_cols = [
        "event_name", "session_default_channel_group", "device_category", "item_id", "item_category",
        "page_path", "landing_page",
    ]
    for col in text_pk_cols:
        if col in df.columns:
            df[col] = df[col].fillna("(not set)").astype(str)
            df.loc[df[col].str.strip() == "", col] = "(not set)"

    # item_id puede venir vacío; usa item_name como fallback parcial para no romper PK.
    if "item_id" in df.columns:
        fallback = df.get("item_name", pd.Series(["(not set)"] * len(df))).fillna("(not set)").astype(str)
        df.loc[df["item_id"].astype(str).str.strip() == "", "item_id"] = fallback

    # Llaves hash para textos largos. SQL Server no permite índices clustered > 900 bytes.
    if "page_path" in df.columns:
        df["page_path_hash"] = df["page_path"].map(stable_sha256)
    if "landing_page" in df.columns:
        df["landing_page_hash"] = df["landing_page"].map(stable_sha256)

    df = aggregate_for_sql(df, report["name"])

    # Orden estable: primero metadata, luego dimensiones/metricas.
    return df


def report_window(report: Dict) -> tuple[str, str]:
    """Ventana efectiva por grano: monthly alineada/cerrada, daily corta, range rodante."""
    if report["grain"] == "monthly":
        return config.MONTHLY_START_DATE, config.MONTHLY_END_DATE
    if report["grain"] == "daily":
        return config.DAILY_START_DATE, config.END_DATE
    return config.START_DATE, config.END_DATE


def save_csv_backup(df: pd.DataFrame, property_name: str, report_name: str) -> None:
    if not config.SAVE_CSV_BACKUP or df.empty:
        return
    safe_brand = re.sub(r"[^a-zA-Z0-9_]+", "_", property_name.strip().lower()).strip("_")
    out_dir = config.OUTPUT_DIR / safe_brand
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{report_name}.csv"
    df.to_csv(path, index=False, encoding="utf-8-sig")


def main() -> None:
    log("START", "GA4 Multi-Property -> SQL Server")
    log("WINDOW", (
        f"range: {config.START_DATE} -> {config.END_DATE}"
        f" | daily: {config.DAILY_START_DATE} -> {config.END_DATE}"
        f" | monthly: {config.MONTHLY_START_DATE} -> {config.MONTHLY_END_DATE}"
    ))
    if config.REPORT_NAMES_TO_RUN:
        log("REPORTS", f"Filtrados por REPORT_NAMES_TO_RUN: {', '.join(r['name'] for r in config.REPORTS)}")

    reports_to_run = [r for r in config.REPORTS if r["grain"] != "monthly" or config.RUN_MONTHLY]
    if not config.RUN_MONTHLY:
        log("MONTHLY", f"Mensuales omitidos: corren solo al cierre (días 1-{config.MONTHLY_CLOSE_DAY_LIMIT} del mes) o con RUN_MONTHLY=true.")
    if not reports_to_run:
        log("DONE", "No hay reportes que correr con la configuración actual.")
        return
    log("PROPERTIES", ", ".join(config.PROPERTY_IDS_TO_RUN))

    engine = get_engine()
    test_connection(engine)
    upsert_properties(engine)

    client = build_client()
    # La fila del run registra la ventana global realmente cubierta por los
    # reportes que corren (los granos usan ventanas distintas).
    run_start = min(report_window(r)[0] for r in reports_to_run)
    run_end = max(report_window(r)[1] for r in reports_to_run)
    run_id = start_run(engine, run_start, run_end, len(config.PROPERTY_IDS_TO_RUN))

    metadata = {
        "run_id": run_id,
        "start_date": run_start,
        "end_date": run_end,
        "generated_at": datetime.now().isoformat(),
        "properties": [],
    }

    successful_properties = 0
    failed_properties = 0
    global_errors = []

    for property_id in config.PROPERTY_IDS_TO_RUN:
        property_name = config.PROPERTY_INFO.get(property_id, property_id)
        property_status = {"property_id": property_id, "property_name": property_name, "reports": [], "status": "ok"}
        property_failed = False

        log("PROPERTY", f"{property_name} | {property_id}")

        for report in reports_to_run:
            try:
                report_start, report_end = report_window(report)
                raw_df = execute_report(
                    client=client,
                    property_id=property_id,
                    report_name=report["name"],
                    dimensions=report["dimensions"],
                    metrics=report["metrics"],
                    start_date=report_start,
                    end_date=report_end,
                    order_by_dim=report.get("order_by_dim"),
                )
                df = normalize_dataframe(raw_df, report, property_id, property_name, run_id)
                save_csv_backup(df, property_name, report["name"])

                rows_loaded = 0
                deleted = 0
                if config.LOAD_TO_SQL and not df.empty:
                    deleted = delete_existing(
                        engine=engine,
                        table=report["table"],
                        grain=report["grain"],
                        property_id=property_id,
                        start_date=report_start,
                        end_date=report_end,
                        start_ym=config.START_YM,
                        end_ym=config.MONTHLY_END_YM,
                    )
                    rows_loaded = load_dataframe(engine, df, report["table"])

                register_report_load(
                    engine, run_id, property_id, property_name, report["name"], report["table"], rows_loaded, "ok"
                )
                log("LOAD", f"{property_name} | {report['table']} | deleted={deleted} | inserted={rows_loaded}")

                property_status["reports"].append({
                    "report": report["name"],
                    "table": report["table"],
                    "rows_raw": len(raw_df),
                    "rows_loaded": rows_loaded,
                    "status": "ok",
                })

            except Exception as e:
                property_failed = True
                error_msg = str(e)
                log("ERROR", f"{property_name} | {report['name']} | {error_msg}")
                register_report_load(
                    engine, run_id, property_id, property_name, report["name"], report["table"], 0, "error", error_msg[:3900]
                )
                property_status["reports"].append({
                    "report": report["name"],
                    "table": report["table"],
                    "rows_loaded": 0,
                    "status": "error",
                    "error": error_msg,
                })
                # No rompemos todo; seguimos con el siguiente reporte/property.

        if property_failed:
            failed_properties += 1
            property_status["status"] = "partial_error"
            global_errors.append(f"{property_name}: revisar ga4_etl_report_loads")
        else:
            successful_properties += 1

        metadata["properties"].append(property_status)

    final_status = "success" if failed_properties == 0 else "partial_error"
    finish_run(
        engine,
        run_id,
        final_status,
        successful_properties,
        failed_properties,
        "; ".join(global_errors) if global_errors else None,
    )

    metadata_path = config.OUTPUT_DIR / f"execution_metadata_run_{run_id}.json"
    with open(metadata_path, "w", encoding="utf-8") as f:
        json.dump(metadata, f, indent=4, ensure_ascii=False, default=str)

    log("DONE", f"Finalizado. run_id={run_id} | status={final_status} | metadata={metadata_path}")


if __name__ == "__main__":
    main()
