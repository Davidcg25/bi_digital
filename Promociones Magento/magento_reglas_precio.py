import requests
from requests_oauthlib import OAuth1
import time
from tqdm import tqdm
import pandas as pd
from datetime import datetime

# ====== CREDENCIALES ======
CONSUMER_KEY = 'wab8paongympcuj7y5x4qdxs0bw1hd4z'
CONSUMER_SECRET = 'erypfmavubmjb520ap1oe4zwbsdl310x'
ACCESS_TOKEN = 'o3pmatsrmolplnsvtdm2oc1ey7jwxej6'
ACCESS_TOKEN_SECRET = '3eftvwbskm5atab6pm8zr6ng51t8kova'

# ====== CONFIG ======
BASE_URL = 'https://converse.cl/rest/V1/'
PAGE_SIZE = 50
SLEEP_BETWEEN_PAGES = 0.5
ADD_TIMESTAMP = True

# ====== MAPA FIJO DE WEBSITES (ID -> Nombre) ======
WEBSITE_MAP = {
    0:  "Admin",
    1:  "Default Store View",
    4:  "Converse Perú",
    7:  "Converse Chile",
    8:  "New Balance Perú",
    10: "Merrell Perú",
    13: "Steve Madden Perú",
    16: "Caterpillar Perú",
    19: "Coliseum Chile",
    22: "Coliseum Perú",
    24: "Fila Chile",
    27: "Umbro Perú",
    31: "Fila Perú",
    28: "Umbro Chile"
}

# ====== AUTH ======
def auth():
    return OAuth1(CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_TOKEN_SECRET, signature_method='HMAC-SHA256')

# ====== HELPERS ======
def get(url, auth_obj):
    r = requests.get(url, auth=auth_obj, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:500]}")
    return r.json()

def get_total_rules(a):
    url = f"{BASE_URL}salesRules/search?searchCriteria[pageSize]={PAGE_SIZE}&searchCriteria[currentPage]=1"
    data = get(url, a)
    return data.get('total_count', 0)

def fetch_all_rules():
    a = auth()
    total = get_total_rules(a)
    if total == 0:
        return []

    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    all_items, page = [], 1

    with tqdm(total=total_pages, desc="[ACCION] Descargando reglas de precio", unit="página") as pbar:
        while True:
            url = f"{BASE_URL}salesRules/search?searchCriteria[pageSize]={PAGE_SIZE}&searchCriteria[currentPage]={page}"
            data = get(url, a)
            items = data.get('items', [])
            if not items:
                break
            all_items.extend(items)
            pbar.update(1)
            if len(all_items) >= total:
                break
            page += 1
            time.sleep(SLEEP_BETWEEN_PAGES)
    return all_items

# ====== NORMALIZACIONES ======
COUPON_INT_TO_LABEL = {1: "NO_COUPON", 2: "SPECIFIC_COUPON", 3: "AUTO"}
def norm_coupon_type(v):
    if v is None: return ""
    if isinstance(v, str): return v.strip().upper()
    try:
        i = int(v)
        return COUPON_INT_TO_LABEL.get(i, str(i))
    except Exception:
        return str(v)

SIMPLE_ACTION_TO_LABEL = {
    "by_percent": "% off por ítem",
    "by_fixed": "Monto fijo off por ítem",
    "cart_fixed": "Monto fijo off en carrito",
    "buy_x_get_y": "Compra X y llévate Y",
    "thecheapest": "Descuento al ítem más barato",
    "each_n_percent": "% off cada N-ésimo ítem",
    "each_n_fixed": "Monto fijo off cada N-ésimo ítem",
}
def norm_simple_action(v):
    if not v: return ""
    v = str(v).strip()
    return SIMPLE_ACTION_TO_LABEL.get(v, v)

# ====== EXPORT ======
def export_excel(rules):
    rows = []
    unknown_website_ids = set()

    for r in rules:
        wids = r.get("website_ids", []) or []
        website_ids_csv = ",".join(map(str, wids))
        website_names = []
        for x in wids:
            name = WEBSITE_MAP.get(int(x))
            if name is None:
                unknown_website_ids.add(int(x))
                name = str(x)
            website_names.append(name)
        website_names_csv = ",".join(website_names)

        coupon_raw = r.get("coupon_type")
        coupon_label = norm_coupon_type(coupon_raw)

        simple_raw = r.get("simple_action", "")
        simple_label = norm_simple_action(simple_raw)

        rows.append({
            "rule_id": r.get("rule_id", ""),
            "name": r.get("name", ""),
            "description": r.get("description", ""),
            "website_ids": website_ids_csv,
            "website_names": website_names_csv,
            "uses_per_customer": r.get("uses_per_customer", ""),
            "from_date": r.get("from_date", ""),
            "is_active": r.get("is_active", ""),
            "discount_amount": r.get("discount_amount", ""),
            "apply_to_shipping": r.get("apply_to_shipping", ""),
            "times_used": r.get("times_used", ""),
            "coupon_type": coupon_raw,
            "coupon_type_label": coupon_label,
            "simple_action": simple_raw,
            "simple_action_label": simple_label,
            "use_auto_generation": r.get("use_auto_generation", ""),
        })

    cols = [
        "rule_id", "name", "description",
        "website_ids", "website_names",
        "uses_per_customer", "from_date", "is_active",
        "discount_amount", "apply_to_shipping", "times_used",
        "coupon_type", "coupon_type_label",
        "simple_action", "simple_action_label",
        "use_auto_generation"
    ]
    df = pd.DataFrame(rows, columns=cols)

    for c in ["rule_id", "uses_per_customer", "times_used", "discount_amount"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    if "is_active" in df:
        df["is_active"] = df["is_active"].map(lambda x: bool(x) if pd.notna(x) else None)

    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M") if ADD_TIMESTAMP else ""
    fname = f"sales_rules_export.xlsx"
    # etiqueta de fecha en nombre{('_' + stamp) if stamp else ''}
    df.to_excel(fname, index=False)
    print(f"[ACCION] Archivo Excel generado: '{fname}'")

    if unknown_website_ids:
        print(f"[INFO] IDs de website no mapeados en WEBSITE_MAP: {sorted(unknown_website_ids)}")

def main():
    try:
        rules = fetch_all_rules()
        if not rules:
            print("[ADVERTENCIA] No se encontraron reglas o hubo error.")
            return
        export_excel(rules)
    except Exception as e:
        print(f"[ERROR] {e}")

if __name__ == "__main__":
    main()
