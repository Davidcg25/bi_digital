from __future__ import annotations

import datetime as dt
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from tqdm import tqdm


BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
URLS_FILE = BASE_DIR / "DI - homepages clientes.xlsx"
TABLE_NAME = "Performance_Web"
API_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
DEVICES = ("desktop", "mobile")

load_dotenv(ENV_FILE)


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}", flush=True)


def get_required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Falta configurar {name} en {ENV_FILE}")
    return value


def build_engine():
    server = os.getenv("SQL_SERVER", "localhost")
    database = os.getenv("SQL_DATABASE", "Digital_Impact_Reportes")
    driver = os.getenv("SQL_DRIVER", "ODBC Driver 17 for SQL Server").replace(" ", "+")
    trusted = os.getenv("SQL_TRUSTED_CONNECTION", "yes").lower() in ("1", "true", "yes", "y")

    if trusted:
        url = f"mssql+pyodbc://@{server}/{database}?trusted_connection=yes&driver={driver}"
    else:
        username = get_required_env("SQL_USERNAME")
        password = get_required_env("SQL_PASSWORD")
        url = f"mssql+pyodbc://{username}:{password}@{server}/{database}?driver={driver}"

    return create_engine(url, fast_executemany=True)


def extract_display_number(audit: dict[str, Any]) -> float | None:
    if not audit:
        return None

    display_value = str(audit.get("displayValue") or "")
    match = re.search(r"[-+]?\d+(?:[.,]\d+)?", display_value)
    if match:
        return float(match.group(0).replace(",", "."))

    numeric_value = audit.get("numericValue")
    if numeric_value is None:
        return None

    try:
        return float(numeric_value)
    except (TypeError, ValueError):
        return None


def get_metric(audits: dict[str, Any], metric_name: str) -> float | None:
    return extract_display_number(audits.get(metric_name, {}))


# Campos extraídos del JSON de PSI: 7 timings de lab + 4 scores de categoría
# (los 0-100 del reporte Lighthouse) + Core Web Vitals de CAMPO (CrUX, usuarios
# reales, en loadingExperience). Antes solo se sacaban los 7 timings de performance.
_METRIC_FIELDS = (
    "fcp", "lcp", "tbt", "cls", "speed_index", "tti", "max_fid",
    "score_performance", "score_seo", "score_accessibility", "score_best_practices",
    "crux_overall", "crux_lcp_ms", "crux_inp_ms", "crux_fcp_ms", "crux_cls",
)


def _empty_metrics() -> dict[str, Any]:
    return {k: None for k in _METRIC_FIELDS}


def _parse_psi(data: dict[str, Any]) -> dict[str, Any]:
    lh = data["lighthouseResult"]
    audits = lh.get("audits", {})
    cats = lh.get("categories", {})

    def score(cat: str) -> int | None:
        s = cats.get(cat, {}).get("score")
        return round(s * 100) if isinstance(s, (int, float)) else None

    # CrUX de campo: loadingExperience = la URL; si no tiene muestras, cae a origin.
    le = data.get("loadingExperience") or {}
    if not le.get("metrics"):
        le = data.get("originLoadingExperience") or le
    lem = le.get("metrics", {}) or {}

    def crux(key: str):
        return lem.get(key, {}).get("percentile")

    cls_p = crux("CUMULATIVE_LAYOUT_SHIFT_SCORE")
    return {
        "fcp": get_metric(audits, "first-contentful-paint"),
        "lcp": get_metric(audits, "largest-contentful-paint"),
        "tbt": get_metric(audits, "total-blocking-time"),
        "cls": get_metric(audits, "cumulative-layout-shift"),
        "speed_index": get_metric(audits, "speed-index"),
        "tti": get_metric(audits, "interactive"),
        "max_fid": get_metric(audits, "max-potential-fid"),
        "score_performance": score("performance"),
        "score_seo": score("seo"),
        "score_accessibility": score("accessibility"),
        "score_best_practices": score("best-practices"),
        "crux_overall": le.get("overall_category"),
        "crux_lcp_ms": crux("LARGEST_CONTENTFUL_PAINT_MS"),
        "crux_inp_ms": crux("INTERACTION_TO_NEXT_PAINT"),
        "crux_fcp_ms": crux("FIRST_CONTENTFUL_PAINT_MS"),
        "crux_cls": (cls_p / 100) if cls_p is not None else None,
    }


