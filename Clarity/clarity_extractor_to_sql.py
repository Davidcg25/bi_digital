from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from tqdm import tqdm
from sqlalchemy import text

import clarity_config as config
from clarity_db import (
    delete_existing,
    finish_run,
    get_engine,
    load_dataframe,
    register_report_load,
    start_run,
    test_connection,
    upsert_projects,
)


JWT_PATTERN = re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+")


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}", flush=True)


def stable_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def safe_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]+", "_", value.strip().lower()).strip("_") or "unknown"


def env_key(value: str) -> str:
    key = re.sub(r"[^A-Za-z0-9]+", "_", value.strip().upper()).strip("_")
    if not key:
        raise ValueError("Clave de proyecto Clarity vacia")
    return key


def read_sql_batches(path: Path) -> list[str]:
    content = path.read_text(encoding="utf-8")
    batches: list[str] = []
    current: list[str] = []
    for line in content.splitlines():
        if line.strip().upper() == "GO":
            batch = "\n".join(current).strip()
            if batch:
                batches.append(batch)
            current = []
        else:
            current.append(line)
    batch = "\n".join(current).strip()
    if batch:
        batches.append(batch)
    return batches


def ensure_schema(engine) -> None:
    sql_path = config.BASE_DIR / "00_create_clarity_tables.sql"
    if not sql_path.exists():
        raise FileNotFoundError(f"No existe {sql_path}")
    with engine.begin() as conn:
        for batch in read_sql_batches(sql_path):
            conn.execute(text(batch))
    log("DB", "Schema Clarity validado/creado")


def extract_project_name(raw_value: str) -> str:
    value = str(raw_value or "").strip()
    if " - " in value:
        return value.split(" - ", 1)[0].strip()
    token_match = JWT_PATTERN.search(value)
    if token_match:
        return value[: token_match.start()].strip(" -") or "unknown"
    return value or "unknown"


def extract_token(row: pd.Series) -> str:
    key = str(row.get("key", "") or "").strip()
    if key:
        return key
    for value in row.astype(str).tolist():
        match = JWT_PATTERN.search(value)
        if match:
            return match.group(0)
    return ""


def read_projects_from_env() -> list[dict[str, str]]:
    projects: list[dict[str, str]] = []
    for raw_key in config.PROJECT_KEYS:
        key = env_key(raw_key)
        project_name = os.getenv(f"CLARITY_PROJECT_{key}_NAME", "").strip()
        token_name = os.getenv(f"CLARITY_PROJECT_{key}_TOKEN_NAME", "").strip()
        token = os.getenv(f"CLARITY_PROJECT_{key}_TOKEN", "").strip()
        if not project_name or not token:
            log("WARN", f"Proyecto Clarity omitido desde .env: key={key} name={'set' if project_name else 'missing'} token={'set' if token else 'missing'}")
            continue
        projects.append({"project_name": project_name, "token_name": token_name, "token": token})
    return projects


