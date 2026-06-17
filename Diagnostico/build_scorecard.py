# -*- coding: utf-8 -*-
"""
build_scorecard.py — Scorecard de diagnóstico por marca/web (Nivel 3).

El entregable "grande" del consolidado Digital Impact: un informe mensual por
tienda (tipo Steve Madden) con semáforos, su Excel de sustento (1 hoja por
sección) y su PDF (Edge headless). Pensado para correr el día 2 de cada mes
sobre el mes recién cerrado.

Filosofía (estrella polar): no es un dump — es el diagnóstico presentable que se
vende. Cada sección es una lectura accionable, cada número es reproducible desde
el Excel, y cada bloque es resiliente (si una query falla, las demás siguen).

Capas de datos (y por qué cada sección usa la que usa):
  * RMH (rpt.v_ventas_base) — única fuente con MARGEN REAL (Costo/Contribucion)
    e historia para comparar contra el año pasado (LY). → performance vs LY,
    distribución precio/margen, top productos.
  * Magento (vw_magento_orders_*) — geografía, courier, medio de pago y
    customer_id (recompra). Confiable solo desde feb-2026 → estas secciones son
    MoM, no LY; la contribución por zona es ESTIMADA (margen fijo 42.5% bruto).

Salida: Diagnostico/<Marca>/<YYYYMM>/scorecard_<marca>_<YYYYMM>.{html,xlsx,pdf}

Correr:
  venv\\Scripts\\python.exe Diagnostico\\build_scorecard.py            (todas, mes cerrado)
  venv\\Scripts\\python.exe Diagnostico\\build_scorecard.py --marca Converse --periodo 2026-05
"""
from __future__ import annotations

import argparse
import calendar
import datetime as dt
import subprocess
import time
import unicodedata
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

BASE = Path(__file__).resolve().parent
ENGINE = create_engine(
    "mssql+pyodbc://@localhost/Digital_Impact_Reportes"
    "?trusted_connection=yes&driver=ODBC+Driver+17+for+SQL+Server"
)

# web_key / Tienda_ecom RMH / property GA4 comparten el mismo rótulo de marca.
# Coliseum es el sitio multimarca (sin filtro de marca dentro). Chile fuera.
MARCAS = ["Coliseum", "New Balance", "Caterpillar", "Converse",
          "Merrell", "Steve Madden", "Umbro"]

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

# ── Margen: regla FIJA de negocio (David, jun-2026) ──────────────────────────
# GM garantizado = 42.5% sobre el PRECIO DE VENTA CON IGV (special_price en
# Magento / Precio en RMH). Costo = 57.5% de ese bruto. Cuando se trabaja sobre
# base NETA (TotalNeto, o venta_items/1.18) la tasa efectiva baja a ~32% porque
# el IGV infla el bruto sin ser margen:
#   contrib_neta = venta_bruta/1.18 − 0.575·venta_bruta = venta_bruta·0.2725
# Se aplica SIEMPRE sobre el precio de venta, NUNCA sobre FullPrecio. Las
# secciones RMH (1-3) usan la Contribucion REAL por línea; esta tasa fija es solo
# para los cortes solo-Magento (geografía) que no traen costo.
GM_BRUTO = 0.425                          # margen garantizado sobre precio con IGV
COSTO_BRUTO = 1 - GM_BRUTO                # 0.575
IGV = 1.18
CONTRIB_RATE_BRUTO = (1 / IGV) - COSTO_BRUTO   # ≈0.2725 contrib neta por sol bruto
MARGEN_NETO_FIJO = CONTRIB_RATE_BRUTO * IGV    # ≈0.3215 margen sobre venta neta

# Zona logística por departamento — réplica de map_logistics_zone (Novedades).
DEPTO_TO_ZONA = {
    "LIMA": "LIMA_CALLAO", "CALLAO": "LIMA_CALLAO",
    "PIURA": "NORTE", "TUMBES": "NORTE", "LAMBAYEQUE": "NORTE",
    "LA LIBERTAD": "NORTE", "CAJAMARCA": "NORTE", "ANCASH": "NORTE",
    "AREQUIPA": "SUR", "CUSCO": "SUR", "PUNO": "SUR", "TACNA": "SUR",
    "MOQUEGUA": "SUR", "APURIMAC": "SUR", "ICA": "SUR",
    "JUNIN": "CENTRO", "HUANCAVELICA": "CENTRO", "AYACUCHO": "CENTRO",
    "PASCO": "CENTRO", "HUANUCO": "CENTRO",
    "LORETO": "SELVA", "UCAYALI": "SELVA", "MADRE DE DIOS": "SELVA",
    "SAN MARTIN": "SELVA", "AMAZONAS": "SELVA",
}
DEPTO_ALIASES = {
    "PROV CONST DEL CALLAO": "CALLAO", "PROVINCIA CONSTITUCIONAL CALLAO": "CALLAO",
    "LIMA METROPOLITANA": "LIMA", "LIMA PROVINCE": "LIMA", "LIMA REGION": "LIMA",
    "CUZCO": "CUSCO",
}
PAYMENT_LABELS = {
    "mercadopago_adbpayment_cc": "Mercado Pago (tarjeta)",
    "mercadopago_adbpayment_yape": "Mercado Pago (Yape)",
    "mercadopago_standard": "Mercado Pago",
    "powerpay": "PowerPay", "banktransfer": "Transferencia",
    "apurata_financing": "Apurata", "izipay_izipay": "Izipay",
    "niubiz_niubiz": "Niubiz", "checkmo": "Efectivo", "free": "Gratuito",
    "paypal_express": "PayPal", "payu_latam": "PayU",
    "openpay_stores": "OpenPay", "openpay_cards": "OpenPay (tarjeta)",
}
# Courier: método de envío Magento (crudo) → etiqueta de negocio (David, jun-2026).
COURIER_LABELS = {
    "tablerate_bestway": "A Domicilio",
    "instore_pickup": "Recojo en tienda",
    "beflow_beflow": "Express (Beflow)",
}


def courier_label(raw: str) -> str:
    """Etiqueta legible para el método de envío. Conocidos → mapa; resto →
    se limpia el prefijo de método (`carrier_method` → Method) para no exponer
    el código crudo de Magento."""
    key = (raw or "").strip().lower()
    if key in COURIER_LABELS:
        return COURIER_LABELS[key]
    if key in ("", "sin dato"):
        return "Sin dato"
    pretty = key.split("_")[-1] if "_" in key else key
    return pretty.replace("-", " ").title()


# ── Helpers ──────────────────────────────────────────────────────────────────
def q(sql: str, **params) -> pd.DataFrame:
    with ENGINE.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def nz(v, default: float = 0.0) -> float:
    return default if v is None or pd.isna(v) else float(v)


def pct(num, den):
    num, den = nz(num), nz(den)
    return num / den if den else None


def yoy(cur, prev):
    cur, prev = nz(cur), nz(prev)
    return (cur / prev - 1) if prev else None


def norm_depto(raw) -> str:
    s = str(raw or "").strip().upper()
    return DEPTO_ALIASES.get(s, s) if s else "SIN DATO"


def slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s.lower().replace(" ", "_")


def closed_month(today: dt.date | None = None) -> str:
    """AnioMes (YYYY-MM) del último mes cerrado."""
    today = today or dt.date.today()
    first = today.replace(day=1)
    last_closed = first - dt.timedelta(days=1)
    return last_closed.strftime("%Y-%m")


def ly_month(ym: str) -> str:
    y, m = ym.split("-")
    return f"{int(y) - 1}-{m}"


def month_bounds(ym: str) -> tuple[dt.date, dt.date]:
    y, m = (int(x) for x in ym.split("-"))
    return dt.date(y, m, 1), dt.date(y, m, calendar.monthrange(y, m)[1])


def prev_months(ym: str, n: int) -> list[str]:
    """Lista de n AnioMes (YYYY-MM) terminando en ym, ascendente."""
    y, m = (int(x) for x in ym.split("-"))
    out = []
    for _ in range(n):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return list(reversed(out))


def semaforo(delta) -> str:
    """Verde si crece, ámbar si plano/leve caída, rojo si cae fuerte."""
    if delta is None:
        return "gris"
    if delta >= 0.05:
        return "verde"
    if delta >= -0.10:
        return "ambar"
    return "rojo"


# ── Sección 0: Tendencia 3 meses (Venta neta + MG% + CR%) ────────────────────
def sec_trend(marca: str, ym: str) -> dict:
    months = prev_months(ym, 3)
    rmh = q("""
        SELECT AnioMes, SUM(TotalNeto) AS neto, SUM(Contribucion) AS contrib
        FROM rpt.v_ventas_base
        WHERE es_ecom = 1 AND Tienda_final = :marca AND AnioMes IN (:a, :b, :c)
        GROUP BY AnioMes
    """, marca=marca, a=months[0], b=months[1], c=months[2])
    rmap = {r["AnioMes"]: r for _, r in rmh.iterrows()}
    g4 = q("""
        SELECT year_month, sessions, ecommerce_purchases
        FROM ga4_monthly_core
        WHERE property_name = :marca AND year_month IN (:a, :b, :c)
    """, marca=marca, a=months[0].replace("-", ""), b=months[1].replace("-", ""),
         c=months[2].replace("-", ""))
    gmap = {str(r["year_month"]): r for _, r in g4.iterrows()}
    points = []
    for mm in months:
        rr, gg = rmap.get(mm), gmap.get(mm.replace("-", ""))
        y, m = mm.split("-")
        points.append({
            "mes": dt.date(int(y), int(m), 1).strftime("%b %y").capitalize(),
            "neto": nz(rr["neto"]) if rr is not None else 0.0,
            "margen": pct(rr["contrib"], rr["neto"]) if rr is not None else None,
            "cr": pct(gg["ecommerce_purchases"], gg["sessions"]) if gg is not None else None,
        })
    return {"points": points}


# ── Sección 1: Performance vs año pasado (RMH + GA4, mes cerrado) ─────────────
# Renderizada como TABLA (no cards): Métrica | Mes | Año pasado | Δ. Suma 3
# métricas GA4 de la tienda (sesiones, CR de compra, bounce) cuando la web tiene
# property GA4. GA4 monthly se cruza por property_name == rótulo de marca (=
# Tienda_final RMH); para Coliseum es la web completa (multimarca).
def sec_performance(marca: str, ym: str) -> dict:
    ly = ly_month(ym)
    df = q("""
        SELECT AnioMes,
               SUM(Cantidad)             AS und,
               SUM(TotalNeto)            AS neto,
               SUM(Contribucion)         AS contrib,
               COUNT(DISTINCT Documento) AS docs
        FROM rpt.v_ventas_base
        WHERE es_ecom = 1 AND Tienda_final = :marca AND AnioMes IN (:cur, :ly)
        GROUP BY AnioMes
    """, marca=marca, cur=ym, ly=ly)
    row = {r["AnioMes"]: r for _, r in df.iterrows()}
    cur, prev = row.get(ym), row.get(ly)

    # GA4 de la tienda (sesiones, conversión de compra, bounce). char(6) sin guion.
    g4 = q("""
        SELECT year_month, sessions, engagement_rate,
               ecommerce_purchases, purchase_revenue
        FROM ga4_monthly_core
        WHERE property_name = :marca AND year_month IN (:cur, :ly)
    """, marca=marca, cur=ym.replace("-", ""), ly=ly.replace("-", ""))
    g4row = {str(r["year_month"]): r for _, r in g4.iterrows()}
    gcur, gprev = g4row.get(ym.replace("-", "")), g4row.get(ly.replace("-", ""))

    def metric(label, kind, deriv):
        c = deriv(cur) if cur is not None else None
        p = deriv(prev) if prev is not None else None
        d = yoy(c, p)
        return {"label": label, "cur": c, "prev": p, "yoy": d,
                "sem": semaforo(d), "kind": kind}

    def g4metric(label, kind, deriv, sem_invert=False):
        c = deriv(gcur) if gcur is not None else None
        p = deriv(gprev) if gprev is not None else None
        d = yoy(c, p)
        sem = semaforo(-d if (sem_invert and d is not None) else d)
        return {"label": label, "cur": c, "prev": p, "yoy": d, "sem": sem,
                "kind": kind, "invert": sem_invert}

    # Ordenadas por relevancia de negocio: plata → margen → demanda → eficiencia.
    metrics = [
        metric("Venta neta (S/)", "money", lambda r: nz(r["neto"])),
        metric("Contribución (S/)", "money", lambda r: nz(r["contrib"])),
        metric("Margen %", "pct1", lambda r: (pct(r["contrib"], r["neto"]) or 0) * 100),
        metric("Órdenes", "int", lambda r: nz(r["docs"])),
        metric("Ticket (S/)", "money", lambda r: pct(r["neto"], r["docs"]) or 0),
        metric("UPT (und/orden)", "num2", lambda r: pct(r["und"], r["docs"]) or 0),
        metric("Unidades", "int", lambda r: nz(r["und"])),
    ]
    if g4row:  # solo si la web tiene property GA4 con data en alguno de los meses
        metrics += [
            g4metric("Sesiones (GA4)", "int", lambda r: nz(r["sessions"])),
            g4metric("CR compra (GA4)", "pct2",
                     lambda r: (pct(r["ecommerce_purchases"], r["sessions"]) or 0) * 100),
            # bounce ≈ 1 − engagement_rate; sube = peor → semáforo invertido.
            g4metric("Bounce (GA4)", "pct1",
                     lambda r: (1 - nz(r["engagement_rate"])) * 100, sem_invert=True),
        ]
    excel = pd.DataFrame([{
        "AnioMes": k, "und": nz(v["und"]), "neto": nz(v["neto"]),
        "contrib": nz(v["contrib"]), "docs": int(nz(v["docs"])),
        "margen_pct": pct(v["contrib"], v["neto"]),
        "ticket": pct(v["neto"], v["docs"]),
        "ga4_sessions": nz(g4row.get(k.replace("-", ""), {}).get("sessions"))
            if k.replace("-", "") in g4row else None,
        "ga4_cr": pct(g4row[k.replace("-", "")]["ecommerce_purchases"],
                      g4row[k.replace("-", "")]["sessions"])
            if k.replace("-", "") in g4row else None,
        "ga4_bounce": (1 - nz(g4row[k.replace("-", "")]["engagement_rate"]))
            if k.replace("-", "") in g4row else None,
    } for k, v in sorted(row.items())])
    return {"metrics": metrics, "excel": excel, "cur": ym, "ly": ly}


