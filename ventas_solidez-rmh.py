import time
from dotenv import load_dotenv
import os
import pyodbc
import pandas as pd
import calendar
from sqlalchemy import create_engine, text, inspect
from datetime import datetime, timedelta, date
from calendar import monthrange

# -------------------------
# 🔑 Cargar credenciales RMH
# -------------------------
load_dotenv()

rmh_server = os.getenv("RMH_SERVER")
rmh_db = os.getenv("RMH_DATABASE")
rmh_user = os.getenv("RMH_USERNAME")
rmh_pass = os.getenv("RMH_PASSWORD")

if not all([rmh_server, rmh_db, rmh_user, rmh_pass]):
    raise Exception("[ERROR] Faltan datos en el archivo .env para conectar a RMH.")


# -------------------------
# 🔌 Conexión RMH
# -------------------------
conn_str = f'DRIVER={{SQL Server}};SERVER={rmh_server};DATABASE={rmh_db};UID={rmh_user};PWD={rmh_pass}'
conn = pyodbc.connect(conn_str)


# -------------------------
# 📅 Selector de rango de fechas dinámico
# -------------------------

modo_fecha = "ultimo_7_dias"  # opciones: "ultimo_30_dias", "mes_anterior", "anio_actual", "anio_2024", "custom"

if modo_fecha == "ultimo_30_dias":
    fecha_fin = date.today()
    fecha_ini = fecha_fin - timedelta(days=30)

if modo_fecha == "ultimo_7_dias":
    fecha_fin = date.today()
    fecha_ini = fecha_fin - timedelta(days=7)

elif modo_fecha == "mes_anterior":
    hoy = datetime.today()
    primer_dia_mes_anterior = (hoy.replace(day=1) - timedelta(days=1)).replace(day=1).date()
    ultimo_dia_mes_anterior = (hoy.replace(day=1) - timedelta(days=1)).date()
    fecha_ini = primer_dia_mes_anterior
    fecha_fin = ultimo_dia_mes_anterior

elif modo_fecha == "anio_actual":
    hoy = date.today()
    fecha_ini = date(hoy.year, 1, 1)
    fecha_fin = hoy

elif modo_fecha == "anio_2024":
    fecha_ini = date(2024, 1, 1)
    fecha_fin = date(2024, 12, 31)

elif modo_fecha == "custom":
    fecha_ini = date(2026, 3, 1)
    fecha_fin = date(2026, 3, 31)

else:
    raise ValueError("[INFO] El valor de 'modo_fecha' no es válido.")

# -------------------------
# 🟢 Consulta filtrada
# -------------------------
query = f"""
SELECT *
FROM HQ_KSDEPOR.dbo.Public_VentaDetalladaRMS
WHERE [Time] >= '{fecha_ini.strftime("%Y-%m-%d")} 00:00:00'
  AND [Time] < '{(fecha_fin + timedelta(days=1)).strftime("%Y-%m-%d")} 00:00:00'
"""
print(f"[INFO] Ejecutando consulta desde {fecha_ini} hasta {fecha_fin}...")
df = pd.read_sql(query, conn)
print(f" [OK] Datos extraídos: {len(df)} filas")
conn.close()


# -------------------------
# 📄 Cargar tablas de referencia
# -------------------------
df_marcas = pd.read_csv('tabla_marcas.csv')
df_locales = pd.read_csv('tabla_locales.csv')
df_ecom = pd.read_csv('tabla_ecommerce.csv', dtype=str, keep_default_na=False)
df_origin = pd.read_csv('tabla_origin.csv', dtype=str, keep_default_na=False)
df_temporadas = pd.read_csv('tabla_temporadas.csv')
df_familias = pd.read_csv('tabla_familias.csv')

# -------------------------
# Normalización básica
# -------------------------
df['Documento'] = df['Documento'].fillna('').astype(str).str.strip()

df_origin.columns = df_origin.columns.str.strip()
df_origin['Origin'] = df_origin['Origin'].astype(str).str.strip()
df_origin['Tienda_ecom'] = df_origin['Tienda_ecom'].astype(str).str.strip()

df_ecom.columns = df_ecom.columns.str.strip()
df_ecom['Marca_Limpia'] = df_ecom['Marca_Limpia'].astype(str).str.strip()
df_ecom['Vendedor'] = df_ecom['Vendedor'].astype(str).str.strip()
df_ecom['Tienda_ecom'] = df_ecom['Tienda_ecom'].astype(str).str.strip()

