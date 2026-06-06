import os, json, time, random, gzip
from math import ceil
from datetime import datetime, timedelta
from typing import Generator, Dict, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from requests_oauthlib import OAuth1
from oauthlib.oauth1 import SIGNATURE_HMAC_SHA256

# =========================
# CONFIG
# =========================

def _load_dotenv_fallback():
    path = os.path.join(os.getcwd(), '.env')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                k, v = line.split('=', 1)
                k = k.strip(); v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)

try:
    from dotenv import load_dotenv  # pip install python-dotenv
    load_dotenv()                   # carga .env al entorno
except Exception:
    _load_dotenv_fallback()  

BASE_URL = os.getenv('MAGENTO_BASE_URL', 'https://converse.cl/rest/V1/')
CONSUMER_KEY = os.getenv('MAGENTO_CONSUMER_KEY', '')
CONSUMER_SECRET = os.getenv('MAGENTO_CONSUMER_SECRET', '')
ACCESS_TOKEN = os.getenv('MAGENTO_ACCESS_TOKEN', '')
ACCESS_TOKEN_SECRET = os.getenv('MAGENTO_ACCESS_TOKEN_SECRET', '')

# Normaliza BASE_URL a /V1/
if not BASE_URL.endswith('/V1/'):
    BASE_URL = BASE_URL.rstrip('/') + '/V1/'
print(f"[BOOT] BASE_URL={BASE_URL}")

OUTPUT_DIR = os.getenv('OUTPUT_DIR', './data/raw')
STATE_DIR = os.getenv('STATE_DIR', './state')
PAGE_SIZE = int(os.getenv('PAGE_SIZE', '100'))
PAUSE_EVERY = int(os.getenv('PAUSE_EVERY', '50'))   # pausa cada N páginas
PAUSE_SECS = float(os.getenv('PAUSE_SECS', '30'))   # segundos de pausa

# Fechas y zona horaria
DATE_FIELD = os.getenv('DATE_FIELD', 'created_at').strip().lower()  # 'created_at' | 'updated_at'
DATE_FROM  = os.getenv('DATE_FROM', '').strip()  # opcional (local)
DATE_TO    = os.getenv('DATE_TO', '').strip()    # opcional (local)
LOCAL_TZ   = os.getenv('LOCAL_TZ', 'America/Lima')
TZ_OFFSET_HOURS = int(os.getenv('TZ_OFFSET_HOURS','-5'))  # fallback si no hay zoneinfo



# Limitar status_histories para no inflar archivos
STATUS_HISTORY_LIMIT = int(os.getenv('STATUS_HISTORY_LIMIT', '5'))

os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(STATE_DIR, exist_ok=True)

# =========================
# HTTP
# =========================
def _auth():
    return OAuth1(CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET,
                  signature_method=SIGNATURE_HMAC_SHA256)

def _session():
    s = requests.Session()
    retry = Retry(total=0, connect=3, backoff_factor=0.4,
                  status_forcelist=[401,408,409,425,429,500,502,503,504],
                  allowed_methods=frozenset(['GET']), raise_on_status=False)
    s.mount('https://', HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=retry))
    s.headers.update({'Accept':'application/json','Accept-Encoding':'gzip, deflate',
                      'Connection':'keep-alive','User-Agent':'DigitalImpact-Extractor/3.6'})
    return s

# =========================
# Fechas (LOCAL → UTC)
# =========================
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

def _parse_date(s: str, end: bool=False) -> str:
    s = (s or '').strip()
    if not s: return ''
    if len(s) == 10:
        return f"{s} {'23:59:59' if end else '00:00:00'}"
    return s

