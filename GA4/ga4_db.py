"""
SQL Server helpers for GA4 extractor.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime
from typing import Optional

import pandas as pd
from sqlalchemy import create_engine, text, event
from sqlalchemy.engine import Engine

import ga4_config as config


def log(level: str, message: str) -> None:
    print(f"[{level}] {message}")


def get_engine() -> Engine:
    """Create SQLAlchemy engine for SQL Server using pyodbc."""
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
        result = conn.execute(text("SELECT DB_NAME() AS database_name")).scalar()
        log("DB", f"Conectado a SQL Server DB: {result}")


def upsert_properties(engine: Engine) -> None:
    sql = text("""
        MERGE dbo.ga4_properties AS target
        USING (SELECT :property_id AS property_id, :property_name AS property_name) AS source
        ON target.property_id = source.property_id
        WHEN MATCHED THEN
            UPDATE SET property_name = source.property_name,
                       brand_name = source.property_name,
                       is_active = 1
        WHEN NOT MATCHED THEN
            INSERT (property_id, property_name, brand_name, is_active)
            VALUES (source.property_id, source.property_name, source.property_name, 1);
    """)
    with engine.begin() as conn:
        for property_id, property_name in config.PROPERTY_INFO.items():
            conn.execute(sql, {"property_id": property_id, "property_name": property_name})
    log("DB", "ga4_properties actualizado.")


def start_run(engine: Engine, start_date: str, end_date: str, total_properties: int) -> int:
    sql = text("""
        INSERT INTO dbo.ga4_etl_runs
            (started_at, start_date, end_date, status, total_properties, successful_properties, failed_properties)
        OUTPUT INSERTED.run_id
        VALUES
            (SYSDATETIME(), :start_date, :end_date, 'running', :total_properties, 0, 0);
    """)
    with engine.begin() as conn:
        run_id = conn.execute(sql, {
            "start_date": start_date,
            "end_date": end_date,
            "total_properties": total_properties,
        }).scalar_one()
    log("RUN", f"run_id={run_id} iniciado.")
    return int(run_id)


def finish_run(engine: Engine, run_id: int, status: str, successful: int, failed: int, error_message: Optional[str] = None) -> None:
    sql = text("""
        UPDATE dbo.ga4_etl_runs
        SET finished_at = SYSDATETIME(),
            status = :status,
            successful_properties = :successful,
            failed_properties = :failed,
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
    log("RUN", f"run_id={run_id} finalizado con status={status}.")


def register_report_load(
    engine: Engine,
    run_id: int,
    property_id: str,
    property_name: str,
    report_name: str,
    table_name: str,
    rows_loaded: int,
    status: str,
    error_message: Optional[str] = None,
) -> None:
    sql = text("""
        INSERT INTO dbo.ga4_etl_report_loads
            (run_id, property_id, property_name, report_name, table_name, rows_loaded, status, error_message, loaded_at)
        VALUES
            (:run_id, :property_id, :property_name, :report_name, :table_name, :rows_loaded, :status, :error_message, SYSDATETIME());
    """)
    with engine.begin() as conn:
        conn.execute(sql, {
            "run_id": run_id,
            "property_id": property_id,
            "property_name": property_name,
            "report_name": report_name,
            "table_name": table_name,
            "rows_loaded": rows_loaded,
            "status": status,
            "error_message": error_message,
        })


def delete_existing(engine: Engine, table: str, grain: str, property_id: str, start_date: str, end_date: str, start_ym: str, end_ym: str) -> int:
    if grain == "monthly":
        sql = text(f"""
            DELETE FROM dbo.{table}
            WHERE property_id = :property_id
              AND year_month BETWEEN :start_ym AND :end_ym;
        """)
        params = {"property_id": property_id, "start_ym": start_ym, "end_ym": end_ym}
    elif grain == "daily":
        sql = text(f"""
            DELETE FROM dbo.{table}
            WHERE property_id = :property_id
              AND [date] BETWEEN :start_date AND :end_date;
        """)
        params = {"property_id": property_id, "start_date": start_date, "end_date": end_date}
    else:
        sql = text(f"""
            DELETE FROM dbo.{table}
            WHERE property_id = :property_id
              AND start_date = :start_date
              AND end_date = :end_date;
        """)
        params = {"property_id": property_id, "start_date": start_date, "end_date": end_date}

    with engine.begin() as conn:
        result = conn.execute(sql, params)
        deleted = result.rowcount if result.rowcount is not None else 0
    return deleted


def load_dataframe(engine: Engine, df: pd.DataFrame, table: str) -> int:
    if df.empty:
        return 0
    df.to_sql(
        name=table,
        con=engine,
        schema="dbo",
        if_exists="append",
        index=False,
        chunksize=1000,
        method=None,
    )
    return len(df)