def read_projects_from_excel() -> list[dict[str, str]]:
    if not config.CREDENTIALS_FILE.exists():
        raise FileNotFoundError(f"No existe el Excel de credenciales: {config.CREDENTIALS_FILE}")

    df = pd.read_excel(config.CREDENTIALS_FILE, dtype=str).fillna("")
    required = {"Instancia", "token_name"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Faltan columnas en credenciales.xlsx: {', '.join(sorted(missing))}")

    projects: list[dict[str, str]] = []
    for _, row in df.iterrows():
        project_name = extract_project_name(row.get("Instancia", ""))
        token = extract_token(row)
        token_name = str(row.get("token_name", "") or "").strip()
        if not project_name or not token:
            log("WARN", f"Fila de credenciales omitida: project={project_name!r} token={'set' if token else 'missing'}")
            continue
        projects.append({"project_name": project_name, "token_name": token_name, "token": token})

    return projects


def read_projects() -> list[dict[str, str]]:
    projects = read_projects_from_env()
    if projects:
        log("CONFIG", f"Credenciales Clarity leidas desde {config.ROOT_ENV_FILE}")
    else:
        log("CONFIG", f"CLARITY_PROJECT_KEYS no configurado; usando fallback Excel {config.CREDENTIALS_FILE}")
        projects = read_projects_from_excel()

    if config.PROJECTS_TO_RUN:
        wanted = {x.lower() for x in config.PROJECTS_TO_RUN}
        projects = [p for p in projects if p["project_name"].lower() in wanted]

    seen: set[str] = set()
    unique_projects: list[dict[str, str]] = []
    for project in projects:
        key = project["project_name"].lower()
        if key in seen:
            log("WARN", f"Proyecto duplicado omitido: {project['project_name']}")
            continue
        seen.add(key)
        unique_projects.append(project)

    if not unique_projects:
        raise ValueError("No hay proyectos Clarity validos para ejecutar")
    return unique_projects


def request_clarity(project: dict[str, str], report: dict[str, Any], num_of_days: int) -> list[dict[str, Any]]:
    params = {"numOfDays": num_of_days}
    dimensions = report["dimensions"]
    for idx, dimension in enumerate(dimensions[:3], start=1):
        params[f"dimension{idx}"] = dimension
    headers = {"Authorization": f"Bearer {project['token']}", "Content-Type": "application/json"}

    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            with httpx.Client(timeout=180) as client:
                response = client.get(config.API_URL, params=params, headers=headers)

            if response.status_code in (429, 500, 502, 503, 504):
                if response.status_code == 429 and "Exceeded daily limit" in response.text:
                    raise RuntimeError(f"{project['project_name']} | {report['name']} | HTTP 429: Exceeded daily limit")
                raise httpx.HTTPStatusError(
                    f"HTTP {response.status_code}: {response.text[:500]}",
                    request=response.request,
                    response=response,
                )

            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, list):
                raise ValueError(f"Respuesta inesperada: {type(payload).__name__}")
            return payload

        except (httpx.HTTPError, ValueError) as exc:
            if attempt == config.MAX_RETRIES:
                raise RuntimeError(f"{project['project_name']} | {report['name']} | {exc}") from exc
            wait_seconds = attempt * config.RETRY_BASE_SECONDS
            log("WARN", f"{project['project_name']} | {report['name']} retry {attempt}/{config.MAX_RETRIES} en {wait_seconds}s")
            time.sleep(wait_seconds)

    raise RuntimeError(f"No se pudo completar {project['project_name']} | {report['name']}")


