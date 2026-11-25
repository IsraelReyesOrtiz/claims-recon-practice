import os, tempfile
from pathlib import Path
import pandas as pd
import snowflake.connector

DATA = Path("data")
TABLES = ["providers", "payers", "cpt_codes", "source_claims", "sf_claims_raw",
          "portal_claims", "remittances", "bank_deposits", "idr_disputes"]

def connect():
    return snowflake.connector.connect(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        authenticator="SNOWFLAKE_JWT",
        private_key_file=os.path.expanduser(os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"]),
        role=os.environ.get("SNOWFLAKE_ROLE", "HALOMD_LOADER"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "WH_HALOMD_XS"),
        database=os.environ.get("SNOWFLAKE_DATABASE", "HALOMD_DEV"),
        schema="RAW",
    )

def main():
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute("""CREATE FILE FORMAT IF NOT EXISTS RAW.CSV_HEADER
                       TYPE = CSV PARSE_HEADER = TRUE FIELD_OPTIONALLY_ENCLOSED_BY = '"'
                       NULL_IF = ('') EMPTY_FIELD_AS_NULL = TRUE""")
        cur.execute("ALTER FILE FORMAT RAW.CSV_HEADER SET ERROR_ON_COLUMN_COUNT_MISMATCH = FALSE")
        cur.execute("CREATE STAGE IF NOT EXISTS RAW.LANDING FILE_FORMAT = RAW.CSV_HEADER")
        with tempfile.TemporaryDirectory() as tmp:
            for t in TABLES:
                T = t.upper()
                df = pd.read_csv(DATA / f"{t}.csv", dtype=str)
                df.columns = [c.upper() for c in df.columns]
                local = Path(tmp) / f"{t}.csv"; df.to_csv(local, index=False)
                cur.execute(f"PUT 'file://{local.as_posix()}' @RAW.LANDING/{t}/ AUTO_COMPRESS=TRUE OVERWRITE=TRUE")
                cur.execute(f"""CREATE OR REPLACE TABLE RAW.{T} USING TEMPLATE (
                      SELECT ARRAY_AGG(OBJECT_CONSTRUCT(*))
                      FROM TABLE(INFER_SCHEMA(LOCATION => '@RAW.LANDING/{t}/', FILE_FORMAT => 'RAW.CSV_HEADER')))""")
                cur.execute(f"ALTER TABLE RAW.{T} ADD COLUMN _SF_LOADED_AT TIMESTAMP_LTZ, _SF_FILE_NAME VARCHAR")
                res = cur.execute(f"""COPY INTO RAW.{T} FROM @RAW.LANDING/{t}/
                      MATCH_BY_COLUMN_NAME = CASE_INSENSITIVE
                      INCLUDE_METADATA = (_SF_LOADED_AT = METADATA$START_SCAN_TIME, _SF_FILE_NAME = METADATA$FILENAME)
                      ON_ERROR = 'CONTINUE'""").fetchall()
                loaded = sum(r[3] for r in res if len(r) > 3 and isinstance(r[3], int))
                errors = sum(r[5] for r in res if len(r) > 5 and isinstance(r[5], int))
                first = next((r[6] for r in res if len(r) > 6 and r[6]), "")
                print(f"RAW.{T:15s} rows_loaded={loaded:>7,}  errors={errors}  {first}")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
