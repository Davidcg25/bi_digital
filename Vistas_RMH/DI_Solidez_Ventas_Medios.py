import pyodbc
import pandas as pd
import gspread
import time
from oauth2client.service_account import ServiceAccountCredentials
from gspread_formatting import (format_cell_range,CellFormat,NumberFormat)
from gspread.utils import rowcol_to_a1
import datetime

# === CONFIGURACIONES ===
nombre_vista = "Vista_Ventas_Solidez_Resumen"
ruta_credenciales = r"D:\Proyectos\4_BI_Ecom\Vistas_RMH\di-auth-gsheets.json"
nombre_google_sheet = "Solidez-RMH-Ventas-Medios"

hoja_detalle = "Detalle Diario"
hoja_resumen_mes = "Resumen_Mes"
hoja_resumen_anio = "Resumen_Año"

# === CONEXIÓN A SQL SERVER ===
conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost;"
    "DATABASE=Digital_Impact_Reportes;"
    "Trusted_Connection=yes;"
)

query = f"SELECT * FROM {nombre_vista}"
df = pd.read_sql(query, conn)
conn.close()

# === NORMALIZACIÓN BÁSICA ===
if 'Fecha' in df.columns:
    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')

# === ORDENAR POR MES Y DÍA ===
if 'Mes' in df.columns and 'Día' in df.columns:
    df = df.sort_values(by=['Mes', 'Día'], ascending=[True, True])

# === CONVERSIÓN SOLO DE COLUMNAS DE TIPO date ===
for col in df.select_dtypes(include=['datetime64[ns]', 'datetime64[ns, UTC]', 'object']):
    if df[col].apply(lambda x: isinstance(x, (datetime.date, datetime.datetime))).any():
        df[col] = df[col].astype(str)

# --- Alias rápidos ---
if 'Cantidad_Total' in df.columns:
    df.rename(columns={'Cantidad_Total': 'Cantidad'}, inplace=True)

if 'Contribucion_Total' in df.columns:
    df.rename(columns={'Contribucion_Total': 'Contribución'}, inplace=True)

def aplicar_formatos_detalle(worksheet, df_subir):
    moneda_format = CellFormat(
        numberFormat=NumberFormat(type='NUMBER', pattern='"S/" #,##0.00')
    )
    porcentaje_format = CellFormat(
        numberFormat=NumberFormat(type='PERCENT', pattern='0.00%')
    )
    entero_format = CellFormat(
        numberFormat=NumberFormat(type='NUMBER', pattern='#,##0')
    )

    headers = df_subir.columns.tolist()

    for idx, col in enumerate(headers, start=1):
        rango = f"{rowcol_to_a1(2, idx)}:{rowcol_to_a1(len(df_subir)+1, idx)}"

        if col in ['TotalNeto_Total', 'TotalNeto']:
            format_cell_range(worksheet, rango, moneda_format)
        elif col in ['Margen']:
            format_cell_range(worksheet, rango, porcentaje_format)
        elif col in ['Cantidad', 'Cantidad_Total', 'Ordenes']:
            format_cell_range(worksheet, rango, entero_format)

