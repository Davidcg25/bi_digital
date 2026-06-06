import time
from dotenv import load_dotenv
import os
import pyodbc
import pandas as pd
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta
from urllib.parse import quote_plus
import argparse
import re

# -------------------------
# 🔑 Cargar credenciales RMH
# -------------------------
load_dotenv()

rmh_server = os.getenv("RMH_SERVER")
rmh_db     = os.getenv("RMH_DATABASE")
rmh_user   = os.getenv("RMH_USERNAME")
rmh_pass   = os.getenv("RMH_PASSWORD")

if not all([rmh_server, rmh_db, rmh_user, rmh_pass]):
    raise Exception("[ERROR] Faltan datos en el archivo .env para conectar a RMH.")

# -------------------------
# 🧭 Helpers de fecha
# -------------------------
def parse_user_date(date_str: str) -> datetime:
    """
    Acepta DD-MM-YYYY, YYYY-MM-DD, DD/MM/YYYY
    Devuelve objeto datetime (sin hora).
    """
    date_str = date_str.strip()
    # normaliza separadores
    date_norm = re.sub(r"[\/.]", "-", date_str)

    fmts = ["%d-%m-%Y", "%Y-%m-%d"]
    for fmt in fmts:
        try:
            return datetime.strptime(date_norm, fmt)
        except ValueError:
            continue
    raise ValueError(f"[ERROR] Formato de fecha no reconocido: '{date_str}'. Usa DD-MM-YYYY o YYYY-MM-DD.")

def compute_auto_business_date(now: datetime) -> datetime:
    """
    Lógica original:
      - Si es sábado/domingo => mover a viernes anterior
      - Si es antes del mediodía => día hábil anterior
    """
    weekday = now.weekday()  # Lunes=0 .. Domingo=6
    hour = now.hour

    # fin de semana => viernes previo
    if weekday >= 5:  # 5=sábado, 6=domingo
        dias_a_restar = weekday - 4
        fecha_obj = now - timedelta(days=dias_a_restar)
    else:
        fecha_obj = now

    # antes del mediodía => día hábil anterior
    if hour < 12:
        fecha_obj -= timedelta(days=1)
        while fecha_obj.weekday() >= 5:
            fecha_obj -= timedelta(days=1)

    return fecha_obj

def fmt_dd_mm_yyyy(d: datetime) -> str:
    return d.strftime("%d-%m-%Y")

# -------------------------
# 🎛️ CLI args
# -------------------------
parser = argparse.ArgumentParser(description="Extract + Load Stock RMH para una fecha puntual o lógica automática.")
parser.add_argument("--date", help="Fecha específica a cargar (DD-MM-YYYY o YYYY-MM-DD). Omite para usar lógica automática.", default=None)
args = parser.parse_args()

# -------------------------
# 📅 Determinar fecha de consulta
# -------------------------
hoy = datetime.today()
if args.date:
    try:
        fecha_obj = parse_user_date(args.date)
        print(f"[INFO] Fecha forzada por usuario: {fmt_dd_mm_yyyy(fecha_obj)} (se ignoran reglas de fin de semana/mediodía)")
    except ValueError as e:
        raise SystemExit(str(e))
else:
    fecha_obj = compute_auto_business_date(hoy)
    print(f"[INFO] Fecha determinada automáticamente: {fmt_dd_mm_yyyy(fecha_obj)}")

fecha_consulta_str = fmt_dd_mm_yyyy(fecha_obj)
print(f"[INFO] Ejecutando consulta de stock para {fecha_consulta_str}...")

# -------------------------
# 🔌 Conexión RMH y extracción
# -------------------------
conn_str = f'DRIVER={{SQL Server}};SERVER={rmh_server};DATABASE={rmh_db};UID={rmh_user};PWD={rmh_pass}'
conn = pyodbc.connect(conn_str)

# Nota: pyodbc admite parámetros "?" en consultas. Pandas read_sql también soporta params con ODBC.
query = "SELECT * FROM reportelunes_1tfd WHERE Fecha = ?"
df = pd.read_sql(query, conn, params=[fecha_consulta_str])
conn.close()
print(f"[OK] Filas extraídas: {len(df)}")

