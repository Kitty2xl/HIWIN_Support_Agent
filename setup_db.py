#!/usr/bin/env python3
"""setup_db.py - create / verify the Postgres side of the HIWIN Support Agent.

Idempotent: safe to run again at any time. It reads the target from `.env`
(DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT, DB_SCHEMA, CHAT_LOG_SCHEMA) and:

  1. connects to the maintenance database (`postgres`) and reports the server
     version and whether the pgvector extension is AVAILABLE on this server
     (if it is not, prints how to install it for your OS and stops);
  2. creates the database if it does not exist;
  3. inside it: CREATE EXTENSION IF NOT EXISTS vector, and the two schemas
     (DB_SCHEMA for the RAG tables, CHAT_LOG_SCHEMA for the chat log);
  4. optionally (--create-role) creates DB_USER with DB_PASSWORD and grants it
     the database + schemas, for installs where the app should not run as the
     superuser.

    python setup_db.py                                  # uses .env; connects as DB_USER
    python setup_db.py --admin-user postgres --admin-password 'xxx'
    python setup_db.py --dry-run                        # print the plan, change nothing
    python setup_db.py --create-role                    # also create/grant DB_USER

Works against PostgreSQL 13+ (pgvector 0.8 requires 13+; 12 works with pgvector
<= 0.7). If several PostgreSQL versions are installed on the machine, make sure
DB_PORT in `.env` points at the cluster you intend to use (each cluster has its
own port, 5432 / 5433 / ...).
"""

import argparse
import getpass
import platform
import sys

import psycopg2

# Console-safe output on every OS (a Windows console/redirect with a legacy code page
# such as cp950 would otherwise raise UnicodeEncodeError on non-ASCII text).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
from psycopg2 import sql

import config


def pgvector_install_hint(major: int) -> str:
    system = platform.system()
    lines = [
        "pgvector is a SERVER-side extension - it must be installed into PostgreSQL itself (pip cannot do it):",
    ]
    if system == "Windows":
        lines += [
            "  * Windows: there are no official binaries. Either build it (Visual Studio Build Tools + nmake, see",
            "    https://github.com/pgvector/pgvector#windows), or copy `lib\\vector.dll` and",
            f"    `share\\extension\\vector*` from another PostgreSQL {major} install that already has it",
            f"    into C:\\Program Files\\PostgreSQL\\{major}\\ (same MAJOR version only), then restart the service.",
        ]
    else:
        lines += [
            f"  * Debian/Ubuntu (PGDG apt repo):  sudo apt install postgresql-{major}-pgvector",
            f"  * RHEL/Rocky (PGDG yum repo):     sudo dnf install pgvector_{major}",
            "  * From source:                    git clone --branch v0.8.1 https://github.com/pgvector/pgvector && cd pgvector && make && sudo make install",
            f"  * Docker:                         image pgvector/pgvector:pg{major} has it pre-installed",
        ]
    lines.append("Then re-run:  python setup_db.py")
    return "\n".join(lines)


