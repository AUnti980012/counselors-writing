"""FTS5 检索骨架（pack M2 STEP 5）。

tokenizer 分流（R4 回退）：
- meta.fts_tokenizer == 'trigram'：FTS5 MATCH（中文 3-gram 子串，理想路径）；
- 否则（unicode61 回退）：CJK 检索不走 FTS（unicode61 把连续中文当一个 token），
  由调用方（core/repo.py）改用 LIKE/Python 子串扫描 canonical 文件。

MATCH 查询防注入：关键词整体包双引号短语，内部双引号转义为 ""，
用户输入不可能逃逸出 FTS 查询语法。
"""
from __future__ import annotations

import sqlite3
from typing import List


def get_tokenizer(conn: sqlite3.Connection) -> str:
    """meta.fts_tokenizer；未记录（未 init）时按 trigram 假设。"""
    row = conn.execute("SELECT value FROM meta WHERE key='fts_tokenizer'").fetchone()
    return row["value"] if row else "trigram"


def escape_match_phrase(kw: str) -> str:
    """把关键词转成安全的 FTS5 短语查询（双引号包裹 + 内部双引号转义）。"""
    return '"' + kw.replace('"', '""') + '"'


def fts_search(conn: sqlite3.Connection, fts_table: str, columns: List[str],
               kw: str, top: int) -> List[sqlite3.Row]:
    """trigram 路径的 MATCH 检索。返回投影行（列名 = columns）。"""
    phrase = escape_match_phrase(kw)
    col_sql = ", ".join(columns)
    rows = conn.execute(
        f"SELECT {col_sql} FROM {fts_table} WHERE {fts_table} MATCH ? LIMIT ?",
        (phrase, max(1, top))).fetchall()
    return list(rows)
