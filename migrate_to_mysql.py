"""
Simple migration script to copy SQLite `datasets`, `chats`, and `reports` tables
into a MySQL database specified by the environment variable `MYSQL_DATABASE_URL`.

Usage:
    set MYSQL_DATABASE_URL=mysql+pymysql://user:pass@host:3306/dbname
    d:/ai/.venv/Scripts/python.exe migrate_to_mysql.py

This script uses SQLAlchemy and requires `SQLAlchemy` and `pymysql` installed.
"""
import os
import sqlite3
from sqlalchemy import create_engine, MetaData, Table, Column, Integer, String, Text
from sqlalchemy.exc import SQLAlchemyError

SQLITE_DB = os.path.join(os.path.dirname(__file__), "insightai.db")
MYSQL_URL = os.getenv("MYSQL_DATABASE_URL")

if not MYSQL_URL:
    print("No MYSQL_DATABASE_URL set; aborting migration.")
    raise SystemExit(1)

engine = create_engine(MYSQL_URL)
conn = sqlite3.connect(SQLITE_DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

metadata = MetaData()

# Define tables
datasets = Table(
    'datasets', metadata,
    Column('dataset_id', String(255), primary_key=True),
    Column('filename', String(255)),
    Column('path', Text),
    Column('row_count', Integer),
    Column('column_count', Integer),
    Column('quality_score', Integer),
    Column('created_by', Integer),
    Column('created_at', String(255)),
)

chats = Table(
    'chats', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('dataset_id', String(255)),
    Column('user_id', Integer),
    Column('role', String(50)),
    Column('message', Text),
    Column('response', Text),
    Column('created_at', String(255)),
)

reports = Table(
    'reports', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('dataset_id', String(255)),
    Column('filename', String(255)),
    Column('path', Text),
    Column('format', String(50)),
    Column('created_by', Integer),
    Column('created_at', String(255)),
)

try:
    metadata.create_all(engine)
    print("Ensured target MySQL tables exist.")

    # Migrate datasets
    cur.execute("SELECT * FROM datasets")
    rows = cur.fetchall()
    with engine.begin() as mysql_conn:
        for r in rows:
            mysql_conn.execute(datasets.insert().values(
                dataset_id=r['dataset_id'], filename=r['filename'], path=r['path'],
                row_count=r['row_count'], column_count=r['column_count'], quality_score=r['quality_score'],
                created_by=r['created_by'], created_at=r['created_at']
            ))
    print(f"Migrated {len(rows)} datasets.")

    # Migrate chats
    cur.execute("SELECT * FROM chats")
    rows = cur.fetchall()
    with engine.begin() as mysql_conn:
        for r in rows:
            mysql_conn.execute(chats.insert().values(
                dataset_id=r['dataset_id'], user_id=r['user_id'], role=r['role'],
                message=r['message'], response=r['response'], created_at=r['created_at']
            ))
    print(f"Migrated {len(rows)} chats.")

    # Migrate reports
    cur.execute("SELECT * FROM reports")
    rows = cur.fetchall()
    with engine.begin() as mysql_conn:
        for r in rows:
            mysql_conn.execute(reports.insert().values(
                dataset_id=r['dataset_id'], filename=r['filename'], path=r['path'],
                format=r['format'], created_by=r['created_by'], created_at=r['created_at']
            ))
    print(f"Migrated {len(rows)} reports.")

except SQLAlchemyError as e:
    print("SQLAlchemy error:", e)
finally:
    conn.close()

print("Migration complete.")
