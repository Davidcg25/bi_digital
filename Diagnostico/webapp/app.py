# -*- coding: utf-8 -*-
"""
app.py — Digital Impact: overview de operación (Nivel 1 del consolidado).

Espejo local del dashboard overview de Novedades (venta, zonas de despacho,
best sellers con imagen, cupones, pasarelas, multisource) + las capas que el
droplet no tiene: tráfico GA4 (demanda y CR), facturado RMH (la realidad
neta) y el cuadrante de productos tráfico×venta×stock.

Definiciones (mismas que el overview de Novedades):
  * Venta = pedidos con pago_confirmado=1, ingresos = SUM(grand_total_item)
    (sin cobro de envío). /1.18 la hace comparable con TotalNeto RMH.
  * Solo Perú: Chile queda fuera de este análisis.
  * Fuentes: vw_magento_orders_pedido/linea + vw_ops_diaria (40_ops_views.sql),
    vw_ga4_item_funnel (12m), Vista_Stock-rmh_MC_Resumen (último snapshot),
    Catalogo_Productos (imágenes; requiere /api/export/catalog desplegado).

Correr:  D:\\Proyectos\\4_BI_Ecom\\venv\\Scripts\\python.exe Diagnostico\\webapp\\app.py
         -> http://127.0.0.1:5050
"""
from __future__ import annotations

import calendar
import datetime as dt
import time

import pandas as pd
from flask import Flask, render_template, request
from sqlalchemy import create_engine, text

app = Flask(__name__)

ENGINE = create_engine(
    "mssql+pyodbc://@localhost/Digital_Impact_Reportes"
    "?trusted_connection=yes&driver=ODBC+Driver+17+for+SQL+Server"
)

_CACHE: dict[str, tuple[float, object]] = {}
CACHE_TTL_S = 600


def cached(key: str, fn):
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < CACHE_TTL_S:
        return hit[1]
    val = fn()
    _CACHE[key] = (now, val)
    return val


def q(sql: str, **params) -> pd.DataFrame:
    with ENGINE.connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def nz(v, default: float = 0.0) -> float:
    """NaN/None -> default ('or 0' no sirve: NaN es truthy)."""
    return default if v is None or pd.isna(v) else float(v)


# ── Universo Perú ────────────────────────────────────────────────────────────
WEBS_PE = ["Coliseum", "New Balance", "Caterpillar", "Converse",
           "Merrell", "Steve Madden", "Umbro", "Marketplaces Peru"]
WEBS_GA4 = WEBS_PE[:-1]  # con property GA4 (Marketplaces no tiene)
PERU_IN = "('" + "','".join(WEBS_PE) + "')"

# web_key -> Marca. del stock RMH (Coliseum = multimarca, sin filtro)
WEB_TO_MARCA_STOCK = {
    "Converse": "CONVERSE", "Caterpillar": "CATERPILLAR",
    "New Balance": "NEW BALANCE", "Merrell": "MERRELL",
    "Steve Madden": "STEVE MADDEN", "Umbro": "UMBRO",
}

PAYMENT_LABELS = {
    "mercadopago_adbpayment_cc": "Mercado Pago (tarjeta)",
    "mercadopago_adbpayment_yape": "Mercado Pago (Yape)",
    "mercadopago_standard": "Mercado Pago",
    "powerpay": "PowerPay", "banktransfer": "Transferencia",
    "apurata_financing": "Apurata", "izipay_izipay": "Izipay",
    "niubiz_niubiz": "Niubiz", "checkmo": "Efectivo", "free": "Gratuito",
    "paypal_express": "PayPal", "payu_latam": "PayU",
}

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


def norm_depto(raw) -> str:
    s = str(raw or "").strip().upper()
    return DEPTO_ALIASES.get(s, s) if s else "SIN DATO"


