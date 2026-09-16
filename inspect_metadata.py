"""Read-only peek at the ingested data: every `data_*` table in DB_SCHEMA with its
row count, the language codes it holds, and a sample `metadata_` row - the fields
the backend turns into citations.

    python inspect_metadata.py            # summary of every table
    python inspect_metadata.py --sample   # also print one metadata_ row per table

(`python doctor.py` runs the same checks plus the inference-server / config ones.)
"""

import argparse
import json

import config
import db

import sys

# Console-safe output on every OS (a Windows console/redirect with a legacy code page
# such as cp950 would otherwise raise UnicodeEncodeError on non-ASCII text).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sample", action="store_true", help="print one metadata_ row per table")
    args = ap.parse_args()

    conn = db.get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(db.SQL_LIST_DATA_TABLES, (config.DB_SCHEMA,))
            tables = sorted(r[0] for r in cur.fetchall())
            if not tables:
                print(f"No data_* tables in schema {config.DB_SCHEMA!r} - run the pipeline first.")
                return
            print(f"{config.DB_SCHEMA}: {len(tables)} data_* table(s)\n")
            print(f"{'table':<44}{'rows':>8}  language_code values")
            for table in tables:
                cur.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema=%s AND table_name=%s",
                    (config.DB_SCHEMA, table),
                )
                cols = {r[0] for r in cur.fetchall()}
                if "metadata_" not in cols:
                    cur.execute(f"SELECT count(*) FROM {config.DB_SCHEMA}.{table}")
                    print(f"{table:<44}{cur.fetchone()[0]:>8}  (plain table, no metadata_/embedding)")
                    continue
                cur.execute(
                    f"SELECT count(*), array_agg(DISTINCT metadata_->>'language_code') "
                    f"FROM {config.DB_SCHEMA}.{table}"
                )
                n, langs = cur.fetchone()
                langs = sorted(str(x) for x in (langs or []))
                print(f"{table:<44}{n:>8}  {', '.join(langs) or '-'}")
                if args.sample and n:
                    cur.execute(f"SELECT metadata_ FROM {config.DB_SCHEMA}.{table} LIMIT 1")
                    (meta,) = cur.fetchone()
                    print("    " + (meta if isinstance(meta, str)
                                    else json.dumps(meta, ensure_ascii=False)))
            print(f"\nBackend skills exist for: {', '.join(config.LANGUAGE_SKILL_MAP)} "
                  f"(rows with any other language_code are never searched).")
    finally:
        db.release_connection(conn)


if __name__ == "__main__":
    main()
