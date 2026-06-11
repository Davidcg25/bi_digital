# Converse DTC PE — GPM Digital Insights, sustento

Carpeta del digest mensual que pide la marca (template `GPM_DTC_PER_YYYYMMDD.xlsx`,
hoja **Digital Insights**, respuestas telegráficas en inglés). El sustento numérico
de cada respuesta sale de `30_gpm_sustento.sql` (cambiar `@mes` y ejecutar contra
`Digital_Impact_Reportes`). Este es el mismo formato de "respuesta + número que la
respalda" que usará el digest por web.

## Fuentes por pregunta

| # | Pregunta | Fuente / definición |
|---|----------|---------------------|
| Q1/Q2/Q7 | Worked well / didn't / marketing moments | Cualitativas (David + marketing) |
| Q3 | Market trends | Búsquedas in-site + criterio (pendiente: reporte search terms GA4) |
| Q4 | Revenue vs LY / MoM | RMH neto: `rpt.v_ventas_base`, `Tienda_final='Converse'`, `es_ecom=1`. **Se reporta sobre RMH, no GA4**; la tabla Magento quedó congelada feb-2026 |
| Q5 | Traffic vs LY / MoM | `ga4_monthly_core`, `property_name='Converse'`; CR = compras/sesiones |
| Q6 | Units / AUR | Misma base RMH que Q4 |
| Q8 | Core | `Coleccion LIKE '%CORE%'` sobre la base RMH |
| Q9 | Elevation | Platform: `Descripcion LIKE '%PLAT%'` |
| Q10 | Full price vs discounted | dscto = `1 − Precio/FullPrecio` redondeado MROUND a 0.05; `FullPrecio>0`; sobreprecios (bucket negativo) → full price; "full + <20%" **incluye** el bucket 20% |
| ctx | Stock web (contexto Q8/Q9) | `Stock_Solidez_RMH`, `Integrada LIKE 'S%'`, **último snapshot del mes** (jamás SUM: ~20 snapshots diarios) |

## Validación contra el GPM de abril-2026 (`GPM_DTC_PER_20260508.xlsx`)

Corrido el 2026-06-11 con `@mes='2026-04'`:

| Respuesta del xlsx | Query reproduce |
|--------------------|-----------------|
| Q4 "Flat vs LY. -5 points GM. -6%" | +0.6% vs LY (320,559 vs 318,776), GM −5.1 pts (18.4% vs 23.5%), −6.3% MoM ✓ |
| Q5 "Decrease in 23% traffic" | −23.8% sesiones (155,475 vs 203,928) ✓ (CR +31% vs el ~34% reportado — extracción de otra fecha) |
| Q6 "10% increase in units, -8% AUR" | +9.1% unidades (1,782 vs 1,634), AUR −7.8% (179.9 vs 195.1) ✓ |
| Q8 "core -17% vs LY, GM 10 points healthier" | −18.1% revenue core, GM core +9.6 pts (37.4% vs 27.8%) ✓ |
| Q9 "elevation -6% vs LY" | −5.0% vs LY; MoM +9.1% revenue / +12% unidades (reportó 11%) ✓ |
| Q10 "A) 80/20 B) 51/49" | Exacto con base **TotalNeto** (la regla de abril). ⚠️ Desde **may-2026 la base oficial es UNIDADES** (con unidades abril daría A) 85/15, B) 43/57) — el SQL trae ambas variantes implícitas vía el detalle por bucket |

## Convención de la carpeta

- `GPM_DTC_PER_YYYYMMDD.xlsx` — entregable enviado (fecha = día de envío; reporta el mes cerrado anterior).
- `30_gpm_sustento.sql` — queries reproducibles del mes (numeración sigue a `Diagnostico/*.sql`).
