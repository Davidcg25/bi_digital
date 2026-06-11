"""
diag_lengths.py — Diagnóstico del truncamiento del backfill (one-shot).

Baja la ventana que falla del API y mide, POR CAMPO, la longitud máxima
observada (chars Python y bytes UTF-16, que es lo que cuenta nvarchar).
No escribe nada en SQL. Imprime los campos que exceden el ancho del #stage.

Uso:
  venv\Scripts\python.exe diag_lengths.py --since 2026-04-20 --until 2026-05-26
"""

from __future__ import annotations

import argparse
from datetime import datetime

from etl_magento_orders import (
    API_TOKEN, COLS, _MAXLEN, build_session, fetch_page, logger,
)

# Anchos reales del #stage (chars) para comparar
STAGE_WIDTH = dict(_MAXLEN)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--since", required=True)
    p.add_argument("--until", required=True)
    p.add_argument("--page-size", type=int, default=1000)
    args = p.parse_args()

    if not API_TOKEN:
        logger.critical("EXPORT_API_TOKEN no configurado (.env)")
        return 1

    since = datetime.strptime(args.since, "%Y-%m-%d")
    until = datetime.strptime(args.until, "%Y-%m-%d")
    session = build_session()

    # por campo: (max_chars, max_utf16_units, id de la fila culpable, muestra)
    stats: dict[str, tuple[int, int, str, str]] = {c: (0, 0, "", "") for c in COLS}
    pages = rows_total = 0
    page = 1
    while True:
        data = fetch_page(session, since, until, page, args.page_size)
        rows = data.get("rows", [])
        pages += 1
        rows_total += len(rows)
        for raw in rows:
            for c in COLS:
                v = raw.get(c)
                if not isinstance(v, str):
                    continue
                chars = len(v)
                if chars > stats[c][0]:
                    u16 = len(v.encode("utf-16-le")) // 2
                    stats[c] = (chars, u16, str(raw.get("id")), v[:80])
        logger.info("Página %d: %d filas (acum %d)", page, len(rows), rows_total)
        if not data.get("has_more"):
            break
        page += 1

    print(f"\n=== {rows_total} filas en {pages} páginas | ventana {args.since} -> {args.until} ===")
    print(f"{'campo':<38}{'max_chars':>10}{'max_u16':>9}{'ancho_stage':>12}  estado")
    for c in COLS:
        chars, u16, rid, sample = stats[c]
        if chars == 0:
            continue
        width = STAGE_WIDTH.get(c)
        flag = ""
        if width is not None and u16 > width:
            flag = f"  *** EXCEDE (id={rid}) muestra: {sample!r}"
        elif width is not None and chars > width:
            flag = f"  *** EXCEDE chars (id={rid})"
        print(f"{c:<38}{chars:>10}{u16:>9}{str(width or '-'):>12}{flag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
