#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
setup_demo.py — Demo 資料初始化腳本
自動插入「已過期 / 即將過期 / 全新品」三筆示範數據至 SQLite 資料庫

執行方式：
    python setup_demo.py           # 插入 Demo 數據
    python setup_demo.py --reset   # 清空資料庫後重新插入
    python setup_demo.py --show    # 僅顯示目前資料庫內容，不插入
    python setup_demo.py --verify  # 驗證數據正確性後退出
"""

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

# ─── 設定 ────────────────────────────────────────────────────────
DB_PATH = Path("inventory.db")

RESET  = "\033[0m"
RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
GREY   = "\033[90m"

# ─── Demo 數據定義 ────────────────────────────────────────────────
def build_demo_rows() -> list[dict]:
    today = date.today()
    return [
        {
            "label":            "已過期",
            "item_name":        "全脂鮮奶",
            "category":         "dairy",
            "quantity":         1,
            "expiry_date":      (today - timedelta(days=5)).isoformat(),
            "image_path":       "demo/milk.jpg",
            "confidence":       0.94,
            "expected_urgency": "pulse-red",
        },
        {
            "label":            "即將過期",
            "item_name":        "雞蛋（10入）",
            "category":         "egg",
            "quantity":         6,
            "expiry_date":      (today + timedelta(days=2)).isoformat(),
            "image_path":       "demo/egg.jpg",
            "confidence":       0.88,
            "expected_urgency": "pulse-red",
        },
        {
            "label":            "全新品",
            "item_name":        "義大利麵條",
            "category":         "pasta",
            "quantity":         2,
            "expiry_date":      (today + timedelta(days=365)).isoformat(),
            "image_path":       "demo/pasta.jpg",
            "confidence":       0.91,
            "expected_urgency": "slide-up (green)",
        },
    ]


# ─── 資料庫工具 ───────────────────────────────────────────────────
def db_connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def db_ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS inventory (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name    TEXT    NOT NULL,
            category     TEXT    DEFAULT 'unknown',
            quantity     INTEGER DEFAULT 1,
            expiry_date  TEXT,
            image_path   TEXT    DEFAULT '',
            confidence   REAL    DEFAULT 0.0,
            created_at   TEXT    DEFAULT (datetime('now','localtime')),
            updated_at   TEXT    DEFAULT (datetime('now','localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_name_date
            ON inventory(item_name, expiry_date);
    """)


def db_is_duplicate(conn: sqlite3.Connection,
                    item_name: str, expiry_date: str) -> bool:
    cur = conn.execute(
        "SELECT id FROM inventory WHERE item_name=? AND expiry_date=?",
        (item_name, expiry_date),
    )
    return cur.fetchone() is not None