# =========================
# FUNCIONES DE RESUMEN
# =========================
def preparar_base_resumen(df_base: pd.DataFrame) -> pd.DataFrame:
    df_res = df_base.copy()

    # Validaciones mínimas
    required_cols = ['Fecha', 'Canal', 'Tienda_ecom', 'Marca_Limpia', 'TotalNeto_Total', 'Contribución', 'Cantidad']
    faltantes = [c for c in required_cols if c not in df_res.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas requeridas para resúmenes: {faltantes}")

    df_res['Fecha'] = pd.to_datetime(df_res['Fecha'], errors='coerce')
    df_res = df_res[df_res['Fecha'].notna()].copy()

    df_res['Día'] = df_res['Fecha'].dt.day
    df_res['Mes_Num'] = df_res['Fecha'].dt.month
    df_res['Mes'] = df_res['Fecha'].dt.strftime('%Y-%m')

    # numéricos
    for col in ['TotalNeto_Total', 'Contribución', 'Cantidad']:
        df_res[col] = pd.to_numeric(df_res[col], errors='coerce').fillna(0)

    return df_res


def generar_resumen_mes(df_base: pd.DataFrame) -> pd.DataFrame:
    df_res = preparar_base_resumen(df_base)

    # limitar al mes más reciente presente en la data
    ultimo_periodo = df_res['Mes'].max()
    df_mes = df_res[df_res['Mes'] == ultimo_periodo].copy()

    group_cols = ['Canal', 'Tienda_ecom', 'Marca_Limpia', 'Día']
    agg = (
        df_mes.groupby(group_cols, dropna=False, as_index=False)
        .agg({
            'TotalNeto_Total': 'sum',
            'Contribución': 'sum',
            'Cantidad': 'sum'
        })
    )

    # margen ponderado
    agg['Margen'] = agg.apply(
        lambda x: (x['Contribución'] / x['TotalNeto_Total']) if x['TotalNeto_Total'] != 0 else 0,
        axis=1
    )

    dims = ['Canal', 'Tienda_ecom', 'Marca_Limpia']

    # pivots
    pvt_total = agg.pivot_table(
        index=dims, columns='Día', values='TotalNeto_Total', aggfunc='sum', fill_value=0
    )
    pvt_margen = agg.pivot_table(
        index=dims, columns='Día', values='Margen', aggfunc='first', fill_value=0
    )
    pvt_cantidad = agg.pivot_table(
        index=dims, columns='Día', values='Cantidad', aggfunc='sum', fill_value=0
    )

    # ordenar columnas DESC
    pvt_total = pvt_total.reindex(sorted(pvt_total.columns, reverse=True), axis=1)
    pvt_margen = pvt_margen.reindex(sorted(pvt_margen.columns, reverse=True), axis=1)
    pvt_cantidad = pvt_cantidad.reindex(sorted(pvt_cantidad.columns, reverse=True), axis=1)

    # total columna
    pvt_total.insert(0, 'TOTAL', pvt_total.sum(axis=1))
    pvt_cantidad.insert(0, 'TOTAL', pvt_cantidad.sum(axis=1))

    contrib_total = (
        agg.groupby(dims, dropna=False)['Contribución'].sum()
    )
    venta_total = (
        agg.groupby(dims, dropna=False)['TotalNeto_Total'].sum()
    )
    margen_total = (contrib_total / venta_total.replace(0, pd.NA)).fillna(0)
    pvt_margen.insert(0, 'TOTAL', margen_total)

    # etiquetar métricas y apilar
    pvt_total = pvt_total.reset_index()
    pvt_total.insert(0, 'Métrica', 'TotalNeto_Total')

    pvt_margen = pvt_margen.reset_index()
    pvt_margen.insert(0, 'Métrica', 'Margen')

    pvt_cantidad = pvt_cantidad.reset_index()
    pvt_cantidad.insert(0, 'Métrica', 'Cantidad')

    resumen = pd.concat([pvt_total, pvt_margen, pvt_cantidad], ignore_index=True)

    return resumen


def generar_resumen_anio(df_base: pd.DataFrame) -> pd.DataFrame:
    df_res = preparar_base_resumen(df_base)

    # limitar al año más reciente presente en la data
    ultimo_anio = df_res['Fecha'].dt.year.max()
    df_anio = df_res[df_res['Fecha'].dt.year == ultimo_anio].copy()

    df_anio['Mes_Label'] = df_anio['Fecha'].dt.strftime('%Y-%m')

    group_cols = ['Canal', 'Tienda_ecom', 'Marca_Limpia', 'Mes_Label']
    agg = (
        df_anio.groupby(group_cols, dropna=False, as_index=False)
        .agg({
            'TotalNeto_Total': 'sum',
            'Contribución': 'sum',
            'Cantidad': 'sum'
        })
    )

    agg['Margen'] = agg.apply(
        lambda x: (x['Contribución'] / x['TotalNeto_Total']) if x['TotalNeto_Total'] != 0 else 0,
        axis=1
    )

    dims = ['Canal', 'Tienda_ecom', 'Marca_Limpia']

    pvt_total = agg.pivot_table(
        index=dims, columns='Mes_Label', values='TotalNeto_Total', aggfunc='sum', fill_value=0
    )
    pvt_margen = agg.pivot_table(
        index=dims, columns='Mes_Label', values='Margen', aggfunc='first', fill_value=0
    )
    pvt_cantidad = agg.pivot_table(
        index=dims, columns='Mes_Label', values='Cantidad', aggfunc='sum', fill_value=0
    )

    # meses DESC
    pvt_total = pvt_total.reindex(sorted(pvt_total.columns, reverse=True), axis=1)
    pvt_margen = pvt_margen.reindex(sorted(pvt_margen.columns, reverse=True), axis=1)
    pvt_cantidad = pvt_cantidad.reindex(sorted(pvt_cantidad.columns, reverse=True), axis=1)

    pvt_total.insert(0, 'YTD', pvt_total.sum(axis=1))
    pvt_cantidad.insert(0, 'YTD', pvt_cantidad.sum(axis=1))

    contrib_total = agg.groupby(dims, dropna=False)['Contribución'].sum()
    venta_total = agg.groupby(dims, dropna=False)['TotalNeto_Total'].sum()
    margen_total = (contrib_total / venta_total.replace(0, pd.NA)).fillna(0)
    pvt_margen.insert(0, 'YTD', margen_total)

    pvt_total = pvt_total.reset_index()
    pvt_total.insert(0, 'Métrica', 'TotalNeto_Total')

    pvt_margen = pvt_margen.reset_index()
    pvt_margen.insert(0, 'Métrica', 'Margen')

    pvt_cantidad = pvt_cantidad.reset_index()
    pvt_cantidad.insert(0, 'Métrica', 'Cantidad')

    resumen = pd.concat([pvt_total, pvt_margen, pvt_cantidad], ignore_index=True)

    return resumen


# Aplicar formatos específicos por métrica en resúmenes (mes y año)
def aplicar_formatos_resumen(worksheet, df_subir):
    moneda_format = CellFormat(
        numberFormat=NumberFormat(type='NUMBER', pattern='"S/" #,##0.00')
    )
    porcentaje_format = CellFormat(
        numberFormat=NumberFormat(type='PERCENT', pattern='0.00%')
    )
    entero_format = CellFormat(
        numberFormat=NumberFormat(type='NUMBER', pattern='#,##0')
    )

    if 'Métrica' not in df_subir.columns:
        return

    col_inicio_valores = df_subir.columns.get_loc('Marca_Limpia') + 2
    col_fin_valores = len(df_subir.columns)

    # agrupar filas consecutivas por métrica para reducir requests
    metricas = df_subir['Métrica'].tolist()

    bloques = []
    inicio = 0
    actual = metricas[0] if metricas else None

    for i in range(1, len(metricas)):
        if metricas[i] != actual:
            bloques.append((actual, inicio, i - 1))
            actual = metricas[i]
            inicio = i

    if metricas:
        bloques.append((actual, inicio, len(metricas) - 1))

    for metrica, fila_ini, fila_fin in bloques:
        excel_row_ini = fila_ini + 2
        excel_row_fin = fila_fin + 2

        inicio_a1 = rowcol_to_a1(excel_row_ini, col_inicio_valores)
        fin_a1 = rowcol_to_a1(excel_row_fin, col_fin_valores)
        rango = f"{inicio_a1}:{fin_a1}"

        if metrica == 'TotalNeto_Total':
            format_cell_range(worksheet, rango, moneda_format)
        elif metrica == 'Margen':
            format_cell_range(worksheet, rango, porcentaje_format)
        elif metrica == 'Cantidad':
            format_cell_range(worksheet, rango, entero_format)

        time.sleep(1)


# =========================
# GENERAR RESÚMENES
# =========================
df_resumen_mes = generar_resumen_mes(df)
df_resumen_anio = generar_resumen_anio(df)

# =========================
# LIMPIEZA DETALLE
# =========================
df.replace([float('inf'), float('-inf')], "", inplace=True)
df.fillna("", inplace=True)

# === REORDENAR COLUMNAS: Margen antes de Contribución ===
columnas = df.columns.tolist()
if 'Margen' in columnas and 'Contribución' in columnas:
    columnas.remove('Margen')
    columnas.remove('Contribución')
    columnas += ['Margen', 'Contribución']
    df = df[columnas]

# =========================
# CONEXIÓN A GOOGLE SHEETS
# =========================
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_name(ruta_credenciales, scope)
client = gspread.authorize(creds)

def obtener_o_crear_hoja(spreadsheet, nombre_hoja, filas=200, cols=50):
    try:
        return spreadsheet.worksheet(nombre_hoja)
    except gspread.exceptions.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=nombre_hoja, rows=filas, cols=cols)