# ── Ventanas: actual + anterior (para deltas), espejo de Novedades ──────────
def windows(rng: str):
    hoy = dt.date.today()
    if rng == "7d":
        a1, a2 = hoy - dt.timedelta(days=6), hoy
        p1, p2 = a1 - dt.timedelta(days=7), a1 - dt.timedelta(days=1)
        label = "Últimos 7 días"
    elif rng == "mes_pasado":
        fin = hoy.replace(day=1) - dt.timedelta(days=1)
        a1, a2 = fin.replace(day=1), fin
        pfin = a1 - dt.timedelta(days=1)
        p1, p2 = pfin.replace(day=1), pfin
        label = f"Mes cerrado ({fin.strftime('%b %Y')})"
    elif rng == "ano":
        a1, a2 = hoy.replace(month=1, day=1), hoy
        p2_day = min(hoy.day, calendar.monthrange(hoy.year - 1, hoy.month)[1])
        p1, p2 = dt.date(hoy.year - 1, 1, 1), dt.date(hoy.year - 1, hoy.month, p2_day)
        label = "Año a la fecha"
    else:  # mes (MTD, default) — vs mes anterior a la misma altura
        a1, a2 = hoy.replace(day=1), hoy
        pm_last = a1 - dt.timedelta(days=1)
        p1 = pm_last.replace(day=1)
        p2 = pm_last.replace(day=min(hoy.day, pm_last.day))
        label = "Mes en curso (MTD)"
    return a1, a2, p1, p2, label


# ── Queries por sección ──────────────────────────────────────────────────────
def kpis_tienda(a1, a2, p1, p2) -> pd.DataFrame:
    return q(f"""
        SELECT web_key, periodo,
               COUNT(*)                  AS ordenes,
               SUM(unidades_confirmadas) AS unidades,
               SUM(venta_items)          AS ingresos
        FROM (
            SELECT web_key, unidades_confirmadas, venta_items,
                   CASE WHEN fecha BETWEEN :a1 AND :a2 THEN 'actual' ELSE 'anterior' END AS periodo
            FROM dbo.vw_magento_orders_pedido
            WHERE pago_confirmado = 1 AND web_key IN {PERU_IN}
              AND (fecha BETWEEN :a1 AND :a2 OR fecha BETWEEN :p1 AND :p2)
        ) t
        GROUP BY web_key, periodo
    """, a1=a1, a2=a2, p1=p1, p2=p2)


def embudo(a1, a2) -> pd.DataFrame:
    return q(f"""
        SELECT web_key,
               SUM(sesiones)          AS sesiones,
               SUM(compras_ga4)       AS compras_ga4,
               SUM(pedidos_pagados)   AS pedidos_pagados,
               SUM(pedidos)           AS pedidos,
               SUM(pedidos_cancelados) AS cancelados,
               SUM(venta_pagada_neta) AS venta_neta,
               SUM(facturado_rmh)     AS facturado
        FROM dbo.vw_ops_diaria
        WHERE fecha BETWEEN :a1 AND :a2 AND web_key IN {PERU_IN}
        GROUP BY web_key
    """, a1=a1, a2=a2)


def tendencia_30d() -> pd.DataFrame:
    d1 = dt.date.today() - dt.timedelta(days=30)
    return q(f"""
        SELECT fecha,
               SUM(pedidos_pagados)   AS pedidos,
               SUM(unidades_pagadas)  AS unidades,
               SUM(sesiones)          AS sesiones
        FROM dbo.vw_ops_diaria
        WHERE fecha >= :d1 AND web_key IN {PERU_IN}
        GROUP BY fecha ORDER BY fecha
    """, d1=d1)


def zonas(a1, a2) -> pd.DataFrame:
    return q(f"""
        SELECT departamento, COUNT(*) AS ordenes, SUM(venta_items) AS ingresos
        FROM dbo.vw_magento_orders_pedido
        WHERE pago_confirmado = 1 AND web_key IN {PERU_IN}
          AND fecha BETWEEN :a1 AND :a2
        GROUP BY departamento
    """, a1=a1, a2=a2)