def db_insert_row(conn: sqlite3.Connection, row: dict) -> int:
    cur = conn.execute(
        """INSERT INTO inventory
           (item_name, category, quantity, expiry_date, image_path, confidence)
           VALUES (?,?,?,?,?,?)""",
        (row["item_name"], row["category"], row["quantity"],
         row["expiry_date"], row["image_path"], row["confidence"]),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def db_clear(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM inventory")
    conn.commit()
    return cur.rowcount


def db_fetch_sorted(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    cur = conn.execute("""
        SELECT
            id,
            item_name,
            category,
            quantity,
            expiry_date,
            CAST(julianday(expiry_date) - julianday('now') AS INTEGER) AS remain_days,
            confidence,
            created_at
        FROM inventory
        ORDER BY
            CASE WHEN expiry_date IS NULL THEN 1 ELSE 0 END,
            julianday(expiry_date)
    """)
    return cur.fetchall()


# ─── 顯示工具 ─────────────────────────────────────────────────────
def urgency_label(remain) -> str:
    try:
        r = int(remain)
    except (TypeError, ValueError):
        return f"{GREY}未知{RESET}"
    if r < 0:        return f"{RED}{BOLD}已過期 {-r} 天{RESET}"
    if r <= 3:       return f"{RED}{BOLD}緊急！剩 {r} 天{RESET}"
    if r <= 7:       return f"{YELLOW}警告 剩 {r} 天{RESET}"
    return f"{GREEN}安全 剩 {r} 天{RESET}"


def urgency_icon(remain) -> str:
    try:
        r = int(remain)
    except (TypeError, ValueError):
        return "⚫"
    if r < 0:   return "🔴"
    if r <= 3:  return "🔴"
    if r <= 7:  return "🟡"
    return "🟢"


def print_table(rows: list[sqlite3.Row]) -> None:
    if not rows:
        print(f"  {GREY}（資料庫為空）{RESET}")
        return

    header = (f"{'ID':<4} {'圖示':<4} {'品項名稱':<14} "
              f"{'到期日':<12} {'剩餘天數':<24} {'數量':<6} 信心")
    print(f"\n  {BOLD}{CYAN}{header}{RESET}")
    print(f"  {'─'*80}")

    for row in rows:
        remain = row["remain_days"]
        icon   = urgency_icon(remain)
        label  = urgency_label(remain)
        name   = (row["item_name"] or "")[:12]
        exp    = row["expiry_date"] or "N/A"
        qty    = row["quantity"]
        conf   = f"{row['confidence']:.0%}"
        print(f"  {row['id']:<4} {icon:<3}  {name:<14} {exp:<12} "
              f"{label:<30} {qty:<6} {conf}")
    print()


# ─── 驗證 ─────────────────────────────────────────────────────────
def verify_demo_data(rows, demo_rows) -> tuple[bool, list[str]]:
    all_ok  = True
    issues: list[str] = []

    # 1. 筆數
    if len(rows) < len(demo_rows):
        issues.append(f"資料筆數不足：期望 ≥ {len(demo_rows)}，實際 {len(rows)}")
        all_ok = False

    # 2. 排序：剩餘天數遞增
    remain_list = []
    for r in rows:
        try:
            remain_list.append(int(r["remain_days"]))
        except (TypeError, ValueError):
            pass
    if remain_list != sorted(remain_list):
        issues.append(f"排序錯誤：{remain_list}")
        all_ok = False

    # 3. 必含過期品
    if not any(True for r in rows
               if r["remain_days"] is not None
               and int(r["remain_days"]) < 0):
        issues.append("缺少已過期品（remain_days < 0）")
        all_ok = False

    # 4. 必含緊急品
    if not any(True for r in rows
               if r["remain_days"] is not None
               and 0 <= int(r["remain_days"]) <= 3):
        issues.append("缺少即將過期品（0 ≤ remain_days ≤ 3）")
        all_ok = False

    # 5. 必含安全品
    if not any(True for r in rows
               if r["remain_days"] is not None
               and int(r["remain_days"]) > 7):
        issues.append("缺少安全品（remain_days > 7）")
        all_ok = False

    # 6. 日期格式
    for r in rows:
        if r["expiry_date"]:
            try:
                date.fromisoformat(r["expiry_date"])
            except ValueError:
                issues.append(f"日期格式錯誤：{r['expiry_date']}")
                all_ok = False

    return all_ok, issues


# ─── 命令處理 ─────────────────────────────────────────────────────
def cmd_show(conn: sqlite3.Connection) -> None:
    print(f"\n{BOLD}{CYAN}── 目前資料庫內容 ({DB_PATH}) ──{RESET}")
    print_table(db_fetch_sorted(conn))


def cmd_insert(conn: sqlite3.Connection,
               demo_rows: list[dict], skip_existing: bool = True) -> None:
    print(f"\n{BOLD}{CYAN}── 插入 Demo 數據 ──{RESET}\n")
    for row in demo_rows:
        label = row["label"]
        name  = row["item_name"]
        exp   = row["expiry_date"]

        if skip_existing and db_is_duplicate(conn, name, exp):
            print(f"  {YELLOW}⏭  [{label}] {name} ({exp}) — 已存在，跳過{RESET}")
            continue

        rid    = db_insert_row(conn, row)
        remain = (date.fromisoformat(exp) - date.today()).days
        print(f"  {GREEN}✓  [{label}] {name}{RESET}")
        print(f"     到期日：{exp}  {urgency_label(remain)}  → ID={rid}")
    print()


def cmd_reset(conn: sqlite3.Connection) -> None:
    deleted = db_clear(conn)
    print(f"\n  {YELLOW}🗑  已清空 {deleted} 筆舊數據{RESET}")


def cmd_verify(conn: sqlite3.Connection, demo_rows: list[dict]) -> bool:
    rows   = db_fetch_sorted(conn)
    ok, issues = verify_demo_data(rows, demo_rows)
    print(f"\n{BOLD}{CYAN}── 驗證結果 ──{RESET}\n")
    if ok:
        print(f"  {GREEN}{BOLD}✓ 驗證通過！三筆 Demo 數據正確，排序符合預期。{RESET}")
    else:
        print(f"  {RED}{BOLD}✗ 驗證失敗：{RESET}")
        for issue in issues:
            print(f"    {RED}→ {issue}{RESET}")
    return ok


# ─── 主入口 ───────────────────────────────────────────────────────
def main() -> None:
    parser = argparse.ArgumentParser(
        description="智慧家居物資管理系統 — Demo 數據初始化",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例：
  python setup_demo.py              # 插入 Demo 數據（跳過已存在）
  python setup_demo.py --reset      # 清空後重新插入
  python setup_demo.py --show       # 僅顯示，不插入
  python setup_demo.py --verify     # 驗證數據後退出
  python setup_demo.py --reset --verify  # 清空、插入、驗證
        """,
    )
    parser.add_argument("--reset",  action="store_true", help="插入前清空資料庫")
    parser.add_argument("--show",   action="store_true", help="僅顯示資料庫內容")
    parser.add_argument("--verify", action="store_true", help="執行數據驗證")
    args = parser.parse_args()

    print(f"\n{BOLD}{'═'*55}{RESET}")
    print(f"{BOLD}{CYAN}  智慧家居物資管理系統 — Demo 數據初始化{RESET}")
    print(f"{BOLD}{'═'*55}{RESET}")
    print(f"\n  資料庫路徑：{BOLD}{DB_PATH.resolve()}{RESET}")

    conn      = db_connect()
    db_ensure_schema(conn)
    demo_rows = build_demo_rows()

    if args.show:
        cmd_show(conn)
        conn.close()
        return

    if args.reset:
        cmd_reset(conn)

    cmd_insert(conn, demo_rows)
    cmd_show(conn)

    if args.verify:
        ok = cmd_verify(conn, demo_rows)
        conn.close()
        sys.exit(0 if ok else 1)

    conn.close()
    print(f"{BOLD}{GREEN}  Demo 數據準備完成！{RESET}")
    print(f"\n  {BOLD}  streamlit run app.py{RESET}")
    print(f"  開啟瀏覽器：{CYAN}http://localhost:8501{RESET}\n")


if __name__ == "__main__":
    main()