# ── Sección 2: Distribución precio / margen por descuento (RMH, unidades) ─────
# Buckets de 10% (antes 5% — demasiado abierto), cap en "+60% dscto". Para
# Coliseum (multimarca) se ofrece desglose por marca en tabs. Toda tabla lleva
# fila TOTAL.
def _bucketize(df: pd.DataFrame) -> dict:
    """df con columnas bucket/und/neto/contrib → buckets+resumen+total."""
    df = df.copy()
    df["bucket"] = df["bucket"].fillna(0.0).clip(lower=0.0, upper=0.60)  # +60% y sobreprecio
    df = df.groupby("bucket", as_index=False).sum().sort_values("bucket")
    tot_und = df["und"].sum() or 1
    # Resumen: full price (<20% dscto, incluye 0.20) vs promo (>20%).
    full = df[df["bucket"] <= 0.20]
    promo = df[df["bucket"] > 0.20]
    resumen = {
        "full_share": full["und"].sum() / tot_und,
        "promo_share": promo["und"].sum() / tot_und,
        "margen_full": pct(full["contrib"].sum(), full["neto"].sum()),
        "margen_promo": pct(promo["contrib"].sum(), promo["neto"].sum()),
    }
    buckets = []
    for _, r in df.iterrows():
        if r["bucket"] <= 0:
            rango = "Full price"
        elif r["bucket"] >= 0.60:
            rango = "+60% dscto"
        else:
            rango = f"{r['bucket']*100:.0f}% dscto"
        buckets.append({
            "rango": rango, "und": int(r["und"]), "share": r["und"] / tot_und,
            "neto": r["neto"], "margen": pct(r["contrib"], r["neto"]),
        })
    total = {"und": int(df["und"].sum()), "share": 1.0,
             "neto": df["neto"].sum(), "margen": pct(df["contrib"].sum(), df["neto"].sum())}
    return {"buckets": buckets, "resumen": resumen, "total": total}


def sec_precios(marca: str, ym: str) -> dict:
    # Q10 del GPM: dscto = 1 - Precio/FullPrecio; base UNIDADES. Sobreprecio
    # (bucket negativo) cuenta como full price.
    df = q("""
        SELECT Marca, bucket,
               SUM(Cantidad)     AS und,
               SUM(TotalNeto)    AS neto,
               SUM(Contribucion) AS contrib
        FROM (
            SELECT Marca, Cantidad, TotalNeto, Contribucion,
                   ROUND((1 - Precio / NULLIF(FullPrecio, 0)) / 0.10, 0) * 0.10 AS bucket
            FROM rpt.v_ventas_base
            WHERE es_ecom = 1 AND Tienda_final = :marca AND AnioMes = :ym
              AND FullPrecio > 0
        ) t
        GROUP BY Marca, bucket
    """, marca=marca, ym=ym)
    if df.empty:
        return {"groups": [], "excel": df}
    cols = ["bucket", "und", "neto", "contrib"]
    groups = [{"tab": "Todas", **_bucketize(df[cols])}]
    if marca == "Coliseum":  # desglose por marca (tabs) solo en la web multimarca
        por_marca = df.groupby("Marca")["und"].sum().sort_values(ascending=False)
        for mk in por_marca.head(8).index:
            sub = df[df["Marca"] == mk]
            groups.append({"tab": str(mk).title(), **_bucketize(sub[cols])})
    return {"groups": groups, "excel": df}


# ── Sección 3: Top productos (RMH + GA4 + curva de stock, mes cerrado) ────────
# Grano PADRE = CodColor (el configurable de Magento / estilo-color). CodigoGp es
# el sku hijo con talla (= sku simple de Magento); aquí interesa el modelo, no la
# talla. CodColor cruza con GA4 (GA4_Url_CodColor_Mensual) y con el stock.
#   · Sesiones (GA4)  : tráfico del modelo en el mes.
#   · % curvado       : cobertura de tallas = codigoGp con stock / codigoGp del
#                       modelo, al último snapshot de stock ≤ fin de mes.
#   · Marca           : se muestra en la tabla solo para Coliseum (multimarca).
# El Excel ("TopProductos") trae TODO el catálogo con stock, con es_top marcado.
def sec_top_productos(marca: str, ym: str) -> dict:
    d1, d2 = month_bounds(ym)
    is_coliseum = (marca == "Coliseum")

    ventas = q("""
        SELECT CodColor,
               MAX(Descripcion)         AS descripcion,
               MAX([Linea.])            AS linea,
               MAX(Marca)               AS marca,
               COUNT(DISTINCT CodigoGp) AS skus,
               SUM(Cantidad)            AS und,
               SUM(TotalNeto)           AS neto,
               SUM(Contribucion)        AS contrib
        FROM rpt.v_ventas_base
        WHERE es_ecom = 1 AND Tienda_final = :marca AND AnioMes = :ym
          AND CodColor IS NOT NULL AND CodColor NOT LIKE '%bolsa%'
        GROUP BY CodColor
    """, marca=marca, ym=ym)

    # Catálogo con stock al último snapshot ≤ fin de mes (curva de tallas).
    # El stock son snapshots diarios L-V por tienda física; se toma la FECHA
    # MÁXIMA dentro del rango (= fin de mes cerrado / más reciente para el actual).
    # % curvado = cobertura de tallas DISPONIBLES EN WEB (Integrada LIKE 'S%'),
    # NO el stock de todas las tiendas (eso inflaba la cobertura a 100%).
    stock = q("""
        WITH snap AS (
            SELECT MAX(TRY_CONVERT(date, Fecha, 105)) AS d
            FROM Stock_Solidez_RMH
            WHERE TRY_CONVERT(date, Fecha, 105) <= :d2
        )
        SELECT s.Codcolor AS CodColor,
               MAX(s.Marca_Limpia)      AS marca_stock,
               MAX(s.Descripcion)       AS desc_stock,
               COUNT(DISTINCT CASE WHEN s.Integrada LIKE 'S%' THEN s.codigoGp END) AS tallas,
               COUNT(DISTINCT CASE WHEN s.stock > 0 AND s.Integrada LIKE 'S%' THEN s.codigoGp END) AS tallas_stock,
               SUM(CASE WHEN s.stock > 0 AND s.Integrada LIKE 'S%' THEN s.stock ELSE 0 END) AS unidades_stock
        FROM Stock_Solidez_RMH s CROSS JOIN snap
        WHERE TRY_CONVERT(date, s.Fecha, 105) = snap.d
          AND s.Codcolor IS NOT NULL AND s.Codcolor <> '' AND s.Codcolor NOT LIKE '%bolsa%'
          AND (:all_marcas = 1 OR UPPER(s.Marca_Limpia) = UPPER(:marca))
        GROUP BY s.Codcolor
    """, d2=d2, marca=marca, all_marcas=1 if is_coliseum else 0)

    # Vistas de producto GA4: item_id = CodColor (tabla ga4_monthly_items, viva).
    g4 = q("""
        SELECT item_id AS CodColor, SUM(items_viewed) AS sessions
        FROM ga4_monthly_items
        WHERE property_name = :web AND year_month = :yyyymm
        GROUP BY item_id
    """, web=marca, yyyymm=ym.replace("-", ""))

    for d in (ventas, stock, g4):
        if not d.empty:
            d["CodColor"] = d["CodColor"].astype(str).str.strip()
    g4map = dict(zip(g4["CodColor"], g4["sessions"])) if not g4.empty else {}

    cat = ventas.merge(stock, on="CodColor", how="outer") if not stock.empty else ventas.copy()
    for c in ("und", "neto", "contrib", "skus", "tallas", "tallas_stock", "unidades_stock"):
        if c in cat:
            cat[c] = cat[c].fillna(0)
    if "marca_stock" in cat:
        cat["marca"] = cat["marca"].fillna(cat["marca_stock"])
        cat["descripcion"] = cat["descripcion"].fillna(cat["desc_stock"])
    cat["sessions"] = cat["CodColor"].map(g4map).fillna(0).astype(int)
    cat["cobertura"] = cat.apply(
        lambda r: (r["tallas_stock"] / r["tallas"]) if r.get("tallas", 0) else None, axis=1)
    cobertura = dict(zip(cat["CodColor"], cat["cobertura"]))

    top = cat[cat["und"] > 0].sort_values("und", ascending=False).head(15)
    top_codes = set(top["CodColor"])
    cat["es_top"] = cat["CodColor"].isin(top_codes)
    rows = []
    for _, r in top.iterrows():
        rows.append({
            "codigo": r["CodColor"], "descripcion": str(r["descripcion"] or "")[:46],
            "marca": str(r["marca"] or "").title(), "skus": int(nz(r["skus"])),
            "und": int(nz(r["und"])), "neto": nz(r["neto"]),
            "margen": pct(r["contrib"], r["neto"]),
            "sesiones": int(nz(r["sessions"])),
            "cobertura": cobertura.get(r["CodColor"]),
        })

    # Imagen del producto: Catalogo_Productos (réplica del catálogo Magento vía
    # /api/export/catalog), join por mc = CodColor. Un mc tiene una sola imagen.
    try:
        img = q("SELECT mc, MAX(base_image) AS img FROM dbo.Catalogo_Productos "
                "WHERE base_image IS NOT NULL AND base_image <> '' GROUP BY mc")
        imgmap = dict(zip(img["mc"].astype(str).str.strip(), img["img"]))
    except Exception:
        imgmap = {}
    for r in rows:
        r["imagen"] = imgmap.get(r["codigo"])
    if imgmap:
        cat["base_image"] = cat["CodColor"].map(imgmap)

    # % que el top representa del mes (unidades y venta neta) + stock total web.
    tot_und = cat["und"].sum() or 1
    tot_neto = cat["neto"].sum() or 1
    top_und = top["und"].sum()
    top_neto = top["neto"].sum()
    resumen = {
        "share_und": top_und / tot_und, "share_neto": top_neto / tot_neto,
        "top_und": int(top_und), "top_neto": float(top_neto),
        "stock_web_und": int(nz(cat["unidades_stock"].sum())) if "unidades_stock" in cat else 0,
        "venta_mes_und": int(tot_und),
    }
    # GA4 por producto: sin productor en repo → si todo es 0, ocultamos la columna.
    has_sesiones = any(r["sesiones"] > 0 for r in rows)

    excel = cat.sort_values(["es_top", "und", "sessions"], ascending=[False, False, False])
    keep = [c for c in ["CodColor", "marca", "descripcion", "base_image", "linea", "skus", "und",
                        "neto", "contrib", "sessions", "tallas", "tallas_stock",
                        "cobertura", "unidades_stock", "es_top"] if c in excel.columns]
    excel = excel[keep]
    return {"rows": rows, "is_coliseum": is_coliseum, "resumen": resumen,
            "has_sesiones": has_sesiones, "excel": excel}


