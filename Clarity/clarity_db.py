from __future__ import annotations

import urllib.parse
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

import clarity_config as config


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}", flush=True)


def get_engine() -> Engine:
    if config.SQL_TRUSTED_CONNECTION:
        conn_str = (
            f"DRIVER={{{config.SQL_DRIVER}}};"
            f"SERVER={config.SQL_SERVER};"
            f"DATABASE={config.SQL_DATABASE};"
            "Trusted_Connection=yes;"
            "TrustServerCertificate=yes;"
        )
    else:
        conn_str = (
            f"DRIVER={{{config.SQL_DRIVER}}};"
            f"SERVER={config.SQL_SERVER};"
            f"DATABASE={config.SQL_DATABASE};"
            f"UID={config.SQL_USERNAME};"
            f"PWD={config.SQL_PASSWORD};"
            "TrustServerCertificate=yes;"
        )

    params = urllib.parse.quote_plus(conn_str)
    engine = create_engine(f"mssql+pyodbc:///?odbc_connect={params}", future=True)

    @event.listens_for(engine, "before_cursor_execute")
    def receive_before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        if executemany:
            try:
                cursor.fast_executemany = True
            except Exception:
                pass

    return engine


def test_connection(engine: Engine) -> None:
    with engine.begin() as conn:
        result = conn.execute(text("SELECT DB_NAME()")).scalar()
        log("DB", f"Conectado a SQL Server DB: {result}")


def upsert_projects(engine: Engine, projects: list[dict[str, str]]) -> None:
    sql = text("""
        MERGE dbo.clarity_projects AS target
        USING (SELECT :project_name AS project_name, :token_name AS token_name) AS source
        ON target.project_name = source.project_name
        WHEN MATCHED THEN
            UPDATE SET token_name = source.token_name,
                       is_active = 1,
                       updated_at = SYSDATETIME()
        WHEN NOT MATCHED THEN
            INSERT (project_name, token_name, is_active, updated_at)
            VALUES (source.project_name, source.token_name, 1, SYSDATETIME());
    """)
    with engine.begin() as conn:
        for project in projects:
            conn.execute(sql, {
                "project_name": project["project_name"],
                "token_name": project.get("token_name"),
            })


def start_run(engine: Engine, num_of_days: int, total_projects: int) -> int:
    sql = text("""
        INSERT INTO dbo.clarity_etl_runs
            (started_at, num_of_days, status, total_projects, successful_projects, failed_projects)
        OUTPUT INSERTED.run_id
        VALUES (SYSDATETIME(), :num_of_days, 'running', :total_projects, 0, 0);
    """)
    with engine.begin() as conn:
        run_id = conn.execute(sql, {
            "num_of_days": num_of_days,
            "total_projects": total_projects,
        }).scalar_one()
    log("RUN", f"run_id={run_id} iniciado")
    return int(run_id)


def finish_run(engine: Engine, run_id: int, status: str, successful: int, failed: int, error_message: Optional[str] = None) -> None:
    sql = text("""
        UPDATE dbo.clarity_etl_runs
        SET finished_at = SYSDATETIME(),
            status = :status,
            successful_projects = :successful,
            failed_projects = :failed,
            error_message = :error_message
        WHERE run_id = :run_id;
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "run_id": run_id,
            "status": status,
            "successful": successful,
            "failed": failed,
            "error_message": error_message,
        })


def register_report_load(
    engine: Engine,
    run_id: int,
    project_name: str,
    report_name: str,
    rows_loaded: int,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    sql = text("""
        INSERT INTO dbo.clarity_etl_report_loads
            (run_id, project_name, report_name, rows_loaded, status, error_message, loaded_at)
        VALUES
            (:run_id, :project_name, :report_name, :rows_loaded, :status, :error_message, SYSDATETIME());
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "run_id": run_id,
            "project_name": project_name,
            "report_name": report_name,
            "rows_loaded": rows_loaded,
            "status": status,
            "error_message": error_message,
        })


def delete_existing(engine: Engine, project_name: str, report_name: str, num_of_days: int, extraction_date_utc: str) -> int:
    sql = text("""
        DELETE FROM dbo.clarity_live_insights
        WHERE project_name = :project_name
          AND report_name = :report_name
          AND num_of_days = :num_of_days
          AND extraction_date_utc = :extraction_date_utc;
    """)
    with engine.begin() as conn:
        result = conn.execute(sql, {
            "project_name": project_name,
            "report_name": report_name,
            "num_of_days": num_of_days,
            "extraction_date_utc": extraction_date_utc,
        })
        return result.rowcount if result.rowcount is not None else 0


def load_dataframe(engine: Engine, df: pd.DataFrame, table: str) -> int:
    if df.empty:
        return 0
    df.to_sql(table, con=engine, schema="dbo", if_exists="append", index=False, chunksize=1000)
    return len(df)
