# transformer_report.py — sube a SQL (sin Excel) y borra por rango
import os, json, gzip, re, html, unicodedata, ast
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from typing import List, Dict, Any, Tuple
from urllib.parse import quote_plus  
import pandas as pd
from sqlalchemy import create_engine, text, inspect
import sys
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# ================== CONFIG ==================
OUTPUT_FOLDER = os.getenv('OUTPUT_FOLDER', '.')
ROW_MODE = os.getenv('ROW_MODE', 'por_simple')            # nos quedamos en por_simple
USE_SQL = os.getenv('USE_SQL', 'true').lower() == 'true'  # default true: sube a SQL
SQL_DELETE_BY = os.getenv('SQL_DELETE_BY','created')      # created | updated
INCLUDE_INGESTED_AT = os.getenv('INCLUDE_INGESTED_AT','false').lower() == 'true'  # si quieres mandar _ingested_at desde Python

server   = 'localhost'                     # <-- AJUSTA
database = 'Digital_Impact_Reportes'       # <-- AJUSTA
driver   = 'ODBC Driver 17 for SQL Server' # ó 'ODBC Driver 18 for SQL Server'
MSSQL_TABLE = 'dbo.Ventas_Solidez_Magento_2025'


# (opcionales) enriquecer con reglas / locales si sigues usándolos
SALES_RULES_PATH = os.getenv('SALES_RULES_PATH', '')
LOCALES_PATH = os.getenv('LOCALES_PATH', '')

# ================== Helpers ==================
PAT_TKT = re.compile(r"(TKT-\d+|B\d{1,3}|F\d{1,3})")