# ── Sección 4: Geografía + GM estimado por zona (Magento) ────────────────────
# GM estimado = venta NETA × MARGEN REAL del mes (de la sec. 1, RMH). Antes usaba
# la tasa fija 42.5% sobre venta bruta → daba ~130k cuando el GM real del mes era
# ~78k (mes muy promocional, margen 19%). Con el margen real, el total de GM por
# zona/courier/pago reconcilia con la Contribución del cuadro de Performance.
def sec_geografia(marca: str, ym: str, margin: float | None = None) -> dict:
    rate = margin if margin is not None else MARGEN_NETO_FIJO
    d1, d2 = month_bounds(ym)
    df = q("""
        SELECT departamento,
               COUNT(*)               AS ordenes,
               SUM(unidades_confirmadas) AS unidades,
               SUM(venta_items)       AS venta,
               AVG(CAST(envio_cobrado AS float)) AS envio_prom
        FROM dbo.vw_magento_orders_pedido
        WHERE pago_confirmado = 1 AND web_key = :web
          AND fecha BETWEEN :d1 AND :d2
        GROUP BY departamento
    """, web=marca, d1=d1, d2=d2)
    agg: dict[str, dict] = {}
    for _, r in df.iterrows():
        dep = norm_depto(r["departamento"])
        zona = DEPTO_TO_ZONA.get(dep, "OTROS")
        x = agg.setdefault(zona, {"zona": zona, "ordenes": 0, "unidades": 0,
                                  "venta": 0.0, "envio": 0.0, "n": 0, "deptos": {}})
        x["ordenes"] += int(nz(r["ordenes"]))
        x["unidades"] += int(nz(r["unidades"]))
        x["venta"] += nz(r["venta"])
        x["envio"] += nz(r["envio_prom"]) * int(nz(r["ordenes"]))
        x["n"] += int(nz(r["ordenes"]))
        x["deptos"][dep] = x["deptos"].get(dep, 0) + int(nz(r["ordenes"]))
    rows = sorted(agg.values(), key=lambda x: -x["venta"])
    tot_ord = sum(x["ordenes"] for x in rows) or 1
    for x in rows:
        venta_neta = x["venta"] / IGV
        # GM est. en soles = venta NETA de la zona × margen real del mes. El margen
        # real (de RMH, sec. 1) reemplaza la tasa fija para que el total reconcilie
        # con la Contribución de Performance. El envío prom. es la señal de costo
        # logístico que SÍ varía por zona.
        x["contrib_est"] = venta_neta * rate
        x["venta_neta"] = venta_neta
        x["envio_prom"] = x["envio"] / x["n"] if x["n"] else 0.0
        x["upt"] = x["unidades"] / x["ordenes"] if x["ordenes"] else 0.0  # und x ticket
        x["share"] = x["ordenes"] / tot_ord
        x["top_deptos"] = ", ".join(
            f"{d} ({n})" for d, n in sorted(x["deptos"].items(), key=lambda kv: -kv[1])[:3])
    tot_und = sum(x["unidades"] for x in rows)
    tot_venta_neta = sum(x["venta_neta"] for x in rows)
    total = {
        "ordenes": tot_ord, "unidades": tot_und, "share": 1.0,
        "venta_neta": tot_venta_neta, "contrib_est": sum(x["contrib_est"] for x in rows),
        "upt": tot_und / tot_ord if tot_ord else 0.0,
        "envio_prom": (sum(x["envio"] for x in rows) / sum(x["n"] for x in rows))
                      if sum(x["n"] for x in rows) else 0.0,
    }
    excel = pd.DataFrame([{
        "zona": x["zona"], "ordenes": x["ordenes"], "unidades": x["unidades"],
        "upt": x["upt"], "venta_items": x["venta"], "venta_neta": x["venta_neta"],
        "envio_prom": x["envio_prom"], "gm_est": x["contrib_est"],
        "share_ordenes": x["share"], "top_deptos": x["top_deptos"],
    } for x in rows])
    return {"rows": rows, "total": total, "excel": excel}


# ── Sección 5: Courier (Magento) ─────────────────────────────────────────────
def sec_courier(marca: str, ym: str, margin: float | None = None) -> dict:
    rate = margin if margin is not None else MARGEN_NETO_FIJO
    d1, d2 = month_bounds(ym)
    df = q("""
        SELECT COALESCE(NULLIF(LTRIM(RTRIM(courrier)), ''), 'sin dato') AS courier,
               COUNT(*)               AS ordenes,
               SUM(unidades_confirmadas) AS unidades,
               SUM(venta_items)       AS venta,
               AVG(CAST(envio_cobrado AS float)) AS envio_prom
        FROM dbo.vw_magento_orders_pedido
        WHERE pago_confirmado = 1 AND web_key = :web
          AND fecha BETWEEN :d1 AND :d2
        GROUP BY COALESCE(NULLIF(LTRIM(RTRIM(courrier)), ''), 'sin dato')
        ORDER BY COUNT(*) DESC
    """, web=marca, d1=d1, d2=d2)
    tot = df["ordenes"].sum() or 1
    rows = [{
        "courier": courier_label(r["courier"]), "ordenes": int(r["ordenes"]),
        "share": r["ordenes"] / tot, "unidades": int(nz(r["unidades"])),
        "venta": nz(r["venta"]) / IGV,                       # venta neta
        "gm_est": (nz(r["venta"]) / IGV) * rate,             # GM est. (margen real)
    } for _, r in df.iterrows()]
    venta_neta_tot = nz(df["venta"].sum()) / IGV
    total = {
        "ordenes": int(df["ordenes"].sum()), "share": 1.0,
        "unidades": int(df["unidades"].sum()), "venta": venta_neta_tot,
        "gm_est": venta_neta_tot * rate,
    }
    return {"rows": rows, "total": total, "excel": df}


# ── Sección 6: Medios de pago (Magento) ──────────────────────────────────────
def sec_pagos(marca: str, ym: str, margin: float | None = None) -> dict:
    rate = margin if margin is not None else MARGEN_NETO_FIJO
    d1, d2 = month_bounds(ym)
    df = q("""
        SELECT payment_method,
               COUNT(*) AS total,
               SUM(CASE WHEN pago_confirmado = 1 THEN 1 ELSE 0 END) AS confirmadas,
               SUM(CASE WHEN pago_confirmado = 1 THEN venta_items ELSE 0 END) AS venta,
               SUM(CASE WHEN pago_confirmado = 1 THEN unidades_confirmadas ELSE 0 END) AS unidades
        FROM dbo.vw_magento_orders_pedido
        WHERE web_key = :web AND fecha BETWEEN :d1 AND :d2
          AND payment_method IS NOT NULL AND payment_method <> ''
        GROUP BY payment_method ORDER BY COUNT(*) DESC
    """, web=marca, d1=d1, d2=d2)
    tot_conf = df["confirmadas"].sum() or 1
    rows = [{
        "label": PAYMENT_LABELS.get(r["payment_method"], r["payment_method"]),
        "total": int(r["total"]), "confirmadas": int(nz(r["confirmadas"])),
        "conv": pct(r["confirmadas"], r["total"]),
        "share": nz(r["confirmadas"]) / tot_conf, "venta": nz(r["venta"]) / IGV,  # neta
        "ticket": pct(nz(r["venta"]) / IGV, r["confirmadas"]),     # ticket medio neto
        "upt": pct(r["unidades"], r["confirmadas"]),               # und x ticket
        "gm_est": (nz(r["venta"]) / IGV) * rate,                   # GM est. (margen real)
    } for _, r in df.iterrows()]
    venta_neta_tot = nz(df["venta"].sum()) / IGV
    total = {
        "confirmadas": int(df["confirmadas"].sum()), "share": 1.0,
        "venta": venta_neta_tot,
        "ticket": pct(venta_neta_tot, df["confirmadas"].sum()),
        "upt": pct(df["unidades"].sum(), df["confirmadas"].sum()),
        "gm_est": venta_neta_tot * rate,
    }
    return {"rows": rows, "total": total, "excel": df}


# ── Sección 7: Recompra (Magento, customer_id) ───────────────────────────────
def sec_recompra(marca: str, ym: str) -> dict:
    d1, d2 = month_bounds(ym)
    # Para cada orden pagada del mes, ¿el cliente tenía una orden pagada ANTERIOR
    # (desde feb-2026)? ROW_NUMBER por cliente en UNA pasada (la subconsulta
    # correlacionada anterior tardaba ~10s al re-materializar la vista por orden).
    # rn>1 ⇒ no es la primera compra del cliente ⇒ recurrente.
    df = q("""
        WITH ped AS (
            SELECT id, customer_id, fecha,
                   ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY fecha, id) AS rn
            FROM dbo.vw_magento_orders_pedido
            WHERE pago_confirmado = 1 AND web_key = :web
              AND customer_id IS NOT NULL AND customer_id > 0
        )
        SELECT id, customer_id, CASE WHEN rn > 1 THEN 1 ELSE 0 END AS es_recurrente
        FROM ped
        WHERE fecha BETWEEN :d1 AND :d2
    """, web=marca, d1=d1, d2=d2)
    n = len(df)
    if n == 0:
        return {"kpi": {}, "excel": df}
    # Órdenes totales del mes (incluye invitado / sin customer_id) para contexto.
    tot = q("""
        SELECT COUNT(*) AS n FROM dbo.vw_magento_orders_pedido
        WHERE pago_confirmado = 1 AND web_key = :web AND fecha BETWEEN :d1 AND :d2
    """, web=marca, d1=d1, d2=d2)
    ordenes_total = int(nz(tot["n"].iloc[0])) if not tot.empty else n
    recurrentes = int(df["es_recurrente"].sum())
    kpi = {
        "ordenes_total": ordenes_total,
        "ordenes_mes": n,
        "recurrentes": recurrentes,
        "share_recurrente": recurrentes / n,
        "clientes_unicos": int(df["customer_id"].nunique()),
    }
    excel = pd.DataFrame([{
        "segmento": "recurrente (compra previa)", "ordenes": recurrentes,
        "share": recurrentes / n,
    }, {
        "segmento": "nuevo (primera compra)", "ordenes": n - recurrentes,
        "share": (n - recurrentes) / n,
    }])
    return {"kpi": kpi, "excel": excel}


# ── Sección 8: Cliente — ventas y categorías por género (RMH + género de stock) ─
# El género es del PRODUCTO (no del cliente): se cruza CodColor → Genero del
# catálogo de stock. % ventas por género + top categorías favoritas por género.
# El rango de edad del cliente (GA4) queda pendiente: necesita una extracción
# demográfica nueva (hoy GA4 no la trae a estas tablas).
def sec_cliente(marca: str, ym: str) -> dict:
    # Normalizar Genero (UPPER+TRIM) para no duplicar 'MUJER' vs 'Mujer ' al agrupar.
    df = q("""
        SELECT genero, categoria, SUM(neto) AS neto, SUM(und) AS und
        FROM (
            SELECT COALESCE(NULLIF(UPPER(LTRIM(RTRIM(g.Genero))), ''), 'SIN DATO') AS genero,
                   v.Categoria AS categoria, v.TotalNeto AS neto, v.Cantidad AS und
            FROM rpt.v_ventas_base v
            LEFT JOIN (
                SELECT Codcolor, MAX(Genero) AS Genero
                FROM Stock_Solidez_RMH WHERE Codcolor <> '' GROUP BY Codcolor
            ) g ON g.Codcolor = v.CodColor
            WHERE v.es_ecom = 1 AND v.Tienda_final = :marca AND v.AnioMes = :ym
        ) t
        GROUP BY genero, categoria
    """, marca=marca, ym=ym)
    if df.empty:
        return {"rows": [], "excel": df}
    tot_neto = df["neto"].sum() or 1
    rows = []
    for gen, sub in df.groupby("genero"):
        cats = sub.groupby("categoria")["und"].sum().sort_values(ascending=False).head(3)
        top_cats = ", ".join(f"{c} ({int(u)})" for c, u in cats.items() if c)
        rows.append({
            "genero": str(gen).title(), "neto": nz(sub["neto"].sum()),
            "share": nz(sub["neto"].sum()) / tot_neto, "und": int(nz(sub["und"].sum())),
            "top_cats": top_cats or "—",
        })
    rows.sort(key=lambda r: -r["neto"])
    return {"rows": rows, "excel": df}