def fetch_pagespeed_metrics(api_key: str, url: str, device: str, max_retries: int = 3) -> tuple[dict[str, Any], str | None]:
    # `category` repetido => PSI devuelve los 4 scores; sin esto solo trae performance.
    params = [("url", url), ("key", api_key), ("strategy", device)]
    params += [("category", c) for c in ("performance", "seo", "accessibility", "best-practices")]

    for attempt in range(1, max_retries + 1):
        try:
            with httpx.Client(timeout=180) as client:
                response = client.get(API_URL, params=params)
                response.raise_for_status()
                data = response.json()

            if "lighthouseResult" not in data:
                error_msg = data.get("error", {}).get("message", "Respuesta sin lighthouseResult")
                return _empty_metrics(), error_msg

            return _parse_psi(data), None

        except (httpx.HTTPError, ValueError) as exc:
            if attempt == max_retries:
                return _empty_metrics(), str(exc)
            wait_seconds = attempt * 10
            log("WARN", f"{url} ({device}) intento {attempt}/{max_retries} fallo; reintento en {wait_seconds}s: {exc}")
            time.sleep(wait_seconds)

    return _empty_metrics(), "Error inesperado"


def read_urls() -> pd.DataFrame:
    if not URLS_FILE.exists():
        raise FileNotFoundError(f"No existe el archivo de URLs: {URLS_FILE}")

    df_urls = pd.read_excel(URLS_FILE)
    required_columns = {"URL", "Tienda"}
    missing = required_columns.difference(df_urls.columns)
    if missing:
        raise ValueError(f"Faltan columnas en {URLS_FILE.name}: {', '.join(sorted(missing))}")

    df_urls = df_urls.dropna(subset=["URL", "Tienda"]).copy()
    df_urls["URL"] = df_urls["URL"].astype(str).str.strip()
    df_urls["Tienda"] = df_urls["Tienda"].astype(str).str.strip()
    df_urls = df_urls[(df_urls["URL"] != "") & (df_urls["Tienda"] != "")]
    df_urls = df_urls.drop_duplicates(subset=["URL", "Tienda"]).reset_index(drop=True)

    if df_urls.empty:
        raise ValueError(f"No hay URLs validas en {URLS_FILE.name}")

    return df_urls


def build_results(api_key: str, df_urls: pd.DataFrame) -> pd.DataFrame:
    today = dt.date.today()
    results: list[dict[str, Any]] = []
    failures = 0

    log("START", f"Evaluando {len(df_urls)} sitios en {', '.join(DEVICES)}")

    with ThreadPoolExecutor(max_workers=int(os.getenv("SEO_MAX_WORKERS", "4"))) as executor:
        future_to_context = {
            executor.submit(fetch_pagespeed_metrics, api_key, row["URL"], device): (row["Tienda"], row["URL"], device)
            for _, row in df_urls.iterrows()
            for device in DEVICES
        }

        for future in tqdm(as_completed(future_to_context), total=len(future_to_context), desc="Procesando URLs"):
            tienda, url, device = future_to_context[future]
            metrics, error = future.result()
            if error:
                failures += 1
                log("ERROR", f"{tienda} | {url} | {device} | {error}")

            results.append({
                "fecha": today,
                "tienda": tienda,
                "sitio_web": url,
                "device": device,
                **metrics,
            })

    df_results = pd.DataFrame(results)
    expected_rows = len(df_urls) * len(DEVICES)
    if len(df_results) != expected_rows:
        raise RuntimeError(f"Resultados incompletos: {len(df_results)} de {expected_rows}")

    if failures == expected_rows:
        raise RuntimeError("Todas las consultas a PageSpeed fallaron; no se cargan datos vacios")

    log("SUMMARY", f"Filas={len(df_results)} | fallos={failures}")
    return df_results


def load_to_sql(df_results: pd.DataFrame) -> None:
    engine = build_engine()
    fecha = df_results["fecha"].iloc[0]

    try:
        with engine.begin() as conn:
            result = conn.execute(text(f"""
                IF OBJECT_ID('dbo.{TABLE_NAME}', 'U') IS NOT NULL
                    DELETE FROM dbo.{TABLE_NAME} WHERE fecha = :fecha;
            """), {"fecha": fecha})
            deleted = result.rowcount if result.rowcount is not None else 0
            log("SQL", f"Filas previas eliminadas para {fecha}: {deleted}")

        df_results.to_sql(TABLE_NAME, con=engine, if_exists="append", index=False)
        log("SQL", f"Datos cargados correctamente en {TABLE_NAME}: {len(df_results)} filas")
    except SQLAlchemyError as exc:
        raise RuntimeError(f"Error SQL cargando {TABLE_NAME}: {exc}") from exc


def ejecutar_reporte() -> None:
    api_key = get_required_env("API_KEY")
    df_urls = read_urls()
    df_results = build_results(api_key, df_urls)
    load_to_sql(df_results)


if __name__ == "__main__":
    try:
        ejecutar_reporte()
    except Exception as exc:
        log("CRITICAL", str(exc))
        sys.exit(1)
