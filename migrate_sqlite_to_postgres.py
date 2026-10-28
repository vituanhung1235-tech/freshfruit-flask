"""Copy the local SQLite store into an empty PostgreSQL database."""

import os
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from database import (
    DATABASE_URL,
    ID_TABLES,
    connect_postgres,
    initialize_postgres_schema,
    sqlite_database_path,
)


TABLE_ORDER = (
    'users',
    'products',
    'coupons',
    'orders',
    'cart_items',
    'order_items',
    'reviews',
    'password_reset_tokens',
)


def migrate(source_path, database_url):
    source_path = Path(source_path)
    if not source_path.is_file():
        raise FileNotFoundError(f'SQLite source database not found: {source_path}')

    source = sqlite3.connect(source_path)
    source.row_factory = sqlite3.Row
    target = connect_postgres(database_url)
    copied = {}
    try:
        initialize_postgres_schema(target)
        occupied = []
        for table in TABLE_ORDER:
            count = target.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if count:
                occupied.append(table)
        if occupied:
            raise RuntimeError(
                'Target database is not empty. No data was copied. '
                f'Existing tables with rows: {", ".join(occupied)}'
            )

        source_tables = {
            row['name']
            for row in source.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        for table in TABLE_ORDER:
            if table not in source_tables:
                continue
            column_names = [
                row['name']
                for row in source.execute(f'PRAGMA table_info("{table}")')
            ]
            if not column_names:
                continue
            columns = ', '.join(f'"{name}"' for name in column_names)
            placeholders = ', '.join('%s' for _ in column_names)
            rows = source.execute(f'SELECT {columns} FROM "{table}"').fetchall()
            for row in rows:
                values = tuple(row[name] for name in column_names)
                target.execute(
                    f'INSERT INTO "{table}" ({columns}) VALUES ({placeholders})',
                    values,
                )
            copied[table] = len(rows)

        for table in ID_TABLES:
            target.execute(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{table}', 'id'),
                    COALESCE(MAX(id), 1),
                    MAX(id) IS NOT NULL
                )
                FROM "{table}"
                """
            )
        target.commit()
        return copied
    except Exception:
        target.rollback()
        raise
    finally:
        source.close()
        target.close()


def main():
    if not DATABASE_URL:
        raise RuntimeError('Set DATABASE_URL in .env to the empty Neon database.')
    root = Path(__file__).resolve().parent
    source_path = sqlite_database_path(
        root,
        os.getenv('DATABASE_PATH', str(root / 'freshfruit.db')),
    )
    copied = migrate(source_path, DATABASE_URL)
    print('Migration completed. Rows copied:')
    for table in TABLE_ORDER:
        print(f'  {table}: {copied.get(table, 0)}')
    print('The local SQLite database was not changed.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print(f'Migration failed: {exc}', file=sys.stderr)
        raise SystemExit(1) from exc