# ── Sección 9: GA4 canales × responsable (Equivalencias_Canales) ─────────────
# Cruza ga4_monthly_channels con la tabla Equivalencias_Canales (canal →
# responsable). Para C-level: vista general de tráfico/venta por responsable de
# canal. Para Marketing (fase 2): el detalle por canal/agrupador.
def sec_canales(marca: str, ym: str) -> dict:
    df = q("""
        SELECT c.session_default_channel_group AS grupo,
               e.canal, e.responsable,
               c.sessions, c.purchase_revenue AS revenue, c.ecommerce_purchases AS compras
        FROM ga4_monthly_channels c
        LEFT JOIN Equivalencias_Canales e
          ON e.session_default_channel_group = c.session_default_channel_group
        WHERE c.property_name = :marca AND c.year_month = :ym
    """, marca=marca, ym=ym.replace("-", ""))
    if df.empty:
        return {"por_responsable": [], "por_canal": [], "excel": df}
    df["responsable"] = df["responsable"].fillna("Sin asignar")
    df["canal"] = df["canal"].fillna("Sin asignar")
    tot = df["sessions"].sum() or 1
    tot_rev = df["revenue"].sum() or 1

    def agg(col):
        g = (df.groupby(col)
             .agg(sessions=("sessions", "sum"), revenue=("revenue", "sum"),
                  compras=("compras", "sum"))
             .reset_index().sort_values("sessions", ascending=False))
        return [{
            col: r[col], "sessions": int(nz(r["sessions"])),
            "share": nz(r["sessions"]) / tot, "revenue": nz(r["revenue"]),
            "share_rev": nz(r["revenue"]) / tot_rev,
            "cr": pct(r["compras"], r["sessions"]),
        } for _, r in g.iterrows()]

    # Foco paid (Marketing): agrupa los channel groups pagados en 3 cubetas.
    paid_sets = {
        "Paid Social": ["Paid Social"],
        "Paid Search + Cross-network": ["Paid Search", "Cross-network"],
        "Otro paid (Display/Shopping/Video)": ["Display", "Paid Shopping", "Paid Video", "Paid Other"],
    }
    paid = []
    for etiqueta, grupos in paid_sets.items():
        sub = df[df["grupo"].isin(grupos)]
        if sub.empty:
            continue
        ses = nz(sub["sessions"].sum())
        paid.append({
            "bucket": etiqueta, "sessions": int(ses), "share": ses / tot,
            "revenue": nz(sub["revenue"].sum()),
            "cr": pct(sub["compras"].sum(), sub["sessions"].sum()),
        })

    return {"por_responsable": agg("responsable"), "por_agrupador": agg("canal"),
            "por_canal": agg("grupo"), "paid": paid, "excel": df}




# ── Sección 11: Funnel GA4 (ga4_monthly_rates) — Marketing ───────────────────
def sec_funnel(marca: str, ym: str) -> dict:
    ly = ly_month(ym)
    df = q("""
        SELECT year_month, cart_to_view_rate, purchase_to_view_rate,
               session_purchase_key_event_rate, items_viewed, items_added_to_cart
        FROM ga4_monthly_rates
        WHERE property_name = :marca AND year_month IN (:cur, :ly)
    """, marca=marca, cur=ym.replace("-", ""), ly=ly.replace("-", ""))
    row = {str(r["year_month"]): r for _, r in df.iterrows()}
    cur, prev = row.get(ym.replace("-", "")), row.get(ly.replace("-", ""))
    if cur is None:
        return {"metrics": []}

    def m(label, kind, deriv, invert=False):
        c = deriv(cur) if cur is not None else None
        p = deriv(prev) if prev is not None else None
        d = yoy(c, p)
        sem = semaforo(-d if (invert and d is not None) else d)
        return {"label": label, "cur": c, "prev": p, "yoy": d, "sem": sem, "kind": kind}

    metrics = [
        m("View→Cart rate", "pct1", lambda r: nz(r["cart_to_view_rate"]) * 100),
        m("View→Compra rate", "pct2", lambda r: nz(r["purchase_to_view_rate"]) * 100),
        m("CR compra (sesión)", "pct2", lambda r: nz(r["session_purchase_key_event_rate"]) * 100),
        m("Items vistos", "int", lambda r: nz(r["items_viewed"])),
        m("Items al carrito", "int", lambda r: nz(r["items_added_to_cart"])),
    ]
    # Etapas para el embudo visual. Benchmark típico ecommerce moda (pct de vistas):
    # add-to-cart ~10%, compra ~2.5% (referencia para detectar fugas).
    iv = nz(cur["items_viewed"]); ic = nz(cur["items_added_to_cart"])
    c2v = nz(cur["cart_to_view_rate"]); p2v = nz(cur["purchase_to_view_rate"])
    stages = [
        {"label": "Vistas de producto", "value": iv, "pct": 1.0, "bench": None},
        {"label": "Agregado a carrito", "value": ic, "pct": c2v, "bench": 0.10},
        {"label": "Compra", "value": iv * p2v, "pct": p2v, "bench": 0.025},
    ]
    return {"metrics": metrics, "stages": stages}


# ── Sección 13: Búsquedas in-site (ga4_search_terms) — Comercial + Marketing ──
def sec_search(marca: str, ym: str) -> dict:
    yyyymm = ym.replace("-", "")
    tot = q("""
        SELECT SUM(sessions) AS n FROM vw_ga4_search_terms_monthly_norm
        WHERE property_name = :marca AND year_month = :ym
    """, marca=marca, ym=yyyymm)
    total = nz(tot["n"].iloc[0]) if not tot.empty else 0
    df = q("""
        SELECT TOP 15 term_norm AS search_term,
               SUM(sessions) AS sessions, SUM(total_users) AS total_users
        FROM vw_ga4_search_terms_monthly_norm
        WHERE property_name = :marca AND year_month = :ym
        GROUP BY term_norm
        ORDER BY SUM(sessions) DESC
    """, marca=marca, ym=yyyymm)
    if df.empty:
        return {"rows": [], "excel": df}
    rows = [{
        "term": str(r["search_term"]), "sessions": int(nz(r["sessions"])),
        "share": (nz(r["sessions"]) / total) if total else None,
        "users": int(nz(r["total_users"])),
    } for _, r in df.iterrows()]
    return {"rows": rows, "total": int(total), "excel": df}


# ── Sección 15: GSC búsqueda orgánica Google (gsc_monthly_*) ──────────────────
# clicks/impresiones/CTR/posición del mes (vs LY) + top queries orgánicas.
# Posición: menor es mejor → semáforo invertido. Fila no tiene sitio GSC.
def sec_gsc(marca: str, ym: str) -> dict:
    cur_ym, ly_ym = ym.replace("-", ""), ly_month(ym).replace("-", "")
    core = q("""
        SELECT year_month, clicks, impressions, ctr, position
        FROM gsc_monthly_core
        WHERE property_name = :m AND year_month IN (:cur, :ly)
    """, m=marca, cur=cur_ym, ly=ly_ym)
    crow = {str(r["year_month"]): r for _, r in core.iterrows()}
    cur, prev = crow.get(cur_ym), crow.get(ly_ym)
    if cur is None:
        return {"kpis": [], "queries": [], "excel": pd.DataFrame()}

    def met(label, kind, key, invert=False):
        c = nz(cur[key]) if cur is not None else None
        p = nz(prev[key]) if prev is not None else None
        d = yoy(c, p)
        sem = semaforo(-d if (invert and d is not None) else d)
        return {"label": label, "cur": c, "prev": p, "yoy": d, "sem": sem,
                "kind": kind, "invert": invert}

    kpis = [
        met("Clicks orgánicos", "int", "clicks"),
        met("Impresiones", "int", "impressions"),
        met("CTR orgánico", "pctf", "ctr"),
        met("Posición media", "num1", "position", invert=True),
    ]
    qs = q("""
        SELECT TOP 12 query, clicks, impressions, ctr, position
        FROM gsc_monthly_queries
        WHERE property_name = :m AND year_month = :ym
        ORDER BY clicks DESC
    """, m=marca, ym=cur_ym)
    tot_clicks = nz(cur["clicks"]) or 1
    queries = [{
        "query": str(r["query"]), "clicks": int(nz(r["clicks"])),
        "impressions": int(nz(r["impressions"])), "share": nz(r["clicks"]) / tot_clicks,
        "ctr": nz(r["ctr"]), "position": nz(r["position"]),
    } for _, r in qs.iterrows()]
    return {"kpis": kpis, "queries": queries, "excel": qs}


# ── Sección 12: Devices GA4 (ga4_monthly_devices) — Marketing ────────────────
def sec_devices(marca: str, ym: str) -> dict:
    df = q("""
        SELECT device_category, sessions, ecommerce_purchases AS compras,
               purchase_revenue AS revenue, session_purchase_key_event_rate AS cr
        FROM ga4_monthly_devices
        WHERE property_name = :marca AND year_month = :ym
        ORDER BY sessions DESC
    """, marca=marca, ym=ym.replace("-", ""))
    if df.empty:
        return {"rows": []}
    tot = df["sessions"].sum() or 1
    tot_rev = df["revenue"].sum() or 1
    rows = [{
        "device": str(r["device_category"]).title(), "sessions": int(nz(r["sessions"])),
        "share": nz(r["sessions"]) / tot, "compras": int(nz(r["compras"])),
        "revenue": nz(r["revenue"]), "share_rev": nz(r["revenue"]) / tot_rev,
        "cr": pct(r["compras"], r["sessions"]),
    } for _, r in df.iterrows()]
    return {"rows": rows}


# ── Sección 14: Campañas GA4 (ga4_monthly_campaigns) — Marketing ──────────────
# sessionCampaignName a grano mensual. GA4 mete el tráfico no-campaña en buckets
# "(direct)/(organic)/(referral)/(not set)/(data not available)"; se separan de
# las campañas NOMBRADAS (lo que Marketing quiere accionar). CR = compras/sesiones
# derivado en consumo (no se promedia una tasa). Revenue es atribución GA4, no RMH.
# OJO: Coliseum (web multimarca) no trae campañas — se atribuyen a las properties
# de cada marca; para esa web la sección no renderiza (guard en render_html).
NO_CAMPAIGN = {"(not set)", "(direct)", "(organic)", "(referral)",
               "(data not available)", "(none)", ""}


def sec_campaigns(marca: str, ym: str) -> dict:
    df = q("""
        SELECT session_campaign_name AS campania,
               SUM(sessions)            AS sessions,
               SUM(ecommerce_purchases) AS compras,
               SUM(purchase_revenue)    AS revenue
        FROM ga4_monthly_campaigns
        WHERE property_name = :marca AND year_month = :ym
        GROUP BY session_campaign_name
    """, marca=marca, ym=ym.replace("-", ""))
    if df.empty:
        return {"rows": [], "excel": df}
    df["campania"] = df["campania"].astype(str).str.strip()
    df["es_campana"] = ~df["campania"].str.lower().isin(NO_CAMPAIGN)
    tot_ses = df["sessions"].sum() or 1
    named = df[df["es_campana"]].sort_values("sessions", ascending=False)
    rows = [{
        # nombre completo (taxonomía con pipes); cap defensivo para strings raros.
        "campania": (str(r["campania"])[:90] + "…") if len(str(r["campania"])) > 91
                    else str(r["campania"]),
        "sessions": int(nz(r["sessions"])),
        "share": nz(r["sessions"]) / tot_ses,         # sobre TODO el tráfico del periodo
        "compras": int(nz(r["compras"])),
        "revenue": nz(r["revenue"]),
        "cr": pct(r["compras"], r["sessions"]),
    } for _, r in named.head(15).iterrows()]
    resumen = {
        "named_count": int(df["es_campana"].sum()),
        "named_sessions": int(named["sessions"].sum()),
        "named_share": (named["sessions"].sum() / tot_ses) if tot_ses else 0.0,
        "named_revenue": float(named["revenue"].sum()),
        "named_compras": int(named["compras"].sum()),
        "total_sessions": int(tot_ses),
    }
    excel = df.sort_values(["es_campana", "sessions"], ascending=[False, False])[
        ["campania", "es_campana", "sessions", "compras", "revenue"]]
    return {"rows": rows, "resumen": resumen, "excel": excel}