def _resolve_range(days_back: int):
    """Interpreta fechas en LOCAL_TZ y devuelve UTC 'YYYY-MM-DD HH:MM:SS'."""
    def to_utc_str(local_str):
        try:
            if ZoneInfo:
                tz = ZoneInfo(LOCAL_TZ)
                dt = datetime.strptime(local_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=tz)
                return dt.astimezone(ZoneInfo('UTC')).strftime('%Y-%m-%d %H:%M:%S')
        except Exception:
            pass
        # Fallback: offset fijo
        dt = datetime.strptime(local_str, '%Y-%m-%d %H:%M:%S') - timedelta(hours=TZ_OFFSET_HOURS)
        return dt.strftime('%Y-%m-%d %H:%M:%S')

    if DATE_FROM and DATE_TO:
        s_loc = _parse_date(DATE_FROM, end=False)
        e_loc = _parse_date(DATE_TO,   end=True)
        return to_utc_str(s_loc), to_utc_str(e_loc)

    # X días atrás en calendario LOCAL (incluye hoy local)
    if ZoneInfo:
        now_utc = datetime.utcnow().replace(tzinfo=ZoneInfo('UTC'))
        now_loc = now_utc.astimezone(ZoneInfo(LOCAL_TZ))
    else:
        now_loc = datetime.utcnow() - timedelta(hours=TZ_OFFSET_HOURS)
    start_loc = (now_loc - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, microsecond=0)
    end_loc   =  now_loc.replace(hour=23, minute=59, second=59, microsecond=0)
    return to_utc_str(start_loc.strftime('%Y-%m-%d %H:%M:%S')), to_utc_str(end_loc.strftime('%Y-%m-%d %H:%M:%S'))

# =========================
# Fields y URL
# =========================
def _fields_param() -> str:
    # Campos del pedido (order) + ítems + status_histories
    return (
        "items["
        "increment_id,entity_id,store_name,created_at,updated_at,state,status,shipping_description,"
        "customer_id,customer_firstname,customer_lastname,customer_email,"
        "applied_rule_ids,coupon_code,grand_total,ext_order_id,base_shipping_amount,"
        "payment[method,entity_id,cc_type],"
        "extension_attributes["
        "document,type_document,razon_social,province,district,numero,depto,rule_name,origin_of_sale,"
        "person_receiver_option,person_receiver_full_name,person_receiver_phone_number,"
        "additional_information,payment_additional_info,"
        "shipping_assignments[shipping[method,address[telephone,customer_address_id,street,region,city,address_type,firstname,lastname,postcode]],items[updated_at]]"
        "],"
        # status_histories del pedido
        "status_histories[comment,created_at],"
        # order items
        "items[item_id,parent_item_id,sku,name,product_type,base_original_price,base_price,base_row_total,base_shipping_amount,base_discount_amount,qty_ordered,qty,updated_at]"
        "],total_count"
    )

def _build_orders_url(page:int, page_size:int, start:str, end:str, fields_param:str) -> str:
    field = DATE_FIELD  # 'created_at' o 'updated_at'
    return (
        f"{BASE_URL}orders?"
        f"searchCriteria[filter_groups][0][filters][0][field]={field}&"
        f"searchCriteria[filter_groups][0][filters][0][value]={start}&"
        f"searchCriteria[filter_groups][0][filters][0][condition_type]=gteq&"
        f"searchCriteria[filter_groups][1][filters][0][field]={field}&"
        f"searchCriteria[filter_groups][1][filters][0][value]={end}&"
        f"searchCriteria[filter_groups][1][filters][0][condition_type]=lteq&"
        f"searchCriteria[sortOrders][0][field]=entity_id&"
        f"searchCriteria[sortOrders][0][direction]=ASC&"
        f"searchCriteria[pageSize]={page_size}&"
        f"searchCriteria[currentPage]={page}&"
        f"fields={fields_param}"
    )

# =========================
# Healthcheck
# =========================
def _healthcheck():
    s = _session()
    r = s.get(BASE_URL + 'store/storeViews', auth=_auth(), timeout=30)
    if r.status_code != 200:
        raise SystemExit(f"[FATAL] Healthcheck -> HTTP {r.status_code} | {(r.text or '')[:240]}")

