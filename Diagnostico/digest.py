# -*- coding: utf-8 -*-
"""
digest.py — Resumen Accionable de diagnóstico (4 áreas) → HTML local.

Lee las vistas del SQL Server Digital_Impact_Reportes y produce un digest
priorizado de "qué accionar". Pensado para correr DESPUÉS del refresh diario.
Salida: Diagnostico/digest/digest_<fecha>.html + digest_latest.html (para Flask).

Filosofía (estrella polar): no es un dump de métricas — es el cerebro que dice
qué tocar. Cada item es una ACCIÓN, rankeada por severidad. Cada área es
resiliente (si una query falla, las demás siguen).

Correr:  D:\\Proyectos\\4_BI_Ecom\\venv\\Scripts\\python.exe Diagnostico\\digest.py
"""
from __future__ import annotations
import os
import datetime as dt
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

BASE = Path(__file__).resolve().parent
OUTDIR = BASE / "digest"
OUTDIR.mkdir(exist_ok=True)

def get_engine():
    return create_engine(
        "mssql+pyodbc://@localhost/Digital_Impact_Reportes"
        "?trusted_connection=yes&driver=ODBC+Driver+17+for+SQL+Server"
    )

# Cada item: dict(area, marca, sev, texto). sev ∈ {"alta","media","info"}.
def _q(eng, sql):
    return pd.read_sql(text(sql), eng.connect())

# ── Área 1: Conversión y fugas ─────────────────────────────────────
def area_conversion(eng):
    items = []
    # MoM en tasa vista→carrito y vista→compra (las tasas no dependen del largo del mes)
    r = _q(eng, """
        SELECT property_name, year_month, cart_to_view_rate c2v, purchase_to_view_rate p2v
        FROM ga4_monthly_rates
        WHERE year_month IN (SELECT TOP 2 year_month FROM ga4_monthly_rates GROUP BY year_month ORDER BY year_month DESC)
    """)
    if not r.empty:
        meses = sorted(r["year_month"].unique())
        if len(meses) == 2:
            prev, cur = meses
            piv = r.pivot_table(index="property_name", columns="year_month", values="c2v")
            for marca, row in piv.iterrows():
                a, b = row.get(prev), row.get(cur)
                if a and b and a > 0:
                    d = (b - a) / a
                    if d <= -0.15:
                        items.append(dict(marca=marca, sev="alta",
                            texto=f"Conversión vista→carrito cayó {d*100:.0f}% ({a*100:.1f}%→{b*100:.1f}%) vs mes previo. Revisar ficha/checkout."))
                    elif d >= 0.15:
                        items.append(dict(marca=marca, sev="info",
                            texto=f"Conversión vista→carrito subió {d*100:.0f}% ({a*100:.1f}%→{b*100:.1f}%). Ver qué se hizo bien y replicar."))
    # Fugas por producto (alto tráfico, baja conversión a carrito vs mediana del catálogo)
    f = _q(eng, """
        SELECT TOP 6 property_name, item_name, items_viewed, view_to_cart, med_view_to_cart, brecha_vs_mediana
        FROM vw_ga4_item_funnel
        WHERE items_viewed >= 200 AND brecha_vs_mediana <= -0.02
        ORDER BY items_viewed DESC
    """)
    for _, x in f.iterrows():
        items.append(dict(marca=x["property_name"], sev="media",
            texto=f"Producto «{str(x['item_name'])[:45]}»: {int(x['items_viewed'])} vistas pero {x['view_to_cart']*100:.1f}% a carrito vs {x['med_view_to_cart']*100:.1f}% de la mediana. Revisar precio/foto/talla/stock."))
    return items