# ── Sección 10: Hallazgos (síntesis) ─────────────────────────────────────────
def sec_hallazgos(secs: dict) -> list[dict]:
    h: list[dict] = []
    perf = secs.get("performance", {})
    by_label = {m["label"]: m for m in perf.get("metrics", [])}
    venta = by_label.get("Venta neta (S/)")
    if venta and venta["yoy"] is not None:
        if venta["yoy"] <= -0.10:
            h.append(dict(sev="alta",
                texto=f"Venta neta {venta['yoy']*100:+.0f}% vs año pasado "
                      f"(S/{venta['cur']:,.0f} vs S/{venta['prev']:,.0f}). Caída relevante."))
        elif venta["yoy"] >= 0.10:
            h.append(dict(sev="info",
                texto=f"Venta neta {venta['yoy']*100:+.0f}% vs año pasado — crecimiento sólido."))
    margen = by_label.get("Margen %")
    if margen and margen["cur"]:
        if margen["cur"] < 30:
            h.append(dict(sev="alta",
                texto=f"Margen del mes en {margen['cur']:.0f}% (bajo). Revisar mix promocional/descuentos."))
        elif margen["yoy"] is not None and margen["yoy"] <= -0.08:
            h.append(dict(sev="media",
                texto=f"Margen se comprime {margen['yoy']*100:+.0f}% vs LY ({margen['cur']:.0f}%)."))
    pre_groups = secs.get("precios", {}).get("groups", [])
    pr = pre_groups[0]["resumen"] if pre_groups else {}
    if pr.get("promo_share") is not None and pr["promo_share"] >= 0.50:
        h.append(dict(sev="media",
            texto=f"{pr['promo_share']*100:.0f}% de las unidades se vendieron con ≥20% de descuento "
                  f"(margen promo {(pr.get('margen_promo') or 0)*100:.0f}% vs full {(pr.get('margen_full') or 0)*100:.0f}%)."))
    top = secs.get("top_productos", {}).get("rows", [])
    if top:
        tot_und = sum(r["und"] for r in top)
        top3 = sum(r["und"] for r in top[:3])
        if tot_und and top3 / tot_und >= 0.40:
            h.append(dict(sev="info",
                texto=f"Top 3 productos concentran {top3/tot_und*100:.0f}% de las unidades del top 15 — "
                      f"alta dependencia de pocos modelos."))
    pagos = secs.get("pagos", {}).get("rows", [])
    for p in pagos:
        if p["total"] >= 30 and p["conv"] is not None and p["conv"] < 0.6:
            h.append(dict(sev="media",
                texto=f"Medio de pago «{p['label']}» convierte {p['conv']*100:.0f}% "
                      f"({p['confirmadas']}/{p['total']}) — fricción en checkout."))
    rec = secs.get("recompra", {}).get("kpi", {})
    if rec.get("share_recurrente") is not None and rec["ordenes_mes"] >= 30:
        if rec["share_recurrente"] < 0.20:
            h.append(dict(sev="media",
                texto=f"Solo {rec['share_recurrente']*100:.0f}% de las órdenes son de clientes recurrentes "
                      f"— baja recompra (ventana feb-2026+)."))
    geo = secs.get("geografia", {}).get("rows", [])
    if geo:
        lima = next((g for g in geo if g["zona"] == "LIMA_CALLAO"), None)
        if lima and lima["share"] >= 0.65:
            h.append(dict(sev="info",
                texto=f"Lima/Callao concentra {lima['share']*100:.0f}% de las órdenes — "
                      f"oportunidad de expansión a provincia."))

    # ── GA4 / dispositivos / funnel / consistencia / stock ──────────────────
    pm = {m["label"]: m for m in secs.get("performance", {}).get("metrics", [])}
    bounce = pm.get("Bounce (GA4)")
    if bounce and bounce["yoy"] is not None and bounce["yoy"] >= 0.08 and bounce["cur"]:
        h.append(dict(sev="media",
            texto=f"Bounce sube {bounce['yoy']*100:+.0f}% vs LY ({bounce['cur']:.0f}%) — peor enganche del tráfico."))
    paid = secs.get("canales", {}).get("paid", [])
    psoc = next((x for x in paid if x["bucket"] == "Paid Social"), None)
    psea = next((x for x in paid if x["bucket"].startswith("Paid Search")), None)
    if (psoc and psea and psoc["cr"] and psea["cr"]
            and psoc["sessions"] > 1000 and psoc["cr"] < psea["cr"] * 0.6):
        h.append(dict(sev="media",
            texto=f"Paid Social trae {psoc['share']*100:.0f}% del tráfico pero convierte {psoc['cr']*100:.2f}% "
                  f"vs Paid Search {psea['cr']*100:.2f}% — revisar eficiencia de social ads."))
    dev = secs.get("devices", {}).get("rows", [])
    mob = next((d for d in dev if d["device"].lower() == "mobile"), None)
    desk = next((d for d in dev if d["device"].lower() == "desktop"), None)
    if (mob and desk and mob["cr"] and desk["cr"]
            and mob["share"] > 0.6 and desk["cr"] > mob["cr"] * 1.8):
        h.append(dict(sev="info",
            texto=f"Mobile concentra {mob['share']*100:.0f}% del tráfico pero convierte {mob['cr']*100:.2f}% "
                  f"vs desktop {desk['cr']*100:.2f}% — la fricción mobile penaliza el grueso del tráfico."))
    fun = {m["label"]: m for m in secs.get("funnel", {}).get("metrics", [])}
    vc = fun.get("View→Compra rate")
    if vc and vc["yoy"] is not None and vc["yoy"] <= -0.15 and vc["cur"]:
        h.append(dict(sev="media",
            texto=f"Conversión view→compra cae {vc['yoy']*100:+.0f}% vs LY ({vc['cur']:.2f}%) — fuga en el funnel GA4."))
    cam = secs.get("campaigns", {})
    crows, cres = cam.get("rows", []), cam.get("resumen", {})
    if crows and cres.get("total_sessions", 0) > 500:
        if cres.get("named_share") is not None and cres["named_share"] < 0.10:
            h.append(dict(sev="media",
                texto=f"Solo {cres['named_share']*100:.0f}% del tráfico viene de campañas nombradas "
                      f"— poca inversión paga o UTMs sin etiquetar (mayoría directo/orgánico)."))
        # Campaña con buen volumen pero CR pobre (<0.5%) → revisar segmentación/landing.
        weak = [r for r in crows if r["sessions"] >= 300 and r["cr"] is not None and r["cr"] < 0.005]
        if weak:
            w = max(weak, key=lambda r: r["sessions"])
            h.append(dict(sev="media",
                texto=f"Campaña «{w['campania']}» trae {w['sessions']:,} sesiones pero convierte "
                      f"{w['cr']*100:.2f}% — revisar segmentación/landing."))
    topd = secs.get("top_productos", {})
    if topd and not topd.get("has_sesiones", True):
        h.append(dict(sev="info",
            texto="Vistas GA4 por producto en 0 este periodo: falta backfill de ga4_monthly_items "
                  "(dato no confiable hasta completarlo)."))
    rs = topd.get("resumen", {}) if topd else {}
    if rs.get("stock_web_und") and rs.get("venta_mes_und"):
        meses = rs["stock_web_und"] / rs["venta_mes_und"]
        if meses >= 4:
            h.append(dict(sev="media",
                texto=f"Inventario web ≈ {rs['stock_web_und']:,} und vs venta del mes {rs['venta_mes_und']:,} und "
                      f"→ ~{meses:.1f} meses de cobertura (sobrestock)."))
        elif meses < 1:
            h.append(dict(sev="alta",
                texto=f"Inventario web ≈ {rs['stock_web_und']:,} und cubre solo ~{meses:.1f} mes de venta "
                      f"— riesgo de quiebre."))
    gscq = secs.get("gsc", {}).get("queries", [])
    opp = next((x for x in gscq if x["impressions"] >= 5000 and x["ctr"] < 0.01
                and x["position"] <= 10), None)
    if opp:
        h.append(dict(sev="info",
            texto=f"Query orgánica «{opp['query']}»: {opp['impressions']:,} impresiones pero CTR "
                  f"{opp['ctr']*100:.1f}% (pos {opp['position']:.1f}) — oportunidad de título/snippet o ranking."))

    if not h:
        h.append(dict(sev="info", texto="Sin señales críticas este mes — operación dentro de rango."))
    return h


# ── Render HTML ──────────────────────────────────────────────────────────────
SEM_COLOR = {"verde": "#1e8e4e", "ambar": "#e08e0b", "rojo": "#c0392b", "gris": "#9aa3ad"}
SEV_COLOR = {"alta": "#c0392b", "media": "#e08e0b", "info": "#0078d4"}


def _fmt(v, money=True):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return f"S/{v:,.0f}" if money else f"{v:,.0f}"


def _fmt_kind(v, kind):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    return {"money": f"S/{v:,.0f}", "int": f"{v:,.0f}", "num2": f"{v:.2f}",
            "num1": f"{v:.1f}", "pct1": f"{v:.1f}%", "pct2": f"{v:.2f}%",
            "pctf": f"{v*100:.2f}%"}.get(kind, f"{v:,.2f}")


def _delta_html(d, invert=False):
    if d is None:
        return "<span style='color:#9aa3ad'>s/d</span>"
    col = SEM_COLOR[semaforo(-d if invert else d)]   # invert: subir es peor (bounce)
    arrow = "▲" if d >= 0 else "▼"
    return f"<span style='color:{col};font-weight:700'>{arrow} {abs(d)*100:.0f}%</span>"


def _heat(frac):
    """Fondo azul con intensidad ∝ valor (heatmap de concentración)."""
    frac = max(0.0, min(1.0, frac or 0.0))
    return f"background:rgba(0,120,212,{0.06 + 0.55 * frac:.2f})"


def render_trend_chart(points: list[dict]) -> str:
    """SVG combinado: barras = venta neta; líneas = MG% y CR% (escala propia)."""
    pts = [p for p in points if p]
    if not pts:
        return ""
    W, H = 920, 230
    padL, padR, padT, padB = 64, 56, 24, 34
    plotW, plotH = W - padL - padR, H - padT - padB
    n = len(pts)
    xs = [padL + plotW * (i + 0.5) / n for i in range(n)]
    max_neto = max([p["neto"] for p in pts] + [1.0]) * 1.2
    mg = [p["margen"] for p in pts if p["margen"] is not None]
    cr = [p["cr"] for p in pts if p["cr"] is not None]
    max_mg = (max(mg) * 1.4) if mg else 1.0
    max_cr = (max(cr) * 1.4) if cr else 1.0
    base = padT + plotH

    def yv(v, mx):
        return base - (v / mx) * plotH if mx else base

    s = [f"<svg viewBox='0 0 {W} {H}' width='100%' "
         f"xmlns='http://www.w3.org/2000/svg' font-family='Segoe UI,Arial,sans-serif'>"]
    s.append(f"<line x1='{padL}' y1='{base}' x2='{W-padR}' y2='{base}' stroke='#d8e0ea'/>")
    bw = plotW / n * 0.36
    for i, p in enumerate(pts):
        x, yb = xs[i], yv(p["neto"], max_neto)
        s.append(f"<rect x='{x-bw/2:.1f}' y='{yb:.1f}' width='{bw:.1f}' "
                 f"height='{base-yb:.1f}' rx='3' fill='#0078d4' opacity='0.85'/>")
        s.append(f"<text x='{x:.1f}' y='{yb-6:.1f}' text-anchor='middle' font-size='11' "
                 f"fill='#001f3f' font-weight='700'>S/{p['neto']/1000:.0f}k</text>")
        s.append(f"<text x='{x:.1f}' y='{base+18:.1f}' text-anchor='middle' font-size='12' "
                 f"fill='#67727e'>{p['mes']}</text>")

    def line(key, mx, color):
        seg = [(xs[i], yv(p[key], mx), p[key]) for i, p in enumerate(pts) if p[key] is not None]
        if len(seg) >= 2:
            path = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in seg)
            s.append(f"<polyline points='{path}' fill='none' stroke='{color}' stroke-width='2.5'/>")
        for x, y, v in seg:
            s.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3.5' fill='{color}'/>")
            s.append(f"<text x='{x:.1f}' y='{y-8:.1f}' text-anchor='middle' font-size='10.5' "
                     f"fill='{color}' font-weight='700'>{v*100:.1f}%</text>")

    line("margen", max_mg, "#1e8e4e")
    line("cr", max_cr, "#e08e0b")
    s.append("<g font-size='11'>")
    s.append(f"<rect x='{padL}' y='6' width='11' height='11' rx='2' fill='#0078d4'/>"
             f"<text x='{padL+16}' y='15' fill='#3a4654'>Venta neta</text>")
    s.append(f"<line x1='{padL+95}' y1='11' x2='{padL+115}' y2='11' stroke='#1e8e4e' stroke-width='2.5'/>"
             f"<text x='{padL+120}' y='15' fill='#3a4654'>MG%</text>")
    s.append(f"<line x1='{padL+165}' y1='11' x2='{padL+185}' y2='11' stroke='#e08e0b' stroke-width='2.5'/>"
             f"<text x='{padL+190}' y='15' fill='#3a4654'>CR%</text>")
    s.append("</g></svg>")
    return "".join(s)


