#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════════════════════════════╗
║   智慧家居物資管理系統 v1.0                                         ║
║   Smart Home Inventory Management System                         ║
║   Stack : YOLOv11 × EasyOCR × Streamlit × SQLite                ║
╚══════════════════════════════════════════════════════════════════╝
"""

# ═══════════════════════════════════════════════════════════════════
# §0  AUTO-INSTALL DEPENDENCIES
# ═══════════════════════════════════════════════════════════════════
import subprocess, sys, importlib

_REQUIRED = [
    ("streamlit",   "streamlit>=1.32.0"),
    ("ultralytics", "ultralytics>=8.3.0"),
    ("easyocr",     "easyocr>=1.7.1"),
    ("cv2",         "opencv-python-headless>=4.9.0"),
    ("PIL",         "Pillow>=10.0.0"),
    ("rapidfuzz",   "rapidfuzz>=3.6.0"),
    ("numpy",       "numpy>=1.24.0"),
    ("pandas",      "pandas>=2.0.0"),
]

def _auto_install() -> None:
    for module, pkg in _REQUIRED:
        try:
            importlib.import_module(module)
        except ModuleNotFoundError:
            print(f"[setup] Installing {pkg} …", flush=True)
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", pkg, "--quiet"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"[setup] ✓ {pkg}")

_auto_install()

# ═══════════════════════════════════════════════════════════════════
# §1  IMPORTS
# ═══════════════════════════════════════════════════════════════════
import hashlib, io, logging, os, re, sqlite3, traceback, tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import cv2
import easyocr
import numpy as np
import pandas as pd
import streamlit as st
from PIL import Image
from rapidfuzz import fuzz
from ultralytics import YOLO

logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# §2  CONFIGURATION
# ═══════════════════════════════════════════════════════════════════
DB_PATH         = Path("inventory.db")
IMG_DIR         = Path("uploaded_images")
YOLO_MODEL_NAME = "yolo11n.pt"   # auto-downloaded by ultralytics on first run
OCR_LANGS       = ["ch_sim", "en"]

# defaults (overridable in sidebar)
DEFAULT_CONF_THRESH     = 0.40
DEFAULT_FUZZ_THRESH     = 85
DEFAULT_EXPIRE_CRITICAL = 3
DEFAULT_EXPIRE_WARNING  = 7

IMG_DIR.mkdir(exist_ok=True)

# COCO class → friendly name (ZH)
COCO_ZH: dict[str, str] = {
    "bottle":      "瓶裝飲料",
    "cup":         "杯子",
    "bowl":        "碗",
    "banana":      "香蕉",
    "apple":       "蘋果",
    "sandwich":    "三明治",
    "orange":      "柳橙",
    "broccoli":    "花椰菜",
    "carrot":      "胡蘿蔔",
    "hot dog":     "熱狗",
    "pizza":       "披薩",
    "donut":       "甜甜圈",
    "cake":        "蛋糕",
    "wine glass":  "酒杯",
    "fork":        "叉子",
    "knife":       "刀子",
    "spoon":       "湯匙",
    "cell phone":  "手機",
    "book":        "書本",
    "scissors":    "剪刀",
}

# ═══════════════════════════════════════════════════════════════════
# §3  DATABASE LAYER
# ═══════════════════════════════════════════════════════════════════

def db_connect(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def db_init(path: Path = DB_PATH) -> None:
    """Create schema if not exists."""
    with db_connect(path) as conn:
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


def db_is_duplicate(
    conn: sqlite3.Connection,
    item_name: str,
    expiry_date: Optional[str],
    fuzz_thresh: int = DEFAULT_FUZZ_THRESH,
) -> bool:
    """Exact check first; then rapidfuzz similarity on same expiry_date bucket."""
    # 1) exact
    cur = conn.execute(
        "SELECT id FROM inventory WHERE item_name=? AND expiry_date IS ?",
        (item_name, expiry_date),
    )
    if cur.fetchone():
        return True
    # 2) fuzzy within same expiry bucket
    cur = conn.execute(
        "SELECT item_name FROM inventory WHERE expiry_date IS ?",
        (expiry_date,),
    )
    for row in cur.fetchall():
        if fuzz.ratio(item_name, row["item_name"]) >= fuzz_thresh:
            return True
    return False


def db_insert(
    conn: sqlite3.Connection,
    item_name: str,
    category: str,
    quantity: int,
    expiry_date: Optional[str],
    image_path: str,
    confidence: float,
) -> int:
    cur = conn.execute(
        """INSERT INTO inventory
           (item_name, category, quantity, expiry_date, image_path, confidence)
           VALUES (?,?,?,?,?,?)""",
        (item_name, category, quantity, expiry_date, image_path, confidence),
    )
    conn.commit()
    return cur.lastrowid  # type: ignore[return-value]


def db_fetch_all(conn: sqlite3.Connection) -> pd.DataFrame:
    """All rows sorted by remaining days (NULLs last)."""
    sql = """
        SELECT
            id,
            item_name   AS 品項名稱,
            category    AS 類別,
            quantity    AS 數量,
            expiry_date AS 到期日,
            CASE
                WHEN expiry_date IS NULL THEN NULL
                ELSE CAST(julianday(expiry_date) - julianday('now') AS INTEGER)
            END          AS 剩餘天數,
            confidence   AS 信心分數,
            created_at   AS 新增時間
        FROM inventory
        ORDER BY
            CASE WHEN expiry_date IS NULL THEN 1 ELSE 0 END,
            julianday(expiry_date)
    """
    return pd.read_sql_query(sql, conn)


def db_delete(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute("DELETE FROM inventory WHERE id=?", (item_id,))
    conn.commit()


def db_update_quantity(conn: sqlite3.Connection, item_id: int, qty: int) -> None:
    conn.execute(
        "UPDATE inventory SET quantity=?, updated_at=datetime('now','localtime') WHERE id=?",
        (qty, item_id),
    )
    conn.commit()


def db_insert_manual(
    conn: sqlite3.Connection,
    item_name: str,
    category: str,
    quantity: int,
    expiry_date: Optional[str],
) -> tuple[int | None, str]:
    """Insert a manually entered item. Returns (row_id, status)."""
    if db_is_duplicate(conn, item_name, expiry_date):
        return None, "duplicate"
    rid = db_insert(conn, item_name, category, quantity, expiry_date, "", 1.0)
    return rid, "added"


# ═══════════════════════════════════════════════════════════════════
# §4  YOLO DETECTION
# ═══════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner="載入 YOLOv11 模型 …")
def load_yolo() -> YOLO:
    model = YOLO(YOLO_MODEL_NAME)
    logger.info("YOLO loaded: %s", YOLO_MODEL_NAME)
    return model


def detect_objects(
    model: YOLO,
    img_bgr: np.ndarray,
    conf_thresh: float = DEFAULT_CONF_THRESH,
) -> list[dict]:
    """
    Run YOLOv11 inference.
    Returns list of dicts:
      { class_name, confidence, bbox:[x1,y1,x2,y2], roi:np.ndarray }
    """
    results = model.predict(
        source=img_bgr,
        conf=conf_thresh,
        verbose=False,
        imgsz=640,
    )
    detections: list[dict] = []
    h, w = img_bgr.shape[:2]
    pad = 12
    for result in results:
        for box in result.boxes:
            cls_id   = int(box.cls[0])
            cls_name = model.names[cls_id]
            conf     = float(box.conf[0])
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            roi = img_bgr[
                max(0, y1 - pad) : min(h, y2 + pad),
                max(0, x1 - pad) : min(w, x2 + pad),
            ]
            detections.append({
                "class_name": cls_name,
                "confidence": conf,
                "bbox":       [x1, y1, x2, y2],
                "roi":        roi,
            })
    return detections


def draw_annotations(img_bgr: np.ndarray, detections: list[dict]) -> np.ndarray:
    out = img_bgr.copy()
    for det in detections:
        x1, y1, x2, y2 = det["bbox"]
        zh   = COCO_ZH.get(det["class_name"], det["class_name"])
        label = f"{zh}  {det['confidence']:.0%}"
        color = (50, 205, 110)
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        cv2.putText(out, label, (x1, max(y1 - 8, 18)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.56, color, 2)
    return out


# ═══════════════════════════════════════════════════════════════════
# §5  OCR + DATE EXTRACTION
# ═══════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner="載入 EasyOCR 模型 …")
def load_ocr() -> easyocr.Reader:
    reader = easyocr.Reader(OCR_LANGS, gpu=False, verbose=False)
    logger.info("EasyOCR loaded: %s", OCR_LANGS)
    return reader


# Keyword prefixes that precede expiry dates
_EXP_PREFIX = re.compile(
    r"(exp(?:iry)?\.?|best\s*before|bb|use\s*by"
    r"|賞味期限|最佳賞味期限|有效期(?:限|至)?|到期日)[：:\s]*",
    re.IGNORECASE,
)

# Ordered date patterns  →  (regex, group_order)
#   group_order: 'ymd' | 'mdy' | 'dmy'
_DATE_RULES: list[tuple[re.Pattern, str]] = [
    # YYYY/MM/DD   YYYY-MM-DD   YYYY.MM.DD   YYYY年MM月DD日
    (re.compile(r"(\d{4})[/\-\.年](\d{1,2})[/\-\.月](\d{1,2})[日]?"), "ymd"),
    # MM/DD/YYYY   MM-DD-YYYY
    (re.compile(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})"),              "mdy"),
    # DD.MM.YYYY
    (re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})"),                    "dmy"),
    # YYYYMMDD  (8 contiguous digits)
    (re.compile(r"\b(\d{4})(\d{2})(\d{2})\b"),                        "ymd"),
    # YY/MM/DD   YY-MM-DD
    (re.compile(r"(\d{2})[/\-](\d{1,2})[/\-](\d{1,2})"),             "ymd"),
]

_DATE_RANGE = (date(2000, 1, 1), date(2040, 12, 31))


def _parse_date(g1: str, g2: str, g3: str, order: str) -> Optional[date]:
    """Convert three string groups to a date object using given field order."""
    try:
        if order == "ymd":
            y, m, d = int(g1), int(g2), int(g3)
            if y < 100:
                y += 2000
        elif order == "mdy":
            m, d, y = int(g1), int(g2), int(g3)
        elif order == "dmy":
            d, m, y = int(g1), int(g2), int(g3)
        else:
            return None
        parsed = date(y, m, d)
        return parsed if _DATE_RANGE[0] <= parsed <= _DATE_RANGE[1] else None
    except (ValueError, TypeError):
        return None


def extract_date(text_lines: list[str]) -> Optional[str]:
    """
    Best-effort expiry date extraction from OCR text lines.
    Returns ISO 8601 string or None.
    """
    full = " ".join(text_lines)
    # strip prefix keywords so they don't confuse number grouping
    cleaned = _EXP_PREFIX.sub(" ", full)

    for pattern, order in _DATE_RULES:
        for m in pattern.finditer(cleaned):
            parsed = _parse_date(m.group(1), m.group(2), m.group(3), order)
            if parsed:
                return parsed.isoformat()
    return None


def _preprocess_for_ocr(roi_bgr: np.ndarray) -> np.ndarray:
    """Enhance ROI contrast for OCR."""
    gray = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2GRAY) if roi_bgr.ndim == 3 else roi_bgr
    # upscale small crops
    scale = max(1, int(200 / max(gray.shape[0], gray.shape[1], 1)))
    if scale > 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale,
                          interpolation=cv2.INTER_CUBIC)
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return bw


def ocr_image(reader: easyocr.Reader, img_bgr: np.ndarray) -> tuple[list[str], Optional[str]]:
    """
    Run EasyOCR on an image (full or ROI).
    Returns (raw_text_lines, expiry_iso_or_None).
    """
    processed = _preprocess_for_ocr(img_bgr)
    try:
        raw = reader.readtext(processed, detail=0, paragraph=True)
    except Exception as exc:
        logger.warning("EasyOCR error: %s", exc)
        raw = []
    lines = [r.strip() for r in raw if isinstance(r, str) and r.strip()]
    return lines, extract_date(lines)


# ═══════════════════════════════════════════════════════════════════
# §6  MAIN AGENT PIPELINE
# ═══════════════════════════════════════════════════════════════════

def _save_pil(img: Image.Image) -> str:
    digest = hashlib.md5(img.tobytes()).hexdigest()[:12]
    path   = IMG_DIR / f"{digest}.jpg"
    img.save(path, "JPEG", quality=90)
    return str(path)


def process_image(
    pil_img:    Image.Image,
    yolo_model: YOLO,
    ocr_reader: easyocr.Reader,
    conn:       sqlite3.Connection,
    conf_thresh: float = DEFAULT_CONF_THRESH,
    fuzz_thresh: int   = DEFAULT_FUZZ_THRESH,
) -> list[dict]:
    """
    Full pipeline:
      upload → YOLO detect → crop ROIs → EasyOCR → date parse
      → dedup check → SQLite insert
    Returns list of result dicts per detected object.
    """
    img_path = _save_pil(pil_img)
    img_bgr  = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

    # ── Step 1: YOLO ──────────────────────────────────────────────
    detections = detect_objects(yolo_model, img_bgr, conf_thresh)
    if not detections:
        # treat whole image as one unknown item
        detections = [{
            "class_name": "unknown",
            "confidence": 0.0,
            "bbox":       [0, 0, img_bgr.shape[1], img_bgr.shape[0]],
            "roi":        img_bgr,
        }]

    # ── Step 2: OCR on full image (date often outside bbox) ────────
    full_lines, full_expiry = ocr_image(ocr_reader, img_bgr)

    results: list[dict] = []
    for det in detections:
        item_name  = COCO_ZH.get(det["class_name"], det["class_name"])
        confidence = det["confidence"]

        # ── Step 3: OCR on cropped ROI ────────────────────────────
        roi_lines, roi_expiry = ocr_image(ocr_reader, det["roi"])
        # ROI-level date takes priority; fall back to full image
        expiry_date = roi_expiry or full_expiry
        ocr_text    = " | ".join(roi_lines or full_lines)

        # ── Step 4: Dedup check ───────────────────────────────────
        is_dup = db_is_duplicate(conn, item_name, expiry_date, fuzz_thresh)
        status = "duplicate" if is_dup else "added"

        # ── Step 5: Write to SQLite ───────────────────────────────
        row_id: Optional[int] = None
        if not is_dup:
            row_id = db_insert(
                conn, item_name, det["class_name"],
                1, expiry_date, img_path, confidence,
            )

        results.append({
            "item_name":   item_name,
            "category":    det["class_name"],
            "confidence":  confidence,
            "expiry_date": expiry_date,
            "ocr_text":    ocr_text[:200],
            "status":      status,
            "row_id":      row_id,
        })

    return results


# ═══════════════════════════════════════════════════════════════════
# §7  CUSTOM CSS  (dark mode + slide-up + pulse-red)
# ═══════════════════════════════════════════════════════════════════

_CSS = """
<style>
/* ── CSS custom properties ─────────────────────────────────────── */
:root {
    --bg0:   #0d1117;
    --bg1:   #161b27;
    --bg2:   #1c2333;
    --acc:   #4f8ef7;
    --green: #22c55e;
    --yell:  #f59e0b;
    --red:   #ef4444;
    --txt:   #e2e8f0;
    --muted: #8892a4;
    --bord:  #2a3347;
    --shad:  0 4px 24px rgba(0,0,0,.55);
    --rad:   12px;
}

