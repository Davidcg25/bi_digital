import os
import re
import json
import time
import random
from math import ceil
from decimal import Decimal, ROUND_FLOOR
from datetime import datetime, timedelta
from sqlalchemy import create_engine, text

import pandas as pd
import requests
from tqdm import tqdm
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from requests_oauthlib import OAuth1

# =========================
# CONFIGURACIÓN (editables)
# =========================
dias_atras = 6                         # rango relativo (incluye hoy)
OUTPUT_FOLDER = "."                     # carpeta de salida
PAGE_SIZE = 100                         # tamaño base (el fetch adaptará si hace falta)
BASE_URL = 'https://converse.cl/rest/converse_pe_store_view/V1/'

# === SQL Server (SSMS) ===
server = 'localhost'
database = 'Digital_Impact_Reportes'
schema = 'dbo'
table_name = 'Ventas_Solidez_Magento'
driver = 'ODBC Driver 17 for SQL Server'

# Conexión con Trusted Auth (Windows)
engine = create_engine(
    f"mssql+pyodbc://@{server}/{database}?trusted_connection=yes&driver={driver}"
)

# Rutas de datos auxiliares
SALES_RULES_PATH = r"D:\Proyectos\4_BI_Ecom\Promociones Magento\sales_rules_export.xlsx"
LOCALES_PATH = r"D:\Proyectos\4_BI_Ecom\tabla_locales.csv"

# Credenciales API de Magento (staging provistas)
CONSUMER_KEY = 'wab8paongympcuj7y5x4qdxs0bw1hd4z'
CONSUMER_SECRET = 'erypfmavubmjb520ap1oe4zwbsdl310x'
ACCESS_TOKEN = 'o3pmatsrmolplnsvtdm2oc1ey7jwxej6'
ACCESS_TOKEN_SECRET = '3eftvwbskm5atab6pm8zr6ng51t8kova'