def distritos_top(a1, a2) -> pd.DataFrame:
    return q(f"""
        SELECT TOP 12 distrito, departamento,
               COUNT(*) AS ordenes, SUM(venta_items) AS ingresos
        FROM dbo.vw_magento_orders_pedido
        WHERE pago_confirmado = 1 AND web_key IN {PERU_IN}
          AND fecha BETWEEN :a1 AND :a2
          AND distrito IS NOT NULL AND distrito <> ''
        GROUP BY distrito, departamento
        ORDER BY ordenes DESC
    """, a1=a1, a2=a2)


def cupones(a1, a2) -> pd.DataFrame:
    return q(f"""
        SELECT CASE WHEN coupon_code IS NULL OR LTRIM(RTRIM(coupon_code)) = ''
                    THEN 'sin' ELSE 'con' END AS grupo,
               COUNT(*) AS ordenes, SUM(venta_items) AS ingresos
        FROM dbo.vw_magento_orders_pedido
        WHERE pago_confirmado = 1 AND web_key IN {PERU_IN}
          AND fecha BETWEEN :a1 AND :a2
        GROUP BY CASE WHEN coupon_code IS NULL OR LTRIM(RTRIM(coupon_code)) = ''
                      THEN 'sin' ELSE 'con' END
    """, a1=a1, a2=a2)


def cupones_top(a1, a2) -> pd.DataFrame:
    return q(f"""
        SELECT TOP 8 coupon_code, COUNT(*) AS ordenes, SUM(venta_items) AS ingresos
        FROM dbo.vw_magento_orders_pedido
        WHERE pago_confirmado = 1 AND web_key IN {PERU_IN}
          AND fecha BETWEEN :a1 AND :a2
          AND coupon_code IS NOT NULL AND LTRIM(RTRIM(coupon_code)) <> ''
        GROUP BY coupon_code ORDER BY ordenes DESC
    """, a1=a1, a2=a2)


def pasarelas(a1, a2) -> pd.DataFrame:
    return q(f"""
        SELECT payment_method,
               COUNT(*)             AS total,
               SUM(CASE WHEN pago_confirmado = 1 THEN 1 ELSE 0 END) AS confirmadas
        FROM dbo.vw_magento_orders_pedido
        WHERE web_key IN {PERU_IN} AND fecha BETWEEN :a1 AND :a2
          AND payment_method IS NOT NULL AND payment_method <> ''
        GROUP BY payment_method ORDER BY total DESC
    """, a1=a1, a2=a2)


def multisource(a1, a2) -> pd.DataFrame:
    return q(f"""
        SELECT CASE WHEN n_sources >= 4 THEN '4+' ELSE CAST(n_sources AS varchar(2)) END AS bucket,
               COUNT(*) AS ordenes,
               AVG(CAST(envio_cobrado AS float)) AS envio_prom,
               SUM(venta_items) AS ingresos
        FROM dbo.vw_magento_orders_pedido
        WHERE pago_confirmado = 1 AND web_key IN {PERU_IN}
          AND fecha BETWEEN :a1 AND :a2 AND n_sources >= 1
        GROUP BY CASE WHEN n_sources >= 4 THEN '4+' ELSE CAST(n_sources AS varchar(2)) END
        ORDER BY bucket
    """, a1=a1, a2=a2)


def _has_catalogo() -> bool:
    def chk():
        n = q("SELECT COUNT(*) AS n FROM dbo.Catalogo_Productos")["n"].iloc[0]
        return bool(n)
    try:
        return cached("has_catalogo", chk)
    except Exception:
        return False


_CAT_JOIN = """
        LEFT JOIN (SELECT mc, MIN(base_image) AS img, MIN(name_web) AS name_web,
                          MIN(marca) AS marca, MIN(linea) AS linea
                   FROM dbo.Catalogo_Productos GROUP BY mc) c ON c.mc = b.mc
"""


