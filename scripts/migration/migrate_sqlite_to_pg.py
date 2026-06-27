#!/usr/bin/env python3
"""
SQLite to PostgreSQL 数据迁移脚本
将所有SQLite数据迁移到统一的PostgreSQL数据库
"""
import os
import sqlite3
import psycopg
from pathlib import Path

# 配置
PROJECT_ROOT = Path(
    os.getenv("SIQ_PROJECT_ROOT")
    or os.getenv("SIQ_PROJECT_ROOT")
    or Path(__file__).resolve().parents[2]
).expanduser().resolve()
SQLITE_DBS = {
    "agent": os.getenv("SIQ_AGENT_SQLITE_PATH")
    or str(PROJECT_ROOT / "data" / "backend" / "agent.db"),
    "tasks": os.getenv("SIQ_PDF_TASK_DB_PATH")
    or os.getenv("TASK_DB_PATH")
    or os.getenv("SIQ_PDF_TASK_DB_PATH")
    or str(PROJECT_ROOT / "data" / "pdf-parser" / "db" / "tasks.db"),
}

PG_URL = os.getenv(
    "SIQ_DATABASE_URL",
    os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:changeme@127.0.0.1:15432/siq",
    ),
)

def migrate_sqlite_to_pg(sqlite_path: str, schema_prefix: str):
    """迁移单个SQLite数据库到PostgreSQL"""
    print(f"正在迁移 {sqlite_path} → PostgreSQL schema: {schema_prefix}")

    # 连接SQLite
    sqlite_conn = sqlite3.connect(sqlite_path)
    sqlite_conn.row_factory = sqlite3.Row

    # 连接PostgreSQL
    pg_conn = psycopg.connect(PG_URL)
    pg_cursor = pg_conn.cursor()

    # 创建schema
    pg_cursor.execute(f"CREATE SCHEMA IF NOT EXISTS {schema_prefix}")

    # 获取所有表
    tables = sqlite_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()

    for table in tables:
        table_name = table[0]
        print(f"  迁移表: {table_name}")

        # 获取表结构
        create_sql = sqlite_conn.execute(
            f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table_name}'"
        ).fetchone()[0]

        # 转换SQLite语法到PostgreSQL
        create_sql = create_sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        create_sql = create_sql.replace("AUTOINCREMENT", "")
        create_sql = create_sql.replace(f"CREATE TABLE {table_name}",
                                       f"CREATE TABLE IF NOT EXISTS {schema_prefix}.{table_name}")

        # 创建表
        try:
            pg_cursor.execute(create_sql)
        except Exception as e:
            print(f"    警告: {e}")

        # 迁移数据
        rows = sqlite_conn.execute(f"SELECT * FROM {table_name}").fetchall()
        if rows:
            columns = [description[0] for description in sqlite_conn.execute(f"SELECT * FROM {table_name}").description]
            placeholders = ",".join(["%s"] * len(columns))
            insert_sql = f"INSERT INTO {schema_prefix}.{table_name} ({','.join(columns)}) VALUES ({placeholders})"

            for row in rows:
                try:
                    pg_cursor.execute(insert_sql, tuple(row))
                except Exception as e:
                    print(f"    数据插入失败: {e}")

        print(f"    完成: {len(rows)} 行数据")

    pg_conn.commit()
    pg_cursor.close()
    pg_conn.close()
    sqlite_conn.close()
    print(f"✅ {sqlite_path} 迁移完成\n")

def main():
    print("=" * 60)
    print("SQLite → PostgreSQL 数据迁移")
    print("=" * 60)

    for schema, db_path in SQLITE_DBS.items():
        if Path(db_path).exists():
            migrate_sqlite_to_pg(db_path, schema)
        else:
            print(f"⚠️  文件不存在: {db_path}")

    print("=" * 60)
    print("✅ 所有数据迁移完成")
    print("请更新应用配置使用PostgreSQL连接")

if __name__ == "__main__":
    main()