# -------------------------
# Validaciones
# -------------------------
dup_origin = df_origin[df_origin.duplicated(subset=['Origin'], keep=False)].copy()
if not dup_origin.empty:
    print("[ERROR] tabla_origin.csv tiene duplicados por Origin:")
    print(dup_origin.sort_values(['Origin']).to_string(index=False))
    raise RuntimeError("Corrige tabla_origin.csv: hay más de una fila por Origin.")

dup_ecom = df_ecom[df_ecom.duplicated(subset=['Marca_Limpia', 'Vendedor'], keep=False)].copy()
if not dup_ecom.empty:
    print("[ERROR] tabla_ecommerce.csv tiene duplicados por Marca_Limpia + Vendedor:")
    print(dup_ecom.sort_values(['Marca_Limpia', 'Vendedor']).to_string(index=False))
    raise RuntimeError("Corrige tabla_ecommerce.csv antes de usarla como fallback.")

# -------------------------
# 1) Merge MARCAS
# -------------------------
df = df.merge(
    df_marcas[['Marca', 'Marca_Limpia', 'Activa']],
    on='Marca',
    how='left',
    validate='m:1'
)

# -------------------------
# 2) Merge Temporada - Familia - Locales
# -------------------------
df = df.merge(df_temporadas, on='Temporada', how='left')
df = df.merge(df_familias,   on='Familia',   how='left')

df_locales = df_locales.drop(columns=['Id_Tienda'], errors='ignore')
df = df.merge(df_locales, on='Local', how='left')

# -------------------------
# 3) Derivar Origin desde Documento
# -------------------------
df['Origin'] = df['Documento'].str[:2].str.strip()

# -------------------------
# 4) Resolver Tienda_ecom por Origin
# -------------------------
df = df.merge(
    df_origin.rename(columns={'Tienda_ecom': 'Tienda_ecom_origin'}),
    on='Origin',
    how='left',
    validate='m:1'
)

# -------------------------
# 5) Fallback por Marca_Limpia + Vendedor
# -------------------------
if 'Marca_Limpia' not in df.columns:
    raise RuntimeError("[ERROR] Falta Marca_Limpia antes del merge fallback e-commerce.")

df_ecom_fb = df_ecom.rename(columns={
    'Marca_Limpia': 'Marca_Limpia_join',
    'Tienda_ecom': 'Tienda_ecom_fallback'
})

df = df.merge(
    df_ecom_fb,
    left_on=['Marca_Limpia', 'Vendedor'],
    right_on=['Marca_Limpia_join', 'Vendedor'],
    how='left',
    validate='m:1',
    suffixes=('', '_fb')
)

df.drop(columns=['Marca_Limpia_join'], errors='ignore', inplace=True)

# -------------------------
# 6) Prioridad final
# -------------------------
df['Tienda_ecom'] = df['Tienda_ecom_origin'].combine_first(df['Tienda_ecom_fallback'])
df['Tienda_final'] = df['Tienda_ecom'].combine_first(df['Tienda'])
df.loc[df['Tienda_ecom'].notna(), 'Tipo'] = 'Ecommerce'

df['Fuente_Tienda_ecom'] = None
df.loc[df['Tienda_ecom_origin'].notna(), 'Fuente_Tienda_ecom'] = 'origin_documento'
df.loc[
    df['Tienda_ecom_origin'].isna() & df['Tienda_ecom_fallback'].notna(),
    'Fuente_Tienda_ecom'
] = 'fallback_vendedor'

print(f"[INFO] Registros con Tienda_ecom por origin: {df['Tienda_ecom_origin'].notna().sum()}")
print(f"[INFO] Registros con Tienda_ecom por fallback: {(df['Tienda_ecom_origin'].isna() & df['Tienda_ecom_fallback'].notna()).sum()}")
print(f"[INFO] Registros sin Tienda_ecom resuelta: {df['Tienda_ecom'].isna().sum()}")

# -------------------------
# [INFO] Reportar faltantes
# -------------------------
if df['Tienda_final'].isnull().any():
    print("[INFO] Existen registros sin Tienda_final asignada. Revisa posibles casos no contemplados.")

# -------------------------
# 🔌 Conexión SQL destino
# -------------------------
server = 'localhost'
database = 'Digital_Impact_Reportes'
table_name = 'Ventas_Solidez_RMH'
driver = 'ODBC Driver 17 for SQL Server'

engine = create_engine(f"mssql+pyodbc://@{server}/{database}?trusted_connection=yes&driver={driver}")

