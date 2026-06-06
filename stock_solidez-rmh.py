import sys
import io
# Forzar UTF-8 en stdout para evitar UnicodeEncodeError en logs de Windows (cp1252)
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import time
import warnings
import unicodedata
warnings.filterwarnings('ignore', message='.*Unrecognized server version.*')
from dotenv import load_dotenv
import os
import pandas as pd
from urllib.parse import quote_plus
from sqlalchemy import create_engine, text
from datetime import datetime, timedelta

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
# 📅 Determinar fecha de consulta
# -------------------------
hoy = datetime.today()
weekday = hoy.weekday()  # Lunes = 0, Domingo = 6
hora_actual = hoy.hour

# Si es fin de semana → mover al viernes anterior
if weekday >= 5:  # Sábado (5) o Domingo (6)
    dias_a_restar = weekday - 4
    fecha_obj = hoy - timedelta(days=dias_a_restar)
else:
    fecha_obj = hoy

# Si es antes del mediodía → tomar día hábil anterior
if hora_actual < 12:
    # Retroceder un día, pero saltar fines de semana
    fecha_obj -= timedelta(days=1)
    while fecha_obj.weekday() >= 5:  # Sábado o Domingo
        fecha_obj -= timedelta(days=1)

fecha_consulta_str = fecha_obj.strftime('%d-%m-%Y')
print(f"[INFO] Ejecutando consulta de stock para {fecha_consulta_str}...")

# -------------------------
# 🔌 Conexión RMH
# -------------------------
rmh_engine = create_engine(
    f"mssql+pyodbc://{quote_plus(rmh_user)}:{quote_plus(rmh_pass)}"
    f"@{rmh_server}/{rmh_db}?driver={quote_plus('SQL Server')}"
)

query = f"""
SELECT *
FROM reportelunes_1tfd
WHERE Fecha = '{fecha_consulta_str}'
"""

with rmh_engine.connect() as rmh_conn:
    df = pd.read_sql(text(query), rmh_conn)
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

def quitar_acentos(s: str) -> str:
    """Elimina diacríticos: Ñ→N, Á→A, É→E, etc. (solo para claves de join)."""
    return unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode('utf-8')

def norm_upper_strip(df, cols, remove_accents=False):
    for c in cols:
        if c in df.columns:
            df[c] = df[c].astype(str).str.strip().str.upper()
            if remove_accents:
                df[c] = df[c].apply(quitar_acentos)
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

# 🔧 MARCAS (quedarnos solo con columnas útiles y dedup por Marca)
cols_marcas = [c for c in ['Marca','Marca_Limpia','Activa'] if c in df_marcas.columns]
if 'Marca' not in cols_marcas:
    raise ValueError("[ERROR] 'tabla_marcas.csv' debe contener la columna 'Marca'.")
df_marcas = df_marcas[cols_marcas].copy()
df_marcas = dedup_on_key(df_marcas, ['Marca'], name='tabla_marcas')

# 🔧 TEMPORADAS (flexible a esquema)
if 'Temporada' not in df_temporadas.columns:
    raise ValueError("[ERROR] 'tabla_temporadas.csv' debe contener la columna 'Temporada'.")

if 'Temporada_Limpia' in df_temporadas.columns:
    df_temporadas = df_temporadas[['Temporada','Temporada_Limpia']].rename(columns={'Temporada_Limpia':'Temp.'})
elif 'Temp.' in df_temporadas.columns:
    df_temporadas = df_temporadas[['Temporada','Temp.']]
else:
    # fallback: usar Temporada como Temp.
    df_temporadas = df_temporadas[['Temporada']].copy()
    df_temporadas['Temp.'] = df_temporadas['Temporada']

df_temporadas = dedup_on_key(df_temporadas, ['Temporada'], name='tabla_temporadas')

# 🔧 FAMILIAS (elige columnas conocidas; dedup por Familia)
keep_cols_fam = [c for c in df_familias.columns if c in ['Familia','Linea.','Subtipo','Categoria','Familia_Limpia']]
if 'Familia' not in keep_cols_fam:
    raise ValueError("[ERROR] 'tabla_familias.csv' debe contener la columna 'Familia'.")
df_familias = df_familias[keep_cols_fam].copy()
df_familias = dedup_on_key(df_familias, ['Familia'], name='tabla_familias')