def best_sellers(web, a1, a2, con_catalogo: bool) -> pd.DataFrame:
    cat_cols = ", c.img, c.name_web, c.marca, c.linea" if con_catalogo else \
               ", CAST(NULL AS nvarchar(1)) AS img, CAST(NULL AS nvarchar(1)) AS name_web," \
               " CAST(NULL AS nvarchar(1)) AS marca, CAST(NULL AS nvarchar(1)) AS linea"
    cat_join = _CAT_JOIN if con_catalogo else ""
    return q(f"""
        WITH b AS (
            SELECT mc, MAX(product_name) AS product_name,
                   SUM(qty_confirmed) AS unidades,
                   COUNT(DISTINCT id) AS ordenes,
                   SUM(grand_total_item) AS ingresos
            FROM dbo.vw_magento_orders_linea
            WHERE pago_confirmado = 1 AND web_key = :web
              AND fecha BETWEEN :a1 AND :a2
            GROUP BY mc
        )
        SELECT TOP 12 b.*, f.items_viewed, f.view_to_cart, f.med_view_to_cart {cat_cols}
        FROM b
        LEFT JOIN dbo.vw_ga4_item_funnel f
               ON f.property_name = :web AND f.item_id = b.mc
        {cat_join}
        ORDER BY b.unidades DESC
    """, web=web, a1=a1, a2=a2)


def trafico_sin_venta(web, a1, a2, con_catalogo: bool) -> pd.DataFrame:
    """Mucho tráfico (12m GA4), conversión bajo la mediana → sospecha curva/ficha."""
    cat_cols = ", c.img, c.name_web" if con_catalogo else \
               ", CAST(NULL AS nvarchar(1)) AS img, CAST(NULL AS nvarchar(1)) AS name_web"
    cat_join = _CAT_JOIN.replace("= b.mc", "= b.item_id") if con_catalogo else ""
    return q(f"""
        WITH b AS (
            SELECT f.item_id, f.item_name, f.items_viewed, f.items_purchased,
                   f.view_to_cart, f.med_view_to_cart
            FROM dbo.vw_ga4_item_funnel f
            WHERE f.property_name = :web
              AND f.items_viewed >= 200 AND f.brecha_vs_mediana <= -0.02
        ),
        v AS (   -- venta del rango (para confirmar que tampoco vende ahora)
            SELECT mc, SUM(qty_confirmed) AS unidades_rango
            FROM dbo.vw_magento_orders_linea
            WHERE pago_confirmado = 1 AND web_key = :web AND fecha BETWEEN :a1 AND :a2
            GROUP BY mc
        ),
        s AS (   -- stock web + curva (último snapshot)
            SELECT MC, [Ecommerce] AS stock_ecom, [Nro Tallas] AS n_tallas, Curvado
            FROM [dbo].[Vista_Stock-rmh_MC_Resumen]
            WHERE Fecha = (SELECT MAX(Fecha) FROM [dbo].[Vista_Stock-rmh_MC_Resumen])
        )
        SELECT TOP 12 b.*, COALESCE(v.unidades_rango, 0) AS unidades_rango,
               s.stock_ecom, s.n_tallas, s.Curvado {cat_cols}
        FROM b
        LEFT JOIN v ON v.mc = b.item_id
        LEFT JOIN s ON s.MC = b.item_id
        {cat_join}
        ORDER BY b.items_viewed DESC
    """, web=web, a1=a1, a2=a2)


def stock_sin_trafico(web, con_catalogo: bool) -> pd.DataFrame:
    """Mucho stock ecom, poco/nulo tráfico 12m → problema de visibilidad/push."""
    marca = WEB_TO_MARCA_STOCK.get(web)
    marca_filter = "AND UPPER(s.[Marca.]) = :marca" if marca else ""
    cat_cols = ", c.img, c.name_web" if con_catalogo else \
               ", CAST(NULL AS nvarchar(1)) AS img, CAST(NULL AS nvarchar(1)) AS name_web"
    cat_join = _CAT_JOIN.replace("= b.mc", "= b.MC") if con_catalogo else ""
    return q(f"""
        WITH b AS (
            SELECT s.MC, s.[Marca.] AS marca_stock, s.[Linea.] AS linea_stock,
                   s.[Ecommerce] AS stock_ecom, s.[Nro Tallas] AS n_tallas, s.Curvado,
                   f.items_viewed
            FROM [dbo].[Vista_Stock-rmh_MC_Resumen] s
            LEFT JOIN dbo.vw_ga4_item_funnel f
                   ON f.property_name = :web AND f.item_id = s.MC
            WHERE s.Fecha = (SELECT MAX(Fecha) FROM [dbo].[Vista_Stock-rmh_MC_Resumen])
              AND s.[Ecommerce] >= 30
              AND COALESCE(f.items_viewed, 0) < 200
              {marca_filter}
        )
        SELECT TOP 12 b.* {cat_cols}
        FROM b
        {cat_join}
        ORDER BY b.stock_ecom DESC
    """, web=web, marca=marca)