def render_funnel_chart(stages: list[dict]) -> str:
    """Embudo horizontal: vistas → carrito → compra, con % de vistas y benchmark."""
    stages = [s for s in stages if s]
    if not stages:
        return ""
    W, H = 920, 24 + len(stages) * 64
    padL, barMax = 168, W - 168 - 200
    colors = ["#0078d4", "#3a8dde", "#7bb8ec"]
    s = [f"<svg viewBox='0 0 {W} {H}' width='100%' xmlns='http://www.w3.org/2000/svg' "
         f"font-family='Segoe UI,Arial,sans-serif'>"]
    for i, st in enumerate(stages):
        y = 14 + i * 64
        w = max(barMax * (st["pct"] or 0), 64)
        col = colors[min(i, len(colors) - 1)]
        s.append(f"<text x='{padL-12}' y='{y+25}' text-anchor='end' font-size='13' "
                 f"fill='#1a2230'>{st['label']}</text>")
        s.append(f"<rect x='{padL}' y='{y}' width='{w:.0f}' height='38' rx='5' fill='{col}'/>")
        s.append(f"<text x='{padL+10}' y='{y+24}' font-size='12.5' fill='#fff' "
                 f"font-weight='700'>{st['value']:,.0f}</text>")
        pctlabel = "100%" if i == 0 else f"{st['pct']*100:.1f}% de vistas"
        s.append(f"<text x='{padL+w+10}' y='{y+24}' font-size='12' fill='#3a4654'>{pctlabel}</text>")
        if st.get("bench"):
            diff = (st["pct"] or 0) - st["bench"]
            col2 = "#1e8e4e" if diff >= 0 else "#c0392b"
            arrow = "✓" if diff >= 0 else "▼"
            s.append(f"<text x='{padL+w+118}' y='{y+24}' font-size='11' fill='{col2}'>"
                     f"{arrow} vs {st['bench']*100:.0f}% típ.</text>")
    s.append("</svg>")
    return "".join(s)


def render_devices_chart(rows: list[dict]) -> str:
    """Barras agrupadas por dispositivo: share de sesiones vs share de revenue."""
    rows = [r for r in rows if r.get("sessions", 0) > 0][:4]
    if not rows:
        return ""
    W, H = 920, 50 + len(rows) * 60
    padL, barMax = 110, W - 110 - 120
    s = [f"<svg viewBox='0 0 {W} {H}' width='100%' xmlns='http://www.w3.org/2000/svg' "
         f"font-family='Segoe UI,Arial,sans-serif'>"]
    s.append(f"<g font-size='11'><rect x='{padL}' y='6' width='11' height='11' rx='2' fill='#0078d4'/>"
             f"<text x='{padL+16}' y='15' fill='#3a4654'>Share sesiones</text>"
             f"<rect x='{padL+130}' y='6' width='11' height='11' rx='2' fill='#1e8e4e'/>"
             f"<text x='{padL+146}' y='15' fill='#3a4654'>Share revenue</text></g>")
    mx = max(max(r["share"], r["share_rev"]) for r in rows) or 1
    for i, r in enumerate(rows):
        y = 34 + i * 60
        s.append(f"<text x='{padL-12}' y='{y+20}' text-anchor='end' font-size='13' "
                 f"fill='#1a2230'>{r['device']}</text>")
        ws = barMax * (r["share"] / mx)
        wr = barMax * (r["share_rev"] / mx)
        s.append(f"<rect x='{padL}' y='{y}' width='{ws:.0f}' height='18' rx='3' fill='#0078d4'/>"
                 f"<text x='{padL+ws+6}' y='{y+14}' font-size='11' fill='#3a4654'>{r['share']*100:.0f}%</text>")
        s.append(f"<rect x='{padL}' y='{y+22}' width='{wr:.0f}' height='18' rx='3' fill='#1e8e4e'/>"
                 f"<text x='{padL+wr+6}' y='{y+36}' font-size='11' fill='#3a4654'>{r['share_rev']*100:.0f}%</text>")
    s.append("</svg>")
    return "".join(s)


# ── Perfiles por audiencia ────────────────────────────────────────────────────
# Cada perfil = secciones a renderizar, en orden. 'full' = todo (David).
# 'comercial' = RMH + GA4 tienda + género (sin logística/pago). 'clevel' =
# condensado + canales por responsable. 'marketing' = fase 2.
PROFILES = {
    "full":      ["trend", "performance", "precios", "top_productos", "search", "gsc",
                  "geografia", "courier", "pagos", "recompra", "cliente", "canales",
                  "campaigns", "funnel", "devices", "hallazgos"],
    "comercial": ["trend", "performance", "precios", "top_productos", "search", "gsc",
                  "cliente", "hallazgos"],
    "clevel":    ["trend", "performance", "top_productos", "cliente", "canales", "hallazgos"],
    "marketing": ["trend", "performance", "canales", "campaigns", "funnel", "devices",
                  "search", "gsc", "top_productos", "recompra", "cliente", "hallazgos"],
}
PROFILE_LABEL = {"full": "Integral (todo)", "comercial": "Comercial",
                 "clevel": "C-level", "marketing": "Marketing"}
_CIRC = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


def _circ(n):
    return _CIRC[n - 1] if 1 <= n <= len(_CIRC) else f"{n}."


