"""Copy a SQLite workspace into an empty PostgreSQL database without deleting source data."""
import argparse
import sqlite3
from contextlib import closing
from pathlib import Path

import server

ORDER = ["settings", "users", "sessions", "products", "customers", "accounts", "rates", "batches", "documents", "lines", "payments", "movements", "journals", "events", "audit", "assets", "tills", "imports", "auth_throttle"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_file", type=Path)
    args = parser.parse_args()
    if not server.POSTGRES:
        raise SystemExit("Set DATABASE_URL to the destination PostgreSQL connection string first.")
    source = args.sqlite_file.resolve()
    if not source.is_file():
        raise SystemExit("SQLite source file was not found.")
    with closing(sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)) as src:
        src.row_factory = sqlite3.Row
        if src.execute("PRAGMA integrity_check").fetchone()[0] != "ok" or src.execute("PRAGMA foreign_key_check").fetchall():
            raise SystemExit("SQLite integrity or foreign key checks failed; source was not copied.")
        if "users" in {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type='table'")} and src.execute("SELECT 1 FROM users WHERE lower(email) LIKE '%@medledger.local' LIMIT 1").fetchone():
            raise SystemExit("Demo accounts were found in the source. Clean/reconcile the workspace before production migration.")
        if not src.execute("SELECT 1 FROM settings WHERE key='demo_data_cleared'").fetchone():
            raise SystemExit("The SQLite source is not marked as demo-free. Verify and clean it with the approved data-maintenance process before migration.")
        server.init(seed_demo=False)
        with closing(server.connect()) as dst, dst:
            occupied = [table for table in ORDER if dst.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()]
            if occupied:
                raise SystemExit("Destination must be empty. Existing records in: " + ", ".join(occupied))
            expected = {}
            for table in ORDER:
                source_tables = {row[0] for row in src.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if table not in source_tables:
                    continue
                rows = src.execute(f'SELECT * FROM "{table}"').fetchall()
                expected[table] = len(rows)
                if table == "documents":
                    pending = {row["id"]: row for row in rows}
                    ordered = []
                    while pending:
                        ready = [row for row in pending.values() if not row["parent_id"] or row["parent_id"] not in pending]
                        if not ready:
                            raise SystemExit("Document parent relationship cycle found in source.")
                        for row in ready:
                            ordered.append(row)
                            del pending[row["id"]]
                    rows = ordered
                target_columns = {row["column_name"] for row in dst.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=?", (table,)).fetchall()}
                for row in rows:
                    columns = [key for key in row.keys() if key in target_columns]
                    marks = ",".join("?" for _ in columns)
                    names = ",".join('"' + name + '"' for name in columns)
                    dst.execute(f'INSERT INTO "{table}" ({names}) VALUES ({marks})', tuple(row[name] for name in columns))
            mismatches = []
            for table, count in expected.items():
                actual = dst.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                if actual != count:
                    mismatches.append(f"{table}: source {count}, destination {actual}")
            unbalanced = dst.execute("SELECT count(*) FROM (SELECT source_id FROM journals GROUP BY source_id HAVING sum(debit)<>sum(credit)) q").fetchone()[0]
            if mismatches or unbalanced:
                raise SystemExit("Migration verification failed: " + "; ".join(mismatches + ([f"{unbalanced} unbalanced journal sources"] if unbalanced else [])))
    print("Migration committed and verified. The SQLite source was not changed.")
    for table, count in expected.items():
        print(f"{table}: {count}")


if __name__ == "__main__":
    main()
