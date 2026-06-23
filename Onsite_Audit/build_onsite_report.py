"""
Genera el reporte HTML de hallazgos on-site para la AGENCIA (de cara a Cybers).

Agrega los errores JS de los ultimos N runs (robustez ante flakiness del scraper),
categoriza por tipo, y toma la mejor foto de la rubrica de checkout por web.
Salida: outputs/reporte_onsite_<fecha>.html

Uso:
    python build_onsite_report.py            (ultimos 3 runs)
    python build_onsite_report.py --runs 5   (ultimos 5 runs)
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import urllib.parse
from datetime import date
from pathlib import Path

from PIL import Image
from sqlalchemy import create_engine, text

# orden y etiqueta legible de las capturas del recorrido
SHOT_LABELS = {
    "pdp": "Ficha de producto",
    "cart_added": "Agregado al carrito",
    "checkout_form_vacio": "Formulario de checkout",
    "metodos_despacho": "Dirección + métodos de envío",
}


def embed_shots(folder: Path, max_w: int = 360) -> str:
    """Lee las capturas de la web, las reduce a thumbnail JPEG y las embebe base64
    (HTML autocontenido). Clicables para abrir a tamaño completo en pestaña nueva."""
    if not folder.is_dir():
        return ""
    pngs = sorted(folder.glob("*.png"))
    figs = []
    for p in pngs:
        try:
            img = Image.open(p).convert("RGB")
            if img.width > max_w:
                img = img.resize((max_w, int(img.height * max_w / img.width)), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=72, optimize=True)
            b64 = base64.b64encode(buf.getvalue()).decode()
        except Exception:
            continue
        key = p.stem.split("_", 1)[-1] if "_" in p.stem else p.stem
        label = SHOT_LABELS.get(key, key.replace("_", " ").title())
        figs.append(
            f'<figure><img src="data:image/jpeg;base64,{b64}" loading="lazy" '
            f'onclick="zoom(this.src)"><figcaption>{html.escape(label)}</figcaption></figure>')
    if not figs:
        return ""
    return f'<div class="gallery">{"".join(figs)}</div>'

BASE_DIR = Path(__file__).resolve().parent
OUT_DIR = BASE_DIR / "outputs"

ERR_CASE = """
  CASE
    WHEN err_text LIKE '%Refused to apply style%' THEN 'CSS rechazado (MIME nosniff) — rompe el render'
    WHEN err_text LIKE '%is not defined%' OR err_text LIKE '%is not a function%'
         OR err_text LIKE '%is not valid JSON%' OR err_text LIKE '%Uncaught%'
         OR err_text LIKE '%Unhandled Promise Rejection%' OR err_text LIKE '%[[]object Object]%'
         THEN 'Excepcion JS (script roto: RequireJS/var no definida/JSON invalido)'
    WHEN err_text LIKE '%500%' THEN 'HTTP 500 (recurso/endpoint backend caido)'
    WHEN err_text LIKE '%429%' THEN 'HTTP 429 (rate-limit / throttling)'
    WHEN err_text LIKE '%404%' THEN 'HTTP 404 (recurso no encontrado: static desincronizado)'
    WHEN err_text LIKE '%Fetch API cannot load%' OR err_text LIKE '%Connecting to %'
         OR err_text LIKE '%Failed to fetch%' THEN 'Fetch/tracking falla (CORS / endpoint de eventos)'
    WHEN err_text LIKE '%Loading the image%' THEN 'Imagen no carga'
    WHEN err_text LIKE '%Loading the stylesheet%' THEN 'Stylesheet/font no carga'
    ELSE 'Otro' END