def _json_lines(paths: List[str]):
    for path in paths:
        opener = gzip.open if path.endswith('.gz') else open
        with opener(path, 'rt', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                yield json.loads(line)

def cargar_mapeo_reglas(path_excel):
    if not path_excel or not os.path.exists(path_excel): return {}, {}
    df = pd.read_excel(path_excel, dtype={'rule_id':'Int64'})
    cols_lower = {c.lower(): c for c in df.columns}
    c_id = cols_lower.get('rule_id','rule_id'); c_nm = cols_lower.get('name','name'); c_ds = cols_lower.get('description','description')
    df = df[[c_id,c_nm,c_ds]].dropna(subset=[c_id]); df[c_id] = df[c_id].astype('Int64')
    name_map, desc_map = {}, {}
    for _,r in df.iterrows():
        rid = r[c_id]
        if pd.isna(rid): continue
        rid = int(rid)
        name_map[rid] = str(r[c_nm]) if pd.notna(r[c_nm]) else ''
        desc_map[rid] = str(r[c_ds]) if pd.notna(r[c_ds]) else ''
    return name_map, desc_map

def parse_rule_ids(value):
    if value is None: return []
    s = str(value).strip()
    if s in ('', '0'): return []
    out, seen = [], set()
    for p in [x.strip() for x in s.split(',') if x.strip()]:
        try:
            i = int(float(p)) if '.' in p else int(p)
            if i!=0 and i not in seen:
                out.append(i); seen.add(i)
        except: pass
    return out

def cargar_mapeo_locales(path_file):
    if not path_file or not os.path.exists(path_file): return {}
    try:
        df = pd.read_csv(path_file, sep=None, engine='python', dtype=str, encoding='utf-8-sig')
    except Exception:
        try:
            df = pd.read_csv(path_file, sep=';', dtype=str, encoding='utf-8-sig')
        except Exception:
            df = pd.read_csv(path_file, sep=',', dtype=str, encoding='utf-8-sig')
    cols_lower = {c.lower(): c for c in df.columns}
    c_id = cols_lower.get('id_tienda'); c_nm = cols_lower.get('local')
    if not c_id or not c_nm: return {}
    df = df[[c_id,c_nm]].dropna(subset=[c_id])
    df[c_id] = df[c_id].astype(str).str.strip(); df[c_nm] = df[c_nm].astype(str).str.strip()
    mp = {}
    for _,r in df.iterrows():
        k, v = r[c_id], r[c_nm]
        if not k or not v: continue
        mp[k] = v
        if k.isdigit(): mp[str(int(k))] = v
    return mp

def get_nested_value(data, path):
    keys = re.split(r'\.(?![^\[]*\])', path)
    for key in keys:
        if isinstance(data, list):
            m = re.match(r"(\w+)\[(\d+)\]", key)
            if m:
                key, idx = m.groups()
                data = [item.get(key) for item in data if key in item]
                try: data = data[int(idx)]
                except: return None
            else:
                return None
        elif isinstance(data, dict):
            m = re.match(r"(\w+)\[(\d+)\]", key)
            if m:
                key, idx = m.groups(); data = data.get(key, [])
                try: data = data[int(idx)]
                except: return None
            else:
                data = data.get(key)
        else:
            return None
    return data

def buscar_en_lista_por_clave(lista, clave):
    if not isinstance(lista, list): return None
    for e in lista:
        if isinstance(e, dict) and e.get('key') == clave:
            return e.get('value')
    return None

def orden_tiene_split_por_source(orden):
    allocs = map_sku_source_allocs(orden)
    return bool(allocs)  # True si hay al menos un sku con sources

def _build_shipping_cents_map(orden, simples, sku_allocs):
    """
    Devuelve dict clave (parent_id, source_id, sku) -> shipping_cents
    con reparto: pedido→source (parejo), source→parent (peso=qty), parent→sku (peso=qty).
    """
    # shipping total del pedido (en centavos)
    total_shipping = _to_float(orden.get('base_shipping_amount'), None)
    if total_shipping is None:
        total_shipping = _to_float(orden.get('payment', {}).get('base_shipping_amount'), 0.0)
    total_cents = int(round((total_shipping or 0.0) * 100))

    # si no hay allocs por source, no hacemos mapa (que el caller use fallback por parent)
    sources = set()
    for allocs in sku_allocs.values():
        for a in allocs:
            sid = normalizar_source_id(a.get('source'))
            if sid != '': sources.add(sid)
    if not sources:
        return {}

    # Paso 1: repartir por source (parejo)
    sources = sorted(list(sources))  # estable
    ship_by_source = _alloc_even_cents(total_cents, sources)

    # Mapeos de apoyo: sku -> parent_id (por simplicidad, primer match)
    sku_to_parent = {}
    # Nota: si hay el mismo sku repetido con distintos parent en la orden (raro), 
    # podrías sofisticarlo a (sku,parent). Para 99% de casos, esto alcanza.
    for it in simples:
        sku_to_parent.setdefault(it.get('sku'), it.get('parent_item_id'))

    # Paso 2: pesos por parent dentro de cada source
    # qty_by_parent[(source, parent)] = suma qty del JSON para los SKU de ese parent y ese source
    from collections import defaultdict
    qty_by_parent = defaultdict(int)
    for sku, allocs in sku_allocs.items():
        p = sku_to_parent.get(sku)
        if p is None: 
            continue
        for a in allocs:
            sid = normalizar_source_id(a.get('source'))
            q   = int(round(_to_float(a.get('qty'), 0) or 0))
            if sid != '' and q > 0:
                qty_by_parent[(sid, p)] += q

    # shipping por (source, parent)
    ship_parent = {}
    for sid in sources:
        # padres vivos en este source
        ps = [p for (s,p) in qty_by_parent.keys() if s == sid]
        if not ps:
            ship_parent.update({(sid,p): 0 for p in set([it.get('parent_item_id') for it in simples])})
            continue
        weights = [qty_by_parent[(sid,p)] for p in ps]
        alloc   = _alloc_by_weights_cents(ship_by_source[sid], weights, ps)
        # corrección de redondeo
        diff = ship_by_source[sid] - sum(alloc.values())
        if diff != 0 and ps:
            alloc[ps[-1]] = alloc.get(ps[-1], 0) + diff
        for p in ps:
            ship_parent[(sid,p)] = alloc.get(p, 0)

    # Paso 3: pesos por sku dentro de cada (source, parent)
    qty_by_sku = defaultdict(int)   # clave (sid, parent, sku)
    for sku, allocs in sku_allocs.items():
        p = sku_to_parent.get(sku)
        if p is None: 
            continue
        for a in allocs:
            sid = normalizar_source_id(a.get('source'))
            q   = int(round(_to_float(a.get('qty'), 0) or 0))
            if sid != '' and q > 0:
                qty_by_sku[(sid, p, sku)] += q

    ship_sku = {}
    from math import isfinite
    # Para cada (sid, parent) reparte a sus skus
    grp = {}
    for (sid,p,sku), q in qty_by_sku.items():
        grp.setdefault((sid,p), []).append((sku, q))
    for (sid,p), lst in grp.items():
        total = ship_parent.get((sid,p), 0)
        if total <= 0:
            for sku,_ in lst:
                ship_sku[(p, sid, sku)] = 0
            continue
        skus   = [sku for sku,_ in lst]
        weights= [q for _,q in lst]
        alloc  = _alloc_by_weights_cents(total, weights, skus)
        # corrección de redondeo intra-(sid,p)
        diff = total - sum(alloc.values())
        if diff != 0 and skus:
            alloc[skus[-1]] = alloc.get(skus[-1], 0) + diff
        for sku in skus:
            ship_sku[(p, sid, sku)] = alloc.get(sku, 0)

    return ship_sku  # clave (parent_id, source_id, sku) -> cents

def extraer_valor_con_ruta(orden, ruta):
    if ruta.startswith('status_histories'):
        for entry in orden.get('status_histories', []) or []:
            c = entry.get('comment')
            if c and PAT_TKT.search(c):
                return c
        return None
    elif 'receiver_name' in ruta:
        try:
            fn = get_nested_value(orden, 'extension_attributes.shipping_assignments[0].shipping.address.firstname') or ''
            ln = get_nested_value(orden, 'extension_attributes.shipping_assignments[0].shipping.address.lastname') or ''
            return f"{fn.strip()} {ln.strip()}".strip() or None
        except: return None
    elif 'payment_additional_info[' in ruta:
        key = ruta.split('[')[-1].rstrip(']')
        lst = orden.get('extension_attributes', {}).get('payment_additional_info', [])
        val = buscar_en_lista_por_clave(lst, key)
        if val is None and key == 'payment_0_paid_amount':
            for alt in ('payment_0_amount_paid','amount_paid','paid_amount'):
                val = buscar_en_lista_por_clave(lst, alt)
                if val not in (None, '', 0, '0'): break
        return val
    else:
        return get_nested_value(orden, ruta)

def map_sku_source_allocs(order_json):
    """
    Devuelve dict: sku -> lista de {'source': <id>, 'qty': <int>}
    parseando extension_attributes.additional_information (string o json).
    """
    out = {}
    info = order_json.get('extension_attributes', {}).get('additional_information')
    if not info:
        return out
    if isinstance(info, str):
        try:
            info = json.loads(info)
        except Exception:
            return out
    for entry in (info or []):
        src = entry.get('source')
        for it in (entry.get('items') or []):
            sku = it.get('sku')
            qty = it.get('qty')
            if not sku:
                continue
            try:
                q = int(round(float(qty or 0)))
            except Exception:
                q = 0
            if q <= 0:
                continue
            out.setdefault(sku, []).append({'source': src, 'qty': q})
    return out

def _delete_by_increment_ids(conn, schema: str, table: str, incr_ids: list[str]) -> None:
    """DELETE por chunks sobre la columna 'id' (increment_id en Magento)."""
    if not incr_ids:
        return
    from sqlalchemy import text
    CHUNK = 400  # menos parámetros por ser NVARCHAR
    i = 0
    while i < len(incr_ids):
        chunk = incr_ids[i:i+CHUNK]
        # genera :p0,:p1,... y pasa dict de str
        params = {f"p{k}": str(v) for k, v in enumerate(chunk)}
        placeholders = ",".join(f":p{k}" for k in range(len(chunk)))
        sql = text(f"DELETE FROM {schema}.{table} WHERE id IN ({placeholders});")
        conn.execute(sql, params)
        i += CHUNK

def normalizar_source_id(v):
    if v is None: return ''
    s = str(v).strip()
    if s=='' or s.lower()=='nan': return ''
    if s.isdigit(): return str(int(s))
    return s

def _to_float(x, default=None):
    if x is None: return default
    if isinstance(x,(int,float)): return float(x)
    try:
        s = str(x).strip()
        if s=='': return default
        return float(s)
    except: return default

# ---- Texto → flag pago_confirmado ----
_PAGO_OK_KEYWORDS = [
    'orden fue pagada',
    'orden sincronizada erp',
    'pago aprobado',
    'captured amount',
    'pago recibido exitosamente',
    'pago confirmado',
]
def _normalize_txt(s: str) -> str:
    if s is None: return ''
    s = html.unescape(str(s))
    s = re.sub(r'<[^>]+>', ' ', s)
    s = s.lower()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r'\s+', ' ', s).strip()
    return s