# ── Área 2: Confianza de datos ─────────────────────────────────────
def area_confianza(eng):
    items = []
    c = _q(eng, "SELECT property_name, pct_ses_utilitarias, pct_ses_sizechart, pct_ses_direct, pct_rev_direct FROM vw_ga4_certificacion_resumen")
    for _, x in c.iterrows():
        m = x["property_name"]
        if (x["pct_ses_direct"] or 0) >= 0.40:
            items.append(dict(marca=m, sev="alta",
                texto=f"{x['pct_ses_direct']*100:.0f}% de sesiones en canal Direct → atribución dudosa (bots/sin taggear). Sanear UTMs antes de leer canales."))
        if (x["pct_rev_direct"] or 0) >= 0.35:
            items.append(dict(marca=m, sev="media",
                texto=f"Direct se lleva {x['pct_rev_direct']*100:.0f}% del revenue → fuga de atribución (pauta sin UTM). Revisar tagging de campañas."))
        if (x["pct_ses_utilitarias"] or 0) >= 0.20:
            items.append(dict(marca=m, sev="media",
                texto=f"{x['pct_ses_utilitarias']*100:.0f}% de sesiones son páginas basura ({x['pct_ses_sizechart']*100:.0f}% sizecharts) → excluirlas al analizar contenido."))
    return items

# ── Área 3: UX / fricción (Clarity) ────────────────────────────────
def area_ux(eng):
    items = []
    u = _q(eng, """
        SELECT TOP 6 project_name,
            CASE WHEN CHARINDEX('?',url)>0 THEN LEFT(url,CHARINDEX('?',url)-1) ELSE url END AS pagina,
            SUM(sessions) ses, MAX(ux_risk_score) risk, SUM(rage_clicks) rage, SUM(dead_clicks) dead
        FROM vw_clarity_url_device_summary
        WHERE extraction_date_utc = (SELECT MAX(extraction_date_utc) FROM vw_clarity_url_device_summary)
        GROUP BY project_name, CASE WHEN CHARINDEX('?',url)>0 THEN LEFT(url,CHARINDEX('?',url)-1) ELSE url END
        HAVING SUM(sessions) >= 30
        ORDER BY MAX(ux_risk_score) DESC
    """)
    for _, x in u.iterrows():
        pag = str(x["pagina"]).replace("https://", "").replace("http://", "")
        sev = "alta" if (x["risk"] or 0) >= 30 else "media"
        items.append(dict(marca=x["project_name"], sev=sev,
            texto=f"Fricción alta en «{pag[:50]}» (risk {x['risk']:.0f}; {int(x['rage'] or 0)} rage / {int(x['dead'] or 0)} dead clicks en {int(x['ses'])} sesiones). Revisar esa página."))
    return items

# ── Área 4: Operación (ventas / stock) ─────────────────────────────
def area_operacion(eng):
    items = []
    # Ventas: últimos 7 días vs 7 previos por marca
    v = _q(eng, """
        SELECT Marca_Limpia marca, CAST(Fecha AS date) f, SUM(TotalNeto_Total) neto
        FROM Vista_Ventas_Solidez_Resumen
        WHERE Fecha >= DATEADD(day,-14, CAST(GETDATE() AS date))
        GROUP BY Marca_Limpia, CAST(Fecha AS date)
    """)
    if not v.empty:
        v["f"] = pd.to_datetime(v["f"])
        hoy = pd.Timestamp(dt.date.today())
        for marca, g in v.groupby("marca"):
            u7 = g[g["f"] >= hoy - pd.Timedelta(days=7)]["neto"].sum()
            p7 = g[(g["f"] < hoy - pd.Timedelta(days=7)) & (g["f"] >= hoy - pd.Timedelta(days=14))]["neto"].sum()
            if p7 and p7 > 0:
                d = (u7 - p7) / p7
                if d <= -0.20:
                    items.append(dict(marca=marca, sev="alta",
                        texto=f"Ventas últimos 7d cayeron {d*100:.0f}% vs los 7 previos. Revisar."))
    # Stock: SKUs con acción sugerida (snapshot más reciente)
    try:
        s = _q(eng, """
            SELECT [Marca.] marca, [Accion Sugerida] accion, COUNT(*) n
            FROM [Vista_Stock-rmh_SKU_Activacion_Magento]
            WHERE [Accion Sugerida] IS NOT NULL AND [Accion Sugerida] <> ''
            GROUP BY [Marca.], [Accion Sugerida]
            HAVING COUNT(*) >= 20
            ORDER BY COUNT(*) DESC
        """)
        for _, x in s.head(6).iterrows():
            acc = str(x["accion"]).lower()
            if "mantener" in acc or "ok" in acc:
                continue
            items.append(dict(marca=x["marca"], sev="info",
                texto=f"{int(x['n'])} SKUs con acción sugerida «{x['accion']}» en Magento. Revisar activación."))
    except Exception:
        pass
    return items

