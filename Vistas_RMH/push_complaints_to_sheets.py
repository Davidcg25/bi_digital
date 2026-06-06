# push_complaints_to_sheets.py
import pyodbc
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import datetime

# === CONFIGURACIONES ===
tabla_sql = "SZ_Complaint_Books"
ruta_credenciales = r"D:\Proyectos\4_BI_Ecom\Vistas_RMH\di-auth-gsheets.json"
nombre_google_sheet = "Solidez | Ecommerce - Libros de reclamos"
nombre_hoja = "Reclamos"

# === CONEXIÓN A SQL SERVER ===
conn = pyodbc.connect(
    "DRIVER={ODBC Driver 17 for SQL Server};"
    "SERVER=localhost;"
    "DATABASE=Digital_Impact_Reportes;"
    "Trusted_Connection=yes;"
)

# Trae TODO ordenado por entity_id DESC
query = f"""
SELECT
    entity_id,
    id_complaintsbook,
    person_type,
    full_name,
    dni_ce,
    cell_phone_number,
    email,
    address,
    minor_age,
    guardian_name,
    guardian_address,
    guardian_phone,
    guardian_email,
    department,
    province,
    district,
    order_id,
    product_amount,
    complaint_type,
    product_description,
    complaint_detail,
    txt_order,
    fecha_insercion
FROM {tabla_sql}
ORDER BY entity_id DESC
"""
df = pd.read_sql(query, conn)
conn.close()

# === LIMPIEZA/FORMATEO SUAVE ===
# (1) Convertir fechas a str solo para columnas que contengan objetos datetime
for col in df.columns:
    if df[col].apply(lambda x: isinstance(x, (datetime.date, datetime.datetime))).any():
        df[col] = df[col].astype(str)

# (2) Reemplazar inf y NaN por cadenas vacías (evita errores en API)
df.replace([float('inf'), float('-inf')], "", inplace=True)
df.fillna("", inplace=True)

# === CONEXIÓN A GOOGLE SHEETS ===
scope = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']
creds = ServiceAccountCredentials.from_json_keyfile_name(ruta_credenciales, scope)
client = gspread.authorize(creds)

# Abrir por nombre y seleccionar/crear hoja "Reclamos"
spreadsheet = client.open(nombre_google_sheet)
try:
    worksheet = spreadsheet.worksheet(nombre_hoja)
except gspread.exceptions.WorksheetNotFound:
    worksheet = spreadsheet.add_worksheet(title=nombre_hoja, rows="100", cols="26")

# === ESCRITURA (full refresh) ===
worksheet.clear()
# Enviar encabezados + datos
worksheet.update([df.columns.tolist()] + df.values.tolist())

print("[OK] 'SZ_Complaint_Books' exportado a Google Sheets > Hoja 'Reclamos' (orden: entity_id DESC).")