def _pago_confirmado_desde_status(orden: dict) -> int:
    sh = orden.get('status_histories') or []
    for e in sh:
        c = e.get('comment')
        if not c: continue
        n = _normalize_txt(c)
        for kw in _PAGO_OK_KEYWORDS:
            if kw in n: return 1
    return 0

# ---- Shipping alloc helpers (cuando el item simple viene en 0 y hay que prorratear) ----
def _alloc_even_cents(total_cents, keys):
    n = len(keys)
    if n==0: return {}
    base = total_cents // n; resid = total_cents - base*n
    return {k: base + (1 if i<resid else 0) for i,k in enumerate(keys)}

def _alloc_by_weights_cents(total_cents, weights, keys):
    if not keys or not weights or sum(weights) <= 0:
        return {k:0 for k in keys}
    wsum = sum(weights)
    quotas = [(total_cents * w)/wsum for w in weights]
    floors = [int(Decimal(q).to_integral_value(rounding=ROUND_FLOOR)) for q in quotas]
    assigned = sum(floors); resid = total_cents - assigned
    fracs = sorted([(i, quotas[i]-floors[i]) for i in range(len(keys))], key=lambda x: x[1], reverse=True)
    chosen = {idx for idx,_ in fracs[:resid]}
    return {keys[i]: floors[i] + (1 if i in chosen else 0) for i in range(len(keys))}

