# -*- coding: utf-8 -*-
"""
Sincroniza vistas de Stock RMH hacia Google Sheets
- Vista_Stock-rmh_Disponibles_MCT  -> pestaña 'Disponible_MCT'
- Vista_Stock-rmh_MC_Resumen       -> pestaña 'MC_Resumen'
- Stock_Solidez_RMH (última fecha, Integrada='SÍ') -> pestaña 'BD'
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import warnings
warnings.filterwarnings('ignore', message=".*doesn't match a supported version.*")

import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime
from gspread.exceptions import WorksheetNotFound
from sqlalchemy import create_engine, text
from urllib.parse import quote_plus

# =========================
# CONFIGURACIÓN
# =========================
RUTA_CREDENCIALES = r"D:\Proyectos\4_BI_Ecom\Vistas_RMH\di-auth-gsheets.json"
NOMBRE_GOOGLE_SHEET = "Solidez-RMH-Stock"

# Map: { nombre_pestaña : query_sql }
VISTAS_A_PESTANAS = {
    "Disponible_MCT": """
        SELECT *
        FROM [dbo].[Vista_Stock-rmh_Disponibles_MCT]
        WHERE [Marca.] IN (
            N'CONVERSE', N'UMBRO', N'STEVE MADDEN',
            N'NEW BALANCE', N'FILA', N'CATERPILLAR',
            N'MERRELL', N'HITEC'
        )
    """,
    "MC_Resumen": """
        SELECT *
        FROM [dbo].[Vista_Stock-rmh_MC_Resumen]
        WHERE [Marca.] IN (
            N'CONVERSE', N'UMBRO', N'STEVE MADDEN',
            N'NEW BALANCE', N'FILA', N'CATERPILLAR',
            N'MERRELL', N'HITEC'
        )
    """,
    "BD": """
    WITH u AS (
        SELECT MAX(TRY_CONVERT(date, [Fecha], 105)) AS fmax
        FROM [dbo].[Stock_Solidez_RMH]
    ),
    base AS (
        SELECT
            s.*,
            UPPER(LTRIM(RTRIM(COALESCE(s.[Integrada], '')))) COLLATE Latin1_General_CI_AI AS Integrada_norm,
            UPPER(LTRIM(RTRIM(COALESCE(s.[Tipo], '')))) AS Tipo_norm
        FROM [dbo].[Stock_Solidez_RMH] s
        CROSS JOIN u
        WHERE TRY_CONVERT(date, s.[Fecha], 105) = u.fmax
    )
    SELECT 
        [storeid],
        [Local],
        [Marca],          -- se conserva el campo original
        [Marca_Limpia] AS [Marca.],  -- este reemplaza al limpio
        [Linea],
        [Id],
        [codigoGp],
        [Codcolor],
        [Descripcion],
        [Genero],
        [Categoria],
        [Coleccion],
        [Temporada],
        [stock],
        [fl],
        [total],
        [Price],
        [PrecioA],
        [PrecioB],
        [PrecioC],
        [pcoliseum],
        [Año],
        [Mes],
        [Fecha],
        [Activa],
        [Tienda],
        [Tipo],
        [Temp.],
        [Linea.],
        [Integrada],
        [Subtipo]
    FROM base
    WHERE Integrada_norm = N'SI'
    AND Tipo_norm <> N'INACTIVE'
    -- filtro sobre el dato limpio, ahora llamado "Marca."
    AND UPPER([Marca_Limpia]) IN (
            N'CONVERSE', N'UMBRO', N'STEVE MADDEN', N'NEW BALANCE',
            N'FILA', N'CATERPILLAR', N'MERRELL', N'HITEC'
    )
    ORDER BY [Marca.], [Linea.], [Local], [codigoGp];
    """,
    "Best_sellers": """
        SELECT
            [Tienda_ecom],
            [Marca_Limpia],
            [Tipo],
            [CodColor],
            [Descripcion],
            [CantidadVendida],
            [Margen],
            [stock]
        FROM [dbo].[vw_top10_skus_ecommerce_ultimas_4_semanas]
        ORDER BY [Tienda_ecom], [Marca_Limpia], [CantidadVendida] DESC, [CodColor]
    """,
    "Resumen_Mktplace": """
        SELECT
            [Marca.],
            [Linea.],
            [MC],
            [Temp.],
            [Fecha],
            [Marketplace],
            [Nro Tallas Marketplace],
            [Curvado Marketplace]
        FROM [dbo].[Vista_Stock-rmh_MC_Resumen_Marketplace]
        WHERE [Marca.] IN (
            N'CONVERSE', N'UMBRO', N'STEVE MADDEN',
            N'NEW BALANCE', N'FILA', N'CATERPILLAR',
            N'MERRELL', N'HITEC'
        )
        ORDER BY [Marca.], [Linea.], [MC]
    """,
    "Validacion_Stock": """
        SELECT
            [MC],
            [CodigoGp],
            [Puede Estar Activo Magento],
            [Stock Ecommerce],
            [Stock Tiendas],
            [Motivo]
        FROM [dbo].[Vista_Stock-rmh_SKU_Activacion_Magento]
        ORDER BY [MC], [CodigoGp]
    """
}

# Conexión SQL Server
SQL_ENGINE = create_engine(
    "mssql+pyodbc://@localhost/Digital_Impact_Reportes"
    "?driver=ODBC+Driver+17+for+SQL+Server&trusted_connection=yes",
    fast_executemany=True
)

# =========================
# FUNCIONES
# =========================
def leer_sql_a_df(query: str) -> pd.DataFrame:
    with SQL_ENGINE.connect() as conn:
        df = pd.read_sql(text(query), conn)
    return df

def normalizar_df_para_sheets(df: pd.DataFrame) -> pd.DataFrame:
    """Convierte fechas a str solo donde aplique, limpia inf/NaN."""
    for col in df.columns:
        if df[col].apply(lambda x: isinstance(x, (datetime.date, datetime.datetime))).any():
            df[col] = df[col].astype(str)
    df.replace([float('inf'), float('-inf')], "", inplace=True)
    df.fillna("", inplace=True)
    return df

def gsheets_client():
    scope = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = ServiceAccountCredentials.from_json_keyfile_name(RUTA_CREDENCIALES, scope)
    return gspread.authorize(creds)

def subir_df_a_hoja(client, nombre_spreadsheet: str, nombre_hoja: str, df: pd.DataFrame):
    ss = client.open(nombre_spreadsheet)
    try:
        ws = ss.worksheet(nombre_hoja)
    except WorksheetNotFound:
        # crea la hoja si no existe, con al menos 1 fila/col
        ws = ss.add_worksheet(title=nombre_hoja, rows=1, cols=max(1, len(df.columns)))
    ws.clear()
    ws.update([df.columns.tolist()] + df.values.tolist())

# =========================
# MAIN
# =========================
if __name__ == "__main__":
    try:
        client = gsheets_client()
        for pestaña, query in VISTAS_A_PESTANAS.items():
            print(f"[INFO] Leyendo datos para pestaña '{pestaña}'...")
            df = leer_sql_a_df(query)
            df = normalizar_df_para_sheets(df)
            print(f"[INFO] Subiendo {len(df):,} filas a '{pestaña}' en '{NOMBRE_GOOGLE_SHEET}'...")
            subir_df_a_hoja(client, NOMBRE_GOOGLE_SHEET, pestaña, df)
            print(f"[OK] Pestaña '{pestaña}' actualizada correctamente.")
        print("[FINALIZADO] Todas las pestañas fueron actualizadas con éxito.")
    except Exception as e:
        print("[ERROR] Ocurrió un problema en la actualización:")
        print(e)

