#!/usr/bin/env python3
"""创建siq数据库"""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    from sqlalchemy import create_engine, text

    admin_database_url = os.getenv("POSTGRES_ADMIN_DATABASE_URL", "").strip()
    if not admin_database_url:
        raise RuntimeError("请先设置 POSTGRES_ADMIN_DATABASE_URL，例如 postgresql+psycopg://user:pass@host:5432/postgres")

    engine = create_engine(admin_database_url, isolation_level="AUTOCOMMIT")

    with engine.connect() as conn:
        # 检查数据库是否已存在
        result = conn.execute(text("SELECT 1 FROM pg_database WHERE datname = 'siq'"))
        exists = result.fetchone() is not None

        if exists:
            print("✅ siq数据库已存在")
        else:
            # 创建数据库
            conn.execute(text("CREATE DATABASE siq"))
            print("✅ siq数据库创建成功")

    print("\n现在可以设置 DATABASE_URL 后运行初始化脚本:")
    print("uv run python scripts/init_auth_system.py")

except Exception as e:
    print(f"❌ 创建数据库失败: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