def connect(dbname, user, password, host, port, sslmode):
    conn = psycopg2.connect(dbname=dbname, user=user, password=password, host=host,
                            port=port, sslmode=sslmode, connect_timeout=10)
    conn.autocommit = True  # CREATE DATABASE cannot run inside a transaction
    return conn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--admin-user", default=None,
                    help="superuser to connect as (default: DB_USER from .env)")
    ap.add_argument("--admin-password", default=None,
                    help="its password (default: DB_PASSWORD from .env; '-' to prompt)")
    ap.add_argument("--admin-db", default="postgres",
                    help="maintenance database to connect to first (default: postgres)")
    ap.add_argument("--create-role", action="store_true",
                    help="create DB_USER (with DB_PASSWORD) if missing and grant it the DB + schemas")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; change nothing")
    args = ap.parse_args()

    admin_user = args.admin_user or config.DB_USER
    admin_pw = args.admin_password
    if admin_pw == "-":
        admin_pw = getpass.getpass(f"Password for {admin_user}: ")
    elif admin_pw is None:
        admin_pw = config.DB_PASSWORD if admin_user == config.DB_USER else ""
        if not admin_pw:
            admin_pw = getpass.getpass(f"Password for {admin_user}: ")

    target = dict(db=config.DB_NAME, user=config.DB_USER, host=config.DB_HOST,
                  port=config.DB_PORT, schema=config.DB_SCHEMA, log=config.CHAT_LOG_SCHEMA)
    print(f"Target: postgresql://{target['user']}@{target['host']}:{target['port']}/{target['db']}"
          f"   schemas: {target['schema']} (RAG data), {target['log']} (chat log)")
    if args.dry_run:
        print("(dry run - nothing will be changed)")

    # 1. maintenance connection -------------------------------------------------
    try:
        admin = connect(args.admin_db, admin_user, admin_pw, config.DB_HOST, config.DB_PORT,
                        config.DB_SSLMODE)
    except Exception as e:
        msg = str(e).strip()
        print(f"\nFAIL: cannot connect to {config.DB_HOST}:{config.DB_PORT}/{args.admin_db} as {admin_user}:\n  {msg}")
        if "password authentication failed" in msg:
            print("  -> wrong password for this cluster. If more than one PostgreSQL is installed, check that")
            print("     DB_PORT points at the one you mean (each version listens on its own port).")
        elif "refused" in msg or "could not connect" in msg or "timeout" in msg:
            print("  -> PostgreSQL is not running on that host/port. Windows: services.msc -> postgresql-x64-NN;")
            print("     Linux: sudo systemctl start postgresql. Or fix DB_HOST / DB_PORT in .env.")
        return 1

    with admin.cursor() as cur:
        cur.execute("SHOW server_version_num")
        vnum = int(cur.fetchone()[0])
        major = vnum // 10000
        cur.execute("SELECT version()")
        print(f"\nServer: {cur.fetchone()[0].split(',')[0]}")
        if major < 12:
            print(f"FAIL: PostgreSQL {major} is too old - pgvector needs 12+ (0.8.x needs 13+). Install a newer PostgreSQL.")
            return 1
        if major == 12:
            print("WARN: PostgreSQL 12 works only with pgvector <= 0.7.x; 13+ is recommended.")

        cur.execute("SELECT default_version FROM pg_available_extensions WHERE name = 'vector'")
        row = cur.fetchone()
        if not row:
            print("FAIL: the `vector` extension is NOT available on this server.\n")
            print(pgvector_install_hint(major))
            return 2
        print(f"pgvector available: {row[0]}")

        # 2. database ---------------------------------------------------------------
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (config.DB_NAME,))
        if cur.fetchone():
            print(f"database {config.DB_NAME}: exists")
        else:
            print(f"database {config.DB_NAME}: creating")
            if not args.dry_run:
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(config.DB_NAME)))

        # 4a. role (optional) -------------------------------------------------------
        if args.create_role and config.DB_USER != admin_user:
            cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (config.DB_USER,))
            if cur.fetchone():
                print(f"role {config.DB_USER}: exists")
            else:
                print(f"role {config.DB_USER}: creating (LOGIN, password from .env)")
                if not args.dry_run:
                    cur.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s")
                                .format(sql.Identifier(config.DB_USER)), (config.DB_PASSWORD,))
            if not args.dry_run:
                cur.execute(sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}")
                            .format(sql.Identifier(config.DB_NAME), sql.Identifier(config.DB_USER)))
    admin.close()

    if args.dry_run and not _db_exists_now(admin_user, admin_pw, args):
        print("\n(dry run) would then: CREATE EXTENSION vector; CREATE SCHEMA "
              f"{config.DB_SCHEMA}; CREATE SCHEMA {config.CHAT_LOG_SCHEMA}")
        return 0

    # 3. inside the target database ------------------------------------------------
    try:
        db = connect(config.DB_NAME, admin_user, admin_pw, config.DB_HOST, config.DB_PORT,
                     config.DB_SSLMODE)
    except Exception as e:
        print(f"\nFAIL: cannot connect to database {config.DB_NAME}: {e}")
        return 1
    with db.cursor() as cur:
        cur.execute("SELECT installed_version FROM pg_available_extensions WHERE name='vector'")
        inst = cur.fetchone()[0]
        if inst:
            print(f"extension vector in {config.DB_NAME}: installed ({inst})")
        else:
            print(f"extension vector in {config.DB_NAME}: creating (needs superuser or CREATE on the database)")
            if not args.dry_run:
                try:
                    cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                except Exception as e:
                    print(f"FAIL: CREATE EXTENSION vector failed: {e}\n  -> re-run as a superuser: "
                          f"python setup_db.py --admin-user postgres --admin-password -")
                    return 1
        for schema in (config.DB_SCHEMA, config.CHAT_LOG_SCHEMA):
            cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (schema,))
            state = "exists" if cur.fetchone() else "creating"
            print(f"schema {schema}: {state}")
            if state == "creating" and not args.dry_run:
                cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema)))
            if args.create_role and config.DB_USER != admin_user and not args.dry_run:
                cur.execute(sql.SQL("GRANT ALL ON SCHEMA {} TO {}")
                            .format(sql.Identifier(schema), sql.Identifier(config.DB_USER)))
                cur.execute(sql.SQL("GRANT ALL ON ALL TABLES IN SCHEMA {} TO {}")
                            .format(sql.Identifier(schema), sql.Identifier(config.DB_USER)))
        cur.execute("SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = %s AND table_name LIKE 'data\\_%%'", (config.DB_SCHEMA,))
        n = cur.fetchone()[0]
    db.close()

    print(f"\nDone. {config.DB_SCHEMA} holds {n} data_* table(s)."
          + ("" if n else "  The database is EMPTY: run the pipeline (pipeline/gui.py, tui.py or "
                          "Pipeline.py) or restore a dump (see README 'Moving the database')."))
    print("Next: python doctor.py")
    return 0


def _db_exists_now(admin_user, admin_pw, args) -> bool:
    try:
        c = connect(args.admin_db, admin_user, admin_pw, config.DB_HOST, config.DB_PORT,
                    config.DB_SSLMODE)
        with c.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (config.DB_NAME,))
            ok = cur.fetchone() is not None
        c.close()
        return ok
    except Exception:
        return False


if __name__ == "__main__":
    sys.exit(main())
