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
    # Fugas por producto VALIDADAS contra stock/curva de tallas.
    # GA4 item_id == MC del stock → cruzamos para saber si la fuga es por
    # STOCK (curva rota / sin ecom) o por la FICHA (foto/precio/contenido).
    f = _q(eng, """
        SELECT TOP 12 f.property_name, f.item_name, f.item_id, f.items_viewed,
               f.view_to_cart, f.med_view_to_cart,
               s.[Total General] tot, s.Ecommerce eco, s.Marketplace mkt,
               s.[Nro Tallas] tallas, s.Curvado
        FROM vw_ga4_item_funnel f
        LEFT JOIN [Vista_Stock-rmh_MC_Resumen] s
               ON s.MC = f.item_id
              AND s.Fecha = (SELECT MAX(Fecha) FROM [Vista_Stock-rmh_MC_Resumen])
        WHERE f.items_viewed >= 200 AND f.brecha_vs_mediana <= -0.02
        ORDER BY f.items_viewed DESC
    """)
    vistos = set()
    for _, x in f.iterrows():
        nombre = str(x["item_name"])[:38]
        if nombre in vistos:
            continue
        vistos.add(nombre)
        base = f"«{nombre}» ({int(x['items_viewed'])} vistas, {x['view_to_cart']*100:.1f}% a carrito vs {x['med_view_to_cart']*100:.1f}% mediana)"
        tot, eco, curv, tallas = x["tot"], x["eco"], x["Curvado"], x["tallas"]
        if pd.isna(tot):
            sev, causa = "media", "sin match de stock (revisar MC) → validar ficha"
        elif (eco or 0) == 0 or (str(curv).upper() not in ("CURVADO", "")):
            sev = "alta"
            causa = f"CAUSA STOCK: {int(tot)}u total, {int(eco or 0)} en ecom, {int(tallas or 0)} tallas ({curv}) → reponer/curva, NO es la ficha"
        else:
            sev = "media"
            causa = f"stock OK ({int(tot)}u, {curv}) → la fuga es la FICHA (foto/precio/contenido)"
        items.append(dict(marca=x["property_name"], sev=sev, texto=f"{base}. {causa}"))
        if len(vistos) >= 6:
            break
    return items

# ── Área 2: Confianza de datos ─────────────────────────────────────
def area_confianza(eng):
    items = []
    # Piso de volumen: ignorar webs sin tráfico relevante (ej. Fila inactiva)
    c = _q(eng, "SELECT property_name, pct_ses_utilitarias, pct_ses_sizechart, pct_ses_direct, pct_rev_direct FROM vw_ga4_certificacion_resumen WHERE ses_total_paginas >= 50000")
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
    # El ux_risk_score pondera % de SESIONES con cada problema (no conteos):
    # script_err×3 + dead×2 + error×2 + rage×3 + quickback×1 + (scroll<25 → +10).
    # Por eso un risk alto con 0 rage/dead viene de errores de script / scroll.
    # Reportamos el DRIVER real, no un conteo que confunde.
    items = []
    u = _q(eng, """
        SELECT TOP 8 project_name,
            CASE WHEN CHARINDEX('?',url)>0 THEN LEFT(url,CHARINDEX('?',url)-1) ELSE url END AS pagina,
            SUM(sessions) ses, MAX(ux_risk_score) risk,
            MAX(script_error_session_pct) script_err, MAX(rage_click_session_pct) rage,
            MAX(dead_click_session_pct) dead, MAX(error_click_session_pct) err,
            MAX(quickback_session_pct) quickback, MIN(avg_scroll_depth) scroll
        FROM vw_clarity_url_device_summary
        WHERE extraction_date_utc = (SELECT MAX(extraction_date_utc) FROM vw_clarity_url_device_summary)
        GROUP BY project_name, CASE WHEN CHARINDEX('?',url)>0 THEN LEFT(url,CHARINDEX('?',url)-1) ELSE url END
        HAVING SUM(sessions) >= 40
        ORDER BY MAX(ux_risk_score) DESC
    """)
    etiquetas = [("script_err", "errores de script"), ("rage", "rage clicks"),
                 ("dead", "dead clicks"), ("err", "error clicks"), ("quickback", "quickbacks (entran y rebotan)")]
    for _, x in u.head(6).iterrows():
        pag = str(x["pagina"]).replace("https://", "").replace("http://", "")
        drivers = sorted(((x[k] or 0), lbl) for k, lbl in etiquetas)
        top_pct, top_lbl = drivers[-1]
        partes = []
        if top_pct >= 1:
            partes.append(f"{top_pct:.0f}% de sesiones con {top_lbl}")
        if (x["scroll"] or 100) < 25:
            partes.append(f"scroll bajo ({x['scroll']:.0f}%)")
        driver = "; ".join(partes) if partes else "fricción difusa"
        sev = "alta" if (x["risk"] or 0) >= 400 else "media"
        items.append(dict(marca=x["project_name"], sev=sev,
            texto=f"«{pag[:48]}» ({int(x['ses'])} sesiones): {driver}. Revisar esa página."))
    return items