# -------------------------
# 📄 Cargar tablas de referencia (robusto a esquemas)
# -------------------------
df_marcas      = pd.read_csv('tabla_marcas.csv')       # esperable: Marca, Marca_Limpia, Activa
df_locales     = pd.read_csv('tabla_locales.csv')      # esperable: Local, Tienda (evitar Id_Tienda)
df_ecom        = pd.read_csv('tabla_ecommerce.csv')    # si no lo usas aquí, no pasa nada
df_temporadas  = pd.read_csv('tabla_temporadas.csv')   # puede traer: (Temporada, Temporada_Limpia) o (Temporada, Temp.)
df_familias    = pd.read_csv('tabla_familias.csv')     # flexible: Familia, Linea., Subtipo, Categoria, Familia_Limpia...

# --- Helpers ---
def dedup_on_key(df_ref, key_cols, keep='first', name='ref'):
    prev = len(df_ref)
    df_ref = (
        df_ref.assign(**{c: df_ref[c].astype(str).str.strip().str.upper() for c in key_cols})
              .drop_duplicates(subset=key_cols, keep=keep)
    )
    now = len(df_ref)
    if now < prev:
        print(f"[DEDUP] {name}: {prev} -> {now} (clave {key_cols})")
    return df_ref

def norm_upper_strip(df, cols):
    for c in cols:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip().str.upper()
    return df

def merge_guard(df_left, df_right, on=None, left_on=None, right_on=None, how='left', tag='merge'):
    before = len(df_left)
    out = df_left.merge(df_right, on=on, left_on=left_on, right_on=right_on, how=how)
    after = len(out)
    mult = after / max(1, before)
    print(f"[MERGE] {tag}: filas {before} -> {after} (x{mult:.2f})")
    if mult > 1.05:
        print(f"[ALERTA] {tag} multiplicó las filas. Revisa unicidad de la clave del lookup.")
    return out

# --- Normalización mínima de headers (por si vienen con espacios)
df_temporadas.columns = [c.strip() for c in df_temporadas.columns]
df_familias.columns   = [c.strip() for c in df_familias.columns]
df_locales.columns    = [c.strip() for c in df_locales.columns]
df_marcas.columns     = [c.strip() for c in df_marcas.columns]

# 🔧 MARCAS
cols_marcas = [c for c in ['Marca','Marca_Limpia','Activa'] if c in df_marcas.columns]
if 'Marca' not in cols_marcas:
    raise ValueError("[ERROR] 'tabla_marcas.csv' debe contener la columna 'Marca'.")
df_marcas = df_marcas[cols_marcas].copy()
df_marcas = dedup_on_key(df_marcas, ['Marca'], name='tabla_marcas')

# 🔧 TEMPORADAS
if 'Temporada' not in df_temporadas.columns:
    raise ValueError("[ERROR] 'tabla_temporadas.csv' debe contener la columna 'Temporada'.")

if 'Temporada_Limpia' in df_temporadas.columns:
    df_temporadas = df_temporadas[['Temporada','Temporada_Limpia']].rename(columns={'Temporada_Limpia':'Temp.'})
elif 'Temp.' in df_temporadas.columns:
    df_temporadas = df_temporadas[['Temporada','Temp.']]
else:
    df_temporadas = df_temporadas[['Temporada']].copy()
    df_temporadas['Temp.'] = df_temporadas['Temporada']

df_temporadas = dedup_on_key(df_temporadas, ['Temporada'], name='tabla_temporadas')

# 🔧 FAMILIAS
keep_cols_fam = [c for c in df_familias.columns if c in ['Familia','Linea.','Subtipo','Categoria','Familia_Limpia']]
if 'Familia' not in keep_cols_fam:
    raise ValueError("[ERROR] 'tabla_familias.csv' debe contener la columna 'Familia'.")
df_familias = df_familias[keep_cols_fam].copy()
df_familias = dedup_on_key(df_familias, ['Familia'], name='tabla_familias')

# 🔧 LOCALES
if 'Local' not in df_locales.columns:
    raise ValueError("[ERROR] 'tabla_locales.csv' debe contener la columna 'Local'.")
df_locales = df_locales.drop(columns=['Id_Tienda'], errors='ignore')
df_locales = dedup_on_key(df_locales, ['Local'], name='tabla_locales')

# --- Normaliza claves en el DF principal
df = norm_upper_strip(df, ['Marca','Temporada','Linea','Local'])

# --- Merges protegidos
df = merge_guard(df, df_marcas,     on='Marca',      how='left', tag='marcas')
df = merge_guard(df, df_temporadas, on='Temporada',  how='left', tag='temporadas')
df = merge_guard(df, df_familias,   left_on='Linea', right_on='Familia', how='left', tag='familias')
df = merge_guard(df, df_locales,    on='Local',      how='left', tag='locales')