# ── Armado de la página ──────────────────────────────────────────────────────
def build_kpis(df: pd.DataFrame) -> list[dict]:
    out = []
    piv = {}
    for _, r in df.iterrows():
        piv.setdefault(r["web_key"], {})[r["periodo"]] = r
    for webk in [w for w in WEBS_PE if w in piv]:
        a = piv[webk].get("actual")
        p = piv[webk].get("anterior")
        ord_a = int(nz(a["ordenes"])) if a is not None else 0
        ing_a = nz(a["ingresos"]) if a is not None else 0.0
        ord_p = int(nz(p["ordenes"])) if p is not None else 0
        ing_p = nz(p["ingresos"]) if p is not None else 0.0
        out.append({
            "web": webk,
            "ordenes": ord_a, "d_ordenes": (ord_a / ord_p - 1) * 100 if ord_p else None,
            "unidades": int(nz(a["unidades"])) if a is not None else 0,
            "ingresos": ing_a, "d_ingresos": (ing_a / ing_p - 1) * 100 if ing_p else None,
            "ticket": ing_a / ord_a if ord_a else None,
        })
    tot_a = sum(x["ordenes"] for x in out)
    tot_ing = sum(x["ingresos"] for x in out)
    out.insert(0, {
        "web": "TOTAL PERÚ", "ordenes": tot_a, "d_ordenes": None,
        "unidades": sum(x["unidades"] for x in out),
        "ingresos": tot_ing, "d_ingresos": None,
        "ticket": tot_ing / tot_a if tot_a else None,
    })
    return out


def build_embudo(df: pd.DataFrame) -> list[dict]:
    rows = []
    for webk in [w for w in WEBS_PE if w in set(df["web_key"])]:
        r = df[df["web_key"] == webk].iloc[0]
        ses, ped_pag = nz(r["sesiones"]), nz(r["pedidos_pagados"])
        neta, fact = nz(r["venta_neta"]), nz(r["facturado"])
        rows.append({
            "web": webk,
            "sesiones": int(ses) or None,
            "cr": nz(r["compras_ga4"]) / ses * 100 if ses else None,
            "pedidos": int(nz(r["pedidos"])), "pagados": int(ped_pag),
            "cancel_pct": nz(r["cancelados"]) / nz(r["pedidos"]) * 100 if nz(r["pedidos"]) else None,
            "neta": neta, "facturado": fact or None,
            "ratio": fact / neta * 100 if neta and fact else None,
        })
    return rows


def build_zonas(df: pd.DataFrame) -> list[dict]:
    agg = {}
    for _, r in df.iterrows():
        dep = norm_depto(r["departamento"])
        zona = DEPTO_TO_ZONA.get(dep, "OTROS")
        x = agg.setdefault(zona, {"zona": zona, "ordenes": 0, "ingresos": 0.0, "deptos": {}})
        x["ordenes"] += int(nz(r["ordenes"]))
        x["ingresos"] += nz(r["ingresos"])
        x["deptos"][dep] = x["deptos"].get(dep, 0) + int(nz(r["ordenes"]))
    out = sorted(agg.values(), key=lambda x: -x["ordenes"])
    total = sum(x["ordenes"] for x in out) or 1
    for x in out:
        x["share"] = x["ordenes"] / total * 100
        x["top_deptos"] = ", ".join(
            f"{d} ({n})" for d, n in sorted(x["deptos"].items(), key=lambda kv: -kv[1])[:3])
    return out


