#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_logic.py — 智慧家居物資管理系統 完整測試腳本
涵蓋：日期辨識 / 去重邏輯 / 資料庫 CRUD / 剩餘天數排序 / 影像前處理 / urgency 分類

執行方式：
    python test_logic.py
    python test_logic.py -v          # verbose（顯示每個子案例）
"""

# ═══════════════════════════════════════════════════════════════════
# §0  MOCK 重量依賴（streamlit / ultralytics / easyocr）
#     必須在 import app 之前完成，避免觸發 GPU / 模型下載
# ═══════════════════════════════════════════════════════════════════
import sys
import types
from unittest.mock import MagicMock

def _mock_module(name: str, **attrs):
    """建立並注入假模組到 sys.modules。"""
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    sys.modules[name] = mod
    return mod

# ── streamlit: cache_resource 必須是 decorator factory ────────────
_st = _mock_module("streamlit")
_st.cache_resource = staticmethod(lambda **kw: (lambda f: f))
_st.set_page_config = MagicMock()
_st.markdown = MagicMock()
_st.info     = MagicMock()
_st.warning  = MagicMock()
_st.success  = MagicMock()
_st.rerun    = MagicMock()
_st.columns  = MagicMock(return_value=[MagicMock(), MagicMock()])
_st.tabs     = MagicMock(return_value=[MagicMock(), MagicMock(), MagicMock()])
_st.sidebar  = MagicMock()
_st.spinner  = MagicMock(__enter__=MagicMock(return_value=None),
                          __exit__=MagicMock(return_value=False))

# ── ultralytics ────────────────────────────────────────────────────
_ul = _mock_module("ultralytics")
_ul.YOLO = MagicMock()

# ── easyocr ────────────────────────────────────────────────────────
_ocr = _mock_module("easyocr")
_ocr.Reader = MagicMock()

# ── 確保 app.py 可被找到 ───────────────────────────────────────────
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── 現在安全地 import app ──────────────────────────────────────────
import app  # noqa: E402  (重量函式已被 mock，純邏輯函式完整載入)

# ═══════════════════════════════════════════════════════════════════
# §1  測試工具
# ═══════════════════════════════════════════════════════════════════
import traceback
import tempfile
import sqlite3
import textwrap
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Optional
import numpy as np
import pandas as pd

VERBOSE = "-v" in sys.argv

_RESET  = "\033[0m"
_GREEN  = "\033[92m"
_RED    = "\033[91m"
_YELLOW = "\033[93m"
_CYAN   = "\033[96m"
_BOLD   = "\033[1m"

class TestSuite:
    """輕量級測試套件，不依賴 pytest。"""

    def __init__(self, name: str):
        self.name    = name
        self.passed  = 0
        self.failed  = 0
        self._cases: list[dict] = []

    # ── assertion helpers ──────────────────────────────────────────
    def eq(self, label: str, got: Any, expected: Any) -> bool:
        ok = got == expected
        self._record(label, ok,
                     f"期望 {expected!r}，得到 {got!r}" if not ok else "")
        return ok

    def true(self, label: str, expr: bool, detail: str = "") -> bool:
        self._record(label, bool(expr), detail)
        return bool(expr)

    def false(self, label: str, expr: bool, detail: str = "") -> bool:
        self._record(label, not bool(expr),
                     f"期望 False，得到 {expr!r}" if expr else detail)
        return not bool(expr)

    def raises(self, label: str, exc_type, fn, *args, **kwargs) -> bool:
        try:
            fn(*args, **kwargs)
            self._record(label, False, f"期望拋出 {exc_type.__name__}，但未拋出")
            return False
        except exc_type:
            self._record(label, True)
            return True
        except Exception as e:
            self._record(label, False, f"拋出錯誤類型錯誤：{type(e).__name__}: {e}")
            return False

    # ── internal ───────────────────────────────────────────────────
    def _record(self, label: str, ok: bool, detail: str = "") -> None:
        self._cases.append({"label": label, "ok": ok, "detail": detail})
        if ok:
            self.passed += 1
        else:
            self.failed += 1
        if VERBOSE:
            icon = f"{_GREEN}✓{_RESET}" if ok else f"{_RED}✗{_RESET}"
            print(f"  {icon}  {label}")
            if not ok and detail:
                print(f"     {_YELLOW}→ {detail}{_RESET}")

    def summary(self) -> tuple[int, int]:
        return self.passed, self.failed

    def print_failures(self) -> None:
        failures = [c for c in self._cases if not c["ok"]]
        if failures:
            print(f"\n  {_RED}失敗案例：{_RESET}")
            for c in failures:
                print(f"    {_RED}✗ {c['label']}{_RESET}")
                if c["detail"]:
                    print(f"      {_YELLOW}{c['detail']}{_RESET}")


# ═══════════════════════════════════════════════════════════════════
# §2  輔助：帶隔離 DB 的 context manager
# ═══════════════════════════════════════════════════════════════════
class TempDB:
    """建立並清理一個隔離的暫存 SQLite 資料庫。"""

    def __enter__(self) -> sqlite3.Connection:
        self._f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.path = Path(self._f.name)
        self._f.close()
        app.db_init(self.path)
        self.conn = app.db_connect(self.path)
        return self.conn

    def __exit__(self, *_):
        self.conn.close()
        self.path.unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════
# §3  TEST GROUP 1 — _parse_date
# ═══════════════════════════════════════════════════════════════════
def test_parse_date() -> TestSuite:
    s = TestSuite("_parse_date 日期解析器")

    # ── ymd 四位年 ─────────────────────────────────────────────────
    s.eq("ymd 標準",        app._parse_date("2026","03","15","ymd"), date(2026, 3, 15))
    s.eq("ymd 單位數月日",   app._parse_date("2026","3","5","ymd"),  date(2026, 3,  5))
    s.eq("ymd 12月31日",    app._parse_date("2025","12","31","ymd"), date(2025,12,31))

    # ── ymd 兩位年（+2000）────────────────────────────────────────
    s.eq("ymd 2位年 25→2025", app._parse_date("25","06","15","ymd"), date(2025, 6, 15))
    s.eq("ymd 2位年 00→2000", app._parse_date("00","01","01","ymd"), date(2000, 1,  1))

    # ── mdy ────────────────────────────────────────────────────────
    s.eq("mdy 12/31/2025",   app._parse_date("12","31","2025","mdy"), date(2025,12,31))
    s.eq("mdy 1/1/2028",     app._parse_date("1","1","2028","mdy"),   date(2028, 1,  1))

    # ── dmy ────────────────────────────────────────────────────────
    s.eq("dmy 31/12/2025",   app._parse_date("31","12","2025","dmy"), date(2025,12,31))
    s.eq("dmy 01/01/2030",   app._parse_date("01","01","2030","dmy"), date(2030, 1,  1))

    # ── 邊界值：恰好在範圍邊緣 ────────────────────────────────────
    s.eq("範圍下界 2000-01-01", app._parse_date("2000","1","1","ymd"), date(2000, 1, 1))
    s.eq("範圍上界 2040-12-31", app._parse_date("2040","12","31","ymd"), date(2040,12,31))

    # ── 無效日期 → None ────────────────────────────────────────────
    s.eq("無效月份 13",   app._parse_date("2026","13","01","ymd"), None)
    s.eq("無效日期 32",   app._parse_date("2026","01","32","ymd"), None)
    s.eq("超範圍 1999",   app._parse_date("1999","12","31","ymd"), None)
    s.eq("超範圍 2041",   app._parse_date("2041","01","01","ymd"), None)
    s.eq("非數字輸入",    app._parse_date("xxxx","yy","zz","ymd"), None)
    s.eq("未知 order",    app._parse_date("2026","01","01","xyz"), None)
    s.eq("空字串",        app._parse_date("","","","ymd"),         None)

    return s


# ═══════════════════════════════════════════════════════════════════
# §4  TEST GROUP 2 — extract_date (OCR 文字 → ISO 8601)
# ═══════════════════════════════════════════════════════════════════
def test_extract_date() -> TestSuite:
    s = TestSuite("extract_date OCR 日期提取")

    cases = [
        # (描述, OCR 文字列表, 預期 ISO 日期)
        ("YYYY/MM/DD with prefix",     ["最佳賞味期限 2026/03/15"],        "2026-03-15"),
        ("EXP MM/DD/YYYY",             ["EXP 12/31/2025"],                 "2025-12-31"),
        ("BB YYYY.MM.DD",              ["BB 2025.08.20"],                  "2025-08-20"),
        ("8位連續 YYYYMMDD",            ["有效期限 20261201"],               "2026-12-01"),
        ("use by ISO",                 ["use by 2027-07-04"],              "2027-07-04"),
        ("中文年月日格式",              ["2028年04月30日"],                  "2028-04-30"),
        ("Best Before",                ["Best Before 2026/11/30"],         "2026-11-30"),
        ("多行，日期在第二行",           ["產品名稱：牛奶", "到期日：2027/06/01"], "2027-06-01"),
        ("無日期文字",                  ["no date whatsoever"],              None),
        ("空列表",                      [],                                  None),
        ("只有關鍵字沒日期",             ["有效期限 請見包裝"],               None),
        ("YYYY-MM-DD 無前綴",           ["2030-12-25"],                     "2030-12-25"),
        ("兩位年 YY/MM/DD",             ["27/08/15"],                       "2027-08-15"),
        ("到期日 keyword",              ["到期日 2029/02/28"],               "2029-02-28"),
    ]

    for desc, lines, expected in cases:
        got = app.extract_date(lines)
        s.eq(desc, got, expected)

    return s


# ═══════════════════════════════════════════════════════════════════
# §5  TEST GROUP 3 — urgency_classes 緊急程度分類
# ═══════════════════════════════════════════════════════════════════
def test_urgency_classes() -> TestSuite:
    s = TestSuite("urgency_classes 緊急程度分類")

    CRIT, WARN = 3, 7

    # ── 已過期 ────────────────────────────────────────────────────
    badge, card, anim, label = app.urgency_classes(-1, CRIT, WARN)
    s.eq("過期 badge",   badge, "bg-red")
    s.eq("過期 card",    card,  "card-red")
    s.eq("過期 anim",    anim,  "pulse-red")
    s.true("過期 label 含「過期」", "過期" in label)

    badge2, _, anim2, label2 = app.urgency_classes(-10, CRIT, WARN)
    s.eq("過期 10天 badge",  badge2, "bg-red")
    s.eq("過期 10天 anim",   anim2,  "pulse-red")
    s.true("過期 10天 label 含天數", "10" in label2)

    # ── 緊急（剩 0 天）────────────────────────────────────────────
    badge3, _, anim3, _ = app.urgency_classes(0, CRIT, WARN)
    s.eq("0天 badge",  badge3, "bg-red")
    s.eq("0天 anim",   anim3,  "pulse-red")

    # ── 緊急（剩 CRIT 天）────────────────────────────────────────
    badge4, _, anim4, label4 = app.urgency_classes(CRIT, CRIT, WARN)
    s.eq("crit 天 badge",  badge4, "bg-red")
    s.eq("crit 天 anim",   anim4,  "pulse-red")
    s.true("crit 天 label 含天數", str(CRIT) in label4)

    # ── 警告（CRIT+1 ~ WARN）─────────────────────────────────────
    badge5, _, anim5, _ = app.urgency_classes(CRIT + 1, CRIT, WARN)
    s.eq("warning badge",  badge5, "bg-yellow")
    s.eq("warning anim",   anim5,  "slide-up")

    badge6, _, anim6, _ = app.urgency_classes(WARN, CRIT, WARN)
    s.eq("warn 邊界 badge", badge6, "bg-yellow")

    # ── 安全 ─────────────────────────────────────────────────────
    badge7, _, anim7, _ = app.urgency_classes(WARN + 1, CRIT, WARN)
    s.eq("safe badge",  badge7, "bg-green")
    s.eq("safe anim",   anim7,  "slide-up")

    badge8, _, anim8, _ = app.urgency_classes(365, CRIT, WARN)
    s.eq("365天 badge", badge8, "bg-green")

    # ── None（無到期日）──────────────────────────────────────────
    badge9, card9, anim9, label9 = app.urgency_classes(None, CRIT, WARN)
    s.eq("None badge",    badge9, "bg-grey")
    s.eq("None card",     card9,  "card-grey")
    s.eq("None anim",     anim9,  "slide-up")
    s.true("None label 含「未知」", "未知" in label9)

    return s


# ═══════════════════════════════════════════════════════════════════
# §6  TEST GROUP 4 — 資料庫 CRUD
# ═══════════════════════════════════════════════════════════════════
def test_database_crud() -> TestSuite:
    s = TestSuite("資料庫 CRUD 操作")

    with TempDB() as conn:

        # ── insert ────────────────────────────────────────────────
        rid = app.db_insert(conn, "蘋果", "apple", 2, "2026-12-31", "a.jpg", 0.92)
        s.true("insert 回傳正整數 id",  isinstance(rid, int) and rid > 0, str(rid))

        # ── fetch_all 回傳 DataFrame ───────────────────────────────
        df = app.db_fetch_all(conn)
        s.true("fetch_all 回傳 DataFrame", isinstance(df, pd.DataFrame))
        s.eq("fetch_all 列數 = 1", len(df), 1)

        # ── 欄位存在 ──────────────────────────────────────────────
        for col in ["品項名稱", "類別", "數量", "到期日", "剩餘天數", "信心分數", "新增時間"]:
            s.true(f"欄位 '{col}' 存在", col in df.columns)

        # ── 值正確 ───────────────────────────────────────────────
        row = df.iloc[0]
        s.eq("品項名稱",  row["品項名稱"],  "蘋果")
        s.eq("類別",      row["類別"],      "apple")
        s.eq("數量",      int(row["數量"]), 2)
        s.eq("到期日",    row["到期日"],    "2026-12-31")

        # ── update_quantity ────────────────────────────────────────
        app.db_update_quantity(conn, rid, 5)
        df2 = app.db_fetch_all(conn)
        s.eq("update_quantity → 5", int(df2.iloc[0]["數量"]), 5)

        # ── delete ────────────────────────────────────────────────
        app.db_delete(conn, rid)
        df3 = app.db_fetch_all(conn)
        s.eq("delete 後列數 = 0", len(df3), 0)

        # ── NULL expiry_date 可插入 ────────────────────────────────
        rid2 = app.db_insert(conn, "牛奶", "dairy", 1, None, "", 0.8)
        s.true("NULL 到期日可插入", rid2 > 0)
        df4 = app.db_fetch_all(conn)
        s.true("NULL 剩餘天數為 NaN", pd.isna(df4.iloc[0]["剩餘天數"]))

    return s


# ═══════════════════════════════════════════════════════════════════
# §7  TEST GROUP 5 — 去重邏輯（精確 + 模糊）
# ═══════════════════════════════════════════════════════════════════
def test_deduplication() -> TestSuite:
    s = TestSuite("去重邏輯（精確 + rapidfuzz 模糊）")

    with TempDB() as conn:
        # 注意：fuzz.ratio 對長度差異敏感，需使用長度相近且只有少數字元不同的字串
        # 才能達到 85% 閾值。例："可口可樂原罐裝" vs "可口可樂新罐裝" = 85.7%
        app.db_insert(conn, "可口可樂原罐裝", "bottle", 1, "2027-06-30", "", 0.9)
        app.db_insert(conn, "全脂牛奶一公升", "dairy",  1, "2026-03-15", "", 0.8)
        app.db_insert(conn, "蘋果汁",         "bottle", 1, None,         "", 0.7)

        # ── 精確命中 ──────────────────────────────────────────────
        s.true("精確：完全相同名稱",
               app.db_is_duplicate(conn, "可口可樂原罐裝", "2027-06-30"))
        s.true("精確：牛奶 + 日期",
               app.db_is_duplicate(conn, "全脂牛奶一公升", "2026-03-15"))

        # ── 模糊命中（fuzz.ratio ≥ 85% 的字串對）────────────────
        # "可口可樂原罐裝" vs "可口可樂新罐裝" = 85.7%
        s.true("模糊：可口可樂原罐裝 ≈ 可口可樂新罐裝 (85.7%)",
               app.db_is_duplicate(conn, "可口可樂新罐裝", "2027-06-30"))
        # "全脂牛奶一公升" vs "全脂牛奶半公升" = 85.7%
        s.true("模糊：全脂牛奶一公升 ≈ 全脂牛奶半公升 (85.7%)",
               app.db_is_duplicate(conn, "全脂牛奶半公升", "2026-03-15"))

        # ── 短字串（<6字元）差異大：fuzz.ratio < 85% → 正確拒絕 ─
        # "可口可樂(罐)" vs "可口可樂" = 72.7%，低於 85% 應為非重複
        s.false("短字串差異大：72.7% < 85% → 正確拒絕",
                app.db_is_duplicate(conn, "可口可樂(罐)", "2027-06-30"))

        # ── NULL expiry 精確命中 ──────────────────────────────────
        s.true("精確：NULL 到期日",
               app.db_is_duplicate(conn, "蘋果汁", None))

        # ── 非重複：名稱不同 ─────────────────────────────────────
        s.false("非重複：不同名稱",
                app.db_is_duplicate(conn, "橙汁", "2027-06-30"))

        # ── 非重複：相同名稱但不同日期 ───────────────────────────
        s.false("非重複：相同名稱不同日期",
                app.db_is_duplicate(conn, "全脂牛奶一公升", "2099-01-01"))

        # ── 模糊閾值邊界：低閾值 70% → 72.7% 的字串也命中 ───────
        s.true("低閾值 70% → 可口可樂(罐) 命中 (72.7% ≥ 70%)",
               app.db_is_duplicate(conn, "可口可樂(罐)", "2027-06-30", fuzz_thresh=70))

        # ── 模糊閾值邊界：高閾值 99% → 嚴格，只有完全相同才命中 ─
        s.false("高閾值 99% → 可口可樂新罐裝 被拒絕 (85.7% < 99%)",
                app.db_is_duplicate(conn, "可口可樂新罐裝", "2027-06-30", fuzz_thresh=99))

        # ── db_insert_manual wrapper ──────────────────────────────
        rid, status = app.db_insert_manual(conn, "柳橙汁", "juice", 2, "2027-12-01")
        s.eq("manual insert status", status, "added")
        s.true("manual insert rid > 0", rid is not None and rid > 0)

        rid2, status2 = app.db_insert_manual(conn, "柳橙汁", "juice", 2, "2027-12-01")
        s.eq("manual dup status",  status2, "duplicate")
        s.eq("manual dup rid None", rid2, None)

    return s


# ═══════════════════════════════════════════════════════════════════
# §8  TEST GROUP 6 — 剩餘天數排序
# ═══════════════════════════════════════════════════════════════════
def test_sorting() -> TestSuite:
    s = TestSuite("剩餘天數排序")

    today = date.today()
    with TempDB() as conn:
        # 插入順序：安全 → 過期 → 緊急 → NULL
        app.db_insert(conn, "安全品A", "x", 1, (today + timedelta(30)).isoformat(), "", 0.9)
        app.db_insert(conn, "過期品B", "x", 1, (today - timedelta(5)).isoformat(),  "", 0.9)
        app.db_insert(conn, "緊急品C", "x", 1, (today + timedelta(2)).isoformat(),  "", 0.9)
        app.db_insert(conn, "無日期D", "x", 1, None,                                "", 0.9)

        df = app.db_fetch_all(conn)
        names = list(df["品項名稱"])

        # 預期順序：過期(−5) < 緊急(+2) < 安全(+30) < NULL 排末尾
        s.eq("第1筆（最舊過期）", names[0], "過期品B")
        s.eq("第2筆（緊急）",     names[1], "緊急品C")
        s.eq("第3筆（安全）",     names[2], "安全品A")
        s.eq("第4筆（NULL末尾）", names[3], "無日期D")

        # 剩餘天數值正確性（允許 ±1 天誤差因時區）
        remain_vals = list(df["剩餘天數"].fillna(9999))
        s.true("過期品 剩餘 < 0", remain_vals[0] < 0,
               f"得到 {remain_vals[0]}")
        s.true("緊急品 0 ≤ 剩餘 ≤ 3", 0 <= remain_vals[1] <= 3,
               f"得到 {remain_vals[1]}")
        s.true("安全品 剩餘 ≥ 7", remain_vals[2] >= 7,
               f"得到 {remain_vals[2]}")
        s.true("無日期 剩餘 NaN", pd.isna(df.iloc[3]["剩餘天數"]))

    return s


# ═══════════════════════════════════════════════════════════════════
# §9  TEST GROUP 7 — 影像前處理（_preprocess_for_ocr）
# ═══════════════════════════════════════════════════════════════════
def test_image_preprocessing() -> TestSuite:
    import cv2

    s = TestSuite("影像前處理 _preprocess_for_ocr")

    # ── BGR 3通道 ─────────────────────────────────────────────────
    bgr = np.zeros((60, 200, 3), dtype=np.uint8)
    out = app._preprocess_for_ocr(bgr)
    s.eq("BGR → 灰階輸出 ndim=2",   out.ndim,   2)
    s.eq("BGR → dtype uint8",        out.dtype,  np.uint8)

    # ── 單通道灰階（ndim=2）──────────────────────────────────────
    gray = np.zeros((60, 200), dtype=np.uint8)
    out2 = app._preprocess_for_ocr(gray)
    s.eq("Gray → ndim=2",  out2.ndim,  2)
    s.eq("Gray → uint8",   out2.dtype, np.uint8)

    # ── 小圖上放大（< 200px 短邊應被 upscale）────────────────────
    tiny = np.zeros((20, 80, 3), dtype=np.uint8)  # scale = int(200/80) = 2
    out3 = app._preprocess_for_ocr(tiny)
    s.true("小圖放大後高度 ≥ 20", out3.shape[0] >= 20)
    s.eq("小圖放大後 ndim=2", out3.ndim, 2)

    # ── 大圖不縮小（scale=1）─────────────────────────────────────
    big = np.zeros((500, 600, 3), dtype=np.uint8)
    out4 = app._preprocess_for_ocr(big)
    s.eq("大圖 ndim=2",    out4.ndim,      2)
    s.eq("大圖高度不變",   out4.shape[0],  500)

    # ── 二值化結果只含 0 和 255 ──────────────────────────────────
    text_img = np.full((50, 150, 3), 128, dtype=np.uint8)
    out5 = app._preprocess_for_ocr(text_img)
    unique_vals = set(np.unique(out5).tolist())
    s.true("二值化只含 {0, 255}", unique_vals.issubset({0, 255}),
           f"包含其他值：{unique_vals}")

    return s


# ═══════════════════════════════════════════════════════════════════
# §10 TEST GROUP 8 — COCO_ZH 映射
# ═══════════════════════════════════════════════════════════════════
def test_coco_mapping() -> TestSuite:
    s = TestSuite("COCO_ZH 類別映射")

    required_keys = ["bottle", "apple", "banana", "carrot", "cup", "bowl"]
    for k in required_keys:
        s.true(f"'{k}' 在 COCO_ZH 中", k in app.COCO_ZH)
        s.true(f"'{k}' 對應非空字串",
               bool(app.COCO_ZH.get(k, "")))

    # 未知 key 應回傳 key 本身（由 COCO_ZH.get 預設行為保證）
    unknown = app.COCO_ZH.get("xyz_unknown", "xyz_unknown")
    s.eq("未知 key fallback", unknown, "xyz_unknown")

    # 確保所有值皆為 str
    s.true("所有 value 皆為 str",
           all(isinstance(v, str) for v in app.COCO_ZH.values()))

    return s


# ═══════════════════════════════════════════════════════════════════
# §11 TEST GROUP 9 — draw_annotations 標註輸出
# ═══════════════════════════════════════════════════════════════════
def test_draw_annotations() -> TestSuite:
    s = TestSuite("draw_annotations 標註繪製")

    img = np.zeros((480, 640, 3), dtype=np.uint8)
    dets = [
        {"class_name": "bottle", "confidence": 0.88, "bbox": [10, 20, 100, 200], "roi": img},
        {"class_name": "apple",  "confidence": 0.72, "bbox": [200, 50, 350, 300], "roi": img},
        {"class_name": "xyz_unknown", "confidence": 0.50,
         "bbox": [400, 100, 500, 200], "roi": img},
    ]
    out = app.draw_annotations(img, dets)

    s.eq("輸出 shape 不變",  out.shape, img.shape)
    s.eq("輸出 dtype=uint8", out.dtype, np.uint8)
    # 原圖應未被修改（draw_annotations 使用 .copy()）
    s.true("原圖未被修改", np.all(img == 0))
    # 輸出圖應有非零像素（畫了框和文字）
    s.true("輸出包含非零像素", np.any(out != 0))
    # 空偵測列表
    out_empty = app.draw_annotations(img, [])
    s.true("空 dets 輸出全零", np.all(out_empty == 0))

    return s


# ═══════════════════════════════════════════════════════════════════
# §12 RUNNER
# ═══════════════════════════════════════════════════════════════════
def run_all() -> None:
    test_groups = [
        ("T1", test_parse_date),
        ("T2", test_extract_date),
        ("T3", test_urgency_classes),
        ("T4", test_database_crud),
        ("T5", test_deduplication),
        ("T6", test_sorting),
        ("T7", test_image_preprocessing),
        ("T8", test_coco_mapping),
        ("T9", test_draw_annotations),
    ]

    total_pass = total_fail = 0
    suites: list[TestSuite] = []

    print(f"\n{_BOLD}{_CYAN}{'═'*60}{_RESET}")
    print(f"{_BOLD}{_CYAN}  智慧家居物資管理系統 — 完整測試報告{_RESET}")
    print(f"{_BOLD}{_CYAN}{'═'*60}{_RESET}\n")

    for tag, fn in test_groups:
        print(f"{_BOLD}[{tag}] ", end="")
        try:
            suite = fn()
        except Exception:
            print(f"{_RED}CRASH{_RESET}")
            traceback.print_exc()
            total_fail += 1
            continue

        p, f = suite.summary()
        total_pass += p
        total_fail += f
        suites.append(suite)

        status = f"{_GREEN}PASS{_RESET}" if f == 0 else f"{_RED}FAIL{_RESET}"
        bar_ok  = "█" * p
        bar_err = "░" * f
        print(
            f"{suite.name}{_RESET}  "
            f"{_GREEN}{bar_ok}{_RED}{bar_err}{_RESET}  "
            f"{status}  ({p}/{p+f})"
        )
        if f > 0 and not VERBOSE:
            suite.print_failures()

    # ── Grand summary ─────────────────────────────────────────────
    total = total_pass + total_fail
    rate  = total_pass / total if total else 0
    color = _GREEN if total_fail == 0 else (_YELLOW if rate >= 0.8 else _RED)

    print(f"\n{_BOLD}{_CYAN}{'─'*60}{_RESET}")
    print(f"{_BOLD}總結果：{color}{total_pass}/{total} 通過  "
          f"({rate:.0%}){_RESET}")

    if total_fail == 0:
        print(f"{_GREEN}{_BOLD}✓ 所有測試通過！系統邏輯驗證成功。{_RESET}\n")
    else:
        print(f"{_RED}{_BOLD}✗ {total_fail} 個測試失敗，請檢查上方細節。{_RESET}\n")
        sys.exit(1)


if __name__ == "__main__":
    run_all()