# === CONSOLIDACIÓN DE NEGOCIO (evita duplicados intra-día) ===
keys = ['Fecha', 'Local', 'Tipo', 'codigoGp']

sum_cols = [c for c in ['stock', 'fl', 'total'] if c in df.columns]
price_cols = [c for c in ['Price', 'PrecioA', 'PrecioB', 'PrecioC', 'pcoliseum'] if c in df.columns]

agg_map = {c: 'first' for c in df.columns if c not in keys + sum_cols + price_cols}
for c in sum_cols:
    agg_map[c] = 'sum'
for c in price_cols:
    agg_map[c] = 'max'

before_c = len(df)
df = (df.groupby(keys, as_index=False).agg(agg_map))
after_c = len(df)
print(f"[CONSOLIDACION] filas {before_c} -> {after_c} agrupando por {keys}.")

# -------------------------
# 🔌 Conexión SQL destino
# -------------------------
server     = 'localhost/DEVSQL'
database   = 'Digital_Impact_Reportes'
table_name = 'Stock_Solidez_RMH'
driver     = 'ODBC Driver 17 for SQL Server'

connect_str = f"mssql+pyodbc://@{server}/{database}?driver={quote_plus(driver)}&trusted_connection=yes"
engine = create_engine(connect_str, fast_executemany=True)

# -------------------------
# 🧼 Normalización de campos antes de cargar
# -------------------------
if 'Fecha' in df.columns:
    df['Fecha'] = (
        df['Fecha'].astype(str).str.strip().str[:10]
    )

with engine.begin() as conn:
    dest_cols = pd.read_sql(
        f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{table_name}'",
        conn
    )['COLUMN_NAME'].str.strip().tolist()

cols_to_drop = [c for c in df.columns if c not in dest_cols]
if cols_to_drop:
    print(f"[INFO] Columnas extra en DataFrame que NO existen en {table_name} y serán eliminadas: {cols_to_drop}")
    df = df.drop(columns=cols_to_drop, errors='ignore')

for num_col in ['Año', 'Mes', 'stock', 'fl', 'total', 'Price', 'PrecioA', 'PrecioB', 'PrecioC', 'pcoliseum']:
    if num_col in df.columns:
        df[num_col] = pd.to_numeric(df[num_col], errors='coerce')

# -------------------------
# 💥 Eliminar registros previos (exact match de la fecha objetivo)
# -------------------------
from sqlalchemy import text as sa_text

if 'Fecha' in dest_cols and len(df):
    target_fecha = df['Fecha'].iloc[0]
    print(f"[LIMPIEZA] Eliminando registros previos para {target_fecha} en {table_name}...")
    with engine.begin() as conn:
        conn.execute(
            sa_text(f"DELETE FROM {table_name} WHERE LTRIM(RTRIM(Fecha)) = :fecha"),
            {"fecha": target_fecha}
        )
else:
    print("[ADVERTENCIA] No se encontró columna 'Fecha' o DataFrame vacío. Se omite limpieza por fecha.")

# === DEBUG: Locales Integrados
if 'Integrada' in df.columns:
    locales_integrados = df[df['Integrada'].astype(str).str.upper().str.strip() == 'SÍ']['Local'].nunique()
    print(f"[INFO] Locales Integrados = 'Sí': {locales_integrados}")
else:
    print("[INFO] No se encontró la columna 'Integrada' en el DataFrame.")

# -------------------------
# [SYNC] Insertar nuevos datos (chunks)
# -------------------------
chunksize = 5000
total = len(df)
print(f"[SYNC] Iniciando carga a SQL con chunks de {chunksize} registros...")

start_time = time.time()
for i in range(0, total, chunksize):
    chunk_df = df.iloc[i:i+chunksize].copy()
    existing_cols_ordered = [c for c in dest_cols if c in chunk_df.columns]
    chunk_df = chunk_df[existing_cols_ordered]
    chunk_df.to_sql(table_name, con=engine, if_exists='append', index=False, chunksize=2000)
    print(f"[OK] Insertado chunk {i//chunksize + 1} ({i + len(chunk_df)}/{total})")

elapsed = time.time() - start_time
print(f"[FINALIZADO] Carga completada en {elapsed:.2f} segundos.")
print(f"[TOTAL] Registros insertados: {total}")

