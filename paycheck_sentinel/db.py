"""
db.py — SQLite sloj za paycheck-sentinel.

Baza se cuva u instance/paycheck_sentinel.db (Flask instance folder,
automatski se ne commit-uje na git zahvaljujuci .gitignore).

Developed by Zeljko Tripcevski
"""

import json
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    created_at TEXT NOT NULL,
    file_names TEXT NOT NULL,       -- JSON lista imena ucitanih fajlova
    columns_json TEXT NOT NULL,     -- JSON lista detektovanih kolona
    row_count INTEGER NOT NULL,
    own_account TEXT,               -- racun vlasnika izvoda (iBank format), ako je prepoznat

    mode TEXT DEFAULT 'generic',    -- 'generic' (plaćeno/vraćeno kolone) ili 'bank_statement' (debit/credit)

    paid_col TEXT,
    returned_col TEXT,
    id_col TEXT,
    debtor_col TEXT,
    date_col TEXT,
    tolerance REAL DEFAULT 0,
    outlier_multiplier REAL DEFAULT 5,
    check_full INTEGER DEFAULT 1,
    check_partial INTEGER DEFAULT 1,
    check_dupid INTEGER DEFAULT 0,
    check_duppay INTEGER DEFAULT 0,
    check_outlier INTEGER DEFAULT 0,

    amount_col TEXT,
    benefit_col TEXT,
    ref_col TEXT,
    debit_value TEXT DEFAULT 'debit',
    credit_value TEXT DEFAULT 'credit',
    max_days_gap REAL DEFAULT 30,
    require_refnumber INTEGER DEFAULT 0,

    analyzed INTEGER DEFAULT 0,
    flagged_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id INTEGER NOT NULL,
    source_file TEXT,
    row_index INTEGER NOT NULL,
    raw_json TEXT NOT NULL,
    paid_amount REAL,
    returned_amount REAL,
    flags_json TEXT DEFAULT '[]',
    FOREIGN KEY(batch_id) REFERENCES batches(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_transactions_batch ON transactions(batch_id);
"""


EXPECTED_BATCH_COLUMNS = {
    "own_account": "TEXT",
    "mode": "TEXT DEFAULT 'generic'",
    "paid_col": "TEXT",
    "returned_col": "TEXT",
    "id_col": "TEXT",
    "debtor_col": "TEXT",
    "date_col": "TEXT",
    "tolerance": "REAL DEFAULT 0",
    "outlier_multiplier": "REAL DEFAULT 5",
    "check_full": "INTEGER DEFAULT 1",
    "check_partial": "INTEGER DEFAULT 1",
    "check_dupid": "INTEGER DEFAULT 0",
    "check_duppay": "INTEGER DEFAULT 0",
    "check_outlier": "INTEGER DEFAULT 0",
    "amount_col": "TEXT",
    "benefit_col": "TEXT",
    "ref_col": "TEXT",
    "debit_value": "TEXT DEFAULT 'debit'",
    "credit_value": "TEXT DEFAULT 'credit'",
    "max_days_gap": "REAL DEFAULT 30",
    "require_refnumber": "INTEGER DEFAULT 0",
    "analyzed": "INTEGER DEFAULT 0",
    "flagged_count": "INTEGER DEFAULT 0",
}


def _migrate_batches_table(conn):
    """Dodaje nedostajuce kolone u vec postojecu 'batches' tabelu (stare baze
    napravljene pre nego sto su dodate nove kolone, npr. own_account)."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(batches)").fetchall()}
    for col_name, col_def in EXPECTED_BATCH_COLUMNS.items():
        if col_name not in existing:
            conn.execute(f"ALTER TABLE batches ADD COLUMN {col_name} {col_def}")
    conn.commit()