# ── Área 4: Operación (ventas / stock) ─────────────────────────────
MKTS = {"Falabella", "MercadoLibre", "Ripley"}

def area_operacion(eng):
    # Solo lo que es operación de David (Ecommerce + Marketplace), por Tienda_ecom.
    # Marketplaces (Falabella/Ripley/MercadoLibre) SIEMPRE visibles aunque estén estables.
    items = []
    v = _q(eng, """
        SELECT Tienda_ecom tienda, CAST(Fecha AS date) f, SUM(TotalNeto_Total) neto, SUM(Ordenes) ord
        FROM Vista_Ventas_Solidez_Resumen
        WHERE Fecha >= DATEADD(day,-14, CAST(GETDATE() AS date)) AND Tienda_ecom IS NOT NULL
        GROUP BY Tienda_ecom, CAST(Fecha AS date)
    """)
    if v.empty:
        return items
    v["f"] = pd.to_datetime(v["f"])
    hoy = pd.Timestamp(dt.date.today())
    for tienda, g in v.groupby("tienda"):
        u7 = g[g["f"] >= hoy - pd.Timedelta(days=7)]["neto"].sum()
        p7 = g[(g["f"] < hoy - pd.Timedelta(days=7)) & (g["f"] >= hoy - pd.Timedelta(days=14))]["neto"].sum()
        es_mkt = tienda in MKTS
        d = (u7 - p7) / p7 if p7 and p7 > 0 else None
        # Piso de volumen para no alarmar con tiendas chicas (salvo marketplaces, que siempre se muestran)
        if not es_mkt and (p7 or 0) < 3000:
            continue
        delta = f"{d*100:+.0f}% vs 7d previos" if d is not None else "sin base previa"
        tag = "🛒 Marketplace" if es_mkt else "Web"
        if d is not None and d <= -0.20:
            items.append(dict(marca=f"{tienda} ({tag})", sev="alta",
                texto=f"Ventas 7d S/{u7:,.0f} ({delta}) — caída fuerte, revisar."))
        elif es_mkt:
            sev = "media" if (d is not None and d <= -0.10) else "info"
            items.append(dict(marca=f"{tienda} ({tag})", sev=sev,
                texto=f"Ventas 7d S/{u7:,.0f} ({delta})."))
    return items

# ── Área 5: Tareas para agencia de desarrollo ──────────────────────
# Temas que NO se resuelven en Magento con el equipo interno → ticket dev.
# Genera briefs ESPECIFICADOS (con evidencia fresca) en briefs_agencia.md.
def _brief_sizecharts(sc):
    sc = sc.sort_values("ses", ascending=False)
    afect = sc[sc["ses"] >= 100000]
    limpios = sc[sc["ses"] < 100000]
    filas = "\n".join(f"| {r['property_name']} | {int(r['ses']):,} |" for _, r in afect.iterrows())
    limp = ", ".join(f"{r['property_name']} ({int(r['ses']):,})" for _, r in limpios.iterrows()) or "—"
    return f"""## Ticket 1 — Sizecharts inflan el analytics (GA4)

**Problema:** las páginas `/sizechart-*` (guía de tallas) son la **página #1 por sesiones** en varios sites independientes. Un widget de tallas no debería rankear #1 ni generar sesiones propias.

**Evidencia (sesiones 12m en URLs /sizechart-*):**

| Site | Sesiones |
|------|----------|
{filas}

**Pista clave:** estos sites SÍ lo tienen, pero **{limp}** casi no → la implementación difiere. Comparar.

**Hipótesis:** en los sites afectados el componente sizechart dispara un `page_view` / virtual pageview al abrirse (debería ser un *event*, no un page_view).

**Qué pedimos revisar:**
1. ¿El sizechart es una ruta/página que carga (con su page_view) o un modal? Debería ser modal/evento.
2. GTM/GA4: ¿hay un `page_view` (o `send_page_view`) al abrir el sizechart? Convertirlo en evento (ej. `view_size_chart`).
3. Comparar la implementación de los sites limpios con la de los afectados.

**Impacto:** infla sesiones ~20-27%, distorsiona top-pages, landing, bounce y conversión por página.
**Criterio de aceptación:** sizechart registrado como evento (no page_view); las URLs `/sizechart-*` salen de los reportes de páginas; las sesiones del site bajan a su volumen real."""

