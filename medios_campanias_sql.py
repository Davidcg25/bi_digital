
import os
import pandas as pd
import pyodbc
from sqlalchemy import create_engine, text
from google.oauth2 import service_account
from googleapiclient.discovery import build
from datetime import datetime, timedelta, date
from googleapiclient.errors import HttpError

# Configuración de credenciales de GA4
SCOPES = ['https://www.googleapis.com/auth/analytics.readonly']
KEY_FILE_LOCATION = 'credenciales.json'
credentials = service_account.Credentials.from_service_account_file(KEY_FILE_LOCATION, scopes=SCOPES)
analytics = build('analyticsdata', 'v1beta', credentials=credentials)

# Configuración SQL Server
server = 'localhost'
database = 'Digital_Impact_Reportes'
table_name = 'Campañas_GA4'
driver = 'ODBC Driver 17 for SQL Server'
engine = create_engine(f"mssql+pyodbc://@{server}/{database}?trusted_connection=yes&driver={driver}")

# Propiedades GA4
property_info = {
    '338208380': 'Caterpillar',
    '287142051': 'Coliseum',
    '407838284': 'Converse',
    '304627263': 'Merrell',
    '427321367': 'New Balance',
    '293692998': 'Steve Madden',
    '495902890': 'Umbro',
    '513757079': 'Fila'
}

def generate_date_ranges(start_date, end_date):
    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d")
    end_date_obj = datetime.strptime(end_date, "%Y-%m-%d")
    date_ranges = []
    while start_date_obj < end_date_obj:
        range_end = start_date_obj + timedelta(days=90)
        if range_end > end_date_obj:
            range_end = end_date_obj
        date_ranges.append({
            "startDate": start_date_obj.strftime("%Y-%m-%d"),
            "endDate": range_end.strftime("%Y-%m-%d")
        })
        start_date_obj = range_end + timedelta(days=1)
    return date_ranges

def run_report(property_id, start_date, end_date, next_page_token=None):
    try:
        end_date = min(end_date, datetime.today().strftime("%Y-%m-%d"))
        request_body = {
            "dimensions": [
                {"name": "date"},
                {"name": "sessionCampaignName"},
                {"name": "sessionDefaultChannelGroup"},
                {"name": "sessionSource"},
                {"name": "sessionMedium"}
            ],
            "metrics": [
                {"name": "sessions"},
                {"name": "totalRevenue"},
                {"name": "transactions"},
                {"name": "averageSessionDuration"},
                {"name": "screenPageViewsPerSession"},
                {"name": "engagedSessions"},
                {"name": "userEngagementDuration"}
            ],
            "dateRanges": [{"startDate": start_date, "endDate": end_date}]
        }

        if next_page_token:
            request_body["pageSize"] = 5000
            request_body["pageToken"] = next_page_token

        request = analytics.properties().runReport(
            property=f'properties/{property_id}',
            body=request_body
        )
        return request.execute()
    except HttpError as err:
        print(f"Error durante la consulta: {err}")
    except Exception as e:
        print(f"Error inesperado: {e}")

def fetch_all_data(property_id, date_ranges):
    all_data = []
    for date_range in date_ranges:
        next_page_token = None
        while True:
            response = run_report(property_id, date_range['startDate'], date_range['endDate'], next_page_token)
            if response and 'rows' in response:
                all_data.extend(response['rows'])
            next_page_token = response.get('nextPageToken')
            if not next_page_token:
                break
    return all_data

# ============================
# Mes anterior completo + mes actual hasta hoy
# ============================
today = date.today()
first_of_current = today.replace(day=1)

# Mes anterior
last_of_prev = first_of_current - timedelta(days=1)
first_of_prev = last_of_prev.replace(day=1)

# Setea los límites para generate_date_ranges (formato YYYY-MM-DD)
start_date = first_of_prev.strftime("%Y-%m-%d")  # p.ej. 2025-07-01
end_date = today.strftime("%Y-%m-%d")            # p.ej. 2025-08-12

date_ranges = generate_date_ranges(start_date, end_date)

print(f"[SEARCH] Rango consolidado: {start_date} -> {end_date}")
print(f"[SEARCH] Total de meses a procesar: {len(date_ranges)}")

# Extracción
all_data = []
for property_id, property_name in property_info.items():
    print(f"[SEARCH] Consultando {property_name} (ID: {property_id})")
    data = fetch_all_data(property_id, date_ranges)
    for row in data:
        date = row['dimensionValues'][0]['value']
        year, month, day = date[:4], date[4:6], date[6:8]
        formatted_date = f"{day}-{month}-{year}"
        sessions = int(row['metricValues'][0]['value'])
        engaged = int(row['metricValues'][5]['value'])
        engagement_rate = (engaged / sessions) * 100 if sessions > 0 else 0
        all_data.append({
            'año': int(year),
            'mes': int(month),
            'dia': int(day),
            'fecha': formatted_date,
            'property_id': int(property_id),
            'property_name': property_name,
            'session_campaign': row['dimensionValues'][1]['value'],
            'session_default_channel_group': row['dimensionValues'][2]['value'],
            'session_source': row['dimensionValues'][3]['value'],
            'session_medium': row['dimensionValues'][4]['value'],
            'sessions': sessions,
            'total_revenue': float(row['metricValues'][1]['value']),
            'transactions': int(row['metricValues'][2]['value']),
            'average_session_duration': float(row['metricValues'][3]['value']),
            'screen_page_views_per_session': float(row['metricValues'][4]['value']),
            'engaged_sessions': engaged,
            'user_engagement_duration': float(row['metricValues'][6]['value']),
            'engagement_rate': engagement_rate
        })

# Crear DataFrame
df = pd.DataFrame(all_data)
df['fecha'] = pd.to_datetime(df['fecha'], dayfirst=True).dt.date

# Leer equivalencias desde SQL Server
query_equivalencias = "SELECT session_default_channel_group, canal, responsable FROM dbo.Equivalencias_Canales"
equivalencias_df = pd.read_sql(query_equivalencias, con=engine)
df['session_default_channel_group'] = df['session_default_channel_group'].str.strip().str.lower()
equivalencias_df['session_default_channel_group'] = equivalencias_df['session_default_channel_group'].str.strip().str.lower()
df = df.merge(equivalencias_df, on='session_default_channel_group', how='left')

# Eliminar datos previos para las combinaciones property_id + año + mes
with engine.begin() as conn:
    ids_meses = df[['property_id', 'año', 'mes']].drop_duplicates()
    for _, row in ids_meses.iterrows():
        conn.execute(
            text(f"DELETE FROM {table_name} WHERE property_id = :pid AND año = :anio AND mes = :mes"),
            {
                "pid": int(row.property_id),
                "anio": int(row.año),
                "mes": int(row.mes)
            }
        )
    print("[CLEAN] Datos anteriores eliminados por combinación property_id + año + mes.")

# Cargar datos nuevos
try:
    df.to_sql(table_name, con=engine, if_exists='append', index=False)
    print(f"[OK] Carga completada con éxito en {table_name}")
    print(f"[INSERT] Total de registros insertados: {len(df)}")
except Exception as e:
    print("[ERROR] Error al insertar los datos en SQL Server:")
    print(e)

