from google.oauth2 import service_account
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import RunReportRequest, DateRange, Metric

SCOPES = ["https://www.googleapis.com/auth/analytics.readonly"]
KEY_PATH = r"credenciales.json"
PROPERTY_ID = "495902890"

creds = service_account.Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
print("SERVICE ACCOUNT:", creds.service_account_email)

client = BetaAnalyticsDataClient(credentials=creds)

request = RunReportRequest(
    property=f"properties/{PROPERTY_ID}",
    dimensions=[],
    metrics=[Metric(name="sessions")],
    date_ranges=[DateRange(start_date="2026-04-01", end_date="2026-04-02")]
)

response = client.run_report(request)
print("OK:", response.row_count)