def get_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path):
    conn = get_db(db_path)
    conn.executescript(SCHEMA)
    conn.commit()
    _migrate_batches_table(conn)
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create_batch(conn, label, file_names, columns, rows, own_account=None):
    cur = conn.execute(
        """INSERT INTO batches (label, created_at, file_names, columns_json, row_count, own_account)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (label, now_iso(), json.dumps(file_names), json.dumps(columns), len(rows), own_account),
    )
    batch_id = cur.lastrowid

    conn.executemany(
        """INSERT INTO transactions (batch_id, source_file, row_index, raw_json)
           VALUES (?, ?, ?, ?)""",
        [
            (batch_id, r.get("__source_file", ""), i, json.dumps(r, ensure_ascii=False))
            for i, r in enumerate(rows)
        ],
    )
    conn.commit()
    return batch_id


def list_batches(conn):
    rows = conn.execute(
        "SELECT * FROM batches ORDER BY id DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def get_batch(conn, batch_id):
    row = conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()
    return dict(row) if row else None


def delete_batch(conn, batch_id):
    conn.execute("DELETE FROM batches WHERE id = ?", (batch_id,))
    conn.commit()


def get_transactions(conn, batch_id):
    rows = conn.execute(
        "SELECT * FROM transactions WHERE batch_id = ? ORDER BY row_index", (batch_id,)
    ).fetchall()
    result = []
    for r in rows:
        raw = json.loads(r["raw_json"])
        result.append({
            "idx": r["row_index"],
            "raw": raw,
            "paid_amount": r["paid_amount"],
            "returned_amount": r["returned_amount"],
            "flags": json.loads(r["flags_json"]),
        })
    return result


def save_bank_analysis(conn, batch_id, mapping, options, analyzed_txns):
    conn.execute(
        """UPDATE batches SET
             mode='bank_statement',
             amount_col=?, benefit_col=?, ref_col=?, date_col=?,
             debit_value=?, credit_value=?, max_days_gap=?, require_refnumber=?,
             analyzed=1, flagged_count=?
           WHERE id=?""",
        (
            mapping.get("amount_col"), mapping.get("benefit_col"),
            mapping.get("ref_col"), mapping.get("date_col"),
            options.get("debit_value", "debit"), options.get("credit_value", "credit"),
            options.get("max_days_gap", 30), int(bool(options.get("require_refnumber"))),
            sum(1 for t in analyzed_txns if t["flags"]),
            batch_id,
        ),
    )

    for t in analyzed_txns:
        conn.execute(
            """UPDATE transactions SET paid_amount=?, flags_json=?
               WHERE batch_id=? AND row_index=?""",
            (
                t["amount"],
                json.dumps(t["flags"], ensure_ascii=False),
                batch_id, t["idx"],
            ),
        )
    conn.commit()


def save_analysis(conn, batch_id, mapping, options, analyzed_txns):
    conn.execute(
        """UPDATE batches SET
             paid_col=?, returned_col=?, id_col=?, debtor_col=?, date_col=?,
             tolerance=?, outlier_multiplier=?,
             check_full=?, check_partial=?, check_dupid=?, check_duppay=?, check_outlier=?,
             analyzed=1, flagged_count=?
           WHERE id=?""",
        (
            mapping.get("paid_col"), mapping.get("returned_col"), mapping.get("id_col"),
            mapping.get("debtor_col"), mapping.get("date_col"),
            options.get("tolerance", 0), options.get("outlier_multiplier", 5),
            int(bool(options.get("check_full"))),
            int(bool(options.get("check_partial"))),
            int(bool(options.get("check_dupid"))),
            int(bool(options.get("check_duppay"))),
            int(bool(options.get("check_outlier"))),
            sum(1 for t in analyzed_txns if t["flags"]),
            batch_id,
        ),
    )

    for t in analyzed_txns:
        conn.execute(
            """UPDATE transactions SET paid_amount=?, returned_amount=?, flags_json=?
               WHERE batch_id=? AND row_index=?""",
            (
                t["paid_amount"], t["returned_amount"],
                json.dumps(t["flags"], ensure_ascii=False),
                batch_id, t["idx"],
            ),
        )
    conn.commit()
