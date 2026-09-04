# @author: liuqinhe
"""给 knowledge_document 补加租户与上传相关列。

为什么需要这个脚本：项目建表走 `Base.metadata.create_all`，而 `create_all`
**对已存在的表不会补加新列**——它只创建缺失的表。knowledge_document 是
既有表，光改 models.py 不会让线上库多出这些列，运行时会报 Unknown column。

幂等：先查 information_schema，已存在的列跳过。可重复执行。

用法（在 backend 目录下）：
    .venv/bin/python -m scripts.migrate_doc_columns
    .venv/bin/python -m scripts.migrate_doc_columns --dry-run
"""

import argparse

from sqlalchemy import text

from app.db import sync_engine

TABLE = "knowledge_document"

# 列名 -> DDL 片段。默认值让存量内置文档自动落到 tenant_id=''/source='builtin'，
# 无需回填。
COLUMNS = {
    "tenant_id": "VARCHAR(64) NOT NULL DEFAULT ''",
    "source": "VARCHAR(16) NOT NULL DEFAULT 'builtin'",
    "uploaded_by": "VARCHAR(64) NULL",
    "original_filename": "VARCHAR(255) NULL",
    "size_bytes": "INT NOT NULL DEFAULT 0",
    "error_message": "VARCHAR(255) NULL",
}

INDEXES = {"idx_kd_tenant": "(tenant_id)"}


def _existing_columns(conn) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = DATABASE() AND table_name = :t"
        ),
        {"t": TABLE},
    ).all()
    return {r[0].lower() for r in rows}


def _existing_indexes(conn) -> set[str]:
    rows = conn.execute(
        text(
            "SELECT DISTINCT index_name FROM information_schema.statistics "
            "WHERE table_schema = DATABASE() AND table_name = :t"
        ),
        {"t": TABLE},
    ).all()
    return {r[0].lower() for r in rows}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的 DDL")
    args = parser.parse_args()

    with sync_engine.begin() as conn:
        have_cols = _existing_columns(conn)
        if not have_cols:
            print(f"[migrate] 表 {TABLE} 不存在，先跑 scripts.init_db")
            return

        stmts = [
            f"ALTER TABLE {TABLE} ADD COLUMN {name} {ddl}"
            for name, ddl in COLUMNS.items()
            if name.lower() not in have_cols
        ]
        have_idx = _existing_indexes(conn)
        stmts += [
            f"ALTER TABLE {TABLE} ADD INDEX {name} {cols}"
            for name, cols in INDEXES.items()
            if name.lower() not in have_idx
        ]

        if not stmts:
            print("[migrate] 无需变更，列与索引都已存在。")
            return

        for s in stmts:
            print(f"[migrate] {s}")
            if not args.dry_run:
                conn.execute(text(s))

    print(f"[migrate] {'预演结束' if args.dry_run else '完成'}，共 {len(stmts)} 条。")


if __name__ == "__main__":
    main()