# =========================
# Core fetch (generator)
# =========================
def fetch_orders(days_back:int=3, page_size:int=PAGE_SIZE) -> Generator[Dict[str,Any], None, None]:
    _healthcheck()
    start, end = _resolve_range(days_back)
    print(f"[INFO] filtro={DATE_FIELD} rango_utc=[{start} .. {end}]")

    s = _session()
    fields_param = _fields_param()

    page = 1
    total_pages = None
    pages_fetched = 0

    while True:
        url = _build_orders_url(page, page_size, start, end, fields_param)
        resp = s.get(url, auth=_auth(), timeout=(10,120))

        if resp.status_code == 429:
            retry_after = int(resp.headers.get('Retry-After','15'))
            print(f"[429] Esperando {retry_after}s..."); time.sleep(retry_after); continue
        if resp.status_code in (401,403,404):
            body = (resp.text or '')[:300].replace('\n',' ')
            raise SystemExit(f"[FATAL] HTTP {resp.status_code} {BASE_URL}orders\n{body}\n-> Revisa BASE_URL y permisos del Integration.")
        if resp.status_code >= 500:
            time.sleep(2.0 + random.random()); continue
        if resp.status_code != 200:
            body = (resp.text or '')[:300].replace('\n',' ')
            print(f"[WARN] HTTP {resp.status_code} pág {page} | {body}")
            break

        data = resp.json() or {}
        if total_pages is None:
            total_count = int(data.get('total_count') or 0)
            total_pages = max(1, ceil(total_count / max(page_size,1)))
            print(f"[INFO] total_count={total_count} pages~{total_pages}")  # '~' ASCII para Windows

        items = data.get('items') or []
        for it in items:
            # Limitar status_histories a últimos N
            if STATUS_HISTORY_LIMIT > 0:
                sh = it.get('status_histories')
                if isinstance(sh, list) and sh:
                    try:
                        sh = sorted(sh, key=lambda e: (e.get('created_at') or ''))
                    except Exception:
                        pass
                    it['status_histories'] = sh[-STATUS_HISTORY_LIMIT:]
            yield it

        pages_fetched += 1
        if PAUSE_EVERY>0 and pages_fetched % PAUSE_EVERY == 0:
            print(f"[PAUSE] Esperando {PAUSE_SECS}s..."); time.sleep(PAUSE_SECS)

        if page >= total_pages: break
        page += 1
        time.sleep(0.15 + random.random()*0.15)

# =========================
# Writer (NDJSON .gz) + checkpoint
# =========================
def write_ndjson(days_back:int=3) -> str:
    date_tag = datetime.now().strftime('%Y%m%d_%H%M%S')
    out_path = os.path.join(OUTPUT_DIR, f'orders_{date_tag}.ndjson.gz')
    cnt = 0

    with gzip.open(out_path, 'wt', encoding='utf-8') as gz:
        for order in fetch_orders(days_back=days_back):
            gz.write(json.dumps(order, ensure_ascii=False) + '\n'); cnt += 1

    # checkpoint detallado
    ck_path = os.path.join(STATE_DIR, 'checkpoint.json')
    start_utc, end_utc = _resolve_range(days_back)
    date_mode = 'range' if (DATE_FROM and DATE_TO) else 'days'

    with open(ck_path,'w',encoding='utf-8') as f:
        json.dump({
            'date_field': DATE_FIELD,
            'date_mode': date_mode,
            'local_tz': LOCAL_TZ,
            'days_back': int(days_back),
            'date_from_local': DATE_FROM,
            'date_to_local': DATE_TO,
            'range_utc_start': start_utc,
            'range_utc_end': end_utc,
            'base_url': BASE_URL,
            'file': os.path.basename(out_path),
            'orders_in_file': cnt,
            'last_run': datetime.now().isoformat()
        }, f, ensure_ascii=False, indent=2)

    print(f"[DONE] Guardado {cnt} órdenes en {out_path}")
    return out_path

if __name__ == '__main__':
    write_ndjson(days_back=int(os.getenv('DAYS_BACK','3')))