/* ── Global dark background ────────────────────────────────────── */
html, body,
[data-testid="stApp"],
[data-testid="stAppViewContainer"],
[data-testid="stMain"] {
    background-color: var(--bg0) !important;
    color: var(--txt) !important;
}
[data-testid="stSidebar"] {
    background-color: var(--bg1) !important;
    border-right: 1px solid var(--bord);
}
header[data-testid="stHeader"] { display: none !important; }
[data-testid="stToolbar"]      { display: none !important; }

/* ── Card base ─────────────────────────────────────────────────── */
.inv-card {
    background: var(--bg2);
    border: 1px solid var(--bord);
    border-radius: var(--rad);
    padding: 14px 18px;
    margin: 8px 0;
    box-shadow: var(--shad);
    position: relative;
    overflow: hidden;
    transition: transform .18s ease, box-shadow .18s ease;
}
.inv-card:hover {
    transform: translateY(-2px);
    box-shadow: 0 8px 32px rgba(0,0,0,.65);
}
/* left accent bar */
.inv-card::before {
    content: '';
    position: absolute;
    left: 0; top: 0; bottom: 0;
    width: 4px;
    border-radius: var(--rad) 0 0 var(--rad);
}
.card-green::before  { background: var(--green); }
.card-yellow::before { background: var(--yell);  }
.card-red::before    { background: var(--red);   }
.card-grey::before   { background: var(--muted); }