def _brief_direct(dr):
    filas = "\n".join(f"| {r['property_name']} | {r['pct_ses_direct']*100:.0f}% | {r['pct_rev_direct']*100:.0f}% |" for _, r in dr.iterrows())
    return f"""## Ticket 2 — Direct anómalo: diagnosticar la atribución (nuevo vs recurrente)

**Problema:** el canal **Direct** está anormalmente alto, sobre todo en **revenue** (compras atribuidas a Direct en vez de a su canal real).

**Evidencia (% de sesiones / % de revenue en Direct):**

| Web | % sesiones Direct | % revenue Direct |
|-----|-------------------|------------------|
{filas}

**Señal:** Direct domina el *revenue* (su % de revenue > % de sesiones) → las sesiones Direct **convierten mejor que el promedio**. Hay que separar dos causas distintas mezcladas ahí.

**Descartado por volumen (con data interna de medios de pago):** las pasarelas que redirigen (Acuotaz/PowerPay/apurata) son ~4% de las órdenes pagadas (PowerPay 49 de ~1,170 últimos 7d). No pueden explicar el grueso del Direct. Excluir sus dominios igual por limpieza, pero NO es la causa.

**Hipótesis reales (diagnosticar, no asumir):**
1. **Atribución rota de pauta / social:** los navegadores in-app (Instagram/Facebook/TikTok) suelen strippear el referrer y perder `utm_*` → tráfico pagado de alto intento cae a Direct. Si la pauta social es fuerte, explica revenue alto en Direct.
2. **Cobertura incompleta de UTMs** en pauta/email/afiliados.
3. **Direct legítimo de marca:** clientes recurrentes de una marca conocida (Converse) entran directo y compran → parte es real, no bug.
4. Consent-mode/cookies que bloquean GA hasta aceptar.

**El diagnóstico clave (1 corte barato): segmentar Direct por usuario NUEVO vs RECURRENTE.**
- Mayormente **recurrente** → buena parte es legítimo (fuerza de marca), no urgente.
- Mayormente **nuevo** → atribución rota (un usuario nuevo no debería ser "directo") → perseguir UTMs / in-app browsers.
Complementar con device (mobile alto + landing en ficha = huele a social-app) y landing page.

**Qué pedimos revisar:**
1. Segmentar el canal Direct por **nuevo/recurrente, device y landing page** → con eso se sabe el mix real (legítimo vs roto).
2. Auditar cobertura de **UTMs** en pauta (Meta/Google) y el parsing de in-app browsers.
3. Menor/limpieza: excluir dominios de Acuotaz/PowerPay de Referral Exclusions.

**Criterio de aceptación:** saber qué % de Direct es legítimo (recurrente) vs roto (nuevo sin UTM), y recuperar la atribución del tráfico pagado."""

def area_agencia(eng):
    items, briefs = [], []
    sc = _q(eng, """
        SELECT property_name, SUM(sessions) ses FROM ga4_pages_12m
        WHERE page_path LIKE '%sizechart%' GROUP BY property_name
        HAVING SUM(sessions) >= 1
    """)
    if not sc.empty and (sc["ses"] >= 100000).any():
        afect = sc[sc["ses"] >= 100000].sort_values("ses", ascending=False)
        resumen = ", ".join(f"{r['property_name']} {r['ses']/1e6:.1f}M" for _, r in afect.iterrows())
        items.append(dict(marca="Sizecharts (GA4)", sev="alta",
            texto=f"Páginas /sizechart-* son la #1 por sesiones en {len(afect)} sites ({resumen}). → Ticket dev. Detalle en briefs_agencia.md."))
        briefs.append(_brief_sizecharts(sc))
    dr = _q(eng, """
        SELECT property_name, pct_ses_direct, pct_rev_direct FROM vw_ga4_certificacion_resumen
        WHERE ses_total_paginas >= 50000 AND (pct_ses_direct >= 0.40 OR pct_rev_direct >= 0.35)
        ORDER BY pct_rev_direct DESC
    """)
    if not dr.empty:
        resumen = ", ".join(f"{r['property_name']} {r['pct_rev_direct']*100:.0f}% rev" for _, r in dr.iterrows())
        items.append(dict(marca="Atribución Direct", sev="alta",
            texto=f"Direct anómalo en {len(dr)} webs ({resumen}) — segmentar nuevo/recurrente para separar marca-legítimo de atribución rota. → Ticket dev. Detalle en briefs_agencia.md."))
        briefs.append(_brief_direct(dr))
    if briefs:
        md = "# Tareas para agencia de desarrollo — generado " + dt.datetime.now().strftime("%Y-%m-%d %H:%M") + "\n\n" + "\n\n---\n\n".join(briefs) + "\n"
        (OUTDIR / "briefs_agencia.md").write_text(md, encoding="utf-8")
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
        "agencia": "⑤ Tareas para agencia (desarrollo)",
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
                    ("ux", area_ux), ("operacion", area_operacion), ("agencia", area_agencia)]:
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
