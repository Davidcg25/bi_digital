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
def _q(eng, sql, **params):
    return pd.read_sql(text(sql), eng.connect(), params=params or None)

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
    #
    # La vista ya normaliza la URL (sin querystring) y agrega bien dentro de cada
    # device. Aqui colapsamos los devices de una pagina ponderando %/scroll por
    # sesiones — NUNCA MIN/MAX, que reportaban el peor fragmento de 1 sesion
    # (de ahi salian falsos "100% rage" y "scroll 5%").
    items = []
    u = _q(eng, """
        SELECT TOP 8 project_name, url AS pagina,
            SUM(sessions) ses,
            SUM(ux_risk_score*sessions)/NULLIF(SUM(sessions),0) risk,
            SUM(script_error_session_pct*sessions)/NULLIF(SUM(sessions),0) script_err,
            SUM(rage_click_session_pct*sessions)/NULLIF(SUM(sessions),0) rage,
            SUM(dead_click_session_pct*sessions)/NULLIF(SUM(sessions),0) dead,
            SUM(error_click_session_pct*sessions)/NULLIF(SUM(sessions),0) err,
            SUM(quickback_session_pct*sessions)/NULLIF(SUM(sessions),0) quickback,
            SUM(avg_scroll_depth*sessions)/NULLIF(SUM(sessions),0) scroll
        FROM vw_clarity_url_device_summary
        WHERE extraction_date_utc = (SELECT MAX(extraction_date_utc) FROM vw_clarity_url_device_summary)
        GROUP BY project_name, url
        HAVING SUM(sessions) >= 40
        ORDER BY risk DESC
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

# ── Área 6: Decisiones de negocio ──────────────────────────────────
# Convierte data operativa en recomendaciones go/no-go (no solo métricas).
# Primer módulo: MÉTODOS DE PAGO. La conversión (pagado/iniciado) por método
# en Perú; para los de FINANCIAMIENTO (BNPL) evalúa si la baja conversión
# justifica apagarlos cruzando 3 cosas: aporte (share de venta confirmada),
# incrementalidad (ticket) y canibalización (¿el cliente que NO concretó
# recompra con otro método?). La conversión baja del BNPL es rechazo de
# crédito, no fricción de checkout → no se apaga por conversión sola.
PAY_SHORT = {
    "mercadopago_adbpayment_cc": "MP tarjeta",
    "mercadopago_adbpayment_yape": "MP Yape",
    "mercadopago_adbpayment_checkout_pro": "MP Checkout Pro",
    "mercadopago_adbpayment_checkout_credits": "MP cuotas",
    "mercadopago_standard": "Mercado Pago",
    "powerpay": "PowerPay", "apurata_financing": "apurata",
    "banktransfer": "Transferencia", "openpay_stores": "OpenPay agentes",
    "checkmo": "Contra entrega/efectivo",
}
# Pistas para detectar financiamiento/BNPL aunque cambie el nombre (ej. aCuotaz).
FINANCING_HINTS = ("powerpay", "apurata", "acuotaz", "financ", "cuota", "apur", "bnpl")
FIN_CUT_CONV = 0.15     # conversión por debajo de la cual preocupa
FIN_CUT_SHARE = 0.015   # < 1.5% de la venta confirmada = aporte marginal
FIN_CUT_CONF = 300      # < 300 órdenes confirmadas 90d = volumen bajo
FIN_MIN_PEDIDOS = 100   # piso para evaluar un método

def _es_financiamiento(metodo: str) -> bool:
    m = (metodo or "").lower()
    return any(h in m for h in FINANCING_HINTS)

def _decision_pagos(eng):
    items = []
    # Perú (Chile fuera, distinto checkout/pasarelas). Ventana 90d para tener
    # base estadística en los métodos de cola larga.
    df = _q(eng, """
        SELECT payment_method,
               COUNT(*) AS pedidos,
               SUM(CASE WHEN pago_confirmado=1 THEN 1 ELSE 0 END) AS conf,
               COALESCE(SUM(CASE WHEN pago_confirmado=1 THEN venta_items ELSE 0 END),0)/1.18 AS venta_neta,
               SUM(CASE WHEN pago_confirmado=0 THEN 1 ELSE 0 END) AS fail_total,
               SUM(CASE WHEN pago_confirmado=0 AND customer_id>0 THEN 1 ELSE 0 END) AS fail_track
        FROM dbo.vw_magento_orders_pedido
        WHERE fecha >= DATEADD(day,-90, CAST(GETDATE() AS date))
          AND web_key NOT LIKE '%Chile%'
          AND payment_method IS NOT NULL AND payment_method <> ''
        GROUP BY payment_method
    """)
    if df.empty:
        return items
    tot_conf = df["conf"].sum() or 1
    tot_venta = df["venta_neta"].sum() or 1
    # Contexto: conversión de los métodos de mayor volumen (frame para los flags).
    top = df.sort_values("conf", ascending=False).head(5)
    ctx = ", ".join(f"{PAY_SHORT.get(r['payment_method'], r['payment_method'])} "
                    f"{r['conf']/r['pedidos']*100:.0f}%"
                    for _, r in top.iterrows() if r["pedidos"])
    if ctx:
        items.append(dict(marca="Conversión de pago (Perú 90d)", sev="info",
            texto=f"Pagado/iniciado por método: {ctx}. Financiamiento (BNPL) evaluado abajo."))
    for _, r in df.iterrows():
        metodo = r["payment_method"]
        if not _es_financiamiento(metodo):
            continue
        pedidos, conf = int(r["pedidos"]), int(r["conf"])
        if pedidos < FIN_MIN_PEDIDOS:
            continue
        conv = conf / pedidos if pedidos else 0
        venta = float(r["venta_neta"])
        ticket = venta / conf if conf else 0
        share = venta / tot_venta
        fail_total, fail_track = int(r["fail_total"]), int(r["fail_track"])
        guest_pct = 1 - (fail_track / fail_total) if fail_total else 0
        # Canibalización: de clientes rastreables con fallo en ESTE método,
        # ¿cuántos recompran (cualquier método) en la ventana?
        rec = _q(eng, """
            WITH fail AS (
              SELECT DISTINCT customer_id FROM dbo.vw_magento_orders_pedido
              WHERE fecha >= DATEADD(day,-90, CAST(GETDATE() AS date))
                AND payment_method = :m AND pago_confirmado=0
                AND customer_id IS NOT NULL AND customer_id>0)
            SELECT (SELECT COUNT(*) FROM fail) AS base,
                   (SELECT COUNT(DISTINCT f.customer_id) FROM fail f
                      JOIN dbo.vw_magento_orders_pedido o ON o.customer_id=f.customer_id
                      WHERE o.pago_confirmado=1
                        AND o.fecha >= DATEADD(day,-90, CAST(GETDATE() AS date))) AS recup
        """, m=metodo)
        base = int(rec["base"].iloc[0]) if not rec.empty else 0
        recup = int(rec["recup"].iloc[0]) if not rec.empty else 0
        recov_pct = (recup / base) if base else None
        nombre = PAY_SHORT.get(metodo, metodo)
        datos = (f"{conv*100:.0f}% conversión ({conf} de {pedidos} pedidos), "
                 f"S/{venta:,.0f} netos 90d, ticket S/{ticket:,.0f}, "
                 f"{share*100:.1f}% de la venta confirmada")
        canib = (f" Canibalización: {guest_pct*100:.0f}% de los fallos son guest "
                 f"(no rastreables); de los rastreables {recov_pct*100:.0f}% recompra "
                 f"con otro método." if recov_pct is not None
                 else f" {guest_pct*100:.0f}% de los fallos son guest (no rastreables).")
        cortar = conv < FIN_CUT_CONV and share < FIN_CUT_SHARE and conf < FIN_CUT_CONF
        if cortar:
            items.append(dict(marca=f"{nombre} (financiamiento)", sev="media",
                texto=f"{datos}.{canib} → CANDIDATO A RECORTAR/renegociar: "
                      f"aporte marginal y la peor conversión del checkout."))
        else:
            items.append(dict(marca=f"{nombre} (financiamiento)", sev="info",
                texto=f"{datos}.{canib} → MANTENER: ingreso incremental de ticket alto; "
                      f"no apagar por conversión baja (es rechazo de crédito, no fricción)."))
    return items

# ── Módulo: courier / lead time de entrega ─────────────────────────
# Decisión: ¿qué courier penaliza la entrega y en qué zona conviene redirigir
# volumen o renegociar? Fuente: dbo.magento_lead_times (réplica del cálculo de
# Novedades; dias_lead_limpio = días al primer package_delivered). Requiere que
# el endpoint /api/export/leadtimes esté desplegado y corrido etl_lead_times.py;
# si la tabla está vacía, el módulo lo dice en vez de fallar.
LT_MIN_ENTREGAS = 30   # piso de entregas para evaluar un courier (global)
LT_MIN_ZONA = 20       # piso de entregas courier×zona
# Penaliza si el peor courier de la zona es a la vez ≥1 día Y ≥20% más lento que
# el mejor. Combinado abs+rel: 1 día pesa distinto en Lima (~2.7d base) que en
# Selva (~5.4d), el % normaliza por el lead time propio de la zona.
LT_PENAL_DIAS = 1.0
LT_PENAL_REL = 0.20

def _decision_courier(eng):
    items = []
    chk = _q(eng, "SELECT COUNT(*) AS n FROM dbo.magento_lead_times")
    if chk.empty or int(chk["n"].iloc[0]) == 0:
        items.append(dict(marca="Lead time entrega", sev="info",
            texto="Sin data de lead times. Desplegar /api/export/leadtimes en el droplet "
                  "(git pull + restart) y correr Magento_Orders/etl_lead_times.py para activar "
                  "el análisis courier×zona."))
        return items
    # Contexto global por courier (ventana 90d de created_at).
    g = _q(eng, """
        SELECT courier,
               COUNT(*) AS pedidos,
               SUM(CASE WHEN delivery_at IS NOT NULL THEN 1 ELSE 0 END) AS entregados,
               AVG(CASE WHEN incluida_en_promedio=1 THEN CAST(dias_lead_limpio AS float) END) AS lead_prom
        FROM dbo.magento_lead_times
        WHERE created_at >= DATEADD(day,-90, CAST(GETDATE() AS date))
          AND courier IS NOT NULL AND courier <> ''
        GROUP BY courier
    """)
    g = g[g["entregados"] >= LT_MIN_ENTREGAS]
    if not g.empty:
        ctx = ", ".join(f"{r['courier']} {r['lead_prom']:.1f}d ({int(r['entregados'])} entr.)"
                        for _, r in g.sort_values("lead_prom").iterrows()
                        if pd.notna(r["lead_prom"]))
        if ctx:
            items.append(dict(marca="Lead time por courier (Perú 90d)", sev="info",
                texto=f"Días promedio a entrega: {ctx}."))
    # Courier × zona: dónde el peor courier es mucho más lento que el mejor de esa zona.
    z = _q(eng, """
        SELECT logistics_zone, courier,
               COUNT(*) AS entregas, AVG(CAST(dias_lead_limpio AS float)) AS lead_prom
        FROM dbo.magento_lead_times
        WHERE incluida_en_promedio=1
          AND created_at >= DATEADD(day,-90, CAST(GETDATE() AS date))
          AND logistics_zone IS NOT NULL AND courier IS NOT NULL AND courier <> ''
        GROUP BY logistics_zone, courier
        HAVING COUNT(*) >= :min
    """, min=LT_MIN_ZONA)
    for zona, grp in z.groupby("logistics_zone"):
        if len(grp) < 2:
            continue
        grp = grp.sort_values("lead_prom")
        mejor, peor = grp.iloc[0], grp.iloc[-1]
        delta = peor["lead_prom"] - mejor["lead_prom"]
        rel = (peor["lead_prom"] / mejor["lead_prom"] - 1) if mejor["lead_prom"] else 0
        if delta >= LT_PENAL_DIAS and rel >= LT_PENAL_REL:
            items.append(dict(marca=f"Lead time {zona}", sev="media",
                texto=f"{peor['courier']} entrega en {peor['lead_prom']:.1f}d vs "
                      f"{mejor['courier']} {mejor['lead_prom']:.1f}d ({rel*100:.0f}% más lento; "
                      f"{int(peor['entregas'])} vs {int(mejor['entregas'])} entregas) → "
                      f"redirigir volumen a {mejor['courier']} o renegociar {peor['courier']} en {zona}."))
    return items

# ── Módulo: productos empujar vs frenar (stock × demanda × venta) ──
# Palanca = el INVENTARIO (distinto de las fugas de Área 1, que son tráfico alto +
# baja conversión = ficha/stock roto). Por web, sobre el último mes cerrado:
#   · EMPUJAR  = stock sano + VENDE pero con poco tráfico → darle visibilidad/pauta
#                (demanda probada no capturada).
#   · LIQUIDAR = stock alto y CERO venta el mes pasado → capital atado, liquidar /
#                sacar de destacados.
# Grano PADRE = CodColor (item_id GA4 = CodColor stock = configurable Magento).
PROD_WEBS = ["Coliseum", "New Balance", "Caterpillar", "Converse",
             "Merrell", "Steve Madden", "Umbro"]
PROD_STOCK_MIN = 30        # piso de unidades en stock web para considerar el modelo
PROD_TRAFICO_BAJO = 200    # vistas/mes por debajo de las cuales el tráfico es "bajo"
PROD_LIQUIDAR_STOCK = 50   # stock alto para marcar liquidación
PROD_CAP = 3               # máx items por tipo en el digest (los más severos)

# Excluye servicios/no-productos (flete, bolsa, gift card) que ensucian el stock.
_PROD_EXCL = ("AND s.Codcolor NOT LIKE '%bolsa%' AND s.Descripcion NOT LIKE '%bolsa%' "
              "AND s.Descripcion NOT LIKE '%delivery%' AND s.Descripcion NOT LIKE '%flete%' "
              "AND s.Descripcion NOT LIKE '%envio%' AND s.Descripcion NOT LIKE '%despacho%' "
              "AND s.Descripcion NOT LIKE '%gift%'")

def _desc(r):
    return str(r["descripcion"] or r["CodColor"])[:40]

def _decision_productos(eng):
    items = []
    ymrow = _q(eng, "SELECT MAX(year_month) AS ym FROM ga4_monthly_items")
    if ymrow.empty or not ymrow["ym"].iloc[0]:
        return items
    ym = str(ymrow["ym"].iloc[0])              # YYYYMM (último mes cerrado)
    ym_dash = f"{ym[:4]}-{ym[4:]}"             # YYYY-MM

    # LIQUIDAR — nivel SKU (dedup), stock = snapshot all-marcas, ventas = SUMA de
    # TODAS las webs ecom (un SKU puede venderse en Coliseum aunque no en su web de
    # marca → no marcarlo liquidable por mirar una sola tienda).
    liq = _q(eng, f"""
        WITH snap AS (SELECT MAX(TRY_CONVERT(date,Fecha,105)) AS d FROM Stock_Solidez_RMH),
        stock AS (
          SELECT s.Codcolor AS CodColor, MAX(s.Descripcion) AS descripcion, MAX(s.Marca_Limpia) AS marca,
                 SUM(CASE WHEN s.stock>0 AND s.Integrada LIKE 'S%' THEN s.stock ELSE 0 END) AS und_stock
          FROM Stock_Solidez_RMH s CROSS JOIN snap
          WHERE TRY_CONVERT(date,s.Fecha,105)=snap.d
            AND s.Codcolor IS NOT NULL AND s.Codcolor<>'' {_PROD_EXCL}
          GROUP BY s.Codcolor),
        ven AS (SELECT CodColor, SUM(Cantidad) AS vendidas FROM rpt.v_ventas_base
                WHERE es_ecom=1 AND AnioMes=:ymdash AND CodColor IS NOT NULL GROUP BY CodColor)
        SELECT TOP (:cap5) st.CodColor, st.descripcion, st.marca, st.und_stock
        FROM stock st LEFT JOIN ven ve ON ve.CodColor=st.CodColor
        WHERE st.und_stock >= :liqmin AND COALESCE(ve.vendidas,0)=0
        ORDER BY st.und_stock DESC
    """, ymdash=ym_dash, liqmin=PROD_LIQUIDAR_STOCK, cap5=PROD_CAP * 5)

    # EMPUJAR — por web (la acción de visibilidad es por tienda): vende en la web W
    # pero con poco tráfico en W. Dedup por CodColor (la mejor instancia).
    empujar = []
    for web in PROD_WEBS:
        df = _q(eng, f"""
            WITH snap AS (SELECT MAX(TRY_CONVERT(date,Fecha,105)) AS d FROM Stock_Solidez_RMH),
            stock AS (
              SELECT s.Codcolor AS CodColor, MAX(s.Descripcion) AS descripcion,
                     SUM(CASE WHEN s.stock>0 AND s.Integrada LIKE 'S%' THEN s.stock ELSE 0 END) AS und_stock
              FROM Stock_Solidez_RMH s CROSS JOIN snap
              WHERE TRY_CONVERT(date,s.Fecha,105)=snap.d
                AND s.Codcolor IS NOT NULL AND s.Codcolor<>'' {_PROD_EXCL}
                AND (:allm=1 OR UPPER(s.Marca_Limpia)=UPPER(:web))
              GROUP BY s.Codcolor),
            ses AS (SELECT item_id AS CodColor, SUM(items_viewed) AS sesiones
                    FROM ga4_monthly_items WHERE property_name=:web AND year_month=:ym GROUP BY item_id),
            ven AS (SELECT CodColor, SUM(Cantidad) AS vendidas FROM rpt.v_ventas_base
                    WHERE es_ecom=1 AND Tienda_final=:web AND AnioMes=:ymdash AND CodColor IS NOT NULL GROUP BY CodColor)
            SELECT st.CodColor, st.descripcion, st.und_stock,
                   COALESCE(se.sesiones,0) AS sesiones, COALESCE(ve.vendidas,0) AS vendidas
            FROM stock st
            LEFT JOIN ses se ON se.CodColor=st.CodColor
            LEFT JOIN ven ve ON ve.CodColor=st.CodColor
            WHERE st.und_stock >= :smin AND COALESCE(ve.vendidas,0) > 0
              AND COALESCE(se.sesiones,0) < :traf
        """, web=web, ym=ym, ymdash=ym_dash, allm=(1 if web == "Coliseum" else 0),
             smin=PROD_STOCK_MIN, traf=PROD_TRAFICO_BAJO)
        for _, r in df.iterrows():
            empujar.append((web, r))

    # Empujar: dedup por descripción (mismo modelo en varios colores = 1 línea) y
    # top por demanda desperdiciada.
    empujar.sort(key=lambda x: x[1]["vendidas"], reverse=True)
    vistos = set()
    for web, r in empujar:
        clave = _desc(r).strip().upper()
        if clave in vistos:
            continue
        vistos.add(clave)
        items.append(dict(marca=f"Empujar · {web}", sev="info",
            texto=f"«{_desc(r)}»: vende {int(r['vendidas'])}u/mes con solo {int(r['sesiones'])} vistas "
                  f"y {int(r['und_stock'])}u en stock → darle visibilidad/pauta (demanda no capturada)."))
        if len(vistos) >= PROD_CAP:
            break

    # Liquidar: dedup por descripción (varios colores del mismo modelo parado).
    vistos_liq = set()
    for _, r in liq.iterrows():
        clave = _desc(r).strip().upper()
        if clave in vistos_liq:
            continue
        vistos_liq.add(clave)
        items.append(dict(marca=f"Liquidar · {str(r['marca'] or '').title()}", sev="media",
            texto=f"«{_desc(r)}»: {int(r['und_stock'])}u en stock y 0 ventas ecom el mes pasado "
                  f"→ liquidar / sacar de destacados (capital atado)."))
        if len(vistos_liq) >= PROD_CAP:
            break
    return items

def area_decisiones(eng):
    items = []
    items += _decision_pagos(eng)
    items += _decision_courier(eng)
    items += _decision_productos(eng)
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
        "decisiones": "⑤ Decisiones de negocio",
        "agencia": "⑥ Tareas para agencia (desarrollo)",
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
                    ("ux", area_ux), ("operacion", area_operacion),
                    ("decisiones", area_decisiones), ("agencia", area_agencia)]:
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