def render_html(marca, ym, secs, hallazgos, profile="full") -> str:
    d1, d2 = month_bounds(ym)
    mes_label = d1.strftime("%B %Y").capitalize()
    ahora = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    keys = PROFILES.get(profile, PROFILES["full"])
    _nc = [0]

    def num():
        _nc[0] += 1
        return _circ(_nc[0])
    css = """
    *{box-sizing:border-box;} body{font-family:'Segoe UI',Arial,sans-serif;background:#eef1f5;color:#1a2230;margin:0;padding:24px;}
    .wrap{max-width:1000px;margin:0 auto;}
    .hd{background:#001f3f;color:#fff;border-radius:12px;padding:22px 26px;margin-bottom:18px;}
    .hd h1{margin:0;font-size:24px;letter-spacing:.3px;} .hd .sub{opacity:.8;font-size:14px;margin-top:4px;}
    .card{background:#fff;border-radius:12px;box-shadow:0 1px 4px rgba(0,0,0,.07);margin-bottom:18px;overflow:hidden;}
    .card h2{margin:0;padding:13px 20px;background:#f3f6fa;border-bottom:1px solid #e6ebf1;font-size:15px;color:#001f3f;}
    .card .body{padding:16px 20px;}
    .kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;}
    .kpi{border:1px solid #eef1f5;border-radius:10px;padding:12px 14px;position:relative;}
    .kpi .lbl{font-size:12px;color:#67727e;margin-bottom:4px;} .kpi .val{font-size:21px;font-weight:700;color:#001f3f;}
    .kpi .dot{position:absolute;top:13px;right:13px;width:11px;height:11px;border-radius:50%;}
    .kpi .ly{font-size:12px;color:#67727e;margin-top:3px;}
    table{width:100%;border-collapse:collapse;font-size:13px;} th,td{text-align:right;padding:7px 10px;border-bottom:1px solid #eef1f5;}
    th:first-child,td:first-child{text-align:left;} th{background:#fafbfd;color:#67727e;font-weight:600;}
    .bar{height:7px;background:#e6ebf1;border-radius:4px;overflow:hidden;} .bar > i{display:block;height:100%;background:#0078d4;}
    .pill{display:inline-block;font-size:11px;font-weight:700;color:#fff;border-radius:4px;padding:2px 8px;}
    .hall{display:flex;gap:11px;padding:10px 0;border-top:1px solid #eef1f5;align-items:flex-start;}
    .hall:first-child{border-top:0;} .est{font-size:11px;color:#9aa3ad;font-style:italic;}
    .tdot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:7px;vertical-align:middle;}
    tr.tot td{font-weight:700;background:#f7f9fc;border-top:2px solid #d8e0ea;color:#001f3f;}
    .tabs{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px;}
    .tab{font-size:12px;font-weight:600;color:#3a4654;background:#eef1f5;border:1px solid #e0e6ee;
         border-radius:6px;padding:5px 12px;cursor:pointer;}
    .tab.active{background:#0078d4;color:#fff;border-color:#0078d4;}
    h3.sub3{font-size:13px;color:#001f3f;margin:18px 0 8px;}
    @media print{body{background:#fff;padding:0;} .card{box-shadow:none;border:1px solid #e6ebf1;} .wrap{max-width:100%;}
      .tabs{display:none;} }
    """
    p = [f"<!doctype html><html lang=es><head><meta charset=utf-8>"
         f"<title>Scorecard {marca} {ym}</title><style>{css}</style></head><body><div class=wrap>"]
    p.append(f"<div class=hd><h1>Scorecard — {marca}</h1>"
             f"<div class=sub>{mes_label} · {PROFILE_LABEL.get(profile, profile)} · "
             f"mes cerrado vs año pasado · generado {ahora}</div></div>")

    # 0. Tendencia 3 meses (gráfica combinada) — sin número
    tr = secs.get("trend")
    if "trend" in keys and tr and tr.get("points"):
        p.append('<div class=card><h2>Tendencia · últimos 3 meses</h2><div class="body">')
        p.append(render_trend_chart(tr["points"]))
        p.append('<p class=est style="margin-top:6px">Barras: venta neta (RMH). '
                 'Líneas (escala propia, valor etiquetado): MG% (RMH) y CR% de compra (GA4).</p>')
        p.append("</div></div>")

    # 1. Performance (tabla: Métrica | Mes | Año pasado | Δ; incluye GA4)
    perf = secs.get("performance")
    if "performance" in keys and perf:
        p.append(f'<div class=card><h2>{num()} Performance vs año pasado</h2><div class="body"><table>')
        p.append(f"<tr><th>Métrica</th><th>{mes_label}</th><th>Año pasado</th><th>Δ YoY</th></tr>")
        for m in perf["metrics"]:
            p.append(
                f"<tr><td><span class=tdot style='background:{SEM_COLOR[m['sem']]}'></span>{m['label']}</td>"
                f"<td>{_fmt_kind(m['cur'], m['kind'])}</td>"
                f"<td>{_fmt_kind(m['prev'], m['kind'])}</td>"
                f"<td>{_delta_html(m['yoy'], m.get('invert', False))}</td></tr>")
        p.append("</table></div></div>")

    # 2. Precios / margen (buckets 10%; tabs por marca en Coliseum; fila total)
    pre = secs.get("precios")
    if "precios" in keys and pre and pre.get("groups"):
        groups = pre["groups"]
        multi = len(groups) > 1
        p.append(f'<div class=card><h2>{num()} Distribución de precio y margen (unidades)</h2><div class="body">')
        if multi:
            p.append("<div class=tabs>")
            for i, g in enumerate(groups):
                p.append(f"<button class='tab{' active' if i == 0 else ''}' "
                         f"data-tab='pre{i}'>{g['tab']}</button>")
            p.append("</div>")
        for i, g in enumerate(groups):
            r = g["resumen"]
            style = "" if (not multi or i == 0) else " style='display:none'"
            p.append(f"<div class=tabpane id=pre{i}{style}>")
            p.append(f"<p style='margin:0 0 12px;font-size:13px;color:#3a4654'>"
                     f"<b>{r['full_share']*100:.0f}%</b> a full price (&lt;20% dscto, margen "
                     f"{(r['margen_full'] or 0)*100:.0f}%) · <b>{r['promo_share']*100:.0f}%</b> promocional "
                     f"(≥20% dscto, margen {(r['margen_promo'] or 0)*100:.0f}%)</p>")
            p.append("<table><tr><th>Rango descuento</th><th>Unidades</th><th>Share</th>"
                     "<th>Venta neta</th><th>Margen</th></tr>")
            maxsh = max((b["share"] for b in g["buckets"]), default=0) or 1
            for b in g["buckets"]:
                p.append(f"<tr><td>{b['rango']}</td><td>{b['und']:,}</td>"
                         f"<td style=\"{_heat(b['share']/maxsh)}\">{b['share']*100:.0f}%</td>"
                         f"<td>S/{b['neto']:,.0f}</td>"
                         f"<td>{(b['margen'] or 0)*100:.0f}%</td></tr>")
            t = g["total"]
            p.append(f"<tr class=tot><td>Total</td><td>{t['und']:,}</td><td>100%</td>"
                     f"<td>S/{t['neto']:,.0f}</td><td>{(t['margen'] or 0)*100:.0f}%</td></tr>")
            p.append("</table></div>")
        p.append("</div></div>")

    # 3. Top productos (+ sesiones GA4, % curvado = cobertura de tallas, marca)
    top = secs.get("top_productos")
    if "top_productos" in keys and top and top["rows"]:
        mc = top.get("is_coliseum")
        ses = top.get("has_sesiones")   # oculta columna Sesiones si no hay data GA4-producto
        top_rows = top["rows"][:5] if profile == "clevel" else top["rows"]
        titulo = "Top productos del mes" + (" (top 5)" if profile == "clevel" else "")
        p.append(f'<div class=card><h2>{num()} {titulo} '
                 '<span class=est>(grano padre / CodColor · % curvado = cobertura de tallas web)</span></h2>'
                 '<div class="body"><table><tr><th>Img</th><th>CodColor</th>'
                 + ("<th>Marca</th>" if mc else "")
                 + "<th>Producto</th><th>Tallas</th><th>Unidades</th>"
                   "<th>Venta neta</th><th>Margen</th>"
                 + ("<th>Vistas GA4</th>" if ses else "")
                 + "<th>% curvado</th></tr>")
        for r in top_rows:
            cov = f"{r['cobertura']*100:.0f}%" if r["cobertura"] is not None else "—"
            marca_td = f"<td>{r['marca']}</td>" if mc else ""
            ses_td = f"<td>{r['sesiones']:,}</td>" if ses else ""
            img_td = (f'<td><img src="{r["imagen"]}" alt="" loading="lazy" '
                      f'style="height:34px;max-width:46px;object-fit:cover;border-radius:4px"></td>'
                      ) if r.get("imagen") else "<td></td>"
            p.append(f"<tr>{img_td}<td>{r['codigo']}</td>{marca_td}<td>{r['descripcion']}</td>"
                     f"<td>{r['skus']}</td><td>{r['und']:,}</td><td>S/{r['neto']:,.0f}</td>"
                     f"<td>{(r['margen'] or 0)*100:.0f}%</td>{ses_td}"
                     f"<td>{cov}</td></tr>")
        p.append("</table>")
        rs = top.get("resumen", {})
        if rs:
            p.append(f"<p style='margin:10px 0 0;font-size:13px;color:#3a4654'>"
                     f"El top {len(top_rows)} concentra <b>{rs['share_und']*100:.0f}%</b> de las unidades "
                     f"y <b>{rs['share_neto']*100:.0f}%</b> de la venta neta del mes.</p>")
        nota = ("% curvado = cobertura de tallas disponibles en web (Integrada). "
                "Todo el catálogo con stock (top con <code>es_top</code>) está en la hoja TopProductos del Excel.")
        nota += " Vistas GA4 = items_viewed por CodColor (ga4_monthly_items)."
        if not ses:
            nota += " <b>Vistas en 0: falta backfill GA4 de este periodo.</b>"
        p.append(f'<p class=est style="margin-top:6px">{nota}</p>')
        p.append("</div></div>")

    # 3b. Búsquedas in-site (Comercial + Marketing + Full)
    sea = secs.get("search")
    if "search" in keys and sea and sea.get("rows"):
        p.append(f'<div class=card><h2>{num()} Búsquedas in-site (top términos)</h2>'
                 '<div class="body"><table>'
                 "<tr><th>Término</th><th>Sesiones</th><th>Share</th><th>Usuarios</th></tr>")
        maxsh_s = max((r["share"] or 0 for r in sea["rows"]), default=0) or 1
        for r in sea["rows"]:
            sh = f"{r['share']*100:.0f}%" if r["share"] is not None else "—"
            heat = _heat((r["share"] or 0) / maxsh_s)
            p.append(f"<tr><td>{r['term']}</td><td>{r['sessions']:,}</td>"
                     f"<td style=\"{heat}\">{sh}</td><td>{r['users']:,}</td></tr>")
        p.append("</table><p class=est style='margin-top:6px'>Lo que los usuarios buscan dentro de la web "
                 "(GA4) — señal de demanda/intención y de surtido buscado.</p></div></div>")

    # 3c. GSC — búsqueda orgánica Google (Comercial + Marketing + Full)
    gsc = secs.get("gsc")
    if "gsc" in keys and gsc and (gsc.get("kpis") or gsc.get("queries")):
        p.append(f'<div class=card><h2>{num()} Búsqueda orgánica Google (Search Console)</h2>'
                 '<div class="body">')
        if gsc.get("kpis"):
            p.append('<table>'
                     f"<tr><th>Métrica</th><th>{mes_label}</th><th>Año pasado</th><th>Δ YoY</th></tr>")
            for m in gsc["kpis"]:
                p.append(f"<tr><td><span class=tdot style='background:{SEM_COLOR[m['sem']]}'></span>{m['label']}</td>"
                         f"<td>{_fmt_kind(m['cur'], m['kind'])}</td>"
                         f"<td>{_fmt_kind(m['prev'], m['kind'])}</td>"
                         f"<td>{_delta_html(m['yoy'], m.get('invert', False))}</td></tr>")
            p.append("</table>")
        if gsc.get("queries"):
            p.append("<h3 class=sub3>Top queries orgánicas</h3>"
                     "<table><tr><th>Query</th><th>Clicks</th><th>Share</th>"
                     "<th>Impresiones</th><th>CTR</th><th>Posición</th></tr>")
            maxsh_g = max((r["share"] for r in gsc["queries"]), default=0) or 1
            for r in gsc["queries"]:
                p.append(f"<tr><td>{r['query']}</td><td>{r['clicks']:,}</td>"
                         f"<td style=\"{_heat(r['share']/maxsh_g)}\">{r['share']*100:.0f}%</td>"
                         f"<td>{r['impressions']:,}</td><td>{r['ctr']*100:.2f}%</td>"
                         f"<td>{r['position']:.1f}</td></tr>")
            p.append("</table>")
        p.append("<p class=est style='margin-top:6px'>Search Console: posición media (menor es mejor); "
                 "alta impresión + bajo CTR = oportunidad de título/snippet o de ranking.</p></div></div>")

    # 4. Geografía (+ UPT; GM est. en soles; fila total)
    geo = secs.get("geografia")
    if "geografia" in keys and geo and geo["rows"]:
        est = " <span class=est>(GM est. = venta neta × margen real del mes; reconcilia con Performance)</span>"
        p.append(f'<div class=card><h2>{num()} Geografía y GM estimado por zona{est}</h2><div class="body"><table>'
                 "<tr><th>Zona</th><th>Órdenes</th><th>Share</th><th>Unidades</th><th>UPT</th>"
                 "<th>Venta neta</th><th>Envío prom.</th><th>GM est.</th></tr>")
        maxsh_geo = max((g["share"] for g in geo["rows"]), default=0) or 1
        for g in geo["rows"]:
            p.append(f"<tr><td>{g['zona']}</td><td>{g['ordenes']:,}</td>"
                     f"<td style=\"{_heat(g['share']/maxsh_geo)}\">{g['share']*100:.0f}%</td>"
                     f"<td>{g['unidades']:,}</td>"
                     f"<td>{g['upt']:.2f}</td>"
                     f"<td>S/{g['venta_neta']:,.0f}</td><td>S/{g['envio_prom']:,.1f}</td>"
                     f"<td>S/{g['contrib_est']:,.0f}</td></tr>")
        t = geo.get("total")
        if t:
            p.append(f"<tr class=tot><td>Total</td><td>{t['ordenes']:,}</td><td>100%</td>"
                     f"<td>{t['unidades']:,}</td><td>{t['upt']:.2f}</td>"
                     f"<td>S/{t['venta_neta']:,.0f}</td><td>S/{t['envio_prom']:,.1f}</td>"
                     f"<td>S/{t['contrib_est']:,.0f}</td></tr>")
        p.append("</table></div></div>")

    # 5. Courier (etiquetas legibles; GM est. en vez de envío prom; total)
    cou = secs.get("courier")
    if "courier" in keys and cou and cou["rows"]:
        gmest = " <span class=est>(GM est. = venta neta × margen real del mes)</span>"
        p.append(f'<div class=card><h2>{num()} Courier{gmest}</h2><div class="body"><table>'
                 "<tr><th>Courier</th><th>Órdenes</th><th>Share</th><th>Unidades</th>"
                 "<th>Venta</th><th>GM est.</th></tr>")
        for r in cou["rows"]:
            p.append(f"<tr><td>{r['courier']}</td><td>{r['ordenes']:,}</td>"
                     f"<td>{r['share']*100:.0f}%</td><td>{r['unidades']:,}</td>"
                     f"<td>S/{r['venta']:,.0f}</td><td>S/{r['gm_est']:,.0f}</td></tr>")
        t = cou.get("total")
        if t:
            p.append(f"<tr class=tot><td>Total</td><td>{t['ordenes']:,}</td><td>100%</td>"
                     f"<td>{t['unidades']:,}</td><td>S/{t['venta']:,.0f}</td>"
                     f"<td>S/{t['gm_est']:,.0f}</td></tr>")
        p.append("</table></div></div>")

    # 6. Pagos (+ ticket medio, UPT, GM est.; total)
    pag = secs.get("pagos")
    if "pagos" in keys and pag and pag["rows"]:
        p.append(f'<div class=card><h2>{num()} Medios de pago</h2><div class="body"><table>'
                 "<tr><th>Medio</th><th>Confirmadas</th><th>Share</th><th>Conversión</th>"
                 "<th>Ticket</th><th>UPT</th><th>Venta</th><th>GM est.</th></tr>")
        for r in pag["rows"]:
            conv = f"{r['conv']*100:.0f}%" if r["conv"] is not None else "—"
            tk = f"S/{r['ticket']:,.0f}" if r["ticket"] is not None else "—"
            upt = f"{r['upt']:.2f}" if r["upt"] is not None else "—"
            p.append(f"<tr><td>{r['label']}</td><td>{r['confirmadas']:,}</td>"
                     f"<td>{r['share']*100:.0f}%</td><td>{conv}</td>"
                     f"<td>{tk}</td><td>{upt}</td>"
                     f"<td>S/{r['venta']:,.0f}</td><td>S/{r['gm_est']:,.0f}</td></tr>")
        t = pag.get("total")
        if t:
            tk = f"S/{t['ticket']:,.0f}" if t["ticket"] is not None else "—"
            upt = f"{t['upt']:.2f}" if t["upt"] is not None else "—"
            p.append(f"<tr class=tot><td>Total</td><td>{t['confirmadas']:,}</td><td>100%</td>"
                     f"<td>—</td><td>{tk}</td><td>{upt}</td>"
                     f"<td>S/{t['venta']:,.0f}</td><td>S/{t['gm_est']:,.0f}</td></tr>")
        p.append("</table></div></div>")

    # 7. Recompra
    rec = secs.get("recompra")
    if "recompra" in keys and rec and rec["kpi"]:
        k = rec["kpi"]
        p.append(f'<div class=card><h2>{num()} Recompra (clientes)</h2><div class="body"><div class=kpis>')
        p.append(f"<div class=kpi><div class=lbl>De clientes recurrentes</div>"
                 f"<div class=val>{k['share_recurrente']*100:.0f}%</div></div>")
        p.append(f"<div class=kpi><div class=lbl>Clientes únicos</div>"
                 f"<div class=val>{k['clientes_unicos']:,}</div></div>")
        p.append(f"<div class=kpi><div class=lbl>Órdenes recurrentes</div>"
                 f"<div class=val>{k['recurrentes']:,}</div></div>")
        p.append("</div>")
        p.append(f"<p style='margin:12px 0 0;font-size:13px;color:#3a4654'>"
                 f"Sobre <b>{k['ordenes_total']:,}</b> órdenes pagadas del mes "
                 f"(<b>{k['ordenes_mes']:,}</b> con cliente identificado).</p>")
        p.append("<p class=est style='margin-top:6px'>Ventana de recompra desde feb-2026 "
                 "(go-live de la réplica de órdenes).</p></div></div>")

    # 8. Cliente — ventas por género
    cli = secs.get("cliente")
    if "cliente" in keys and cli and cli.get("rows"):
        p.append(f'<div class=card><h2>{num()} Cliente — ventas y categorías por género</h2>'
                 '<div class="body"><table>'
                 "<tr><th>Género</th><th>% venta</th><th>Venta neta</th><th>Unidades</th>"
                 "<th>Top categorías (und)</th></tr>")
        maxsh_cli = max((r["share"] for r in cli["rows"]), default=0) or 1
        for r in cli["rows"]:
            p.append(f"<tr><td>{r['genero']}</td>"
                     f"<td style=\"{_heat(r['share']/maxsh_cli)}\">{r['share']*100:.0f}%</td>"
                     f"<td>S/{r['neto']:,.0f}</td><td>{r['und']:,}</td>"
                     f"<td>{r['top_cats']}</td></tr>")
        p.append("</table><p class=est style='margin-top:6px'>Género del PRODUCTO "
                 "(no del cliente). Rango de edad GA4 pendiente (extracción nueva).</p></div></div>")

    # 9. GA4 canales × responsable
    can = secs.get("canales")
    if "canales" in keys and can and can.get("por_responsable"):
        p.append(f'<div class=card><h2>{num()} GA4 · canales por responsable</h2><div class="body">'
                 "<table><tr><th>Responsable</th><th>Sesiones</th><th>Share ses.</th>"
                 "<th>Revenue (GA4)</th><th>Share rev.</th><th>CR compra</th></tr>")
        maxsh_c = max((r["share"] for r in can["por_responsable"]), default=0) or 1
        for r in can["por_responsable"]:
            cr = f"{r['cr']*100:.2f}%" if r["cr"] is not None else "—"
            p.append(f"<tr><td>{r['responsable']}</td><td>{r['sessions']:,}</td>"
                     f"<td style=\"{_heat(r['share']/maxsh_c)}\">{r['share']*100:.0f}%</td>"
                     f"<td>S/{r['revenue']:,.0f}</td><td>{r['share_rev']*100:.0f}%</td>"
                     f"<td>{cr}</td></tr>")
        p.append("</table>")
        if profile in ("full", "marketing") and can.get("por_agrupador"):
            p.append("<h3 class=sub3>Detalle por canal</h3>"
                     "<table><tr><th>Canal</th><th>Sesiones</th><th>Share ses.</th>"
                     "<th>Revenue</th><th>Share rev.</th><th>CR compra</th></tr>")
            for r in can["por_agrupador"]:
                cr = f"{r['cr']*100:.2f}%" if r["cr"] is not None else "—"
                p.append(f"<tr><td>{r['canal']}</td><td>{r['sessions']:,}</td>"
                         f"<td>{r['share']*100:.0f}%</td><td>S/{r['revenue']:,.0f}</td>"
                         f"<td>{r['share_rev']*100:.0f}%</td><td>{cr}</td></tr>")
            p.append("</table>")
        if profile == "marketing" and can.get("paid"):
            p.append("<h3 class=sub3>Foco paid</h3>"
                     "<table><tr><th>Paid</th><th>Sesiones</th><th>Share</th>"
                     "<th>Revenue</th><th>CR compra</th></tr>")
            for r in can["paid"]:
                cr = f"{r['cr']*100:.2f}%" if r["cr"] is not None else "—"
                p.append(f"<tr><td>{r['bucket']}</td><td>{r['sessions']:,}</td>"
                         f"<td>{r['share']*100:.0f}%</td><td>S/{r['revenue']:,.0f}</td>"
                         f"<td>{cr}</td></tr>")
            p.append("</table>")
        p.append('<p class=est style="margin-top:6px">Tráfico/venta GA4 por responsable de canal '
                 "(tabla Equivalencias_Canales). Revenue es atribución GA4, no RMH.</p></div></div>")

    # 9a2. Campañas GA4 (Marketing/Full)
    camp = secs.get("campaigns")
    if "campaigns" in keys and camp and camp.get("rows"):
        cres = camp.get("resumen", {})
        p.append(f'<div class=card><h2>{num()} GA4 · campañas nombradas</h2><div class="body">')
        if cres.get("named_share") is not None:
            p.append(f"<p style='margin:0 0 12px;font-size:13px;color:#3a4654'>"
                     f"<b>{cres['named_share']*100:.0f}%</b> del tráfico viene de campañas nombradas "
                     f"({cres['named_sessions']:,} sesiones · S/{cres['named_revenue']:,.0f} revenue GA4).</p>")
        p.append("<table><tr><th>Campaña</th><th>Sesiones</th><th>Share</th>"
                 "<th>Compras</th><th>Revenue</th><th>CR compra</th></tr>")
        maxsh_k = max((r["share"] for r in camp["rows"]), default=0) or 1
        for r in camp["rows"]:
            cr = f"{r['cr']*100:.2f}%" if r["cr"] is not None else "—"
            p.append(f"<tr><td>{r['campania']}</td><td>{r['sessions']:,}</td>"
                     f"<td style=\"{_heat(r['share']/maxsh_k)}\">{r['share']*100:.0f}%</td>"
                     f"<td>{r['compras']:,}</td><td>S/{r['revenue']:,.0f}</td>"
                     f"<td>{cr}</td></tr>")
        p.append("</table><p class=est style='margin-top:6px'>Solo campañas nombradas (excluye "
                 "(direct)/(organic)/(referral)). Nombre completo + no-campaña en la hoja Campanias del Excel.</p></div></div>")

    # 9b. Funnel GA4 (Marketing/Full)
    fun = secs.get("funnel")
    if "funnel" in keys and fun and fun.get("metrics"):
        p.append(f'<div class=card><h2>{num()} Funnel GA4 (vista → carrito → compra)</h2>'
                 '<div class="body">')
        if fun.get("stages"):
            p.append(render_funnel_chart(fun["stages"]))
            p.append('<p class=est style="margin:4px 0 12px">Benchmark típico ecommerce moda: '
                     '~10% add-to-cart y ~2.5% compra (sobre vistas). ✓/▼ marca si estás sobre/bajo.</p>')
        p.append('<table>'
                 f"<tr><th>Métrica</th><th>{mes_label}</th><th>Año pasado</th><th>Δ YoY</th></tr>")
        for m in fun["metrics"]:
            p.append(f"<tr><td><span class=tdot style='background:{SEM_COLOR[m['sem']]}'></span>{m['label']}</td>"
                     f"<td>{_fmt_kind(m['cur'], m['kind'])}</td>"
                     f"<td>{_fmt_kind(m['prev'], m['kind'])}</td>"
                     f"<td>{_delta_html(m['yoy'])}</td></tr>")
        p.append("</table></div></div>")

    # 9c. Devices GA4 (Marketing/Full)
    dev = secs.get("devices")
    if "devices" in keys and dev and dev.get("rows"):
        p.append(f'<div class=card><h2>{num()} Tráfico y conversión por dispositivo (GA4)</h2>'
                 '<div class="body">')
        p.append(render_devices_chart(dev["rows"]))
        p.append('<table>'
                 "<tr><th>Dispositivo</th><th>Sesiones</th><th>Share ses.</th><th>Compras</th>"
                 "<th>Revenue</th><th>Share rev.</th><th>CR compra</th></tr>")
        maxsh_d = max((r["share"] for r in dev["rows"]), default=0) or 1
        for r in dev["rows"]:
            cr = f"{r['cr']*100:.2f}%" if r["cr"] is not None else "—"
            p.append(f"<tr><td>{r['device']}</td><td>{r['sessions']:,}</td>"
                     f"<td style=\"{_heat(r['share']/maxsh_d)}\">{r['share']*100:.0f}%</td>"
                     f"<td>{r['compras']:,}</td><td>S/{r['revenue']:,.0f}</td>"
                     f"<td>{r['share_rev']*100:.0f}%</td><td>{cr}</td></tr>")
        p.append("</table><p class=est style='margin-top:6px'>Contrasta share de sesiones vs "
                 "share de revenue: si un dispositivo pesa más en revenue que en tráfico, convierte mejor.</p>"
                 "</div></div>")

    # 10. Hallazgos (top 3 en C-level)
    if "hallazgos" in keys:
        items = sorted(hallazgos, key=lambda x: {"alta": 0, "media": 1, "info": 2}.get(x["sev"], 9))
        if profile == "clevel":
            items = items[:3]
        p.append(f'<div class=card><h2>{num()} Hallazgos</h2><div class="body">')
        for it in items:
            col = SEV_COLOR.get(it["sev"], "#67727e")
            p.append(f"<div class=hall><span class=pill style='background:{col}'>{it['sev'].upper()}</span>"
                     f"<div>{it['texto']}</div></div>")
        p.append("</div></div>")

    # Tabs (§2 por marca): muestra el panel del botón, oculta hermanos del mismo card.
    p.append("""<script>
document.querySelectorAll('.tab').forEach(function(btn){
  btn.addEventListener('click', function(){
    var card = btn.closest('.card');
    card.querySelectorAll('.tabpane').forEach(function(pane){ pane.style.display='none'; });
    var t = card.querySelector('#'+btn.dataset.tab); if(t){ t.style.display='block'; }
    btn.parentNode.querySelectorAll('.tab').forEach(function(b){ b.classList.remove('active'); });
    btn.classList.add('active');
  });
});
</script>""")
    p.append("</div></body></html>")
    return "\n".join(p)


