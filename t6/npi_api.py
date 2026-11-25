import time
import requests
import pandas as pd

URL = "https://npiregistry.cms.hhs.gov/api/"


def get_with_retry(params, retries=4, backoff=1):
    for i in range(retries):
        try:
            r = requests.get(URL, params=params, timeout=30)
        except requests.RequestException as e:
            wait = backoff * 2 ** i
            print(f"error de red: {e} -> reintento en {wait}s"); time.sleep(wait); continue
        if r.status_code == 200:
            return r.json()
        if r.status_code in (429, 500, 502, 503, 504):        # errores temporales
            wait = backoff * 2 ** i
            print(f"HTTP {r.status_code} -> reintento en {wait}s"); time.sleep(wait); continue
        r.raise_for_status()                                   # errores permanentes: fallar rápido
    raise RuntimeError(f"La API no respondió tras {retries} intentos")


def fetch_providers(state="TX", taxonomy="Emergency Medicine", page_size=200, max_rows=600):
    rows, skip = [], 0
    while skip < max_rows:
        data = get_with_retry({"version": "2.1", "state": state, "taxonomy_description": taxonomy,
                               "enumeration_type": "NPI-2", "limit": page_size, "skip": skip})
        batch = data.get("results", [])
        print(f"skip={skip:>4} -> {len(batch)} filas")
        rows += batch
        if len(batch) < page_size:
            break
        skip += page_size
    df = pd.json_normalize(rows)                               # aplana el JSON anidado
    return df[["number", "basic.organization_name", "basic.status", "enumeration_type"]]


if __name__ == "__main__":
    df = fetch_providers()
    print(df.head())
    print("filas:", len(df), "| NPIs únicos:", df["number"].is_unique)