def subir_dataframe(worksheet, df_subir):
    df_subir = df_subir.copy()
    df_subir.replace([float('inf'), float('-inf')], None, inplace=True)
    df_subir = df_subir.where(pd.notnull(df_subir), None)

    worksheet.clear()
    worksheet.update([df_subir.columns.tolist()] + df_subir.values.tolist())

def aplicar_formatos(worksheet):
    # Moneda
    moneda_format = CellFormat(
        numberFormat=NumberFormat(type='CURRENCY', pattern='"S/" #,##0')
    )

    # Porcentaje
    porcentaje_format = CellFormat(
        numberFormat=NumberFormat(type='PERCENT', pattern='0.00%')
    )

    # Entero con separador de miles
    entero_format = CellFormat(
        numberFormat=NumberFormat(type='NUMBER', pattern='#,##0')
    )

    headers = worksheet.row_values(1)

    for i, col in enumerate(headers):
        col_letter = chr(65 + i)

        if "TotalNeto" in col:
            format_cell_range(worksheet, f"{col_letter}2:{col_letter}", moneda_format)

        elif "Margen" in col:
            format_cell_range(worksheet, f"{col_letter}2:{col_letter}", porcentaje_format)

        elif "Cantidad" in col:
            format_cell_range(worksheet, f"{col_letter}2:{col_letter}", entero_format)