# ── Excel sustento (1 hoja por sección del perfil) ───────────────────────────
SHEET_OF = {
    "performance": "Performance", "precios": "Precios", "top_productos": "TopProductos",
    "geografia": "Geografia", "courier": "Courier", "pagos": "Pagos",
    "recompra": "Recompra", "cliente": "Cliente", "canales": "Canales",
    "search": "Busquedas", "campaigns": "Campanias", "gsc": "GSC_Organico",
}


def write_excel(path: Path, secs: dict, profile: str = "full"):
    keys = PROFILES.get(profile, PROFILES["full"])
    sheets = {SHEET_OF[k]: secs.get(k, {}).get("excel")
              for k in keys if k in SHEET_OF}
    with pd.ExcelWriter(path, engine="openpyxl") as xl:
        wrote = False
        for name, df in sheets.items():
            if isinstance(df, pd.DataFrame) and not df.empty:
                df.to_excel(xl, sheet_name=name[:31], index=False)
                wrote = True
        if not wrote:  # openpyxl exige al menos una hoja
            pd.DataFrame({"info": ["sin datos"]}).to_excel(xl, sheet_name="vacio", index=False)


# ── PDF vía Edge headless ────────────────────────────────────────────────────
def to_pdf(html_path: Path, pdf_path: Path) -> bool:
    if not Path(EDGE).exists():
        print("  [WARN] Edge no encontrado, se omite PDF")
        return False
    url = html_path.resolve().as_uri()
    try:
        subprocess.run(
            [EDGE, "--headless", "--disable-gpu", "--no-pdf-header-footer",
             f"--print-to-pdf={pdf_path}", url],
            check=True, timeout=120,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return pdf_path.exists()
    except Exception as e:
        print(f"  [WARN] PDF falló: {type(e).__name__}: {e}")
        return False


# ── Orquestación ─────────────────────────────────────────────────────────────
# Perfiles que se generan por defecto. Se computan TODAS las secciones una vez y
# se renderiza cada perfil del mismo dict (no recalcula). Marketing = fase 2.
DEFAULT_PROFILES = ("full", "comercial", "clevel", "marketing")


def build_marca(marca: str, ym: str, profiles=DEFAULT_PROFILES):
    print(f"[{marca}] {ym}")
    secs: dict = {}

    def run(key, fn, *args):
        t0 = time.perf_counter()
        try:
            secs[key] = fn(marca, ym, *args)
            print(f"  [OK] {key} ({time.perf_counter() - t0:.1f}s)")
        except Exception as e:
            secs[key] = {}
            print(f"  [WARN] {key} falló: {type(e).__name__}: {e}")

    run("trend", sec_trend)
    run("performance", sec_performance)
    # Margen real del mes (fracción) → GM estimado coherente en geo/courier/pago.
    margin = None
    for m in secs.get("performance", {}).get("metrics", []):
        if m["label"] == "Margen %" and m["cur"] is not None:
            margin = m["cur"] / 100
    run("precios", sec_precios)
    run("top_productos", sec_top_productos)
    run("geografia", sec_geografia, margin)
    run("courier", sec_courier, margin)
    run("pagos", sec_pagos, margin)
    run("recompra", sec_recompra)
    run("cliente", sec_cliente)
    run("canales", sec_canales)
    run("campaigns", sec_campaigns)
    run("funnel", sec_funnel)
    run("devices", sec_devices)
    run("search", sec_search)
    run("gsc", sec_gsc)
    hallazgos = sec_hallazgos(secs)

    outdir = BASE / marca.replace(" ", "_") / ym.replace("-", "")
    outdir.mkdir(parents=True, exist_ok=True)
    for prof in profiles:
        suffix = "" if prof == "full" else f"_{prof}"   # 'full' conserva el nombre canónico
        stem = f"scorecard_{slug(marca)}_{ym.replace('-', '')}{suffix}"
        html_path = outdir / f"{stem}.html"
        html_path.write_text(render_html(marca, ym, secs, hallazgos, prof), encoding="utf-8")
        write_excel(outdir / f"{stem}.xlsx", secs, prof)
        pdf_ok = to_pdf(html_path, outdir / f"{stem}.pdf")
        print(f"  -> {prof}: {stem}.pdf ({'ok' if pdf_ok else 'sin pdf'})")


def main():
    ap = argparse.ArgumentParser(description="Scorecard de diagnóstico por marca/web")
    ap.add_argument("--marca", help="una marca (default: todas)")
    ap.add_argument("--periodo", help="YYYY-MM, o 'actual' (mes en curso, parcial) "
                    "o 'cerrado' (default: último mes cerrado)")
    ap.add_argument("--perfil", help="uno o varios separados por coma "
                    f"(default: {','.join(DEFAULT_PROFILES)}). Opciones: {','.join(PROFILES)}")
    args = ap.parse_args()
    # 'actual' = mes en curso (corrida semanal con data parcial); default/'cerrado'
    # = último mes cerrado (corrida de inicio de mes). Un YYYY-MM explícito manda.
    periodo = (args.periodo or "").strip().lower()
    if periodo in ("actual", "current", "now", "en_curso", "en-curso"):
        ym = dt.date.today().strftime("%Y-%m")
    elif periodo in ("", "cerrado", "closed"):
        ym = closed_month()
    else:
        ym = args.periodo
    marcas = [args.marca] if args.marca else MARCAS
    profiles = tuple(args.perfil.split(",")) if args.perfil else DEFAULT_PROFILES
    for m in marcas:
        try:
            build_marca(m, ym, profiles)
        except Exception as e:
            print(f"[ERROR] {m}: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