# 🔧 LOCALES (quitar columnas que pueden duplicar, p.ej. Id_Tienda; dedup por Local)
if 'Local' not in df_locales.columns:
    raise ValueError("[ERROR] 'tabla_locales.csv' debe contener la columna 'Local'.")
df_locales = df_locales.drop(columns=['Id_Tienda'], errors='ignore')
# remove_accents=True: normaliza "Almacén" == "Almacen", "Ñ" == "N", etc.
df_locales = dedup_on_key(df_locales, ['Local'], name='tabla_locales')
df_locales['Local'] = df_locales['Local'].apply(quitar_acentos)

# --- Normaliza claves en el DF principal (para que el match no falle por espacios/case)
# remove_accents=True solo en Local para que los nombres legacy sin tilde (RMH) hagan match
df = norm_upper_strip(df, ['Marca','Temporada','Linea'])
df = norm_upper_strip(df, ['Local'], remove_accents=True)

# --- Merges protegidos (uno-a-uno esperado) ---
df = merge_guard(df, df_marcas,     on='Marca',      how='left', tag='marcas')
df = merge_guard(df, df_temporadas, on='Temporada',  how='left', tag='temporadas')
df = merge_guard(df, df_familias,   left_on='Linea', right_on='Familia', how='left', tag='familias')
df = merge_guard(df, df_locales,    on='Local',      how='left', tag='locales')

# === DIAGNÓSTICO DE LOCALES ===
print("\n[DIAGNÓSTICO LOCALES] =====================================")

# Locales que tabla_locales considera Integrada=SÍ
_integ_ok = df_locales['Integrada'].astype(str).str.strip().str.upper().isin(['SÍ', 'SI'])
_locales_ref_si = df_locales[_integ_ok].copy()
_locales_en_rmh = set(df['Local'].unique())

# a) Integrados que NO aparecen en reportelunes_1tfd
_faltantes = set(_locales_ref_si['Local']) - _locales_en_rmh
if _faltantes:
    print(f"  [!] {len(_faltantes)} local(es) Integrada=SI AUSENTES en reportelunes_1tfd:")
    for _loc in sorted(_faltantes):
        _tipo = _locales_ref_si[_locales_ref_si['Local'] == _loc]['Tipo'].values
        print(f"       '{_loc}'  |  Tipo: {_tipo[0] if len(_tipo) else '?'}")
else:
    print("  [OK] Todos los locales Integrada=SI tienen datos en RMH.")

# b) Locales tipo Devoluciones / Marketplace -- resumen de situacion
_tipos_nuevos = df_locales[
    df_locales['Tipo'].astype(str).str.upper().isin(['DEVOLUCIONES', 'MARKETPLACE'])
]
if len(_tipos_nuevos):
    print(f"\n  Locales Devoluciones/Marketplace en tabla_locales ({len(_tipos_nuevos)}):")
    for _, _row in _tipos_nuevos.iterrows():
        _estado = "[EN RMH]" if _row['Local'] in _locales_en_rmh else "[AUSENTE EN RMH]"
        print(f"       '{_row['Local']}'  |  Tipo: {_row['Tipo']}"
              f"  |  Integrada: {_row['Integrada']}  |  {_estado}")

# c) Locales en RMH que no hicieron match (Tipo=NaN -> Integrada=NaN)
#    Se muestran con su storeid para facilitar completar tabla_locales.csv
if 'Tipo' in df.columns:
    _cols_diag = [c for c in ['Local', 'storeid'] if c in df.columns]
    _sin_tipo_df = (df[df['Tipo'].isna()][_cols_diag]
                    .drop_duplicates(subset=['Local'])
                    .sort_values('Local'))
    if len(_sin_tipo_df):
        print(f"\n  [!] {len(_sin_tipo_df)} local(es) en RMH sin match en tabla_locales (Tipo=NaN - DESCARTADOS por groupby):")
        for _, _r in _sin_tipo_df.head(20).iterrows():
            _sid = f"  storeid={int(_r['storeid'])}" if 'storeid' in _r and not pd.isna(_r['storeid']) else ''
            print(f"       '{_r['Local']}'{_sid}")

print("[DIAGNÓSTICO LOCALES] =====================================\n")

# === CONSOLIDACIÓN DE NEGOCIO (evita duplicados intra-día) ===
keys = ['Fecha', 'Local', 'Tipo', 'codigoGp']