# =========================
# CAMPOS (lo que sí usamos)
# =========================
campos_orden = {
    "purchase_point": "store_name",
    "id": "increment_id",
    "order_id": "entity_id",
    "created": "created_at",
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

campos_item = {
    "qty_confirmed": "qty",
    "sku": "sku",
    "product_name": "name",
    "source_id": "source_id",
    "source_Name": "",
    "original_price": "base_original_price",
    "price": "base_price",
    "row_total": "base_row_total",
    "total_shipping_charges": "base_shipping_amount",
    "coupon_discount": "base_discount_amount",
    "price_discount": "price_discount",
    "qty_ordered": "qty_ordered"
}

# =====================================================
# HTTP/Autenticación
# =====================================================
def obtener_autenticacion():
    # OAuth1 fresco por request evita problemas de nonce/timestamp
    return OAuth1(
        CONSUMER_KEY,
        CONSUMER_SECRET,
        ACCESS_TOKEN,
        ACCESS_TOKEN_SECRET,
        signature_method='HMAC-SHA256'
    )

def build_session():
    # Sesión con keep-alive y pequeña tolerancia a fallas de conexión
    sess = requests.Session()
    retry = Retry(
        total=0,
        connect=3,
        read=0,
        backoff_factor=0.5,
        status_forcelist=[401,408,409,425,429,500,502,503,504],
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(pool_connections=8, pool_maxsize=8, max_retries=retry)
    sess.mount("https://", adapter)
    sess.headers.update({
        "Connection": "keep-alive",
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate",
        "User-Agent": "DigitalImpact-MagentoFetcher/2.0"
    })
    return sess

# =====================================================
# Utilidades
# =====================================================
def calcular_rango_fechas(dias_atras: int):
    hoy = datetime.today()
    fecha_fin = hoy.replace(hour=23, minute=59, second=59)
    fecha_inicio = (hoy - timedelta(days=dias_atras)).replace(hour=0, minute=0, second=0)
    return fecha_inicio.isoformat(), fecha_fin.isoformat()

def get_nested_value(data, path):
    keys = re.split(r'\.(?![^\[]*\])', path)
    for key in keys:
        if isinstance(data, list):
            match = re.match(r"(\w+)\[(\d+)\]", key)
            if match:
                key, idx = match.groups()
                data = [item.get(key) for item in data if key in item]
                try:
                    data = data[int(idx)]
                except (IndexError, TypeError):
                    return None
            else:
                return None
        elif isinstance(data, dict):
            match = re.match(r"(\w+)\[(\d+)\]", key)
            if match:
                key, idx = match.groups()
                data = data.get(key, [])
                try:
                    data = data[int(idx)]
                except (IndexError, TypeError):
                    return None
            else:
                data = data.get(key)
        else:
            return None
    return data

def buscar_en_lista_por_clave(lista, clave_buscada):
    if not isinstance(lista, list):
        return None
    for elemento in lista:
        if isinstance(elemento, dict) and elemento.get("key") == clave_buscada:
            return elemento.get("value")
    return None

def extraer_valor_con_ruta(orden, ruta):
    if ruta.startswith("status_histories"):
        patrones = re.compile(r"(TKT-\d+|B\d{1,3}|F\d{1,3})")
        for entry in orden.get("status_histories", []):
            comment = entry.get("comment")
            if comment and patrones.search(comment):
                return comment
        return None
    elif "receiver_name" in ruta:
        try:
            fname = get_nested_value(orden, "extension_attributes.shipping_assignments[0].shipping.address.firstname") or ""
            lname = get_nested_value(orden, "extension_attributes.shipping_assignments[0].shipping.address.lastname") or ""
            return f"{fname.strip()} {lname.strip()}".strip() or None
        except:
            return None
    elif "payment_additional_info[" in ruta:
        clave = ruta.split("[")[-1].rstrip("]")
        return buscar_en_lista_por_clave(
            orden.get("extension_attributes", {}).get("payment_additional_info", []),
            clave
        )
    else:
        return get_nested_value(orden, ruta)

def buscar_valor_item(item, ruta, orden=None):
    if ruta in ["qty", "id_sku", "sku", "source_id"]:
        sku = item.get("sku")
        source, qty = obtener_source_y_qty_desde_additional_info(orden, sku)
        if ruta == "qty":
            return qty
        elif ruta == "id_sku":
            return sku
        elif ruta == "sku":
            return sku
        elif ruta == "source_id":
            return source
    else:
        return get_nested_value(item, ruta)

def obtener_source_y_qty_desde_additional_info(order_json, sku):
    try:
        adicional_info_str = order_json.get("extension_attributes", {}).get("additional_information")
        if not adicional_info_str:
            return None, None
        if isinstance(adicional_info_str, str):
            adicional_info = json.loads(adicional_info_str)
        else:
            adicional_info = adicional_info_str  # ya es objeto JSON

        for entry in adicional_info:
            source = entry.get("source")
            for it in entry.get("items", []):
                if it.get("sku") == sku:
                    qty = it.get("qty")
                    return source, qty
        return None, None
    except Exception:
        return None, None

def _allocate_even_cents(total_cents, keys):
    n = len(keys)
    if n == 0:
        return {}
    base = total_cents // n
    residuo = total_cents - base * n
    return {k: base + (1 if i < residuo else 0) for i, k in enumerate(keys)}

def _allocate_by_weights_cents(total_cents, weights, keys):
    if not keys or not weights or sum(weights) <= 0:
        return {k: 0 for k in keys}
    wsum = sum(weights)
    cuotas = [(total_cents * w) / wsum for w in weights]
    pisos = [int(Decimal(c).to_integral_value(rounding=ROUND_FLOOR)) for c in cuotas]
    asignado = sum(pisos)
    residuo = total_cents - asignado
    fracs = sorted([(i, cuotas[i] - pisos[i]) for i in range(len(keys))], key=lambda x: x[1], reverse=True)
    extra = {idx for idx, _ in fracs[:residuo]}
    return {keys[i]: pisos[i] + (1 if i in extra else 0) for i in range(len(keys))}

def normalizar_source_id(v):
    if v is None:
        return ""
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return ""
    if s.isdigit():
        return str(int(s))
    return s

# =====================================================
# `fields` para recortar el payload (ajustado a nuestros mapeos)
# =====================================================
def build_fields_param() -> str:
    """
    Campos mínimos requeridos por nuestras transformaciones.
    Nota: para arrays, Magento acepta notación anidada con corchetes.
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

# =====================================================
# Datos auxiliares (reglas y locales)
# =====================================================
def cargar_mapeo_reglas(path_excel):
    try:
        df_rules = pd.read_excel(path_excel, dtype={"rule_id": "Int64"})
    except Exception as e:
        print(f"[ERROR] No se pudo leer el Excel de reglas: {e}")
        return {}, {}

    cols_lower = {c.lower(): c for c in df_rules.columns}
    c_rule = cols_lower.get("rule_id", "rule_id")
    c_name = cols_lower.get("name", "name")
    c_desc = cols_lower.get("description", "description")

    df_rules = df_rules[[c_rule, c_name, c_desc]].dropna(subset=[c_rule])
    df_rules[c_rule] = df_rules[c_rule].astype("Int64")

    name_map, desc_map = {}, {}
    for _, row in df_rules.iterrows():
        rid = row[c_rule]
        if pd.isna(rid):
            continue
        rid = int(rid)
        name_map[rid] = (str(row[c_name]).strip() if pd.notna(row[c_name]) else "")
        desc_map[rid] = (str(row[c_desc]).strip() if pd.notna(row[c_desc]) else "")
    return name_map, desc_map

def parse_rule_ids(value):
    if value is None:
        return []
    s = str(value).strip()
    if s == "" or s == "0":
        return []
    parts = [p.strip() for p in s.split(",") if p.strip() != ""]
    ids, seen = [], set()
    for p in parts:
        try:
            i = int(float(p)) if "." in p else int(p)
            if i != 0 and i not in seen:
                ids.append(i)
                seen.add(i)
        except:
            continue
    return ids

def map_rule_fields(rule_ids, name_map, desc_map):
    names = [name_map.get(i, "") for i in rule_ids]
    descs = [desc_map.get(i, "") for i in rule_ids]
    names = [n for n in names if n]
    descs = [d for d in descs if d]
    return ",".join(names), ",".join(descs)

def cargar_mapeo_locales(path_file):
    if not os.path.exists(path_file):
        print(f"[ERROR] No existe el archivo de locales: {path_file}")
        return {}

    _, ext = os.path.splitext(path_file.lower())
    df = None
    try:
        if ext == ".csv":
            try:
                df = pd.read_csv(path_file, sep=None, engine="python", dtype=str, encoding="utf-8-sig")
            except Exception:
                try:
                    df = pd.read_csv(path_file, sep=";", dtype=str, encoding="utf-8-sig")
                except Exception:
                    try:
                        df = pd.read_csv(path_file, sep=",", dtype=str, encoding="utf-8-sig")
                    except Exception:
                        df = pd.read_csv(path_file, sep=",", dtype=str, encoding="latin-1")
        else:
            df = pd.read_excel(path_file, dtype=str)
    except Exception as e:
        print(f"[ERROR] No se pudo leer locales ({ext}): {e}")
        return {}

    if df is None or df.empty:
        print("[ADVERTENCIA] tabla_locales sin filas.")
        return {}

    rename_map = {c: c.strip() for c in df.columns}
    df.rename(columns=rename_map, inplace=True)
    cols_lower = {c.lower(): c for c in df.columns}

    req = {"id_tienda", "local"}
    if not req.issubset(set(cols_lower.keys())):
        print(f"[ADVERTENCIA] Encabezados requeridos no presentes. Encontrado: {list(df.columns)}. Se necesitan: Id_Tienda, Local")
        return {}

    c_id = cols_lower["id_tienda"]
    c_nm = cols_lower["local"]

    df = df[[c_id, c_nm]].dropna(subset=[c_id])
    df[c_id] = df[c_id].astype(str).str.strip()
    df[c_nm] = df[c_nm].astype(str).str.strip()

    id_to_local = {}
    for _, row in df.iterrows():
        k = row[c_id]
        v = row[c_nm]
        if not k or not v:
            continue
        id_to_local[k] = v
        if k.isdigit():
            id_to_local[str(int(k))] = v

    print(f"[ACCION] Locales cargados: {len(id_to_local)} (desde {os.path.basename(path_file)})")
    return id_to_local

# =====================================================
# Fetch de órdenes con paginación robusta + `fields`
# =====================================================
def obtener_ordenes(fecha_inicio, fecha_fin):
    PAGE_SIZES_TRY = [PAGE_SIZE, 50, 25]
    PAGE_RETRIES = 5
    BACKOFF_BASE = 2.0
    JITTER = 0.25
    TIMEOUT = (10, 120)
    RETRIABLE = {401, 408, 409, 425, 429, 500, 502, 503, 504}

    errores = []
    todas = []

    session = build_session()
    fields_param = build_fields_param()

    def build_url(page, page_size):
        return (
            f"{BASE_URL}orders?"
            f"searchCriteria[filter_groups][0][filters][0][field]=created_at&"
            f"searchCriteria[filter_groups][0][filters][0][value]={fecha_inicio}&"
            f"searchCriteria[filter_groups][0][filters][0][condition_type]=gteq&"
            f"searchCriteria[filter_groups][1][filters][0][field]=created_at&"
            f"searchCriteria[filter_groups][1][filters][0][value]={fecha_fin}&"
            f"searchCriteria[filter_groups][1][filters][0][condition_type]=lteq&"
            f"searchCriteria[sortOrders][0][field]=entity_id&"
            f"searchCriteria[sortOrders][0][direction]=ASC&"
            f"searchCriteria[pageSize]={page_size}&"
            f"searchCriteria[currentPage]={page}&"
            f"fields={fields_param}"
        )

    pagina = 1
    total_paginas = None
    pbar = None
    failed_pages = []

    while True:
        page_ok = False

        for ps in PAGE_SIZES_TRY:
            for intento in range(1, PAGE_RETRIES + 1):
                auth = obtener_autenticacion()
                url = build_url(pagina, ps)
                try:
                    resp = session.get(url, auth=auth, timeout=TIMEOUT)
                except requests.RequestException as e:
                    if intento >= PAGE_RETRIES:
                        errores.append(f"❌ Página {pagina} (size={ps}): excepción tras {intento} intentos: {e}")
                        break
                    errores.append(f"⚠️ Página {pagina} (size={ps}): excepción {e} | reintento {intento}/{PAGE_RETRIES}")
                    time.sleep((BACKOFF_BASE ** intento) + random.random() * JITTER)
                    continue

                if resp.status_code == 200:
                    data = resp.json()
                    if total_paginas is None:
                        total_count = data.get("total_count", 0) or 0
                        total_paginas = max(1, ceil(total_count / ps)) if total_count else 1
                        pbar = tqdm(total=total_paginas, desc="Consultando Magento", unit="pág", ncols=80)

                    items = data.get("items", []) or []
                    todas.extend(items)
                    page_ok = True
                    if pbar:
                        pbar.update(1)
                    time.sleep(0.15 + random.random() * 0.15)
                    break
                else:
                    body_preview = (resp.text or "")[:300].replace("\n", " ")
                    msg = f"⚠️ Página {pagina} (size={ps}): HTTP {resp.status_code} | cuerpo: {body_preview}"
                    if resp.status_code in RETRIABLE and intento < PAGE_RETRIES:
                        errores.append(msg + f" | reintento {intento}/{PAGE_RETRIES}")
                        time.sleep((BACKOFF_BASE ** intento) + random.random() * JITTER)
                        continue
                    else:
                        errores.append("❌ " + msg + " | sin más reintentos")
                        break  # probar con ps menor

            if page_ok:
                break

        if not page_ok:
            failed_pages.append(pagina)
            if total_paginas is None:
                if pbar:
                    pbar.close()
                return todas, errores
            if pbar:
                pbar.update(1)

        if total_paginas is not None and pagina >= total_paginas:
            break

        pagina += 1

    # Reintento diferido de páginas fallidas
    for page in failed_pages:
        for intento in range(1, PAGE_RETRIES + 1):
            auth = obtener_autenticacion()
            url = build_url(page, 25)
            try:
                resp = session.get(url, auth=auth, timeout=(10, 150))
            except requests.RequestException as e:
                if intento >= PAGE_RETRIES:
                    errores.append(f"❌ Reintento final pág {page}: excepción tras {intento} intentos: {e}")
                    break
                errores.append(f"⚠️ Reintento final pág {page}: excepción {e} | reintento {intento}/{PAGE_RETRIES}")
                time.sleep((BACKOFF_BASE ** intento) + random.random() * JITTER)
                continue

            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", []) or []
                todas.extend(items)
                break
            else:
                if intento >= PAGE_RETRIES:
                    body_preview = (resp.text or "")[:300].replace("\n", " ")
                    errores.append(f"❌ Reintento final pág {page}: HTTP {resp.status_code} | cuerpo: {body_preview}")
                else:
                    time.sleep((BACKOFF_BASE ** intento) + random.random() * JITTER)

    if pbar:
        pbar.close()

    return todas, errores

# =====================================================
# Transformación y export
# =====================================================
def generar_reporte_ordenes(ordenes, campos_orden, campos_item, fecha_hoy_str, name_map, desc_map, locales_map):
    filas = []
    errores = []

    for orden in tqdm(ordenes, desc="Procesando órdenes"):
        datos_orden = {}
        for nombre_columna, ruta in campos_orden.items():
            valor = extraer_valor_con_ruta(orden, ruta)
            datos_orden[nombre_columna] = valor

        # Reglas: traducir applied_rule_ids a name/description
        applied_ids = parse_rule_ids(datos_orden.get("promo_id"))
        discount_name, discount_description = map_rule_fields(applied_ids, name_map, desc_map)
        datos_orden["discount_name"] = discount_name
        datos_orden["discount_description"] = discount_description

        ids_no_mapeados = [i for i in applied_ids if i not in name_map]
        if ids_no_mapeados:
            errores.append(f"[INFO] Orden {orden.get('increment_id')}: reglas no mapeadas en Excel {ids_no_mapeados}")

        # ===== PRE-CÁLCULO shipping por ítem padre (configurable) en centavos =====
        items = orden.get("items", []) or []
        padres = [it for it in items if it.get("product_type") == "configurable"]
        simples = [it for it in items if it.get("product_type") == "simple"]

        child_sku_to_parent = {}
        for ch in simples:
            pid = ch.get("parent_item_id")
            if pid:
                child_sku_to_parent[ch.get("sku")] = pid

        # Nota: en algunos Magento la métrica shipping está a nivel de orden, no payment.*
        # Mantenemos tu lógica original por compatibilidad:
        total_shipping = float(orden.get("base_shipping_amount") or 0.0)
        if total_shipping == 0.0:
            total_shipping = float(orden.get("payment", {}).get("base_shipping_amount") or 0.0)
        total_shipping_cents = int(round(total_shipping * 100))
        shipping_cents_por_parent = {pd.get("item_id"): 0 for pd in padres}

        adicional_info_str = orden.get("extension_attributes", {}).get("additional_information")
        try:
            info = json.loads(adicional_info_str) if isinstance(adicional_info_str, str) else adicional_info_str
        except Exception:
            info = None

        if not info:
            parent_weights = {}
            for ch in simples:
                pid = ch.get("parent_item_id")
                if pid:
                    qty = int(round(float(ch.get("qty_ordered") or 0)))
                    parent_weights[pid] = parent_weights.get(pid, 0) + (qty if qty > 0 else 0)
            if not parent_weights and padres:
                parent_weights = {pd.get("item_id"): 1 for pd in padres}
            keys = list(parent_weights.keys())
            weights = [parent_weights[k] for k in keys]
            reparto = _allocate_by_weights_cents(total_shipping_cents, weights, keys)
            for pid, cents in reparto.items():
                shipping_cents_por_parent[pid] = shipping_cents_por_parent.get(pid, 0) + cents
        else:
            sources = [e.get("source") for e in info if e.get("source") is not None]
            sources = sorted(set(sources), key=lambda x: str(x))
            reparto_sources = _allocate_even_cents(total_shipping_cents, sources)

            for s in sources:
                entry = next((e for e in info if e.get("source") == s), None)
                if not entry:
                    continue
                items_src = entry.get("items", []) or []

                parent_weights_src = {}
                for ent in items_src:
                    sku = ent.get("sku")
                    qty = int(round(float(ent.get("qty") or 0)))
                    if not sku or qty <= 0:
                        continue
                    pid = child_sku_to_parent.get(sku)
                    if not pid:
                        continue
                    parent_weights_src[pid] = parent_weights_src.get(pid, 0) + qty

                if not parent_weights_src:
                    continue

                keys = list(parent_weights_src.keys())
                weights = [parent_weights_src[k] for k in keys]
                quota_cents = reparto_sources.get(s, 0)
                reparto_src = _allocate_by_weights_cents(quota_cents, weights, keys)
                for pid, cents in reparto_src.items():
                    shipping_cents_por_parent[pid] = shipping_cents_por_parent.get(pid, 0) + cents

        suma = sum(shipping_cents_por_parent.values())
        diff = total_shipping_cents - suma
        if suma != total_shipping_cents:
            errores.append(f"[ERROR] Shipping no cuadra: {suma} vs {total_shipping_cents} | orden {orden.get('increment_id')}")
        if diff != 0 and padres:
            last_parent_id = padres[-1].get("item_id")
            shipping_cents_por_parent[last_parent_id] = shipping_cents_por_parent.get(last_parent_id, 0) + diff

        # ===== Construir filas por ítem padre (configurable) =====
        for item in orden.get("items", []):
            if item.get("product_type") == "simple":
                continue

            fila = datos_orden.copy()

            # price_discount
            try:
                original_price = item.get("base_original_price")
                price = item.get("base_price")
                fila["price_discount"] = round(original_price - price, 2) if original_price and price else None
            except Exception:
                fila["price_discount"] = None

            # shipping split del ítem
            item_id = item.get("item_id")
            shipping_split = (shipping_cents_por_parent.get(item_id, 0) or 0) / 100.0

            for nombre_columna, ruta in campos_item.items():
                if nombre_columna == "price_discount":
                    # ya calculado
                    continue
                elif nombre_columna == "total_shipping_charges":
                    fila[nombre_columna] = shipping_split
                else:
                    valor = buscar_valor_item(item, ruta, orden)
                    fila[nombre_columna] = valor

            # Enriquecer source_Name
            sid_raw = fila.get("source_id", "")
            sid_key = normalizar_source_id(sid_raw)
            source_name = ""
            if sid_key:
                source_name = locales_map.get(sid_key, "") or locales_map.get(str(sid_raw).strip(), "")
            fila["source_Name"] = source_name
            if sid_key and not source_name:
                errores.append(f"[INFO] source_id no mapeado en tabla_locales: '{sid_raw}' | orden {datos_orden.get('increment_id')}")

            # grand_total_item = price * qty_ordered + shipping - coupon_discount
            try:
                price = float(fila.get("price", 0) or 0)
                qty_ordered = float(fila.get("qty_ordered", 0) or 0)
                shipping = float(fila.get("total_shipping_charges", 0) or 0)
                coupon_discount = float(fila.get("coupon_discount", 0) or 0)
                grand_total_item = (price * qty_ordered) + shipping - coupon_discount
                fila["grand_total_item"] = round(grand_total_item, 2)
            except Exception as e:
                fila["grand_total_item"] = None
                errores.append(f"❌ Error al calcular grand_total_item en orden {orden.get('increment_id')}: {e}")

            filas.append(fila)

    df = pd.DataFrame(filas)
    print("Vista previa de las primeras 5 filas:")
    print(df.head(5))

    output_path = os.path.join(OUTPUT_FOLDER, f"reporte_ordenes_{fecha_hoy_str}.xlsx")
    df.to_excel(output_path, index=False)
    return output_path, errores

# =====================================================
# MAIN
# =====================================================
if __name__ == "__main__":
    # Auxiliares
    name_map, desc_map = cargar_mapeo_reglas(SALES_RULES_PATH)
    locales_map = cargar_mapeo_locales(LOCALES_PATH)

    fecha_inicio, fecha_fin = calcular_rango_fechas(dias_atras)
    ordenes, errores_api = obtener_ordenes(fecha_inicio, fecha_fin)
    hoy_str = datetime.today().strftime('%Y%m%d %H%M%S')

    excel_path, errores_extraccion = generar_reporte_ordenes(
        ordenes, campos_orden, campos_item, hoy_str, name_map, desc_map, locales_map
    )

    # Log
    log_path = os.path.join(OUTPUT_FOLDER, f"log_errores_{hoy_str}.txt")
    with open(log_path, "w", encoding="utf-8") as log_file:
        for error in errores_api + errores_extraccion:
            log_file.write(error + "\n")

    print(f"[ACCION] Reporte generado: {excel_path}")
    print(f"[ACCION] Log de errores: {log_path}")