# ================== Mapeo de campos ==================
CAMPOS_ORDEN = {
    "purchase_point": "store_name",
    "id": "increment_id",
    "order_id": "entity_id",
    "created": "created_at",
    "updated": "updated_at",
    "order_state": "state",
    "order_status": "status",
    "shipping_and_handling_information": "shipping_description",
    "courrier": "extension_attributes.shipping_assignments[0].shipping.method",
    "customer_id": "customer_id",
    "customer_name": "customer_firstname",
    "customer_last_name": "customer_lastname",
    "document": "extension_attributes.document",
    "tipo_de_documento": "extension_attributes.type_document",
    "razon_social": "extension_attributes.razon_social",
    "email": "customer_email",
    "phone": "extension_attributes.shipping_assignments[0].shipping.address.telephone",
    "address_id": "extension_attributes.shipping_assignments[0].shipping.address.customer_address_id",
    "calle": "extension_attributes.shipping_assignments[0].shipping.address.street",
    "numero": "extension_attributes.numero",
    "depto": "extension_attributes.depto",
    "departamento": "extension_attributes.shipping_assignments[0].shipping.address.region",
    "provincia": "extension_attributes.province",
    "distrito": "extension_attributes.district",
    "address_type": "extension_attributes.shipping_assignments[0].shipping.address.address_type",
    "receiver_name": "extension_attributes.shipping_assignments[0].shipping.address.firstname extension_attributes.shipping_assignments[0].shipping.address.lastname",
    "postal_code": "extension_attributes.shipping_assignments[0].shipping.address.postcode",
    "last_change_date": "extension_attributes.shipping_assignments[0].items[0].updated_at",
    "promo_id": "applied_rule_ids",
    "coupon_code": "coupon_code",
    "discount_name": "",
    "discount_description": "",
    "payment_method": "payment.method",
    "pago_confirmado": "",
    "cuotas": "extension_attributes.payment_additional_info[card_installments]",
    "tarjeta_de_credito_o_debito": "payment.cc_type",
    "transaction_id": "ext_order_id",
    "numero_de_tarjeta": "extension_attributes.payment_additional_info[card_number]",
    "card_last_digits": "extension_attributes.payment_additional_info[payment_0_card_number]",
    "invoice_date": "items[0].updated_at",
    "paymentid": "payment.entity_id",
    "saleschannel": "extension_attributes.origin_of_sale",
    "invoice": "status_histories.TKT_comment",
    "person_receiver_option": "extension_attributes.person_receiver_option",
    "person_receiver_full_name": "extension_attributes.person_receiver_full_name",
    "person_receiver_phone_number": "extension_attributes.person_receiver_phone_number",
    "payment_value": "extension_attributes.payment_additional_info[payment_0_paid_amount]",
    "grand_total_purchased": "grand_total"
}