try:
    spreadsheet = client.open(nombre_google_sheet)

    ws_detalle = obtener_o_crear_hoja(spreadsheet, hoja_detalle, filas=max(len(df)+10, 200), cols=max(len(df.columns)+10, 20))
    ws_mes = obtener_o_crear_hoja(spreadsheet, hoja_resumen_mes, filas=max(len(df_resumen_mes)+10, 200), cols=max(len(df_resumen_mes.columns)+10, 20))
    ws_anio = obtener_o_crear_hoja(spreadsheet, hoja_resumen_anio, filas=max(len(df_resumen_anio)+10, 200), cols=max(len(df_resumen_anio.columns)+10, 20))

    subir_dataframe(ws_detalle, df)
    time.sleep(2)
    aplicar_formatos_detalle(ws_detalle, df)

    subir_dataframe(ws_mes, df_resumen_mes)
    time.sleep(2)
    aplicar_formatos_resumen(ws_mes, df_resumen_mes)

    subir_dataframe(ws_anio, df_resumen_anio)
    time.sleep(2)
    aplicar_formatos_resumen(ws_anio, df_resumen_anio)

    print("[OK] Datos actualizados correctamente en Google Sheets.")
    print(f"[OK] Detalle Diario: {len(df)} filas")
    print(f"[OK] Resumen_Mes: {len(df_resumen_mes)} filas")
    print(f"[OK] Resumen_Año: {len(df_resumen_anio)} filas")

except Exception as e:
    print("[ERROR] Error al actualizar Google Sheets:")
    print(e)
