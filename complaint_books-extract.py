import os
import time
import math
import logging
from typing import List, Dict, Any

import requests
from requests_oauthlib import OAuth1
import pyodbc
from dotenv import load_dotenv

# =========================
# Setup
# =========================
load_dotenv()

# --- Magento (.env) ---
BASE_URL = os.getenv("MAGENTO_BASE_URL", "https://converse.cl/rest/V1/")
CK  = os.getenv("MAGENTO_CONSUMER_KEY", "")
CS  = os.getenv("MAGENTO_CONSUMER_SECRET", "")
AT  = os.getenv("MAGENTO_ACCESS_TOKEN", "")
ATS = os.getenv("MAGENTO_ACCESS_TOKEN_SECRET", "")

# --- SQL Destino (usa Trusted_Connection, como tus otros scripts) ---
SQL_SERVER   = os.getenv("DI_SQL_SERVER", "localhost")
SQL_DATABASE = os.getenv("DI_SQL_DATABASE", "Digital_Impact_Reportes")
SQL_DRIVER   = os.getenv("DI_SQL_DRIVER", "ODBC Driver 17 for SQL Server")

TABLE_NAME = "SZ_Complaint_Books"
PAGE_SIZE = int(os.getenv("PAGE_SIZE", "500"))             # recomendado 500–1000
SLEEP_BETWEEN_PAGES = float(os.getenv("SLEEP_BETWEEN_PAGES", "0.25"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# =========================
# Conexión SQL (pyodbc)
# =========================
def sql_connect():
    """
    Conecta por Trusted_Connection a SQL Server con:
    - LoginTimeout/Connection Timeout explícitos
    - Fallback de servidor (localhost, 127.0.0.1, .)
    - Reintentos exponenciales ante 08001/258
    """
    driverserver = SQL_SERVER  # p.ej. 'localhost' o 'localhost,1433'
    candidates = []
    # si no trajiste puerto, probamos variantes comunes
    if ("," not in driverserver) and ("\\" not in driverserver):
        candidates = [driverserver, f"{driverserver},1433"]
        if driverserver.lower() in ("localhost",):
            candidates += ["127.0.0.1", "127.0.0.1,1433", "."]
    else:
        candidates = [driverserver]

    last_err = None
    for attempt in range(1, 5):  # hasta 4 reintentos
        for host in candidates:
            conn_str = (
                f"DRIVER={{{SQL_DRIVER}}};"
                f"SERVER={host};"
                f"DATABASE={SQL_DATABASE};"
                "Trusted_Connection=yes;"
                "TrustServerCertificate=yes;"
                "LoginTimeout=10;"
                "Connection Timeout=15;"
            ).replace("{SQL_DRIVER}", SQL_DRIVER)
            try:
                return pyodbc.connect(conn_str, autocommit=False)
            except pyodbc.Error as e:
                last_err = e
        # backoff exponencial
        time.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"[SQL] Error de conexión ODBC tras reintentos: {last_err}")

# =========================
# Helpers
# =========================
def get_existing_entity_ids(cursor) -> set:
    cursor.execute(f"SELECT entity_id FROM {TABLE_NAME};")
    return {row[0] for row in cursor.fetchall()}

def insert_batch(cursor, rows: List[Dict[str, Any]]):
    """
    Inserta solo columnas definidas en la tabla.
    Omitimos fecha_insercion para que SQL asigne GETDATE() por DEFAULT.
    """
    if not rows:
        return

    sql = f"""
    INSERT INTO {TABLE_NAME} (
        entity_id, id_complaintsbook, person_type, full_name, dni_ce,
        cell_phone_number, email, address, minor_age, guardian_name,
        guardian_address, guardian_phone, guardian_email, department,
        province, district, order_id, product_amount, complaint_type,
        product_description, complaint_detail, txt_order
    )
    VALUES (
        ?, ?, ?, ?, ?,
        ?, ?, ?, ?, ?,
        ?, ?, ?, ?,
        ?, ?, ?, ?, ?,
        ?, ?, ?
    );
    """

    params = []
    for r in rows:
        params.append((
            r.get("entity_id"),
            r.get("id_complaintsbook"),
            r.get("person_type"),
            r.get("full_name"),
            r.get("dni_ce"),
            r.get("cell_phone_number"),
            r.get("email"),
            r.get("address"),
            _to_bit(r.get("minor_age")),
            r.get("guardian_name"),
            r.get("guardian_address"),
            r.get("guardian_phone"),
            r.get("guardian_email"),
            r.get("department"),
            r.get("province"),
            r.get("district"),
            _to_str_or_none(r.get("order_id")),
            _to_decimal(r.get("product_amount")),
            r.get("complaint_type"),
            r.get("product_description"),
            r.get("complaint_detail"),
            r.get("txt_order"),
        ))

    # cursor.fast_executemany = True   # <- DESACTIVADO por bug de buffer 200
    cursor.executemany(sql, params)

def _to_decimal(x):
    """Convierte montos a float/decimal seguro (o None)."""
    if x in (None, "", "null", "None"):
        return None
    try:
        return float(x)
    except Exception:
        try:
            return float(str(x).replace(",", "."))
        except Exception:
            return None

def _to_bit(x):
    if x is None or x == "":
        return None
    s = str(x).strip().lower()
    if s in ("1", "true", "yes", "si", "sí", "verdadero"):
        return 1
    if s in ("0", "false", "no", "falso"):
        return 0
    return None

def _to_str_or_none(x):
    return None if x in (None, "") else str(x)

def fetch_page(session: requests.Session, page: int) -> Dict[str, Any]:
    url = BASE_URL.rstrip("/") + "/complaintsbook/search"
    params = {
        "searchCriteria[pageSize]": PAGE_SIZE,
        "searchCriteria[currentPage]": page,
        "searchCriteria[sortOrders][0][field]": "entity_id",
        "searchCriteria[sortOrders][0][direction]": "DESC",
    }
    resp = session.get(url, params=params, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text}")
    return resp.json()

# =========================
# Main
# =========================
def main():
    # Validación rápida de Magento
    for key, val in {
        "MAGENTO_BASE_URL": BASE_URL,
        "MAGENTO_CONSUMER_KEY": CK,
        "MAGENTO_CONSUMER_SECRET": CS,
        "MAGENTO_ACCESS_TOKEN": AT,
        "MAGENTO_ACCESS_TOKEN_SECRET": ATS,
    }.items():
        if not val:
            logging.warning(f"{key} vacío. Revisa tu .env")

    # Log de conexión SQL
    logging.info(f"Conectando a SQL -> server='{SQL_SERVER}' db='{SQL_DATABASE}' driver='{SQL_DRIVER}'")

    # Sesión OAuth1 (Magento)
    auth = OAuth1(CK, CS, AT, ATS, signature_method="HMAC-SHA256")
    session = requests.Session()
    session.auth = auth

    # 1) Primera página: para saber total_count
    logging.info("Consultando primera página…")
    data = fetch_page(session, 1)
    total_count = int(data.get("total_count") or data.get("totalCount") or 0)
    items = data.get("items", [])
    logging.info(f"total_count reportado por API: {total_count}")

    # 2) Calcular páginas
    pages = max(1, int(math.ceil(total_count / PAGE_SIZE))) if PAGE_SIZE else 1
    logging.info(f"Páginas estimadas: {pages}")

    # 3) Conexión SQL y carga de entity_id existentes
    conn = sql_connect()
    cursor = conn.cursor()
    existing = get_existing_entity_ids(cursor)
    logging.info(f"IDs existentes en {TABLE_NAME}: {len(existing)}")

    # 4) Recolectar nuevos
    new_rows = []
    processed = 0

    def collect_items(items_batch):
        nonlocal new_rows, processed
        for it in items_batch:
            eid = it.get("entity_id")
            if eid is None or eid in existing:
                continue
            new_rows.append({
                "entity_id": eid,
                "id_complaintsbook": it.get("id_complaintsbook"),
                "person_type": it.get("person_type"),
                "full_name": it.get("full_name"),
                "dni_ce": it.get("dni_ce"),
                "cell_phone_number": it.get("cell_phone_number"),
                "email": it.get("email"),
                "address": it.get("address"),
                "minor_age": it.get("minor_age"),
                "guardian_name": it.get("guardian_name"),
                "guardian_address": it.get("guardian_address"),
                "guardian_phone": it.get("guardian_phone"),
                "guardian_email": it.get("guardian_email"),
                "department": it.get("department"),
                "province": it.get("province"),
                "district": it.get("district"),
                "order_id": it.get("order_id"),
                "product_amount": it.get("product_amount"),
                "complaint_type": it.get("complaint_type"),
                "product_description": it.get("product_description"),
                "complaint_detail": it.get("complaint_detail"),
                "txt_order": it.get("txt_order"),
            })
            processed += 1

    # Primera tanda
    collect_items(items)

    # Resto de páginas
    for page in range(2, pages + 1):
        time.sleep(SLEEP_BETWEEN_PAGES)
        logging.info(f"Descargando página {page}/{pages}…")
        data = fetch_page(session, page)
        items = data.get("items", [])
        if not items:
            break
        collect_items(items)

    # 5) Insertar por lotes
    logging.info(f"Nuevos registros a insertar: {len(new_rows)}")
    if new_rows:
        CHUNK = 1000
        for i in range(0, len(new_rows), CHUNK):
            chunk = new_rows[i:i+CHUNK]
            insert_batch(cursor, chunk)
            conn.commit()
            logging.info(f"Insertados {i + len(chunk)}/{len(new_rows)}")
    else:
        logging.info("No hay registros nuevos para insertar.")

    cursor.close()
    conn.close()
    logging.info("Proceso finalizado con éxito.")

if __name__ == "__main__":
    main()