"""


def get_engine():
    conn = ("DRIVER={ODBC Driver 17 for SQL Server};SERVER=localhost;"
            "DATABASE=Digital_Impact_Reportes;Trusted_Connection=yes;TrustServerCertificate=yes;")
    return create_engine(f"mssql+pyodbc:///?odbc_connect={urllib.parse.quote_plus(conn)}", future=True)


def fetch(engine, n_runs: int):
    with engine.begin() as c:
        max_run = c.execute(text("SELECT MAX(run_id) FROM dbo.onsite_audit_checkout")).scalar() or 0
        min_run = max(1, max_run - n_runs + 1)

        # mejor foto de rubrica por web (prioriza llegar a checkout, luego score)
        rubric = c.execute(text(f"""
            WITH ranked AS (
              SELECT *, ROW_NUMBER() OVER (PARTITION BY property_name
                ORDER BY reached_checkout DESC, rubric_score DESC, run_id DESC) rn
              FROM dbo.onsite_audit_checkout WHERE run_id BETWEEN {min_run} AND {max_run})
            SELECT property_name, reached_pdp, reached_cart, reached_checkout, guest_checkout,
                   form_fields, autocomplete_pct, cascading_selects, shipping_cost_text,
                   shipping_eta_text, cost_gated_behind_cascade, rubric_score, status, flags
            FROM ranked WHERE rn=1 ORDER BY property_name
        """)).mappings().all()

        # errores JS agregados por web + categoria (union de runs, dedup por texto)
        errs = c.execute(text(f"""
            WITH dedup AS (
              SELECT DISTINCT property_name, {ERR_CASE} AS categoria, err_text,
                     err_location, stage
              FROM dbo.onsite_audit_console WHERE run_id BETWEEN {min_run} AND {max_run})
            SELECT property_name, categoria, COUNT(*) AS n,
                   MAX(ISNULL(err_location, LEFT(err_text,60))) AS ejemplo
            FROM dedup GROUP BY property_name, categoria
            ORDER BY property_name, n DESC
        """)).mappings().all()
    return min_run, max_run, rubric, errs


def esc(v):
    return html.escape(str(v)) if v is not None else ""


def build_html(min_run, max_run, rubric, errs) -> str:
    errs_by_web: dict[str, list] = {}
    for e in errs:
        errs_by_web.setdefault(e["property_name"], []).append(e)

    cards = []
    for r in rubric:
        web = r["property_name"]
        folder = web.replace(" ", "_")
        gallery = embed_shots(OUT_DIR / folder)
        st = r["status"]
        st_color = {"ok": "#1a7f37", "partial": "#9a6700", "error": "#cf222e"}.get(st, "#57606a")
        ac = r["autocomplete_pct"]
        ac_txt = f"{ac}%" if ac is not None else "—"
        rows = [
            ("Llegó a checkout", "Sí" if r["reached_checkout"] else "No"),
            ("Guest checkout (sin login)", "Sí" if r["guest_checkout"] else "No / no detectado"),
            ("Campos del formulario", esc(r["form_fields"])),
            ("Autocomplete", ac_txt + ("  ⚠ sin autofill" if ac == 0 else "")),
            ("Cascada depto/prov/distrito", "Sí" if r["cascading_selects"] else "—"),
            ("Costo envío capturado", esc(r["shipping_cost_text"]) or "—"),
            ("ETA mostrado", esc(r["shipping_eta_text"]) or "—"),
            ("Costo oculto tras cascada", "Sí ⚠" if r["cost_gated_behind_cascade"] else "—"),
        ]
        rubric_rows = "".join(
            f"<tr><td>{esc(k)}</td><td><b>{esc(v)}</b></td></tr>" for k, v in rows)

        err_rows = "".join(
            f"<tr><td>{esc(e['categoria'])}</td><td style='text-align:center'>{esc(e['n'])}</td>"
            f"<td class='loc'>{esc(e['ejemplo'])}</td></tr>"
            for e in errs_by_web.get(web, [])) or \
            "<tr><td colspan='3' style='color:#57606a'>Sin errores capturados</td></tr>"

        cards.append(f"""
        <div class="card">
          <div class="card-h">
            <h2>{esc(web)}</h2>
            <span class="badge" style="background:{st_color}">{esc(st)} · score {esc(r['rubric_score'])}/100</span>
          </div>
          <p class="flags">{esc(r['flags'])}</p>
          <div class="cols">
            <div>
              <h3>Rúbrica de checkout (mobile)</h3>
              <table class="t">{rubric_rows}</table>
            </div>
            <div>
              <h3>Errores JS (categorizados)</h3>
              <table class="t"><tr><th>Categoría</th><th>#</th><th>Ejemplo</th></tr>{err_rows}</table>
            </div>
          </div>
          <h3 style="margin-top:16px">Recorrido capturado (clic para ampliar)</h3>
          {gallery or '<p class="hint">Sin capturas para este run.</p>'}
        </div>""")

    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<title>Hallazgos on-site — Solidez · {date.today()}</title>
<style>
  body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;background:#f6f8fa;color:#1f2328}}
  header{{background:#0d1117;color:#fff;padding:24px 32px}}
  header h1{{margin:0 0 4px}} header p{{margin:0;color:#9da7b1;font-size:14px}}
  main{{max-width:1080px;margin:0 auto;padding:24px}}
  .summary{{background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:16px 20px;margin-bottom:20px}}
  .summary li{{margin:6px 0}}
  .card{{background:#fff;border:1px solid #d0d7de;border-radius:10px;padding:18px 20px;margin-bottom:18px}}
  .card-h{{display:flex;align-items:center;justify-content:space-between}}
  .card-h h2{{margin:0;font-size:20px}}
  .badge{{color:#fff;padding:4px 10px;border-radius:20px;font-size:12px;font-weight:600}}
  .flags{{color:#9a6700;font-size:13px;margin:8px 0 14px}}
  .cols{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}
  h3{{font-size:13px;text-transform:uppercase;color:#57606a;letter-spacing:.04em;margin:0 0 8px}}
  table.t{{width:100%;border-collapse:collapse;font-size:13px}}
  table.t td,table.t th{{border-bottom:1px solid #eaeef2;padding:5px 8px;text-align:left;vertical-align:top}}
  table.t th{{color:#57606a;font-weight:600}}
  .loc{{font-family:ui-monospace,monospace;font-size:11px;color:#57606a;word-break:break-all}}
  .hint{{font-size:12px;color:#57606a;margin:8px 0 0}}
  code{{background:#eff1f3;padding:1px 5px;border-radius:4px}}
  .gallery{{display:flex;gap:12px;overflow-x:auto;padding:6px 2px}}
  .gallery figure{{margin:0;flex:0 0 auto;text-align:center}}
  .gallery img{{height:300px;width:auto;border:1px solid #d0d7de;border-radius:8px;
    box-shadow:0 1px 4px rgba(0,0,0,.08);cursor:zoom-in}}
  .gallery figcaption{{font-size:11px;color:#57606a;margin-top:4px}}
  @media(max-width:760px){{.cols{{grid-template-columns:1fr}}}}
</style></head><body>
<header>
  <h1>Hallazgos on-site — Tiendas Solidez (mobile)</h1>
  <p>Auditoría automatizada del flujo de compra · runs {min_run}–{max_run} · generado {date.today()}</p>
</header>
<main>
  <div class="summary">
    <b>Sistémico (mismo theme Magento → un fix escala a varias webs):</b>
    <ul>
      <li><b>0% de autocomplete</b> en el formulario de dirección de todas las webs → el autofill del navegador no funciona; mobile sufre.</li>
      <li><b>404 en <code>/static/version…</code></b> en varias webs → static content de Magento desincronizado (revisar deploy/<code>setup:static-content:deploy</code>).</li>
      <li><b>Converse</b>: tormenta de <b>CSS rechazado (MIME nosniff)</b> + HTTP 500 → rompe el add-to-cart mobile. Theme outlier (base AU reconstruida = deuda técnica).</li>
      <li><b>New Balance</b>: HTTP 500 en <code>purchase-event-receiver</code> → endpoint de atribución caído (puede degradar data de compra).</li>
      <li>Costo y ETA de envío <b>ocultos hasta completar el triple cascada</b>; ETA hasta <b>10 días hábiles</b> (disuasivo).</li>
    </ul>
  </div>
  {''.join(cards)}
</main>
<div id="lb" onclick="this.style.display='none'"><img id="lbimg"></div>
<style>
  #lb{{display:none;position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:99;
    align-items:center;justify-content:center;cursor:zoom-out}}
  #lb img{{max-height:94vh;max-width:94vw;border-radius:8px}}
  #lb[style*="flex"]{{display:flex}}
</style>
<script>
  function zoom(src){{var lb=document.getElementById('lb');
    document.getElementById('lbimg').src=src;lb.style.display='flex';}}
</script>
</body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()
    engine = get_engine()
    min_run, max_run, rubric, errs = fetch(engine, args.runs)
    out = build_html(min_run, max_run, rubric, errs)
    path = OUT_DIR / f"reporte_onsite_{date.today():%Y%m%d}.html"
    path.write_text(out, encoding="utf-8")
    print(f"[OK] Reporte generado: {path}")
    print(f"[INFO] runs {min_run}-{max_run} | {len(rubric)} webs | {len(errs)} filas de error")


if __name__ == "__main__":
    main()
