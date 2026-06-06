# transformer_report.py — versión saneada
import os, json, gzip, re
from datetime import datetime
from decimal import Decimal, ROUND_FLOOR
from typing import List, Dict, Any
import html, unicodedata

import pandas as pd
from sqlalchemy import create_engine

# ================ CONFIG
OUTPUT_FOLDER = os.getenv('OUTPUT_FOLDER', '.')
SALES_RULES_PATH = os.getenv('SALES_RULES_PATH', '')  # opcional
LOCALES_PATH = os.getenv('LOCALES_PATH', '')          # opcional
ROW_MODE = os.getenv('ROW_MODE', 'por_simple')        # por_simple | por_configurable

USE_SQL = os.getenv('USE_SQL', 'false').lower() == 'true'
MSSQL_CONN = os.getenv('MSSQL_CONN', 'mssql+pyodbc://@localhost/Digital_Impact_Reportes?trusted_connection=yes&driver=ODBC Driver 17 for SQL Server')
MSSQL_TABLE = os.getenv('MSSQL_TABLE','dbo.Ventas_Solidez_Magento')

PAT_TKT = re.compile(r"(TKT-\d+|B\d{1,3}|F\d{1,3})")

# ---------- IO helpers ----------
def _json_lines(paths: List[str]):
    for path in paths:
        opener = gzip.open if path.endswith('.gz') else open
        with opener(path, 'rt', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line: continue
                yield json.loads(line)

# ---------- Reglas / Locales ----------
def cargar_mapeo_reglas(path_excel):
    if not path_excel or not os.path.exists(path_excel):
        return {}, {}
    df = pd.read_excel(path_excel, dtype={'rule_id':'Int64'})
    cols_lower = {c.lower(): c for c in df.columns}
    c_id = cols_lower.get('rule_id','rule_id'); c_nm = cols_lower.get('name','name'); c_ds = cols_lower.get('description','description')
    df = df[[c_id,c_nm,c_ds]].dropna(subset=[c_id])
    df[c_id] = df[c_id].astype('Int64')
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

# ---------- nested helpers ----------
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

# Normalización de texto (minúsculas, sin HTML, sin acentos)
def _normalize_txt(s: str) -> str:
    if s is None:
        return ''
    # quita HTML y entidades
    s = html.unescape(str(s))
    s = re.sub(r'<[^>]+>', ' ', s)
    # lower + quitar acentos
    s = s.lower()
    s = unicodedata.normalize('NFD', s)
    s = ''.join(ch for ch in s if not unicodedata.combining(ch))
    # espacios
    s = re.sub(r'\s+', ' ', s).strip()
    return s

# Lista de frases que disparan 1
_PAGO_OK_KEYWORDS = [
    'orden fue pagada',
    'orden sincronizada erp',
    'pago aprobado',
    'captured amount',
    'pago recibido exitosamente',
    'pago confirmado',
    'importe capturado'
]

def _pago_confirmado_desde_status(orden: dict) -> int:
    """Devuelve 1 si algún comment en status_histories contiene las frases clave (normalizadas), si no 0."""
    sh = orden.get('status_histories') or []
    for e in sh:
        c = e.get('comment')
        if not c:
            continue
        n = _normalize_txt(c)
        for kw in _PAGO_OK_KEYWORDS:
            if kw in n:
                return 1
    return 0

def obtener_source_y_qty_desde_additional_info(order_json, sku):
    try:
        info = order_json.get('extension_attributes',{}).get('additional_information')
        if not info: return None, None
        if isinstance(info, str): info = json.loads(info)
        for entry in info:
            source = entry.get('source')
            for it in entry.get('items', []) or []:
                if it.get('sku') == sku:
                    return source, it.get('qty')
        return None, None
    except: return None, None

def normalizar_source_id(v):
    if v is None: return ''
    s = str(v).strip()
    if s=='' or s.lower()=='nan': return ''
    if s.isdigit(): return str(int(s))
    return s

def _to_float(x, default=0.0):
    """Convierte a float de forma segura. Si default es None, puede devolver None."""
    if x is None:
        return default  # ← evita float(None)
    if isinstance(x, (int, float)):
        return float(x)
    try:
        s = str(x).strip()
        if s == '':
            return default
        return float(s)
    except Exception:
        return default

# ---------- shipping alloc helpers ----------
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
    assigned = sum(floors)
    resid = total_cents - assigned
    fracs = sorted([(i, quotas[i]-floors[i]) for i in range(len(keys))], key=lambda x: x[1], reverse=True)
    chosen = {idx for idx,_ in fracs[:resid]}
    return {keys[i]: floors[i] + (1 if i in chosen else 0) for i in range(len(keys))}

# ---------- derive prices for simples ----------
def derive_item_values(child: dict, parent: dict | None, qty_used: float):
    """
    Devuelve (orig, price, row_total, discount, price_discount) para un ítem simple.
    Si el hijo trae 0, toma valores del padre y prorratea por qty.
    """
    parent_qty = _to_float(parent.get('qty_ordered')) if parent else 0.0

    # ORIGINAL PRICE
    orig = child.get('base_original_price')
    if not _to_float(orig) and parent:
        orig = parent.get('base_original_price')

    # PRICE UNIT
    price = child.get('base_price')
    if not _to_float(price) and parent:
        price = parent.get('base_price')
        if not _to_float(price) and _to_float(parent.get('base_row_total')) and parent_qty:
            price = _to_float(parent.get('base_row_total')) / parent_qty

    # ROW TOTAL
    row_total = child.get('base_row_total')
    if not _to_float(row_total) and _to_float(price) and _to_float(qty_used):
        row_total = _to_float(price) * _to_float(qty_used)

    # DISCOUNT
    discount = child.get('base_discount_amount')
    if not _to_float(discount) and parent and _to_float(parent.get('base_discount_amount')) and parent_qty:
        discount = round(_to_float(parent.get('base_discount_amount')) * (_to_float(qty_used) / parent_qty), 2)

    # PRICE DISCOUNT
    price_discount = None
    if _to_float(orig) or _to_float(price):
        price_discount = round(_to_float(orig) - _to_float(price), 2)

    return (_to_float(orig, None),
            _to_float(price, None),
            _to_float(row_total, None),
            _to_float(discount, None),
            price_discount)

# ---------- Campos ----------
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
    "numero": "",
    "depto": "",
    "departamento": "extension_attributes.shipping_assignments[0].shipping.address.region",
    "provincia": "extension_attributes.province",
    "distrito": "extension_attributes.shipping_assignments[0].shipping.address.city",
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

# (CAMPOS_ITEM lo dejamos como referencia; no se usa directamente en el armado)

# ---------- Transform ----------
def transformar(paths_ndjson: List[str]) -> str:
    name_map, desc_map = cargar_mapeo_reglas(SALES_RULES_PATH)
    locales_map = cargar_mapeo_locales(LOCALES_PATH)

    filas: List[Dict[str, Any]] = []

    for orden in _json_lines(paths_ndjson):
        # ---------- Datos del pedido ----------
        datos_orden = {}
        for col, ruta in CAMPOS_ORDEN.items():
            datos_orden[col] = extraer_valor_con_ruta(orden, ruta)

        # Reglas/promos
        applied_ids = parse_rule_ids(datos_orden.get('promo_id'))
        dn = [name_map.get(i,'') for i in applied_ids if name_map.get(i,'')]
        dd = [desc_map.get(i,'') for i in applied_ids if desc_map.get(i,'')]
        datos_orden['discount_name'] = ','.join(dn)
        datos_orden['discount_description'] = ','.join(dd)

        datos_orden['pago_confirmado'] = _pago_confirmado_desde_status(orden)

        # ---------- Ítems ----------
        items = orden.get('items') or []
        padres  = [it for it in items if it.get('product_type') == 'configurable']
        simples = [it for it in items if it.get('product_type') == 'simple']

        parents_index = {pd.get('item_id'): pd for pd in padres}

        # Mapa sku_simple -> parent_id
        child_to_parent: Dict[str, Any] = {}
        for ch in simples:
            pid = ch.get('parent_item_id')
            if pid:
                child_to_parent[ch.get('sku')] = pid

        # ---------- Shipping total ----------
        total_shipping = _to_float(orden.get('base_shipping_amount'))
        if total_shipping == 0.0:
            total_shipping = _to_float(orden.get('payment',{}).get('base_shipping_amount'))
        total_shipping_cents = int(round(total_shipping * 100))

        # ---------- additional_information (split por source) ----------
        info = orden.get('extension_attributes',{}).get('additional_information')
        try:
            info = json.loads(info) if isinstance(info, str) else info
        except Exception:
            info = None

        shipping_cents_por_parent: Dict[Any, int] = {pd.get('item_id'): 0 for pd in padres}

        if info:
            # 1) repartir shipping entre sources por partes iguales
            sources = sorted({e.get('source') for e in info if e.get('source') is not None}, key=lambda x: str(x))
            reparto_sources = _alloc_even_cents(total_shipping_cents, sources) if sources else {}

            # 2) por cada source, repartir a padres según qty de sus simples
            shipping_cents_by_pid_source_sku: Dict[tuple, int] = {}
            for s in sources:
                entry = next((e for e in info if e.get('source') == s), None)
                if not entry: continue
                qty_by_pid: Dict[Any,int] = {}
                skus_by_pid: Dict[Any, list] = {}
                for ent in entry.get('items') or []:
                    sku = ent.get('sku'); qty = int(round(_to_float(ent.get('qty'))))
                    if not sku or qty <= 0: continue
                    pid = child_to_parent.get(sku)
                    if not pid: continue
                    qty_by_pid[pid] = qty_by_pid.get(pid, 0) + qty
                    skus_by_pid.setdefault(pid, []).append((sku, qty))

                quota_source = reparto_sources.get(s, 0)
                if qty_by_pid:
                    keys_pid = list(qty_by_pid.keys())
                    weights_pid = [qty_by_pid[k] for k in keys_pid]
                    reparto_pid = _alloc_by_weights_cents(quota_source, weights_pid, keys_pid)
                else:
                    reparto_pid = {}

                # 3) repartir por SKU dentro de cada padre
                for pid, cents_pid in reparto_pid.items():
                    pairs = skus_by_pid.get(pid, [])
                    if not pairs: continue
                    keys_sku = [sku for (sku, q) in pairs]
                    weights_sku = [q for (sku, q) in pairs]
                    reparto_sku = _alloc_by_weights_cents(cents_pid, weights_sku, keys_sku)
                    for sku, cents in reparto_sku.items():
                        shipping_cents_by_pid_source_sku[(pid, s, sku)] = cents
                        shipping_cents_por_parent[pid] = shipping_cents_por_parent.get(pid, 0) + cents

            # ajuste global por redondeo
            diff = total_shipping_cents - sum(shipping_cents_por_parent.values())
            if diff != 0 and padres:
                last_parent = padres[-1].get('item_id')
                shipping_cents_por_parent[last_parent] = shipping_cents_por_parent.get(last_parent, 0) + diff
        else:
            # Sin info: repartir shipping de cada padre a sus simples por qty
            parent_weights: Dict[Any,int] = {}
            for ch in simples:
                pid = ch.get('parent_item_id')
                if pid:
                    q = int(round(_to_float(ch.get('qty_ordered') or ch.get('qty'))))
                    parent_weights[pid] = parent_weights.get(pid, 0) + max(q, 0)
            if not parent_weights and padres:
                parent_weights = {pd.get('item_id'): 1 for pd in padres}

            keys = list(parent_weights.keys()); weights = [parent_weights[k] for k in keys]
            reparto_parents = _alloc_by_weights_cents(total_shipping_cents, weights, keys)
            for pid, cents in reparto_parents.items():
                shipping_cents_por_parent[pid] = shipping_cents_por_parent.get(pid, 0) + cents

            # y ahora por SKU dentro de cada padre
            shipping_cents_by_pid_sku: Dict[tuple, int] = {}
            simples_by_parent: Dict[Any, list] = {}
            for ch in simples:
                pid = ch.get('parent_item_id')
                if pid:
                    simples_by_parent.setdefault(pid, []).append(ch)
            for pid, cents in shipping_cents_por_parent.items():
                children = simples_by_parent.get(pid, [])
                if not children: continue
                keys_sku = [ch.get('sku') for ch in children]
                weights_sku = [int(round(_to_float(ch.get('qty_ordered') or ch.get('qty')))) for ch in children]
                reparto = _alloc_by_weights_cents(cents, weights_sku, keys_sku)
                for sku, c in reparto.items():
                    shipping_cents_by_pid_sku[(pid, sku)] = c

            # ajuste global
            diff = total_shipping_cents - sum(shipping_cents_por_parent.values())
            if diff != 0 and padres:
                last_parent = padres[-1].get('item_id')
                shipping_cents_por_parent[last_parent] = shipping_cents_por_parent.get(last_parent, 0) + diff

        # ---------- Construcción de filas ----------
        # pre-build split por sku si hay info
        alloc_by_sku: Dict[str, list] = {}
        if info:
            for e in info:
                s = e.get('source')
                for ent in e.get('items') or []:
                    sku = ent.get('sku'); qty = int(round(_to_float(ent.get('qty'))))
                    if sku and qty > 0:
                        alloc_by_sku.setdefault(sku, []).append((s, qty))

        iterable_items = simples if ROW_MODE == 'por_simple' else padres

        for item in iterable_items:
            if ROW_MODE == 'por_configurable':
                parent = item
                pid = parent.get('item_id')
                sku = parent.get('sku') or ''
                qty_used = _to_float(parent.get('qty_ordered'))
                (orig, price, row_total, discount, price_disc) = derive_item_values(parent, parent, qty_used)
                ship_split = (shipping_cents_por_parent.get(pid, 0) or 0) / 100.0

                fila = datos_orden.copy()
                fila.update({
                    'qty_confirmed': qty_used,
                    'sku': sku,
                    'product_name': parent.get('name'),
                    'source_id': None,
                    'original_price': orig,
                    'price': price,
                    'row_total': row_total,
                    'total_shipping_charges': ship_split,
                    'coupon_discount': discount,
                    'price_discount': price_disc,
                    'qty_ordered': qty_used
                })
                sid_key = normalizar_source_id(fila.get('source_id',''))
                fila['source_Name'] = locales_map.get(sid_key,'') or locales_map.get(str(fila.get('source_id','')).strip(),'')
                fila['grand_total_item'] = round(_to_float(fila['price']) * _to_float(fila['qty_ordered']) +
                                                 _to_float(fila['total_shipping_charges']) -
                                                 _to_float(fila['coupon_discount']), 2)
                filas.append(fila)
                continue

            # --- por_simple ---
            parent_id = item.get('parent_item_id')
            parent = parents_index.get(parent_id)
            sku = item.get('sku')
            qty_item = _to_float(item.get('qty_ordered') or item.get('qty'))

            if info and sku in alloc_by_sku:
                for (src, qty_src) in alloc_by_sku[sku]:
                    ship_cents = 0
                    if parent_id is not None:
                        ship_cents = (shipping_cents_by_pid_source_sku.get((parent_id, src, sku), 0) if 'shipping_cents_by_pid_source_sku' in locals() else 0)
                    ship_split = ship_cents / 100.0
                    (orig, price, row_total, discount, price_disc) = derive_item_values(item, parent, qty_src)

                    fila = datos_orden.copy()
                    fila.update({
                        'qty_confirmed': qty_src,
                        'sku': sku,
                        'product_name': item.get('name'),
                        'source_id': src,
                        'original_price': orig,
                        'price': price,
                        'row_total': row_total,
                        'total_shipping_charges': ship_split,
                        'coupon_discount': discount,
                        'price_discount': price_disc,
                        'qty_ordered': qty_src
                    })
                    sid_key = normalizar_source_id(fila.get('source_id',''))
                    fila['source_Name'] = locales_map.get(sid_key,'') or locales_map.get(str(fila.get('source_id','')).strip(),'')
                    fila['grand_total_item'] = round(_to_float(fila['price']) * _to_float(fila['qty_ordered']) +
                                                     _to_float(fila['total_shipping_charges']) -
                                                     _to_float(fila['coupon_discount']), 2)
                    filas.append(fila)
            else:
                # sin split (o sin info para ese sku)
                if info:
                    ship_cents = (shipping_cents_by_pid_sku.get((parent_id, sku), 0) if 'shipping_cents_by_pid_sku' in locals() else 0)
                else:
                    ship_cents = (shipping_cents_by_pid_sku.get((parent_id, sku), 0) if 'shipping_cents_by_pid_sku' in locals() else (shipping_cents_por_parent.get(parent_id, 0) or 0))
                ship_split = ship_cents / 100.0

                (orig, price, row_total, discount, price_disc) = derive_item_values(item, parent, qty_item)
                src, qty_conf = obtener_source_y_qty_desde_additional_info(orden, sku)

                fila = datos_orden.copy()
                fila.update({
                    'qty_confirmed': qty_conf if qty_conf not in (None, 0, '0') else qty_item,
                    'sku': sku,
                    'product_name': item.get('name'),
                    'source_id': src if src is not None else item.get('source_id'),
                    'original_price': orig,
                    'price': price,
                    'row_total': row_total,
                    'total_shipping_charges': ship_split,
                    'coupon_discount': discount,
                    'price_discount': price_disc,
                    'qty_ordered': qty_item
                })
                sid_key = normalizar_source_id(fila.get('source_id',''))
                fila['source_Name'] = locales_map.get(sid_key,'') or locales_map.get(str(fila.get('source_id','')).strip(),'')
                fila['grand_total_item'] = round(_to_float(fila['price']) * _to_float(fila['qty_ordered']) +
                                                 _to_float(fila['total_shipping_charges']) -
                                                 _to_float(fila['coupon_discount']), 2)
                filas.append(fila)

    df = pd.DataFrame(filas)

    stamp = datetime.now().strftime('%Y%m%d %H%M%S')
    base = os.path.join(OUTPUT_FOLDER, f'reporte_ordenes_{stamp}.xlsx')
    path = base
    i = 2
    while os.path.exists(path):
        path = base.replace('.xlsx', f' ({i}).xlsx'); i += 1
    df.to_excel(path, index=False)

    if USE_SQL and not df.empty:
        engine = create_engine(MSSQL_CONN, fast_executemany=True)
        with engine.begin() as conn:
            df.to_sql(MSSQL_TABLE.split('.')[-1], conn, schema=MSSQL_TABLE.split('.')[0], if_exists='append', index=False)

    print(f"[DONE] Reporte generado: {path} | filas={len(df)}")
    return path

if __name__=='__main__':
    import sys, glob, os, json
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
    transformar(paths)
