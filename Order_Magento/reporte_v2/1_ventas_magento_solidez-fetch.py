# magento_fetch_orders.py
import os, json, gzip, time, random
from datetime import datetime, timedelta
from math import ceil
from typing import List, Dict, Tuple
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm
from requests_oauthlib import OAuth1

# === CONFIG ===
BASE_URL = 'https://converse.cl/rest/converse_pe_store_view'
CONSUMER_KEY = 'wab8paongympcuj7y5x4qdxs0bw1hd4z'
CONSUMER_SECRET = 'erypfmavubmjb520ap1oe4zwbsdl310x'
ACCESS_TOKEN = 'o3pmatsrmolplnsvtdm2oc1ey7jwxej6'
ACCESS_TOKEN_SECRET = '3eftvwbskm5atab6pm8zr6ng51t8kova'

PAGE_SIZE = 100          # Magento suele tolerar 100 bien
WORKERS = 4              # Sube a 6 si el server aguanta; baja a 3 si ves 429
WINDOW_DAYS = 2          # Particiona por sub-rangos de 2 días (más estable que paralelizar todo junto)
TIMEOUT = (10, 120)      # (connect, read) en segundos
OUT_DIR = "./raw_orders" # Aquí se guardan los .ndjson.gz

# === CAMPOS MAGENTO — slim, sin items.updated_at y conservando payment.entity_id ===
def build_fields_param() -> str:
    """

    """
    return (
        "items["
            "increment_id,entity_id,store_name,created_at,state,status,shipping_description,"
            "customer_id,customer_firstname,customer_lastname,customer_email,"
            "applied_rule_ids,coupon_code,grand_total,ext_order_id,base_shipping_amount,"
            "payment[method,entity_id,cc_type],"
            "extension_attributes["
                "document,type_document,razon_social,province,origin_of_sale,"
                "person_receiver_option,person_receiver_full_name,person_receiver_phone_number,"
                "additional_information,payment_additional_info,"
                "shipping_assignments[shipping[method,address[telephone,customer_address_id,street,region,city,address_type,firstname,lastname,postcode]],items[updated_at]]"
            "],"
            "items[item_id,parent_item_id,sku,name,product_type,base_original_price,base_price,base_row_total,base_shipping_amount,base_discount_amount,qty_ordered,qty,updated_at]"
        "],"
        "total_count,"
        "status_histories[comment]"
    )

def obtener_auth():
    return OAuth1(CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET)

def build_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(
        total=5, connect=5, read=5, backoff_factor=1.2,
        status_forcelist=[401,408,409,425,429,500,502,503,504],
        allowed_methods=frozenset(["GET"])
    )
    adapter = HTTPAdapter(pool_connections=16, pool_maxsize=16, max_retries=retry)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    s.headers.update({"Accept-Encoding": "gzip, deflate", "Accept": "application/json"})
    return s

def daterange_windows(start_date: str, end_date: str, window_days: int) -> List[Tuple[str, str]]:
    sd = datetime.strptime(start_date, "%Y-%m-%d")
    ed = datetime.strptime(end_date, "%Y-%m-%d")
    cur = sd
    windows = []
    while cur <= ed:
        hi = min(cur + timedelta(days=window_days-1), ed)
        windows.append((cur.strftime("%Y-%m-%d"), hi.strftime("%Y-%m-%d")))
        cur = hi + timedelta(days=1)
    return windows

def build_url(page: int, page_size: int, date_from: str, date_to: str) -> str:
    # Filtro por rango de fechas (created_at): ajusta si usas otro campo
    filters = (
        "searchCriteria[filter_groups][0][filters][0][field]=created_at&"
        f"searchCriteria[filter_groups][0][filters][0][value]={date_from} 00:00:00&"
        "searchCriteria[filter_groups][0][filters][0][condition_type]=from&"
        "searchCriteria[filter_groups][0][filters][1][field]=created_at&"
        f"searchCriteria[filter_groups][0][filters][1][value]={date_to} 23:59:59&"
        "searchCriteria[filter_groups][0][filters][1][condition_type]=to&"
    )
    paging = f"searchCriteria[currentPage]={page}&searchCriteria[pageSize]={page_size}&"
    fields = f"fields={build_fields_param()}"
    return f"{BASE_URL}/V1/orders?{filters}{paging}{fields}"

def save_items(items: List[Dict], out_dir: str, tag: str, page: int):
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"orders_{tag}_p{page:04d}.ndjson.gz")
    with gzip.open(path, "at", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

def fetch_page(session: requests.Session, page: int, page_size: int, date_from: str, date_to: str):
    RETRIABLE = {401,408,409,425,429,500,502,503,504}
    for attempt in range(1, 6):
        try:
            url = build_url(page, page_size, date_from, date_to)
            r = session.get(url, auth=obtener_auth(), timeout=TIMEOUT)
            if r.status_code == 200:
                j = r.json()
                return j.get("items", []) or []
            if r.status_code in RETRIABLE and attempt < 5:
                # backoff exponencial con un jitter suave
                time.sleep((2 ** attempt) + random.random() * 0.3)
                continue
            return ("HTTP", r.status_code, (r.text or "")[:300].replace("\n", " "))
        except requests.RequestException as e:
            if attempt >= 5:
                return ("EXC", str(e))
            time.sleep((2 ** attempt) + random.random() * 0.3)

def fetch_window(session: requests.Session, date_from: str, date_to: str) -> Tuple[int, List[str]]:
    tag = f"{date_from}_a_{date_to}"
    errors = []

    # Primer disparo para conocer total_count
    url0 = build_url(1, PAGE_SIZE, date_from, date_to)
    r0 = session.get(url0, auth=obtener_auth(), timeout=TIMEOUT)
    r0.raise_for_status()
    j0 = r0.json()
    items0 = j0.get("items", []) or []
    total_count = j0.get("total_count", 0) or 0
    total_pages = max(1, ceil(total_count / PAGE_SIZE)) if total_count else 1

    if items0:
        save_items(items0, OUT_DIR, tag, 1)

    if total_pages == 1:
        return total_count, errors

    # Páginas 2..N en paralelo
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = {ex.submit(fetch_page, session, p, PAGE_SIZE, date_from, date_to): p
                for p in range(2, total_pages + 1)}
        for fut in tqdm(as_completed(futs), total=len(futs),
                        desc=f"{tag} páginas 2..{total_pages}", unit="pág", ncols=90):
            res = fut.result()
            if isinstance(res, list):
                save_items(res, OUT_DIR, tag, futs[fut])
            else:
                kind = res[0]
                errors.append(f"❌ {tag} p{futs[fut]} -> {kind}: {res[1:]}")
    return total_count, errors

def main(start_date: str, end_date: str):
    session = build_session()
    windows = daterange_windows(start_date, end_date, WINDOW_DAYS)
    total = 0
    all_errors = []
    for (df, dt) in windows:
        cnt, errs = fetch_window(session, df, dt)
        total += cnt
        all_errors.extend(errs)
    print(f"[OK] Órdenes descargadas (estimadas por Magento total_count): {total}")
    if all_errors:
        print("[WARN] Errores durante la descarga:")
        for e in all_errors:
            print("   ", e)
    print(f"[ACCION] Archivos guardados en: {os.path.abspath(OUT_DIR)}")

if __name__ == "__main__":
    # Ejemplo rápido: del 2024-01-01 al 2024-12-31
    main("2024-01-01", datetime.today().strftime("%Y-%m-%d"))
