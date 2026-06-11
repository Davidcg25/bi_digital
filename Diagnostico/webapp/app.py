# -*- coding: utf-8 -*-
"""
app.py — Digital Impact: overview de operación (Nivel 1 del consolidado).

App Flask LOCAL sobre Digital_Impact_Reportes. Página overview:
embudo macro por web (GA4 demanda × Magento pedido × RMH facturado)
desde dbo.vw_ops_diaria (Diagnostico/40_ops_views.sql).

Correr:  D:\\Proyectos\\4_BI_Ecom\\venv\\Scripts\\python.exe Diagnostico\\webapp\\app.py
         -> http://127.0.0.1:5050
"""
from __future__ import annotations

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

# Caché TTL simple (mismo patrón que el dashboard de Novedades)
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


# Webs Perú con las 3 capas primero; Chile/Marketplaces solo capa Magento
WEBS_FULL = ["Coliseum", "New Balance", "Caterpillar", "Converse",
             "Merrell", "Steve Madden", "Umbro"]
WEBS_MAG = ["Marketplaces Peru", "Converse Chile", "Coliseum Chile",
            "Fila Chile", "Umbro Chile"]


def date_range(rango: str) -> tuple[dt.date, dt.date, str]:
    today = dt.date.today()
    if rango == "7d":
        return today - dt.timedelta(days=7), today - dt.timedelta(days=1), "Últimos 7 días"
    if rango == "mes_prev":
        fin = today.replace(day=1) - dt.timedelta(days=1)
        return fin.replace(day=1), fin, f"Mes cerrado ({fin.strftime('%B %Y')})"
    return today.replace(day=1), today, "Mes en curso (MTD)"


def load_resumen(d1: dt.date, d2: dt.date) -> pd.DataFrame:
    return q("""
        SELECT web_key,
               SUM(sesiones)            AS sesiones,
               SUM(compras_ga4)         AS compras_ga4,
               SUM(pedidos)             AS pedidos,
               SUM(pedidos_cancelados)  AS cancelados,
               SUM(pedidos_pendientes)  AS pendientes,
               SUM(venta_ordenada)      AS ordenado,
               SUM(venta_cancelada)     AS cancelado_monto,
               SUM(facturado_rmh)       AS facturado,
               SUM(unidades_rmh)        AS unidades
        FROM dbo.vw_ops_diaria
        WHERE fecha BETWEEN :d1 AND :d2
        GROUP BY web_key
    """, d1=d1, d2=d2)


def load_serie(web: str, d1: dt.date, d2: dt.date) -> pd.DataFrame:
    return q("""
        SELECT fecha, sesiones, compras_ga4, pedidos, pedidos_cancelados,
               venta_ordenada, venta_cancelada, facturado_rmh
        FROM dbo.vw_ops_diaria
        WHERE web_key = :web AND fecha BETWEEN :d1 AND :d2
        ORDER BY fecha
    """, web=web, d1=d1, d2=d2)


def nz(v, default: float = 0.0) -> float:
    """NaN/None -> default ('or 0' no sirve: NaN es truthy)."""
    return default if v is None or pd.isna(v) else float(v)


def fmt_row(r: pd.Series) -> dict:
    ses = nz(r.get("sesiones"))
    ped = int(nz(r.get("pedidos")))
    can = int(nz(r.get("cancelados")))
    return {
        "web": r["web_key"],
        "sesiones": int(ses),
        "cr": (nz(r.get("compras_ga4")) / ses * 100) if ses else None,
        "pedidos": ped,
        "cancel_pct": (can / ped * 100) if ped else None,
        "ordenado": nz(r.get("ordenado")),
        "facturado": nz(r.get("facturado")) or None,
    }


@app.route("/")
def overview():
    rango = request.args.get("rango", "mtd")
    web = request.args.get("web", "Coliseum")
    d1, d2, rango_label = date_range(rango)

    resumen = cached(f"res|{rango}|{d1}|{d2}", lambda: load_resumen(d1, d2))
    orden = {w: i for i, w in enumerate(WEBS_FULL + WEBS_MAG)}
    resumen = resumen.sort_values(by="web_key", key=lambda s: s.map(lambda x: orden.get(x, 99)))
    filas = [fmt_row(r) for _, r in resumen.iterrows()]

    serie = cached(f"ser|{web}|{rango}|{d1}|{d2}", lambda: load_serie(web, d1, d2))
    chart = {
        "labels": [f.strftime("%d/%m") for f in pd.to_datetime(serie["fecha"]).dt.date] if not serie.empty else [],
        "sesiones": [nz(x) for x in serie.get("sesiones", [])],
        "pedidos": [nz(x) for x in serie.get("pedidos", [])],
        "ordenado": [round(nz(x), 0) for x in serie.get("venta_ordenada", [])],
        "facturado": [round(nz(x), 0) for x in serie.get("facturado_rmh", [])],
    }

    return render_template(
        "overview.html",
        filas=filas, chart=chart, web=web, rango=rango, rango_label=rango_label,
        webs=WEBS_FULL + WEBS_MAG, d1=d1, d2=d2,
        generado=dt.datetime.now().strftime("%d/%m/%Y %H:%M"),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5050, debug=False)
