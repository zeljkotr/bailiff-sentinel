#!/usr/bin/env python3
"""
One-off migration: copy batches and transactions from the old SQLite
database into PostgreSQL.

Run it from the project directory with the same environment variables as
the app (DB_HOST, DB_PASSWORD, ...), e.g. after `set -a; . ./.env; set +a`:

    python migrate_sqlite_to_postgres.py instance/paycheck_sentinel.db --dry-run
    python migrate_sqlite_to_postgres.py instance/paycheck_sentinel.db

The SQLite file is opened read-only and is never modified. The copy runs in
a single PostgreSQL transaction: either everything is copied or nothing is.
The script refuses to run if the target tables already contain data.

Developed by Zeljko Tripcevski
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from psycopg import sql

from paycheck_sentinel import db

CHUNK = 1000


def sqlite_columns(src, table):
    return [r["name"] for r in src.execute(f"PRAGMA table_info({table})")]


def pg_columns(conn, table):
    rows = conn.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table,),
    ).fetchall()
    return {r["column_name"] for r in rows}


def copy_table(src, conn, table, alias, join=""):
    """Copy all rows of `table`, using only columns present on both sides."""
    pg_cols = pg_columns(conn, table)
    cols = [c for c in sqlite_columns(src, table) if c in pg_cols]
    skipped_cols = [c for c in sqlite_columns(src, table) if c not in pg_cols]
    if skipped_cols:
        print(f"  {table}: columns not in PostgreSQL, skipped: {', '.join(skipped_cols)}")

    select = "SELECT {} FROM {} {} {} ORDER BY {}.id".format(
        ", ".join(f'{alias}."{c}"' for c in cols), table, alias, join, alias
    )
    insert = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        sql.SQL(", ").join([sql.Placeholder()] * len(cols)),
    )

    total = 0
    cursor = src.execute(select)
    with conn.cursor() as cur:
        while True:
            rows = cursor.fetchmany(CHUNK)
            if not rows:
                break
            cur.executemany(insert, [tuple(r) for r in rows])
            total += len(rows)
    return total


def reset_sequence(conn, table):
    """Point the identity sequence after the highest copied id."""
    conn.execute(
        f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
        f"COALESCE((SELECT MAX(id) FROM {table}), 1), "
        f"(SELECT MAX(id) IS NOT NULL FROM {table}))"
    )


def main():
    parser = argparse.ArgumentParser(description="Copy SQLite data into PostgreSQL.")
    parser.add_argument("sqlite_path", help="path to the old paycheck_sentinel.db")
    parser.add_argument("--dry-run", action="store_true",
                        help="do everything, then roll back instead of committing")
    args = parser.parse_args()

    path = Path(args.sqlite_path).resolve()
    if not path.is_file():
        sys.exit(f"SQLite file not found: {path}")

    src = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    src_batches = src.execute("SELECT COUNT(*) FROM batches").fetchone()[0]
    src_txns = src.execute(
        "SELECT COUNT(*) FROM transactions t JOIN batches b ON b.id = t.batch_id"
    ).fetchone()[0]
    src_txns_all = src.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    print(f"Source: {src_batches} batches, {src_txns} transactions"
          f" ({src_txns_all - src_txns} orphaned rows will be skipped)")

    db.init_db()  # creates the schema if it does not exist yet

    with db.get_db() as conn:  # commits on success, rolls back on any exception
        existing = conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"]
        if existing:
            sys.exit(f"Target already has {existing} batches. Refusing to continue.")

        n_batches = copy_table(src, conn, "batches", "b")
        n_txns = copy_table(src, conn, "transactions", "t",
                            join="JOIN batches b ON b.id = t.batch_id")

        for table in ("batches", "transactions"):
            reset_sequence(conn, table)

        pg_batches = conn.execute("SELECT COUNT(*) AS n FROM batches").fetchone()["n"]
        pg_txns = conn.execute("SELECT COUNT(*) AS n FROM transactions").fetchone()["n"]
        print(f"Copied: {n_batches} batches, {n_txns} transactions")
        print(f"Target: {pg_batches} batches, {pg_txns} transactions")

        if (n_batches, n_txns) != (src_batches, src_txns) or (pg_batches, pg_txns) != (src_batches, src_txns):
            sys.exit("Row counts do not match. Nothing was committed.")

        if args.dry_run:
            conn.rollback()
            print("Dry run OK: counts match, rolled back (nothing was written).")
        else:
            print("Counts match. Committing.")

    src.close()


if __name__ == "__main__":
    main()