# columnas destino en SQL (sin _ingested_at)
SQL_TARGET_COLS = [
    'id','order_id','created','order_state','order_status','purchase_point',
    'shipping_and_handling_information','courrier','customer_id',
    'customer_name','customer_last_name','document','tipo_de_documento',
    'razon_social','email','phone','address_id','calle','numero','depto',
    'departamento','provincia','distrito','address_type','receiver_name',
    'postal_code','last_change_date','promo_id','coupon_code','discount_name',
    'discount_description','payment_method','pago_confirmado','cuotas',
    'tarjeta_de_credito_o_debito','transaction_id','numero_de_tarjeta',
    'card_last_digits','invoice_date','paymentid','saleschannel','invoice',
    'person_receiver_option','person_receiver_full_name','person_receiver_phone_number',
    'payment_value','grand_total_purchased','qty_confirmed','sku','product_name',
    'source_id','source_Name','original_price','price','row_total',
    'total_shipping_charges','coupon_discount','price_discount','qty_ordered',
    'grand_total_item','_ingested_at','updated',
]

# ================== Core Transform ==================
def transformar(paths_ndjson: List[str]) -> str:
    # 1) Mapeos (opcionales)
    name_map, desc_map = cargar_mapeo_reglas(SALES_RULES_PATH)
    locales_map = cargar_mapeo_locales(LOCALES_PATH)

    filas: List[Dict[str, Any]] = []

    for orden in _json_lines(paths_ndjson):
        # ---------- Datos del pedido ----------
        datos_orden = {}
        for col, ruta in CAMPOS_ORDEN.items():
            # estos los calculamos nosotros más abajo
            if col in ('discount_name', 'discount_description', 'pago_confirmado'):
                continue
            datos_orden[col] = extraer_valor_con_ruta(orden, ruta)

        # Reglas/promos
        applied_ids = parse_rule_ids(datos_orden.get('promo_id'))
        dn = [name_map.get(i, '') for i in applied_ids if name_map.get(i, '')]
        dd = [desc_map.get(i, '') for i in applied_ids if desc_map.get(i, '')]
        datos_orden['discount_name'] = ','.join(dn)
        datos_orden['discount_description'] = ','.join(dd)

        # Flag pago_confirmado desde status_histories
        datos_orden['pago_confirmado'] = _pago_confirmado_desde_status(orden)

        # ---------- Ítems ----------
        items   = orden.get('items') or []
        padres  = [it for it in items if it.get('product_type') == 'configurable']
        simples = [it for it in items if it.get('product_type') == 'simple']
        parents_index = {pd.get('item_id'): pd for pd in padres}

        # total qty por parent (para shipping proporcional)
        total_qty_parent: Dict[Any, int] = {}
        for ch in simples:
            pid = ch.get('parent_item_id')
            if not pid:
                continue
            q = _to_float(ch.get('qty_ordered') or ch.get('qty'), 0) or 0
            total_qty_parent[pid] = total_qty_parent.get(pid, 0) + int(round(q))

        # shipping total del pedido y prorrateo a padres (simple por qty)
        total_shipping = _to_float(orden.get('base_shipping_amount'), None)
        if total_shipping is None:
            total_shipping = _to_float(orden.get('payment', {}).get('base_shipping_amount'), 0.0)
        total_shipping_cents = int(round((total_shipping or 0.0) * 100))
        shipping_cents_por_parent: Dict[Any, int] = {pd.get('item_id'): 0 for pd in padres}

        if padres and not orden_tiene_split_por_source(orden):  # ← agrega este guard (ver abajo)
            weights = {pid: (total_qty_parent.get(pid, 0) or 1) for pid in shipping_cents_por_parent.keys()}
            keys = list(weights.keys())
            w    = [weights[k] for k in keys]
            reparto = _alloc_by_weights_cents(total_shipping_cents, w, keys)
            shipping_cents_por_parent.update(reparto)
            # ajuste por redondeo
            diff = total_shipping_cents - sum(shipping_cents_por_parent.values())
            if diff != 0 and keys:
                shipping_cents_por_parent[keys[-1]] += diff

        # Asignaciones por source→SKU (JSON additional_information)
        sku_allocs = map_sku_source_allocs(orden)

        # Shipping por (parent, source, sku) — PRIMARIO cuando hay split
        ship_map = _build_shipping_cents_map(orden, simples, sku_allocs)

        # Recorremos simples (ROW_MODE recomendado)
        iterable_items = simples if ROW_MODE == 'por_simple' else padres
        for item in iterable_items:
            pid = item.get('parent_item_id') if ROW_MODE == 'por_simple' else item.get('item_id')
            parent_ship_cents = int(shipping_cents_por_parent.get(pid, 0) or 0)
            parent_qty_total  = max(1, int(total_qty_parent.get(pid, 0)))
            sku = item.get('sku')


            # asignaciones por source para ESTE sku
            allocs = sku_allocs.get(sku, [])
            if not allocs:
                # sin additional_information → una sola fila con toda la qty
                q_item = _to_float(item.get('qty_ordered') or item.get('qty'), 0) or 0
                allocs = [{'source': item.get('source_id'), 'qty': int(round(q_item))}]

            # Una fila por cada (source, qty)
            for a in allocs:
                qty_row = int(round(_to_float(a.get('qty'), 0) or 0))
                if qty_row <= 0:
                    continue

                # shipping por fila proporcional a la qty dentro del parent
                sid_norm = normalizar_source_id(a.get('source') if a.get('source') is not None else item.get('source_id'))

                # 1) Shipping guiado por (parent, source, sku) si existe:
                ship_cents = ship_map.get((pid, sid_norm, sku))
                if ship_cents is not None:
                    ship_row = ship_cents / 100.0
                else:
                    if parent_qty_total <= 0 and parent_ship_cents > 0:
                        print(f"[WARN] Fallback shipping con parent_qty_total=0 en order_id={datos_orden.get('order_id')} pid={pid}")
                    ship_row = (parent_ship_cents * (qty_row / float(parent_qty_total))) / 100.0

                fila = datos_orden.copy()

                # descuento unitario (si existe)
                try:
                    orig  = _to_float(item.get('base_original_price'))
                    price = _to_float(item.get('base_price'))
                    fila['price_discount'] = round(orig - price, 2) if orig is not None and price is not None else None
                except:
                    fila['price_discount'] = None

                # descuento prorrateado por qty (si aplica)
                disc_total      = _to_float(item.get('base_discount_amount'), 0.0) or 0.0
                qty_item_total  = max(1, int(round(_to_float(item.get('qty_ordered') or item.get('qty'), 0) or 0)))
                ratio = qty_row / float(qty_item_total)
                # row_total proporcional
                row_total_item = _to_float(item.get('base_row_total'), 0.0) or 0.0
                fila['row_total'] = round(row_total_item * ratio, 2)
                disc_row        = round(disc_total * (qty_row / qty_item_total), 2) if disc_total else 0.0

                sid = a.get('source') if a.get('source') is not None else item.get('source_id')
                try:
                    sid = int(float(sid)) if sid not in (None, '') else 0
                except Exception:
                    sid = 0

                fila.update({
                    'qty_confirmed': qty_row,
                    'sku': sku,
                    'product_name': item.get('name'),
                    'source_id': sid,  # <--- ya normalizado a int, nunca None
                    'original_price': item.get('base_original_price'),
                    'price': item.get('base_price'),
                    'total_shipping_charges': ship_row,
                    'coupon_discount': disc_row,
                    'qty_ordered': item.get('qty_ordered')
                })

                # source_Name por mapeo local
                sid_key = normalizar_source_id(fila.get('source_id', ''))
                fila['source_Name'] = locales_map.get(sid_key, '')

                # total por fila
                try:
                    price_u = _to_float(fila.get('price'), 0.0) or 0.0
                    fila['grand_total_item'] = round(price_u * qty_row + ship_row - disc_row, 2)
                except:
                    fila['grand_total_item'] = None

                filas.append(fila)

    # ---------- DataFrame + saneos + SQL ----------
    df = pd.DataFrame(filas)

    # Listas en "calle" → string
    if 'calle' in df.columns:
        def _fix_calle(v):
            if isinstance(v, list): return ' // '.join([str(x) for x in v])
            if isinstance(v, str) and v.strip().startswith('['):
                try:
                    val = json.loads(v)
                except Exception:
                    try:
                        import ast
                        val = ast.literal_eval(v)
                    except Exception:
                        val = v
                if isinstance(val, list): return ' // '.join([str(x) for x in val])
            return v
        df['calle'] = df['calle'].apply(_fix_calle)

    # booleans a BIT
    if 'person_receiver_option' in df.columns:
        df['person_receiver_option'] = df['person_receiver_option'].map(
            lambda v: 1 if str(v).strip().lower() in ('1','true','t','si','sí') else 0
        )

    # tipos
    for c in ['order_id','paymentid','cuotas']:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce').astype('Int64')
    for c in ['payment_value','grand_total_purchased','qty_confirmed','original_price','price',
              'row_total','total_shipping_charges','coupon_discount','price_discount',
              'qty_ordered','grand_total_item']:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
    for c in ['created','updated','invoice_date','last_change_date']:
        if c in df.columns: df[c] = pd.to_datetime(df[c], errors='coerce')

    # Orden de columnas para SQL (ajusta si tu lista difiere)
    for col in SQL_TARGET_COLS:
        if col not in df.columns: df[col] = None
    df['_ingested_at'] = datetime.utcnow() 
    df = df[[c for c in SQL_TARGET_COLS if c in df.columns]]
    

    if USE_SQL and not df.empty:
        # Normaliza tipos clave antes de tocar SQL
        if 'pago_confirmado' in df.columns:
            df['pago_confirmado'] = df['pago_confirmado'].fillna(0).astype(int)
        if 'source_id' in df.columns:
            # source_id siempre entero; usa 0 si viene vacío
            df['source_id'] = pd.to_numeric(df['source_id'], errors='coerce').fillna(0).astype('Int64')

        engine = create_engine(
            f"mssql+pyodbc://@{server}/{database}?trusted_connection=yes&driver={driver}",
            fast_executemany=True
        )
        schema, table = MSSQL_TABLE.split('.', 1) if '.' in MSSQL_TABLE else ('dbo', MSSQL_TABLE)

        # Truncamiento seguro de textos largos (usa tu función si ya la tienes)
        # Ejemplo: limitar 'invoice' a 500 chars
        if 'invoice' in df.columns:
            long_mask = df['invoice'].astype(str).str.len() > 4000
            if long_mask.any():
                print(f"[WARN] 'invoice' > 4000 chars en {long_mask.sum()} filas.")

                # --- Validador de longitudes vs. esquema SQL (pegar antes de to_sql) ---
        SQL_MAXLEN = {
            # De tu sp_help (ajusta si cambiaste el esquema)
            'id': 40,
            'order_status': 30,
            'purchase_point': 4000,  # nvarchar(max) -> si realmente es MAX, puedes omitirlo
            'shipping_and_handling_information': 4000,
            'courrier': 120,
            'customer_name': 150,
            'customer_last_name': 150,
            'document': 60,
            'tipo_de_documento': 60,
            'razon_social': 200,
            'email': 200,
            'phone': 60,
            'address_id': 60,
            'calle': 4000,
            'numero': 200,
            'depto': 500,
            'departamento': 120,
            'provincia': 120,
            'distrito': 120,
            'address_type': 60,
            'receiver_name': 800,
            'postal_code': 40,
            'promo_id': 200,
            'coupon_code': 120,
            'discount_name': 4000,
            'discount_description': 4000,
            'payment_method': 80,
            'cuotas': 60,
            'tarjeta_de_credito_o_debito': 60,
            'transaction_id': 120,
            'numero_de_tarjeta': 120,
            'card_last_digits': 60,
            'saleschannel': 80,
            'invoice': 4000,
            'person_receiver_option': 120,
            'person_receiver_full_name': 200,
            'person_receiver_phone_number': 60,
            'sku': 120,
            'product_name': 400,
            'source_id': 40,        # aunque sea numérico, lo validamos igual (se envía como texto)
            'source_Name': 200,
            'order_state': 100,
        }

        def _strlen_safe(x):
            try:
                return len(str(x)) if x is not None else 0
            except Exception:
                return 0

        viol = []
        for col, maxlen in SQL_MAXLEN.items():
            if col in df.columns:
                s = df[col].astype(str)
                mask = s.map(_strlen_safe) > maxlen
                if mask.any():
                    # Muestra algunas violaciones por columna
                    sample = df.loc[mask, [c for c in ['id','order_id','sku',col] if c in df.columns]].head(10)
                    print(f"[LEN][{col}] > {maxlen} chars — {mask.sum()} filas. Ejemplos:")
                    print(sample.to_string(index=False))
                    viol.append(col)

        if viol:
            raise SystemExit(f"[ABORT] Hay columnas que exceden el largo del esquema: {', '.join(viol)}")
        # --- Fin validador ---

        with engine.begin() as conn:
            # 1) Borrado idempotente por 'id' (increment_id)
            incr_ids = df['id'].dropna().astype(str).str.strip().unique().tolist()
            _delete_by_increment_ids(conn, schema, table, incr_ids)
            print(f"[SQL] DELETE por id (increment_id) -> {len(incr_ids)} ids")

            # 2) Inserción
            df.to_sql(table, conn, schema=schema, if_exists='append', index=False, chunksize=1000)
            print(f"[SQL] INSERT {len(df)} filas en {schema}.{table}")

    return f"OK: {len(df)} filas"

if __name__=='__main__':
    import sys, glob
    args = sys.argv[1:] or []
    paths = []
    for a in args:
        paths += glob.glob(a)
    if not paths:
        ck = os.path.join('./state', 'checkpoint.json')
        if os.path.exists(ck):
            with open(ck,'r',encoding='utf-8') as f:
                j = json.load(f); candidate = os.path.join('./data/raw', j.get('file',''))
                if os.path.exists(candidate): paths = [candidate]
    if not paths:
        raise SystemExit('No hay archivos NDJSON para procesar.')
    out = transformar(paths)
    print(out)

