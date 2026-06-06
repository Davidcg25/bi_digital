# magento_transform_orders.py
import os, json, gzip, re
from datetime import datetime
from glob import glob
import pandas as pd

RAW_DIR = "./raw_orders"   # Debe coincidir con OUT_DIR del extractor
OUT_XLSX = "./reporte_ordenes_transformado.xlsx"

# === Helpers para cargar NDJSON ===
def iter_orders(files):
    for path in files:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)

def find_files_between(raw_dir: str, date_from: str, date_to: str):
    # Matchea por tag 'orders_YYYY-MM-DD_a_YYYY-MM-DD_pXXXX.ndjson.gz'
    patt = re.compile(r"orders_(\d{4}-\d{2}-\d{2})_a_(\d{4}-\d{2}-\d{2})_p\d+\.ndjson\.gz$")
    files = []
    for path in glob(os.path.join(raw_dir, "*.ndjson.gz")):
        m = patt.search(os.path.basename(path))
        if not m: 
            continue
        w_from, w_to = m.group(1), m.group(2)
        if w_to < date_from or w_from > date_to:
            continue
        files.append(path)
    return sorted(files)

# === Transformación — ADAPTA A TU LÓGICA EXISTENTE ===
def rows_from_order(order: dict):
    """
    Desglosa por ítem 'comprado'. 
    Ajusta aquí tus reglas (configurable vs simple, shipping prorrateado, cupones, etc).
    Devuelve una lista de dicts por fila final.
    """
    base = {
        "order_id": order.get("increment_id"),
        "entity_id": order.get("entity_id"),
        "created": order.get("created_at"),
        "order_status": order.get("status"),
        "state": order.get("state"),
        "customer_id": order.get("customer_id"),
        "customer_name": f"{order.get('customer_firstname','')} {order.get('customer_lastname','')}".strip(),
        "email": order.get("customer_email"),
        "payment_entity_id": (order.get("payment") or {}).get("entity_id"),
        "payment_method": (order.get("payment") or {}).get("method"),
        "cc_type": (order.get("payment") or {}).get("cc_type"),
        "coupon_code": order.get("coupon_code"),
        "grand_total_pedido": order.get("grand_total"),
        "shipping_description": order.get("shipping_description"),
    }
    # Dirección de envío (customs incluidos si existen)
    ext = order.get("extension_attributes") or {}
    assign = (ext.get("shipping_assignments") or [])
    ship = (assign[0].get("shipping") if assign else {}) or {}
    addr = (ship.get("address") or {}) 
    base.update({
        "receiver_name": f"{addr.get('firstname','')} {addr.get('lastname','')}".strip(),
        "phone": addr.get("telephone"),
        "calle": " ".join(addr.get("street") or []),
        "departamento": addr.get("region"),
        "provincia": addr.get("city"),
        "distrito": addr.get("city"),  # ajusta si tienes mapping distinto
        "postal_code": addr.get("postcode"),
        "address_type": addr.get("address_type"),
        "document": ext.get("document"),
        "tipo_de_documento": ext.get("type_document"),
        "razon_social": ext.get("razon_social"),
        "person_receiver_option": ext.get("person_receiver_option"),
        "person_receiver_full_name": ext.get("person_receiver_full_name"),
        "person_receiver_phone_number": ext.get("person_receiver_phone_number"),
    })

    filas = []
    items = order.get("items") or []
    for it in items:
        # Ajusta tu criterio: si trabajas con configurables como “fuente” de datos
        # y omites simples, filtra acá.
        # Ejemplo: usar solo configurables
        if it.get("product_type") != "configurable":
            continue

        fila = base.copy()
        original_price = it.get("base_original_price")
        price = it.get("base_price")
        qty = it.get("qty_ordered") or it.get("qty") or 0

        fila.update({
            "sku": it.get("sku"),
            "product_name": it.get("name"),
            "original_price": original_price,
            "price": price,
            "qty_ordered": qty,
        })

        # Si prorrateas shipping y descuentas cupones por ítem,
        # aquí invocas tu lógica ya probada (no la repito por brevedad):
        # fila["total_shipping_charges"] = ...
        # fila["coupon_discount"] = ...
        # fila["grand_total_item"] = (price * qty) + shipping - discount
        filas.append(fila)

    return filas

def main_transform(date_from: str, date_to: str):
    files = find_files_between(RAW_DIR, date_from, date_to)
    if not files:
        print("[WARN] No se encontraron archivos para ese rango.")
        return
    rows = []
    for order in iter_orders(files):
        rows.extend(rows_from_order(order))

    if not rows:
        print("[WARN] No hay filas generadas; revisa tus filtros en rows_from_order().")
        return

    df = pd.DataFrame(rows)
    # Ordena columnas a tu gusto:
    cols = [
        "order_id","entity_id","created","order_status","state",
        "customer_id","customer_name","email",
        "payment_entity_id","payment_method","cc_type",
        "coupon_code","grand_total_pedido","shipping_description",
        "receiver_name","phone","calle","departamento","provincia","distrito","postal_code","address_type",
        "document","tipo_de_documento","razon_social","person_receiver_option","person_receiver_full_name","person_receiver_phone_number",
        "sku","product_name","original_price","price","qty_ordered",
        # "total_shipping_charges","coupon_discount","grand_total_item" # si los calculas
    ]
    df = df.reindex(columns=[c for c in cols if c in df.columns])

    df.to_excel(OUT_XLSX, index=False)
    print(f"[ACCION] Exportado: {os.path.abspath(OUT_XLSX)} | Filas: {len(df)}")

if __name__ == "__main__":
    # Ejemplo: transforma todo lo descargado 2024 completo
    main_transform("2024-01-01", datetime.today().strftime("%Y-%m-%d"))