/* ── ① slide-up entrance ──────────────────────────────────────── */
@keyframes slideUp {
    from { opacity: 0; transform: translateY(28px); }
    to   { opacity: 1; transform: translateY(0);    }
}
.slide-up {
    animation: slideUp .42s cubic-bezier(.22,.61,.36,1) both;
}

/* ── ② pulse-red for expiring / expired ───────────────────────── */
@keyframes pulseRed {
    0%   { box-shadow: var(--shad), 0 0  0px 0px rgba(239,68,68,0);    }
    50%  { box-shadow: var(--shad), 0 0 22px 8px rgba(239,68,68,.55);  }
    100% { box-shadow: var(--shad), 0 0  0px 0px rgba(239,68,68,0);    }
}
.pulse-red {
    border-color: var(--red) !important;
    animation:
        slideUp  .42s cubic-bezier(.22,.61,.36,1) both,
        pulseRed 1.9s ease-in-out .5s infinite;
}

/* ── Typography ────────────────────────────────────────────────── */
.item-name {
    font-size: 1.05rem;
    font-weight: 700;
    color: var(--txt);
    margin-bottom: 5px;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 8px;
}
.item-meta {
    font-size: .8rem;
    color: var(--muted);
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    margin-top: 4px;
}

/* ── Badges ────────────────────────────────────────────────────── */
.badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 99px;
    font-size: .76rem;
    font-weight: 700;
    letter-spacing: .4px;
}
.bg-green  { background: #14532d; color: #4ade80; }
.bg-yellow { background: #451a03; color: #fbbf24; }
.bg-red    { background: #450a0a; color: #f87171; }
.bg-grey   { background: #1e293b; color: #94a3b8; }

/* ── Section dividers ──────────────────────────────────────────── */
.sec-title {
    font-size: 1.25rem;
    font-weight: 800;
    color: var(--txt);
    margin: 24px 0 10px;
    display: flex;
    align-items: center;
    gap: 10px;
}
.sec-title::after {
    content: '';
    flex: 1;
    height: 1px;
    background: var(--bord);
}

/* ── Streamlit overrides ───────────────────────────────────────── */
[data-testid="stFileUploader"] {
    background: var(--bg1);
    border: 2px dashed var(--bord);
    border-radius: var(--rad);
}
.stButton > button {
    background: linear-gradient(135deg, var(--acc) 0%, #7c3aed 100%);
    color: #fff !important;
    border: none;
    border-radius: 8px;
    font-weight: 700;
    transition: transform .15s, box-shadow .15s;
}
.stButton > button:hover {
    transform: translateY(-2px);
    box-shadow: 0 6px 20px rgba(79,142,247,.45);
}
[data-testid="metric-container"] {
    background: var(--bg2) !important;
    border: 1px solid var(--bord) !important;
    border-radius: 10px !important;
}
[data-testid="stDataFrame"] thead th {
    background: var(--bg1) !important;
    color: var(--acc) !important;
}
/* tab styling */
[data-testid="stTabs"] [role="tab"] {
    color: var(--muted) !important;
    font-weight: 600;
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
    color: var(--acc) !important;
    border-bottom: 2px solid var(--acc);
}

/* ── Scrollbar ─────────────────────────────────────────────────── */
::-webkit-scrollbar       { width: 6px; }
::-webkit-scrollbar-track { background: var(--bg0); }
::-webkit-scrollbar-thumb { background: var(--bord); border-radius: 3px; }
</style>
"""


def inject_css() -> None:
    st.markdown(_CSS, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# §8  UI HELPER COMPONENTS
# ═══════════════════════════════════════════════════════════════════

def urgency_classes(remain: Optional[int], crit: int, warn: int) -> tuple[str, str, str, str]:
    """Return (badge_bg, card_cls, anim_cls, label_text)."""
    if remain is None:
        return "bg-grey",   "card-grey",   "slide-up",  "未知到期日"
    if remain < 0:
        return "bg-red",    "card-red",    "pulse-red", f"已過期 {-remain} 天"
    if remain <= crit:
        return "bg-red",    "card-red",    "pulse-red", f"剩 {remain} 天 ⚠"
    if remain <= warn:
        return "bg-yellow", "card-yellow", "slide-up",  f"剩 {remain} 天"
    return "bg-green",  "card-green",  "slide-up",  f"剩 {remain} 天"


def render_metrics(df: pd.DataFrame, crit: int) -> None:
    total    = len(df)
    have_rem = "剩餘天數" in df.columns and total > 0
    expired  = int((df["剩餘天數"] <  0).sum())            if have_rem else 0
    critical = int(((df["剩餘天數"] >= 0) & (df["剩餘天數"] <= crit)).sum()) \
               if have_rem else 0
    safe     = total - expired - critical

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("📦 總品項",   total)
    c2.metric("✅ 安全",     safe)
    c3.metric("⚠️ 即將到期", critical,
              delta=f"-{critical}" if critical else None, delta_color="inverse")
    c4.metric("❌ 已過期",   expired,
              delta=f"-{expired}"  if expired  else None, delta_color="inverse")


def render_cards(df: pd.DataFrame, crit: int, warn: int) -> None:
    if df.empty:
        st.info("目前庫存為空，請上傳圖片或手動新增物品。")
        return

    html = '<div id="inv-grid">'
    for i, row in enumerate(df.itertuples(index=False)):
        remain = getattr(row, "剩餘天數", None)
        remain = int(remain) if (remain is not None and not np.isnan(remain)) else None

        badge_bg, card_cls, anim_cls, label = urgency_classes(remain, crit, warn)
        delay = min(i * 0.07, 1.0)

        qty_txt  = getattr(row, "數量",   "N/A")
        cat_txt  = getattr(row, "類別",   "N/A")
        exp_txt  = getattr(row, "到期日", None) or "N/A"
        conf_pct = f"{getattr(row, '信心分數', 0):.0%}"
        ts_txt   = getattr(row, "新增時間", "")

        html += f"""
        <div class="inv-card {card_cls} {anim_cls}"
             style="animation-delay:{delay:.2f}s">
            <div class="item-name">
                {getattr(row, '品項名稱', '?')}
                <span class="badge {badge_bg}">{label}</span>
            </div>
            <div class="item-meta">
                <span>📦 數量 {qty_txt}</span>
                <span>🏷 類別 {cat_txt}</span>
                <span>📅 到期 {exp_txt}</span>
                <span>🎯 信心 {conf_pct}</span>
                <span>🕐 {ts_txt}</span>
            </div>
        </div>"""
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════════════
# §9  INLINE TEST SUITE
# ═══════════════════════════════════════════════════════════════════

def _run_tests() -> list[dict]:
    """
    Pure-logic tests — no YOLO / OCR models required.
    Returns list of {name, passed, detail}.
    """
    res: list[dict] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        res.append({"name": name, "passed": ok, "detail": detail})

    # ── T1: extract_date ──────────────────────────────────────────
    cases = [
        (["最佳賞味期限 2026/03/15"],          "2026-03-15"),
        (["EXP 12/31/2025"],                   "2025-12-31"),
        (["BB 2025.08.20"],                    "2025-08-20"),
        (["有效期限 20261201"],                 "2026-12-01"),
        (["use by 2027-07-04"],                "2027-07-04"),
        (["2028年04月30日"],                   "2028-04-30"),
        (["no date whatsoever"],               None),
    ]
    for i, (lines, expected) in enumerate(cases, 1):
        got = extract_date(lines)
        check(f"extract_date [{i}]", got == expected,
              f"expect={expected!r}  got={got!r}")

    # ── T2: _parse_date ───────────────────────────────────────────
    check("_parse_date ymd 4-digit", _parse_date("2025","06","15","ymd") == date(2025,6,15))
    check("_parse_date ymd 2-digit", _parse_date("25","06","15","ymd")   == date(2025,6,15))
    check("_parse_date mdy",         _parse_date("12","31","2025","mdy") == date(2025,12,31))
    check("_parse_date dmy",         _parse_date("31","12","2025","dmy") == date(2025,12,31))
    check("_parse_date invalid",     _parse_date("99","99","9999","ymd") is None)

    # ── T3: urgency_classes ───────────────────────────────────────
    badge, card, anim, label = urgency_classes(-3, 3, 7)
    check("urgency expired   → pulse-red",  anim  == "pulse-red",  anim)
    check("urgency expired   → badge-red",  badge == "bg-red",     badge)

    badge2, _, anim2, _ = urgency_classes(2, 3, 7)
    check("urgency critical  → pulse-red",  anim2 == "pulse-red",  anim2)

    badge3, _, anim3, _ = urgency_classes(5, 3, 7)
    check("urgency warning   → slide-up",   anim3 == "slide-up",   anim3)
    check("urgency warning   → bg-yellow",  badge3 == "bg-yellow", badge3)

    badge4, _, anim4, _ = urgency_classes(30, 3, 7)
    check("urgency safe      → bg-green",   badge4 == "bg-green",  badge4)

    badge5, _, _, _ = urgency_classes(None, 3, 7)
    check("urgency unknown   → bg-grey",    badge5 == "bg-grey",   badge5)

    # ── T4: Database operations ───────────────────────────────────
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        tmp_db = Path(f.name)
    try:
        db_init(tmp_db)
        conn = db_connect(tmp_db)

        # insert
        rid = db_insert(conn, "蘋果", "apple", 2, "2026-12-31", "test.jpg", 0.92)
        check("DB insert returns id",  rid is not None and rid > 0, str(rid))

        # exact dedup
        check("DB exact dedup",        db_is_duplicate(conn, "蘋果", "2026-12-31"))

        # fuzzy dedup  (similarity ≥ 85)
        check("DB fuzzy dedup",        db_is_duplicate(conn, "蘋果(個)", "2026-12-31"))

        # non-duplicate different name
        check("DB non-dup diff name",  not db_is_duplicate(conn, "香蕉", "2026-12-31"))

        # non-duplicate different date
        check("DB non-dup diff date",  not db_is_duplicate(conn, "蘋果", "2027-01-01"))

        # fetch_all returns DataFrame
        df = db_fetch_all(conn)
        check("DB fetch_all DataFrame", isinstance(df, pd.DataFrame) and len(df) == 1)
        check("DB remain days column",  "剩餘天數" in df.columns)

        # update quantity
        db_update_quantity(conn, rid, 5)
        df2 = db_fetch_all(conn)
        check("DB update quantity",    int(df2.iloc[0]["數量"]) == 5)

        # delete
        db_delete(conn, rid)
        df3 = db_fetch_all(conn)
        check("DB delete",             df3.empty)

        conn.close()
    finally:
        tmp_db.unlink(missing_ok=True)

    # ── T5: OCR preprocessing ─────────────────────────────────────
    dummy = np.zeros((60, 200, 3), dtype=np.uint8)
    proc  = _preprocess_for_ocr(dummy)
    check("OCR preprocess returns 2D",  proc.ndim == 2)
    check("OCR preprocess dtype uint8", proc.dtype == np.uint8)

    return res


def render_test_panel() -> None:
    with st.sidebar.expander("🧪 執行測試套件", expanded=False):
        if st.button("▶ Run All Tests", key="btn_tests"):
            with st.spinner("測試中 …"):
                results = _run_tests()
            passed = sum(1 for r in results if r["passed"])
            total  = len(results)
            color  = "#22c55e" if passed == total else "#ef4444"
            st.markdown(
                f"<b style='color:{color}'>結果：{passed}/{total} 通過</b>",
                unsafe_allow_html=True,
            )
            for r in results:
                icon = "✅" if r["passed"] else "❌"
                st.markdown(f"{icon} `{r['name']}`")
                if not r["passed"]:
                    st.caption(f"   → {r['detail']}")


# ═══════════════════════════════════════════════════════════════════
# §10 SIDEBAR
# ═══════════════════════════════════════════════════════════════════

def render_sidebar(conn: sqlite3.Connection) -> tuple[Optional[Image.Image], dict]:
    """
    Render full sidebar UI.
    Returns (uploaded_pil_image_or_None, settings_dict).
    """
    st.sidebar.markdown(
        "<div style='text-align:center;padding:14px 0'>"
        "<div style='font-size:2.2rem'>🏠</div>"
        "<div style='font-size:1.05rem;font-weight:800;color:#e2e8f0'>"
        "智慧物資管理系統</div>"
        "<div style='font-size:.72rem;color:#8892a4'>YOLOv11 × EasyOCR × SQLite</div>"
        "</div>",
        unsafe_allow_html=True,
    )
    st.sidebar.markdown("---")

    # ── Upload ────────────────────────────────────────────────────
    st.sidebar.markdown("### 📤 上傳圖片辨識")
    uploaded = st.sidebar.file_uploader(
        "支援 JPG / PNG / WEBP",
        type=["jpg", "jpeg", "png", "webp"],
        label_visibility="collapsed",
    )

    # ── Manual add ────────────────────────────────────────────────
    st.sidebar.markdown("### ✏️ 手動新增物品")
    with st.sidebar.form("manual_add", clear_on_submit=True):
        m_name = st.text_input("品項名稱", placeholder="e.g. 牛奶")
        m_cat  = st.text_input("類別",     placeholder="e.g. dairy")
        m_qty  = st.number_input("數量", min_value=1, value=1)
        m_exp  = st.date_input("到期日", value=None)
        submitted = st.form_submit_button("新增")
        if submitted and m_name.strip():
            exp_str = m_exp.isoformat() if m_exp else None
            rid, status = db_insert_manual(
                conn, m_name.strip(), m_cat.strip() or "unknown",
                int(m_qty), exp_str,
            )
            if status == "added":
                st.success(f"已新增：{m_name}")
                st.rerun()
            else:
                st.warning("重複項目，已跳過。")

    st.sidebar.markdown("---")

    # ── Settings ──────────────────────────────────────────────────
    st.sidebar.markdown("### ⚙️ 辨識設定")
    conf_thresh = st.sidebar.slider(
        "YOLO 信心閾值", 0.10, 0.90, DEFAULT_CONF_THRESH, 0.05)
    fuzz_thresh = st.sidebar.slider(
        "模糊去重閾值 (%)", 60, 100, DEFAULT_FUZZ_THRESH, 5)
    expire_crit = st.sidebar.number_input(
        "🔴 緊急天數 (≤ N天)", 1, 14, DEFAULT_EXPIRE_CRITICAL)
    expire_warn = st.sidebar.number_input(
        "🟡 警告天數 (≤ N天)", 1, 30, DEFAULT_EXPIRE_WARNING)

    st.sidebar.markdown("---")

    # ── Delete ────────────────────────────────────────────────────
    df = db_fetch_all(conn)
    if not df.empty:
        st.sidebar.markdown("### 🗑️ 刪除品項")
        options = {f"[{r.id}] {getattr(r,'品項名稱','?')}": r.id
                   for r in df.itertuples()}
        choice = st.sidebar.selectbox(
            "選擇刪除",
            list(options.keys()),
            index=None,
            placeholder="選擇…",
        )
        if choice and st.sidebar.button("確認刪除", type="primary"):
            db_delete(conn, options[choice])
            st.sidebar.success("已刪除！")
            st.rerun()

    # ── Tests ─────────────────────────────────────────────────────
    st.sidebar.markdown("---")
    render_test_panel()
    st.sidebar.markdown("---")
    st.sidebar.caption("© 2026 Smart Inventory Agent · v1.0")

    settings = {
        "conf_thresh": conf_thresh,
        "fuzz_thresh": fuzz_thresh,
        "expire_crit": expire_crit,
        "expire_warn": expire_warn,
    }
    return (Image.open(uploaded) if uploaded else None), settings


# ═══════════════════════════════════════════════════════════════════
# §11 MAIN APP ENTRY POINT
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    st.set_page_config(
        page_title="智慧家居物資管理",
        page_icon="🏠",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_css()
    db_init()

    # shared resources
    conn       = db_connect()
    yolo_model = load_yolo()
    ocr_reader = load_ocr()

    # ── Sidebar ───────────────────────────────────────────────────
    uploaded_img, settings = render_sidebar(conn)

    # ── Page header ───────────────────────────────────────────────
    st.markdown(
        "<div style='padding:6px 0 2px'>"
        "<h1 style='font-size:1.75rem;font-weight:900;color:#e2e8f0;margin:0'>"
        "🏠 智慧家居物資管理系統</h1>"
        "<p style='color:#8892a4;margin:4px 0 0;font-size:.9rem'>"
        "YOLOv11 × EasyOCR × SQLite — 自動辨識物品、OCR 到期日、即時庫存管理"
        "</p></div>",
        unsafe_allow_html=True,
    )

    # ── Pipeline result for newly uploaded image ───────────────────
    if uploaded_img is not None:
        st.markdown('<div class="sec-title">📸 圖片辨識結果</div>',
                    unsafe_allow_html=True)
        col_img, col_result = st.columns([1, 1], gap="large")

        with col_img:
            img_bgr = cv2.cvtColor(np.array(uploaded_img), cv2.COLOR_RGB2BGR)
            with st.spinner("YOLOv11 偵測中 …"):
                dets = detect_objects(
                    yolo_model, img_bgr, settings["conf_thresh"])
            annotated = draw_annotations(img_bgr, dets)
            st.image(
                cv2.cvtColor(annotated, cv2.COLOR_BGR2RGB),
                caption=f"偵測到 {len(dets)} 個物件",
                use_container_width=True,
            )

        with col_result:
            with st.spinner("EasyOCR + 寫入資料庫 …"):
                pipeline_out = process_image(
                    uploaded_img, yolo_model, ocr_reader, conn,
                    conf_thresh=settings["conf_thresh"],
                    fuzz_thresh=settings["fuzz_thresh"],
                )
            for r in pipeline_out:
                icon  = "✅ 已新增" if r["status"] == "added" else "⏭️ 重複跳過"
                badge = "bg-green"  if r["status"] == "added" else "bg-grey"
                st.markdown(
                    f"<div style='margin:8px 0;padding:10px 14px;"
                    f"background:var(--bg2);border-radius:8px;"
                    f"border:1px solid var(--bord)'>"
                    f"<b>{r['item_name']}</b>&nbsp;"
                    f"<span class='badge {badge}'>{icon}</span>"
                    f"</div>",
                    unsafe_allow_html=True,
                )
                c1, c2 = st.columns(2)
                c1.metric("信心分數", f"{r['confidence']:.0%}")
                c2.metric("辨識到期日", r["expiry_date"] or "未偵測")
                if r["ocr_text"]:
                    with st.expander("OCR 原始文字"):
                        st.code(r["ocr_text"], language=None)

    # ── Inventory display ─────────────────────────────────────────
    st.markdown('<div class="sec-title">📋 目前庫存（依剩餘天數排序）</div>',
                unsafe_allow_html=True)

    df = db_fetch_all(conn)
    render_metrics(df, settings["expire_crit"])

    tab_card, tab_table, tab_about = st.tabs(["🃏 卡片檢視", "📊 表格檢視", "ℹ️ 系統說明"])

    with tab_card:
        render_cards(df, settings["expire_crit"], settings["expire_warn"])

    with tab_table:
        if df.empty:
            st.info("暫無資料。")
        else:
            display_df = df.drop(columns=["id"], errors="ignore")
            st.dataframe(display_df, use_container_width=True, hide_index=True)

            st.markdown("#### ✏️ 快速修改數量")
            opts = {f"[{r.id}] {getattr(r,'品項名稱','?')}": r.id
                    for r in df.itertuples()}
            sel = st.selectbox("選擇品項", list(opts.keys()),
                               index=None, placeholder="選擇…", key="qty_sel")
            if sel:
                cur = int(df.loc[df["id"] == opts[sel], "數量"].values[0])
                new_qty = st.number_input("新數量", 0, 999, cur, key="qty_inp")
                if st.button("更新數量", key="qty_btn"):
                    db_update_quantity(conn, opts[sel], int(new_qty))
                    st.success("✅ 已更新！")
                    st.rerun()

    with tab_about:
        st.markdown("""
### 系統架構

| 層次 | 技術 | 說明 |
|------|------|------|
| 物件偵測 | **YOLOv11** (`yolo11n.pt`) | 識別圖中物品 BBox |
| 文字辨識 | **EasyOCR** (`ch_sim` + `en`) | 提取包裝上文字 |
| 日期解析 | **Regex** × 7 種格式 | 解析到期日 → ISO 8601 |
| 去重檢查 | **SQLite** + **rapidfuzz** | 精確 + 模糊去重 |
| 持久化   | **SQLite** (`inventory.db`) | 本地資料庫 |
| 前端     | **Streamlit** + 自定義 CSS | 深色模式 + 動畫 |

### CSS 動畫說明
- **slide-up** — 所有卡片載入時由下往上淡入，每張卡片 stagger 70 ms
- **pulse-red** — 剩餘天數 ≤ 緊急閾值（預設 3 天）或已過期，紅光持續脈動

### 快速開始
```bash
pip install streamlit ultralytics easyocr opencv-python-headless pillow rapidfuzz
streamlit run app.py
```
        """)

    conn.close()


if __name__ == "__main__":
    main()