def normalize_payload(
    payload: list[dict[str, Any]],
    project: dict[str, str],
    report: dict[str, Any],
    run_id: int,
    num_of_days: int,
    window_start_utc: datetime,
    window_end_utc: datetime,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    dimensions = report["dimensions"]
    extraction_date_utc = window_end_utc.date().isoformat()

    for block in payload:
        metric_name = str(block.get("metricName") or "unknown")
        information = block.get("information") or []
        if isinstance(information, dict):
            information = [information]
        if not information:
            information = [{}]

        for item in information:
            if not isinstance(item, dict):
                item = {"value": item}

            dimension_pairs = []
            for dim in dimensions[:3]:
                dimension_pairs.append((dim, item.get(dim)))
            while len(dimension_pairs) < 3:
                dimension_pairs.append((None, None))

            measure_keys = [k for k in item.keys() if k not in set(dimensions)]
            measures = {k: item.get(k) for k in measure_keys}
            hash_payload = {
                "project_name": project["project_name"],
                "report_name": report["name"],
                "metric_name": metric_name,
                "dimensions": dimension_pairs,
                "measures": measures,
            }

            rows.append({
                "run_id": run_id,
                "project_name": project["project_name"],
                "token_name": project.get("token_name"),
                "report_name": report["name"],
                "num_of_days": num_of_days,
                "metric_name": metric_name,
                "dimension1_name": dimension_pairs[0][0],
                "dimension1_value": None if dimension_pairs[0][1] is None else str(dimension_pairs[0][1]),
                "dimension2_name": dimension_pairs[1][0],
                "dimension2_value": None if dimension_pairs[1][1] is None else str(dimension_pairs[1][1]),
                "dimension3_name": dimension_pairs[2][0],
                "dimension3_value": None if dimension_pairs[2][1] is None else str(dimension_pairs[2][1]),
                "measures_json": json.dumps(measures, ensure_ascii=False, default=str),
                "row_json": json.dumps(item, ensure_ascii=False, default=str),
                "row_hash": stable_hash(hash_payload),
                "extraction_date_utc": extraction_date_utc,
                "extracted_at": window_end_utc,
                "window_start_utc": window_start_utc,
                "window_end_utc": window_end_utc,
            })

    return pd.DataFrame(rows)


def save_json_backup(project: dict[str, str], report_name: str, payload: list[dict[str, Any]], run_id: int) -> None:
    if not config.SAVE_JSON_BACKUP:
        return
    out_dir = config.OUTPUT_DIR / safe_name(project["project_name"])
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"run_{run_id}_{report_name}.json"
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Microsoft Clarity live insights -> SQL Server")
    parser.add_argument("--dry-run", action="store_true", help="Extrae y normaliza sin escribir en SQL")
    parser.add_argument("--limit-projects", type=int, default=None, help="Procesa solo N proyectos para pruebas")
    parser.add_argument("--num-days", type=int, choices=[1, 2, 3], default=config.NUM_OF_DAYS, help="Ventana Clarity: 1, 2 o 3 dias")
    parser.add_argument("--reports", default="", help="Reportes a ejecutar, separados por coma. Ej: overview,by_device")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    projects = read_projects()
    if args.limit_projects:
        projects = projects[: args.limit_projects]

    window_end_utc = datetime.utcnow().replace(microsecond=0)
    window_start_utc = window_end_utc - timedelta(days=args.num_days)
    extraction_date_utc = window_end_utc.date().isoformat()

    log("START", "Clarity live insights -> SQL Server")
    log("WINDOW", f"num_days={args.num_days} | {window_start_utc.isoformat()} -> {window_end_utc.isoformat()} UTC")
    log("PROJECTS", ", ".join(p["project_name"] for p in projects))
    reports = config.REPORTS
    if args.reports.strip():
        wanted_reports = {x.strip().lower() for x in args.reports.split(",") if x.strip()}
        reports = [report for report in reports if report["name"].lower() in wanted_reports]
        if not reports:
            raise ValueError("--reports no coincide con ningun reporte definido")

    log("REPORTS", ", ".join(r["name"] for r in reports))

    engine = None
    run_id = 0
    if config.LOAD_TO_SQL and not args.dry_run:
        engine = get_engine()
        test_connection(engine)
        ensure_schema(engine)
        upsert_projects(engine, projects)
        run_id = start_run(engine, args.num_days, len(projects))
    else:
        log("DRY_RUN", "No se escribira en SQL")

    successful_projects: set[str] = set()
    failed_projects: set[str] = set()
    global_errors: list[str] = []

    tasks = [(project, report) for project in projects for report in reports]
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
        future_map = {
            executor.submit(request_clarity, project, report, args.num_days): (project, report)
            for project, report in tasks
        }

        for future in tqdm(as_completed(future_map), total=len(future_map), desc="Clarity reports"):
            project, report = future_map[future]
            rows_loaded = 0
            try:
                payload = future.result()
                save_json_backup(project, report["name"], payload, run_id)
                df = normalize_payload(payload, project, report, run_id, args.num_days, window_start_utc, window_end_utc)

                if engine is not None and config.LOAD_TO_SQL and not args.dry_run:
                    deleted = delete_existing(engine, project["project_name"], report["name"], args.num_days, extraction_date_utc)
                    rows_loaded = load_dataframe(engine, df, "clarity_live_insights")
                    register_report_load(engine, run_id, project["project_name"], report["name"], rows_loaded, "ok")
                    log("LOAD", f"{project['project_name']} | {report['name']} | deleted={deleted} inserted={rows_loaded}")
                else:
                    log("DRY", f"{project['project_name']} | {report['name']} | rows={len(df)}")

                successful_projects.add(project["project_name"])

            except Exception as exc:
                failed_projects.add(project["project_name"])
                error_msg = str(exc)
                global_errors.append(error_msg)
                log("ERROR", error_msg)
                if engine is not None and run_id:
                    register_report_load(engine, run_id, project["project_name"], report["name"], rows_loaded, "error", error_msg[:3900])

    if engine is not None and run_id:
        final_status = "success" if not failed_projects else "partial_error"
        finish_run(
            engine,
            run_id,
            final_status,
            len(successful_projects - failed_projects),
            len(failed_projects),
            "; ".join(global_errors)[:3900] if global_errors else None,
        )
        log("DONE", f"run_id={run_id} status={final_status}")
    else:
        log("DONE", "dry-run finalizado")

    if failed_projects:
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log("CRITICAL", str(exc))
        sys.exit(1)