def df_records(df: pd.DataFrame) -> list[dict]:
    return df.where(pd.notna(df), None).to_dict("records")


@app.route("/")
def overview():
    rng = request.args.get("rango", "mes")
    if rng not in ("7d", "mes", "mes_pasado", "ano"):
        rng = "mes"
    web = request.args.get("web", "Coliseum")
    if web not in WEBS_GA4:
        web = "Coliseum"
    a1, a2, p1, p2, rango_label = windows(rng)
    ck = f"{rng}|{a1}|{a2}"
    con_cat = _has_catalogo()

    kpis = build_kpis(cached(f"kpi|{ck}", lambda: kpis_tienda(a1, a2, p1, p2)))
    emb = build_embudo(cached(f"emb|{ck}", lambda: embudo(a1, a2)))
    tend = cached("tend", tendencia_30d)
    zon = build_zonas(cached(f"zon|{ck}", lambda: zonas(a1, a2)))
    dtt = df_records(cached(f"dis|{ck}", lambda: distritos_top(a1, a2)))
    cup = {r["grupo"]: r for r in df_records(cached(f"cup|{ck}", lambda: cupones(a1, a2)))}
    cup_top = df_records(cached(f"cupt|{ck}", lambda: cupones_top(a1, a2)))
    pas = df_records(cached(f"pas|{ck}", lambda: pasarelas(a1, a2)))
    mus = df_records(cached(f"mus|{ck}", lambda: multisource(a1, a2)))
    best = df_records(cached(f"bs|{web}|{ck}|{con_cat}", lambda: best_sellers(web, a1, a2, con_cat)))
    tsv = df_records(cached(f"tsv|{web}|{ck}|{con_cat}", lambda: trafico_sin_venta(web, a1, a2, con_cat)))
    sst = df_records(cached(f"sst|{web}|{con_cat}", lambda: stock_sin_trafico(web, con_cat)))

    # Cupones: participación + impacto en ticket
    con, sin = cup.get("con"), cup.get("sin")
    t_con = (con["ingresos"] / con["ordenes"]) if con and con["ordenes"] else None
    t_sin = (sin["ingresos"] / sin["ordenes"]) if sin and sin["ordenes"] else None
    cupones_kpi = {
        "ordenes_con": int(con["ordenes"]) if con else 0,
        "ordenes_sin": int(sin["ordenes"]) if sin else 0,
        "share": (con["ordenes"] / (con["ordenes"] + sin["ordenes"]) * 100)
                 if con and sin and (con["ordenes"] + sin["ordenes"]) else None,
        "ticket_con": t_con, "ticket_sin": t_sin,
        "impacto": (t_con / t_sin - 1) * 100 if t_con and t_sin else None,
    }
    for r in pas:
        r["label"] = PAYMENT_LABELS.get(r["payment_method"], r["payment_method"])
        r["conv"] = nz(r["confirmadas"]) / nz(r["total"]) * 100 if nz(r["total"]) else None

    chart_tend = {
        "labels": [pd.Timestamp(f).strftime("%d/%m") for f in tend["fecha"]],
        "pedidos": [nz(x) for x in tend["pedidos"]],
        "unidades": [nz(x) for x in tend["unidades"]],
        "sesiones": [nz(x) for x in tend["sesiones"]],
    }

    return render_template(
        "overview.html",
        rango=rng, rango_label=rango_label, d1=a1, d2=a2, web=web,
        webs=WEBS_GA4, kpis=kpis, embudo=emb, chart_tend=chart_tend,
        zonas=zon, distritos=dtt, cupones=cupones_kpi, cupones_top=cup_top,
        pasarelas=pas, multisource=mus,
        best=best, trafico_sin_venta=tsv, stock_sin_trafico=sst,
        con_catalogo=con_cat,
        generado=dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
