"""Read-only peek at the `metadata_` jsonb column, to see what fields are
available for building structured citations. Run on the server:

    python inspect_metadata.py
"""

import json

import config
import db

TABLES = ["data_linear_guideway", "data_certificates", "data_all_products"]


def main():
    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            for table in TABLES:
                print(f"\n=== {config.DB_SCHEMA}.{table} ===")
                try:
                    cur.execute(
                        f"SELECT metadata_ FROM {config.DB_SCHEMA}.{table} LIMIT 2"
                    )
                    rows = cur.fetchall()
                    if not rows:
                        print("(no rows)")
                        continue
                    for (meta,) in rows:
                        # psycopg2 returns jsonb as a dict; guard for str just in case.
                        print(
                            meta
                            if isinstance(meta, str)
                            else json.dumps(meta, ensure_ascii=False, indent=2)
                        )
                except Exception as e:
                    conn.rollback()
                    print(f"(error: {e})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