# ── Render HTML ────────────────────────────────────────────────────
SEV_ORDER = {"alta": 0, "media": 1, "info": 2}
SEV_COLOR = {"alta": "#c0392b", "media": "#e08e0b", "info": "#0078d4"}

def render(areas: dict[str, list]):
    ahora = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    total = sum(len(v) for v in areas.values())
    css = """
    body{font-family:Segoe UI,Arial,sans-serif;background:#f4f6f9;color:#1a2230;margin:0;padding:24px;}
    .wrap{max-width:960px;margin:0 auto;}
    h1{color:#001f3f;margin:0 0 4px;} .sub{color:#67727e;margin:0 0 24px;font-size:14px;}
    .area{background:#fff;border-radius:10px;box-shadow:0 1px 4px rgba(0,0,0,.08);margin-bottom:18px;overflow:hidden;}
    .area h2{margin:0;padding:14px 18px;background:#001f3f;color:#fff;font-size:16px;}
    .item{display:flex;gap:12px;padding:11px 18px;border-top:1px solid #eef1f5;align-items:flex-start;}
    .badge{flex:none;font-size:11px;font-weight:700;color:#fff;border-radius:4px;padding:2px 8px;margin-top:1px;}
    .marca{font-weight:700;color:#001f3f;}
    .empty{padding:14px 18px;color:#8a939e;font-style:italic;}
    """
    parts = [f"<!doctype html><html lang=es><head><meta charset=utf-8><title>Resumen Accionable</title><style>{css}</style></head><body><div class=wrap>"]
    parts.append(f"<h1>Resumen Accionable — Diagnóstico</h1><p class=sub>Generado {ahora} · {total} señales · ordenadas por severidad</p>")
    titulos = {
        "conversion": "① Conversión y fugas",
        "confianza": "② Confianza de datos",
        "ux": "③ UX / fricción (Clarity)",
        "operacion": "④ Operación (ventas / stock)",
    }
    for key, titulo in titulos.items():
        items = sorted(areas.get(key, []), key=lambda i: SEV_ORDER.get(i["sev"], 9))
        parts.append(f"<div class=area><h2>{titulo}</h2>")
        if not items:
            parts.append("<div class=empty>Sin señales relevantes hoy ✓</div>")
        for it in items:
            col = SEV_COLOR.get(it["sev"], "#67727e")
            parts.append(
                f"<div class=item><span class=badge style='background:{col}'>{it['sev'].upper()}</span>"
                f"<div><span class=marca>{it['marca']}</span> — {it['texto']}</div></div>")
        parts.append("</div>")
    parts.append("</div></body></html>")
    return "\n".join(parts)

def main():
    eng = get_engine()
    areas = {}
    for key, fn in [("conversion", area_conversion), ("confianza", area_confianza),
                    ("ux", area_ux), ("operacion", area_operacion)]:
        try:
            areas[key] = fn(eng)
            print(f"[OK] {key}: {len(areas[key])} señales")
        except Exception as e:
            areas[key] = []
            print(f"[WARN] {key} falló: {type(e).__name__}: {e}")
    html = render(areas)
    fecha = dt.datetime.now().strftime("%Y%m%d_%H%M")
    (OUTDIR / f"digest_{fecha}.html").write_text(html, encoding="utf-8")
    (OUTDIR / "digest_latest.html").write_text(html, encoding="utf-8")
    print(f"[DONE] {OUTDIR / 'digest_latest.html'}")

if __name__ == "__main__":
    main()