# Columnas que SÍ se SUMAN:
sum_cols = [c for c in ['stock', 'fl', 'total'] if c in df.columns]

# Precios: NO se suman. Elegimos un representativo (MAX o first no nulo)
price_cols = [c for c in ['Price', 'PrecioA', 'PrecioB', 'PrecioC', 'pcoliseum'] if c in df.columns]

# Construir agg_map:
agg_map = {c: 'first' for c in df.columns if c not in keys + sum_cols + price_cols}
for c in sum_cols:
    agg_map[c] = 'sum'
for c in price_cols:
    agg_map[c] = 'max'   # o 'first' si prefieres exactamente el primero

before_c = len(df)
df = (df
      .groupby(keys, as_index=False, dropna=False)
      .agg(agg_map))
after_c = len(df)
dropped = before_c - after_c
print(f"[CONSOLIDACION] filas {before_c} -> {after_c} agrupando por {keys}."
      + (f" ({dropped} filas consolidadas)" if dropped > 0 else ""))

# -------------------------
# 🔌 Conexión SQL destino (reemplaza tu engine por este con fast_executemany)
# -------------------------
from urllib.parse import quote_plus

server     = 'localhost'
database   = 'Digital_Impact_Reportes'
table_name = 'Stock_Solidez_RMH'
driver     = 'ODBC Driver 17 for SQL Server'

connect_str = f"mssql+pyodbc://@{server}/{database}?driver={quote_plus(driver)}&trusted_connection=yes"
engine = create_engine(connect_str, fast_executemany=True)

# -------------------------
# 🧼 Normalización de campos antes de cargar
# -------------------------
# 1) Fecha: quitar espacios y asegurar formato 'dd-mm-YYYY'
if 'Fecha' in df.columns:
    df['Fecha'] = (
        df['Fecha']
        .astype(str)
        .str.strip()
        .str[:10]  # por si viene 'dd-mm-YYYY          '
    )

# 2) Quitar columna(s) que no existen en SQL
#    - Detectamos columnas reales de la tabla destino y cruzamos
with engine.begin() as conn:
    dest_cols = pd.read_sql(
        f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{table_name}'",
        conn
    )['COLUMN_NAME'].str.strip().tolist()

# Si el DataFrame tiene columnas no presentes en la tabla, las eliminamos
cols_to_drop = [c for c in df.columns if c not in dest_cols]
if cols_to_drop:
    print(f"[INFO] Columnas extra en DataFrame que NO existen en {table_name} y serán eliminadas: {cols_to_drop}")
    df = df.drop(columns=cols_to_drop, errors='ignore')

# 3) Opcional: asegurar tipos razonables (evita fallos por objetos raros)
for num_col in ['Año', 'Mes', 'stock', 'fl', 'total', 'Price', 'PrecioA', 'PrecioB', 'PrecioC', 'pcoliseum']:
    if num_col in df.columns:
        df[num_col] = pd.to_numeric(df[num_col], errors='coerce')

# -------------------------
# 💥 Eliminar registros previos (match por Fecha sin espacios)
# -------------------------
from sqlalchemy import text as sa_text

if 'Fecha' in dest_cols:
    print(f"[LIMPIEZA] Eliminando registros previos para {df['Fecha'].iloc[0] if len(df) else 'N/A'}...")
    with engine.begin() as conn:
        conn.execute(
            sa_text(f"DELETE FROM {table_name} WHERE LTRIM(RTRIM(Fecha)) = :fecha"),
            {"fecha": df['Fecha'].iloc[0] if len(df) else None}
        )
else:
    print("[ADVERTENCIA] La tabla destino no tiene columna 'Fecha'. Se omite limpieza por fecha.")

# === DEBUG: Locales únicos con Integrada = "Sí"
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

    # Asegurar que las columnas estén en el mismo orden que la tabla (evita sorpresas)
    existing_cols_ordered = [c for c in dest_cols if c in chunk_df.columns]
    chunk_df = chunk_df[existing_cols_ordered]

    # Insert
    chunk_df.to_sql(table_name, con=engine, if_exists='append', index=False, chunksize=2000)
    print(f"[OK] Insertado chunk {i//chunksize + 1} ({i + len(chunk_df)}/{total})")

elapsed = time.time() - start_time
print(f"[FINALIZADO] Carga completada en {elapsed:.2f} segundos.")
print(f"[TOTAL] Registros insertados: {total}")

