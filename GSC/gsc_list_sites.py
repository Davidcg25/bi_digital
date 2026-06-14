# -*- coding: utf-8 -*-
"""Probe: lista los sitios de Search Console accesibles por el service account.
Valida la conexión GSC y revela los siteUrl exactos para mapear a cada marca."""
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build

CREDS = Path(__file__).resolve().parent.parent / "GA4" / "credenciales.json"
SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]

creds = service_account.Credentials.from_service_account_file(str(CREDS), scopes=SCOPES)
svc = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
sites = svc.sites().list().execute().get("siteEntry", [])
print(f"Sitios accesibles: {len(sites)}")
for s in sorted(sites, key=lambda x: x["siteUrl"]):
    print(f"  {s['permissionLevel']:18} {s['siteUrl']}")
