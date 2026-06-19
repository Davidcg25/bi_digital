"""
Auditor on-site del flujo de checkout — scraper Playwright (emulacion mobile).

Llena el hueco que GA4 y Clarity dejan: GA4 prueba que el mobile se cae en
begin_checkout -> add_shipping_info (el formulario de direccion), pero ni GA4 ni
la API de Clarity ven DENTRO del checkout SPA de Magento. Este scraper entra,
camina home -> PDP -> add to cart -> checkout y mide la rubrica del formulario.

v1: solo New Balance, device mobile. Las 7 webs Solidez son Magento (Luma onepage
checkout) -> el mismo motor replica luego cambiando SITES.

Uso:
    python onsite_checkout_audit.py
    python onsite_checkout_audit.py --headed   (ver el navegador)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from datetime import datetime, date
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from sqlalchemy import create_engine, text

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "outputs"
OUT_DIR.mkdir(exist_ok=True)

# ── Config ────────────────────────────────────────────────────────────────
SITES = [
    {"property_name": "New Balance", "dominio": "newbalance.com.pe",
     "home": "https://newbalance.com.pe", "category": "https://newbalance.com.pe/zapatillas"},
]

SQL_SERVER = "localhost"
SQL_DATABASE = "Digital_Impact_Reportes"
SQL_DRIVER = "ODBC Driver 17 for SQL Server"

NAV_TIMEOUT = 45000
# rutas que NO son PDP (para descartar al buscar producto)
NON_PDP = ("catalogsearch", "customer", "checkout", "/cart", "wishlist", "cms",
           "contact", "login", "account", "sales", "review", "#", "javascript:",
           "/blog", "whatsapp", "facebook", "instagram", "tiktok", "mailto:", "tel:")


def log(level: str, msg: str) -> None:
    print(f"[{level}] {msg}")


def get_engine():
    conn = (f"DRIVER={{{SQL_DRIVER}}};SERVER={SQL_SERVER};DATABASE={SQL_DATABASE};"
            "Trusted_Connection=yes;TrustServerCertificate=yes;")
    return create_engine(f"mssql+pyodbc:///?odbc_connect={urllib.parse.quote_plus(conn)}", future=True)


def next_run_id(engine) -> int:
    with engine.begin() as c:
        return int(c.execute(text(
            "SELECT ISNULL(MAX(run_id),0)+1 FROM dbo.onsite_audit_checkout")).scalar_one())


# ── Pasos del recorrido ───────────────────────────────────────────────────
def find_pdp_candidates(page, site, limit=8) -> list[str]:
    """Lista de PDPs candidatos de la categoria (la rejilla Magento es confiable)."""
    cands: list[str] = []
    for entry in (site["category"], site["home"]):
        try:
            page.goto(entry, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
            page.wait_for_timeout(3000)
        except PWTimeout:
            continue
        grid = page.eval_on_selector_all(
            "a.product-item-link, li.product-item a.product-item-photo",
            "els => els.map(e => e.href)")
        for h in grid:
            if site["dominio"] in h and "#" not in h.split(site["dominio"])[-1][:2]:
                cands.append(h)
        # fallback heuristico si la rejilla no matchea el tema
        if not cands:
            for h in page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"):
                low = h.lower()
                if site["dominio"] not in h or any(b in low for b in NON_PDP):
                    continue
                path = urllib.parse.urlparse(h).path.strip("/")
                if path and path.count("/") <= 2 and len(path) > 12:
                    cands.append(h)
        if cands:
            break
    # dedup conservando orden
    return list(dict.fromkeys(cands))[:limit]


def try_add_to_cart(page) -> bool:
    """Selecciona una talla disponible y agrega al carrito. Best-effort."""
    # 1) seleccionar una talla disponible (Magento swatch). Tomar la primera no
    #    deshabilitada/agotada; iterar por si la 1ra esta sin stock.
    opts = page.query_selector_all("div.swatch-option:not(.disabled)")
    for opt in opts:
        try:
            cls = (opt.get_attribute("class") or "").lower()
            if "disabled" in cls or "out" in cls or "unavailable" in cls:
                continue
            opt.scroll_into_view_if_needed(timeout=3000)
            opt.click(timeout=4000)
            page.wait_for_timeout(700)
            break
        except Exception:
            continue
    # 2) boton add to cart
    for sel in ("#product-addtocart-button", "button.tocart",
                "button[title*='bolsa']", "button:has-text('Añadir a la bolsa')"):
        try:
            btn = page.query_selector(sel)
            if btn and btn.is_visible():
                btn.scroll_into_view_if_needed(timeout=3000)
                btn.click(timeout=5000)
                # exito solo si aparece confirmacion real (contador/mensaje), no por timeout
                try:
                    page.wait_for_selector(".counter-number, .minicart-wrapper .count, "
                                           ".message-success", timeout=9000)
                    return True
                except PWTimeout:
                    return False
        except Exception:
            pass
    return False


def _grab(page, selectors):
    for sel in selectors:
        try:
            el = page.query_selector(sel)
            if el and el.is_visible():
                return el
        except Exception:
            pass
    return None


def audit_checkout(page, site) -> dict:
    """En /checkout (con item en carrito), mide la rubrica del formulario."""
    r = {}
    try:
        page.goto(f"{site['home']}/checkout/", wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
        page.wait_for_timeout(4500)
    except PWTimeout:
        pass

    cur = page.url.lower()
    r["reached_checkout"] = int("checkout" in cur and "cart" not in cur)
    r["login_wall"] = int("login" in cur or "account/login" in cur)

    # guest checkout = hay input de email en el paso de envio sin exigir password
    email = page.query_selector("input[name='username'], input#customer-email, input[type='email']")
    pwd_required = page.query_selector("input[type='password']")
    r["guest_checkout"] = int(bool(email) and not r["login_wall"])
    if pwd_required and not email:
        r["login_wall"] = 1

    # campos del formulario de direccion. Tema custom -> escaneo amplio de todos
    # los inputs/selects visibles, descartando ruido (busqueda, newsletter, etc.).
    SKIP = ("search", "newsletter", "qty", "coupon", "captcha")
    raw_inputs = [i for i in page.query_selector_all("input") if i.is_visible()
                  and (i.get_attribute("type") or "text") not in ("hidden", "checkbox", "radio", "submit")]
    visible_inputs = []
    for i in raw_inputs:
        nm = ((i.get_attribute("name") or "") + (i.get_attribute("id") or "")).lower()
        if nm and not any(s in nm for s in SKIP):
            visible_inputs.append(i)
    selects = [s for s in page.query_selector_all("select") if s.is_visible()]
    r["form_fields"] = len(visible_inputs) + len(selects)

    req = sum(1 for i in visible_inputs if i.get_attribute("aria-required") == "true"
              or "required" in (i.get_attribute("class") or ""))
    with_ac = sum(1 for i in visible_inputs if i.get_attribute("autocomplete"))
    r["form_required"] = req
    r["autocomplete_pct"] = round(100.0 * with_ac / len(visible_inputs), 1) if visible_inputs else None

    sel_names = " ".join((s.get_attribute("name") or "") + (s.get_attribute("id") or "")
                         for s in selects).lower()
    r["cascading_selects"] = int(sum(k in sel_names for k in
                                 ("region", "province", "district", "depto", "provincia", "distrito")) >= 2)

    body = (page.inner_text("body") or "").lower()
    # costo ANTES de completar el cascada -> sirve para detectar el "gating"
    r["_cost_shown_pre"] = int(("envío" in body or "envio" in body) and re.search(r"s/\s?\d", body) is not None)
    r["free_ship_threshold"] = int(("envío gratis" in body or "envio gratis" in body) and "desde" in body)
    return r


SHIP_COST_RE = re.compile(r"S/\s?\d[\d.,]*", re.I)
ETA_RE = re.compile(r"[^.\n]*\b(?:d[ií]a|h[áa]bil|entrega|llega)[^.\n]*", re.I)


def fill_address(page) -> dict:
    """Llena el form de envio + el triple cascada (region->province->district) para
    REVELAR el costo/ETA, que NB recien muestra al final. Best-effort, no compra nada."""
    out = {"address_filled": 0, "shipping_cost_text": None, "shipping_eta_text": None,
           "payment_methods": None}
    dummy = {"username": "qa.audit@example.com", "firstname": "QA", "lastname": "Audit",
             "street[0]": "Av Test 123", "custom_attributes[number]": "123",
             "custom_attributes[department]": "Ref", "custom_attributes[document]": "12345678",
             "telephone": "987654321"}
    for name, val in dummy.items():
        try:
            el = page.query_selector(f"input[name='{name}']")
            if el and el.is_visible():
                el.fill(val)
        except Exception:
            pass
    # triple cascada: cada select gatilla un AJAX que puebla el siguiente nivel
    cascade = ["region_id", "custom_attributes[province_id]", "custom_attributes[district_id]"]
    for idx, name in enumerate(cascade):
        try:
            sel = page.query_selector(f"select[name='{name}']")
            if not sel:
                continue
            opts = sel.query_selector_all("option")
            chosen = None
            if idx == 0:  # region: preferir Lima (mayoria del trafico)
                for o in opts:
                    if "lima" in (o.inner_text() or "").lower() and o.get_attribute("value"):
                        chosen = o.get_attribute("value"); break
            if not chosen and len(opts) > 1:
                chosen = opts[1].get_attribute("value")
            if chosen:
                sel.select_option(chosen)
                page.wait_for_timeout(2800)
        except Exception:
            pass
    out["address_filled"] = 1
    page.wait_for_timeout(2500)  # esperar carga de metodos de despacho
    block = _grab(page, [".table-checkout-shipping-method", "#checkout-shipping-method-load",
                         "[class*=shipping-method]", ".checkout-shipping-method",
                         "form#co-shipping-method-form"])
    try:
        txt = block.inner_text() if block else (page.inner_text("body") or "")
    except Exception:
        txt = ""
    m = SHIP_COST_RE.search(txt)
    if m:
        out["shipping_cost_text"] = m.group(0).strip()[:120]
    e = ETA_RE.search(txt)
    if e:
        out["shipping_eta_text"] = re.sub(r"\s+", " ", e.group(0)).strip()[:120]
    pays = [p for p in page.query_selector_all(".payment-method, [class*=payment-method] input[type=radio]")
            if p.is_visible()]
    out["payment_methods"] = len(pays) or None
    return out


def compute_rubric(row: dict) -> tuple[int, str]:
    """Score 0-100 + flags, ponderado segun el impacto probado por GA4."""
    score = 0; flags = []
    if row.get("guest_checkout"): score += 30
    else: flags.append("sin guest checkout / posible muro de login (causa #1 del leak mobile)")
    if row.get("login_wall"): flags.append("exige login antes de la direccion")
    ff = row.get("form_fields") or 0
    if ff and ff <= 10: score += 15
    elif ff > 14: flags.append(f"formulario largo ({ff} campos)")
    else: score += 8
    ac = row.get("autocomplete_pct") or 0
    if ac >= 70: score += 15
    elif ac >= 30: score += 8
    else: flags.append(f"autocomplete pobre ({ac}% de campos)")
    if row.get("shipping_cost_shown"): score += 15
    else: flags.append("costo de envio no visible en el paso de envio")
    if row.get("cost_gated_behind_cascade"):
        flags.append("costo de envio OCULTO hasta completar el triple cascada (esfuerzo antes que precio)")
    if row.get("shipping_eta_shown"):
        score += 10
        if row.get("shipping_eta_text"):
            flags.append(f"ETA mostrado: '{row['shipping_eta_text']}'")
    else:
        flags.append("sin fecha/plazo de entrega visible")
    if row.get("free_ship_threshold"): score += 5
    if (row.get("console_errors") or 0) == 0: score += 10
    else: flags.append(f"{row['console_errors']} errores JS en consola durante el flujo")
    return score, " | ".join(flags) if flags else "sin hallazgos criticos"


def run_site(pw, site, device_name, run_id) -> dict:
    row = {"run_id": run_id, "property_name": site["property_name"], "dominio": site["dominio"],
           "device": "mobile", "fecha": date.today(),
           "reached_pdp": 0, "reached_cart": 0, "reached_checkout": 0,
           "console_errors": 0, "status": "error", "error_message": None}
    site_dir = OUT_DIR / site["property_name"].replace(" ", "_")
    site_dir.mkdir(parents=True, exist_ok=True)
    iphone = pw.devices["iPhone 13"]
    browser = pw.chromium.launch()
    ctx = browser.new_context(**iphone, locale="es-PE")
    page = ctx.new_page()

    # captura de errores JS con DETALLE (tipo, texto, ubicacion, etapa del viaje)
    errs = []
    stage = ["start"]

    def on_err(kind, txtval, loc):
        errs.append({"type": kind, "text": (txtval or "")[:900],
                     "location": (loc or "")[:550], "stage": stage[0]})
    page.on("pageerror", lambda e: on_err("pageerror", str(e), ""))

    def on_console(m):
        if m.type == "error":
            l = m.location if isinstance(m.location, dict) else {}
            loc = f"{l.get('url','')}:{l.get('lineNumber','')}" if l else ""
            on_err("console", m.text, loc)
    page.on("console", on_console)

    # capturas paso a paso del viaje
    cnt = [0]

    def snap(name):
        cnt[0] += 1
        try:
            page.screenshot(path=str(site_dir / f"{cnt[0]:02d}_{name}.png"), full_page=True)
        except Exception:
            pass

    try:
        stage[0] = "pdp"
        for pdp in find_pdp_candidates(page, site):  # el 1ro a veces es ropa sin tallas
            try:
                page.goto(pdp, wait_until="domcontentloaded", timeout=NAV_TIMEOUT)
                page.wait_for_timeout(2500)
            except PWTimeout:
                continue
            row["reached_pdp"] = 1
            row["pdp_url"] = pdp
            snap("pdp")
            stage[0] = "add_to_cart"
            if try_add_to_cart(page):
                row["reached_cart"] = 1
                snap("cart_added")
                break

        stage[0] = "checkout"
        row.update(audit_checkout(page, site))
        snap("checkout_form_vacio")

        if row.get("reached_checkout"):
            stage[0] = "fill_address"
            row.update(fill_address(page))
            snap("metodos_despacho")  # aqui recien aparece costo/ETA

        # flags de envio derivados (post-fill)
        row["shipping_cost_shown"] = int(bool(row.get("shipping_cost_text")) or row.get("_cost_shown_pre", 0))
        row["shipping_eta_shown"] = int(bool(row.get("shipping_eta_text")))
        row["cost_gated_behind_cascade"] = int(bool(row.get("shipping_cost_text")) and not row.get("_cost_shown_pre", 0))

        try:
            row["checkout_lcp_ms"] = page.evaluate(
                "() => { const e = performance.getEntriesByName('first-contentful-paint')[0];"
                " return e ? Math.round(e.startTime) : null; }")
        except Exception:
            row["checkout_lcp_ms"] = None
        row["console_errors"] = len(errs)
        row["shots"] = cnt[0]
        row["status"] = "ok" if row.get("reached_checkout") else "partial"
    except Exception as e:
        row["error_message"] = str(e)[:1900]
        log("ERROR", f"{site['property_name']} | {e}")
    finally:
        ctx.close(); browser.close()

    row["_errs"] = errs
    row["rubric_score"], row["flags"] = compute_rubric(row)
    return row


COLS = ["run_id","property_name","dominio","device","fecha","pdp_url","reached_pdp","reached_cart",
        "reached_checkout","guest_checkout","login_wall","form_fields","form_required",
        "autocomplete_pct","cascading_selects","shipping_cost_shown","shipping_eta_shown",
        "free_ship_threshold","payment_methods","console_errors","checkout_lcp_ms","rubric_score",
        "flags","status","error_message","shipping_cost_text","shipping_eta_text","address_filled",
        "cost_gated_behind_cascade","shots"]


def save_sql(engine, row: dict) -> None:
    cols = [c for c in COLS if c in row]
    placeholders = ", ".join(f":{c}" for c in cols)
    sql = text(f"INSERT INTO dbo.onsite_audit_checkout ({', '.join(cols)}) VALUES ({placeholders})")
    with engine.begin() as c:
        c.execute(sql, {k: row.get(k) for k in cols})


def save_console(engine, row: dict) -> None:
    """Persiste el detalle de cada error JS capturado durante el viaje."""
    errs = row.get("_errs") or []
    if not errs:
        return
    sql = text("INSERT INTO dbo.onsite_audit_console "
               "(run_id, property_name, device, seq, err_type, err_text, err_location, stage) "
               "VALUES (:run_id, :property_name, :device, :seq, :err_type, :err_text, :err_location, :stage)")
    with engine.begin() as c:
        for i, e in enumerate(errs, 1):
            c.execute(sql, {"run_id": row["run_id"], "property_name": row["property_name"],
                            "device": row["device"], "seq": i, "err_type": e.get("type"),
                            "err_text": e.get("text"), "err_location": e.get("location"),
                            "stage": e.get("stage")})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--headed", action="store_true")
    ap.add_argument("--no-sql", action="store_true")
    args = ap.parse_args()

    engine = None if args.no_sql else get_engine()
    run_id = next_run_id(engine) if engine else 1
    log("START", f"Onsite checkout audit | run_id={run_id}")
    results = []
    with sync_playwright() as pw:
        for site in SITES:
            log("SITE", site["property_name"])
            row = run_site(pw, site, "mobile", run_id)
            results.append(row)
            if engine:
                save_sql(engine, row)
                save_console(engine, row)
            log("RESULT", json.dumps({k: row.get(k) for k in
                ("reached_pdp","reached_cart","reached_checkout","guest_checkout","login_wall",
                 "form_fields","autocomplete_pct","cascading_selects","shipping_cost_text",
                 "shipping_eta_text","cost_gated_behind_cascade","console_errors","shots",
                 "rubric_score","status")}, ensure_ascii=False))
            log("FLAGS", row["flags"])
    (OUT_DIR / f"audit_run_{run_id}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log("DONE", f"run_id={run_id}")


if __name__ == "__main__":
    main()