# -------------------------
# 🔄 Eliminar registros previos (por DÍA, cada día en su propia transacción)
# -------------------------

fecha_ini_str = fecha_ini.strftime('%Y-%m-%d')
fecha_fin_exclusive = fecha_fin + timedelta(days=1)

print(f"[CALENDARIO] Eliminando datos desde {fecha_ini_str} hasta {fecha_fin} en SQL (por días)...")

delete_day_sql = text(f"""
    SET NOCOUNT ON;
    DECLARE @batch INT = :batch, @rows INT = 1;
    WHILE (@rows > 0)
    BEGIN
        DELETE TOP (@batch)
        FROM {table_name}
        WHERE [Time] >= :start_dt AND [Time] < :end_dt;
        SET @rows = @@ROWCOUNT;
    END
""")

dia = fecha_ini
batch = 20000   # si el log sigue sufriendo, baja a 10k
dias_borrados = 0

while dia < fecha_fin_exclusive:
    start_dt = f"{dia.strftime('%Y-%m-%d')} 00:00:00"
    end_dt   = f"{(dia + timedelta(days=1)).strftime('%Y-%m-%d')} 00:00:00"

    # Transacción independiente por día
    with engine.begin() as conn:
        conn.execute(delete_day_sql, {"batch": batch, "start_dt": start_dt, "end_dt": end_dt})
    dias_borrados += 1
    if dias_borrados % 7 == 0:
        print(f"[LIMPIEZA] Días procesados: {dias_borrados} (hasta {dia})")

    dia += timedelta(days=1)

# Verificación sargable (sin CAST)
verif_sql = text(f"""
    SELECT COUNT(*) AS registros_restantes
    FROM {table_name}
    WHERE [Time] >= :start_all AND [Time] < :end_all
""")
verificacion = pd.read_sql(
    verif_sql, engine,
    params={
        "start_all": f"{fecha_ini.strftime('%Y-%m-%d')} 00:00:00",
        "end_all":   f"{(fecha_fin + timedelta(days=1)).strftime('%Y-%m-%d')} 00:00:00"
    }
)

restantes = int(verificacion.iloc[0]['registros_restantes'])
if restantes > 0:
    print(f"[ERROR] Quedaron {restantes} registros en el rango después del borrado. Revisa antes de insertar para evitar duplicados.")
    sys.exit(1)
else:
    print("[LIMPIEZA] Datos anteriores eliminados correctamente (0 registros restantes).")


# 🔍 Validación: distribución de fechas en el campo 'Time'
print("[CALENDARIO] Validación de fechas presentes en los datos extraídos:")
df['Fecha_Normalizada'] = pd.to_datetime(df['Time']).dt.date
conteo_fechas = df['Fecha_Normalizada'].value_counts().sort_index()
print(conteo_fechas)

# Validación de cobertura de fechas (dinámico)
fecha_minima = df['Fecha_Normalizada'].min()
anio = fecha_minima.year
mes = fecha_minima.month
_, dias_mes = calendar.monthrange(anio, mes)
fecha_esperada = date(anio, mes, dias_mes)
fecha_maxima = df['Fecha_Normalizada'].max()

if fecha_maxima >= fecha_esperada:
    print("[OK] Se extrajeron datos hasta la última fecha esperada del mes.")
else:
    print(f"[INFO] Atención: La última fecha en los datos es {fecha_maxima}, se esperaba llegar hasta {fecha_esperada}.")

# ❌ Eliminar columna auxiliar para evitar error en carga a SQL
df.drop(columns=['Fecha_Normalizada'], inplace=True)

# -------------------------
# [SYNC] Insertar nuevos datos con visibilidad
# -------------------------
chunksize = 5000
total = len(df)
print(f"[SYNC] Iniciando carga a SQL con chunks de {chunksize} registros...")

start_time = time.time()

insp = inspect(engine)
table_cols = [c['name'] for c in insp.get_columns(table_name)]

# conservar solo columnas que existen físicamente en SQL
df = df.reindex(columns=table_cols)

# reemplazar NaN por None
df = df.where(df.notna(), None)

for i in range(0, total, chunksize):
    chunk_df = df.iloc[i:i+chunksize]
    chunk_df.to_sql(table_name, con=engine, if_exists='append', index=False)
    print(f"[OK] Insertado chunk {i//chunksize + 1} ({i + len(chunk_df)}/{total})")


elapsed = time.time() - start_time
print(f"[OK] Carga completada en {elapsed:.2f} segundos.")
print(f"[TOTAL] Total de registros insertados: {total}")
