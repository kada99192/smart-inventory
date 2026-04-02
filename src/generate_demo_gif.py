#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_demo_gif.py — 自動產生 Demo 動畫 GIF
展示：slide-up 卡片入場動畫 + pulse-red 紅光警示效果 + 深色主題
輸出：demo_animation.gif（可直接嵌入 GitHub README）

執行方式：
    python generate_demo_gif.py
    python generate_demo_gif.py --output my_demo.gif
    python generate_demo_gif.py --fps 30 --width 900
"""

import argparse
import math
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

# ── Auto-install Pillow ───────────────────────────────────────────
try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("[setup] Installing Pillow …")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "Pillow", "-q"])
    from PIL import Image, ImageDraw, ImageFont

# ═══════════════════════════════════════════════════════════════════
# §1  COLOR PALETTE  (mirrors app.py CSS variables)
# ═══════════════════════════════════════════════════════════════════
C_BG0    = (13,  17,  23)    # --bg0   page background
C_BG1    = (22,  27,  39)    # --bg1   sidebar / header
C_BG2    = (28,  35,  51)    # --bg2   card background
C_BORD   = (42,  51,  71)    # --bord  border
C_RED    = (239, 68,  68)    # --red
C_GREEN  = (34,  197, 94)    # --green
C_YELLOW = (245, 158, 11)    # --yell
C_BLUE   = (79,  142, 247)   # --acc   accent
C_TXT    = (226, 232, 240)   # --txt   primary text
C_MUTED  = (136, 146, 164)   # --muted secondary text

BADGE_STYLES = {
    "red":    {"bg": (69, 10, 10),   "fg": (248, 113, 113)},
    "yellow": {"bg": (69, 26, 3),    "fg": (251, 191, 36)},
    "green":  {"bg": (20, 83, 45),   "fg": (74,  222, 128)},
    "grey":   {"bg": (30, 41, 59),   "fg": (148, 163, 184)},
}

# ═══════════════════════════════════════════════════════════════════
# §2  DEMO DATA
# ═══════════════════════════════════════════════════════════════════
TODAY = date.today()

DEMO_CARDS = [
    {
        "icon":      "●",          # 🔴 → rendered as colored dot
        "name":      "全脂鮮奶",
        "meta":      f"到期 {(TODAY - timedelta(5)).isoformat()}  ·  數量 1  ·  信心 94%",
        "badge_txt": "已過期 5 天",
        "badge":     "red",
        "accent":    C_RED,
        "urgency":   "pulse",
    },
    {
        "icon":      "●",
        "name":      "雞蛋（10入）",
        "meta":      f"到期 {(TODAY + timedelta(2)).isoformat()}  ·  數量 6  ·  信心 88%",
        "badge_txt": "剩 2 天 ⚠",
        "badge":     "red",
        "accent":    C_RED,
        "urgency":   "pulse",
    },
    {
        "icon":      "●",
        "name":      "義大利麵條",
        "meta":      f"到期 {(TODAY + timedelta(365)).isoformat()}  ·  數量 2  ·  信心 91%",
        "badge_txt": "剩 365 天",
        "badge":     "green",
        "accent":    C_GREEN,
        "urgency":   "safe",
    },
]

# ═══════════════════════════════════════════════════════════════════
# §3  FONT LOADER
# ═══════════════════════════════════════════════════════════════════
_FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Light.ttc",          # macOS CJK
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",    # Linux CJK
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",  # Latin fallback
]

def _find_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# Pre-load font sizes
class Fonts:
    title  = _find_font(20)
    name   = _find_font(17)
    meta   = _find_font(13)
    badge  = _find_font(12)
    kpi    = _find_font(22)
    kpi_lbl= _find_font(11)
    tag    = _find_font(13)

# ═══════════════════════════════════════════════════════════════════
# §4  DRAWING PRIMITIVES
# ═══════════════════════════════════════════════════════════════════

def rgba(rgb: tuple, a: int) -> tuple:
    return (*rgb, a)


def draw_rounded_rect(
    draw: ImageDraw.ImageDraw,
    xy: tuple,
    radius: int,
    fill: tuple,
    outline: tuple | None = None,
    outline_width: int = 1,
) -> None:
    draw.rounded_rectangle(xy, radius=radius, fill=fill,
                           outline=outline, width=outline_width)


def draw_glow(
    base: Image.Image,
    card_xy: tuple,
    color: tuple,
    intensity: float,    # 0.0 – 1.0
    layers: int = 6,
    max_spread: int = 18,
) -> None:
    """Simulate CSS box-shadow glow by compositing semi-transparent layers."""
    x1, y1, x2, y2 = card_xy
    for i in range(layers, 0, -1):
        spread = int(max_spread * (i / layers) * intensity)
        alpha  = int(90 * (1 - i / layers) * intensity)
        if alpha < 4 or spread < 1:
            continue
        glow_layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_layer)
        gd.rounded_rectangle(
            (x1 - spread, y1 - spread, x2 + spread, y2 + spread),
            radius=12 + spread,
            fill=(*color, alpha),
        )
        base.alpha_composite(glow_layer)


def draw_card(
    canvas: Image.Image,
    card: dict,
    x: int,
    y: int,
    w: int,
    h: int,
    alpha: float = 1.0,        # overall card opacity (0–1, for slide-up)
    glow_intensity: float = 0, # 0–1, for pulse-red
) -> None:
    """Draw one inventory card onto canvas (RGBA)."""
    if alpha <= 0:
        return

    card_layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(card_layer)

    card_alpha = int(255 * alpha)

    # ── glow (drawn behind card) ──────────────────────────────────
    if glow_intensity > 0.05:
        draw_glow(card_layer, (x, y, x + w, y + h),
                  card["accent"], glow_intensity)

    # ── card background ───────────────────────────────────────────
    draw_rounded_rect(
        d, (x, y, x + w, y + h),
        radius=10,
        fill=(*C_BG2, card_alpha),
        outline=(*card["accent"], min(card_alpha, int(200 * alpha))) if glow_intensity > 0.1
                else (*C_BORD, card_alpha),
        outline_width=1,
    )

    # ── left accent bar ───────────────────────────────────────────
    draw_rounded_rect(
        d, (x, y, x + 4, y + h),
        radius=2,
        fill=(*card["accent"], card_alpha),
    )

    # ── icon dot ─────────────────────────────────────────────────
    dot_x, dot_y = x + 20, y + 22
    dot_r = 5
    d.ellipse(
        (dot_x - dot_r, dot_y - dot_r, dot_x + dot_r, dot_y + dot_r),
        fill=(*card["accent"], card_alpha),
    )

    # ── item name ─────────────────────────────────────────────────
    d.text((x + 34, y + 14), card["name"],
           font=Fonts.name, fill=(*C_TXT, card_alpha))

    # ── badge ─────────────────────────────────────────────────────
    badge_style = BADGE_STYLES[card["badge"]]
    badge_text  = card["badge_txt"]

    # Measure badge width
    bbox = Fonts.badge.getbbox(badge_text)
    b_w  = (bbox[2] - bbox[0]) + 22
    b_h  = 20
    b_x  = x + w - b_w - 16
    b_y  = y + 14

    draw_rounded_rect(
        d, (b_x, b_y, b_x + b_w, b_y + b_h),
        radius=10,
        fill=(*badge_style["bg"], card_alpha),
    )
    d.text(
        (b_x + 11, b_y + 3),
        badge_text,
        font=Fonts.badge,
        fill=(*badge_style["fg"], card_alpha),
    )

    # ── meta line ─────────────────────────────────────────────────
    d.text((x + 34, y + 48), card["meta"],
           font=Fonts.meta, fill=(*C_MUTED, card_alpha))

    # ── type tag ──────────────────────────────────────────────────
    d.text((x + 34, y + 72), f"# {card['urgency']}",
           font=Fonts.tag, fill=(*card["accent"], min(card_alpha, 130)))

    canvas.alpha_composite(card_layer)


def draw_header(canvas: Image.Image) -> None:
    """Draw the top bar and KPI metrics."""
    d = ImageDraw.Draw(canvas)

    # ── top bar ───────────────────────────────────────────────────
    d.rectangle((0, 0, canvas.width, 56), fill=(*C_BG1, 255))
    d.text((20, 14), "🏠  智慧家居物資管理系統",
           font=Fonts.title, fill=(*C_TXT, 255))
    d.text((canvas.width - 230, 18),
           "YOLOv11 × EasyOCR × SQLite",
           font=Fonts.meta, fill=(*C_MUTED, 255))

    # ── section label ─────────────────────────────────────────────
    d.text((20, 68), "📋  目前庫存（依剩餘天數排序）",
           font=Fonts.tag, fill=(*C_MUTED, 255))

    # ── divider ───────────────────────────────────────────────────
    d.line((20, 88, canvas.width - 20, 88), fill=(*C_BORD, 200), width=1)

    # ── KPI metrics (right side) ──────────────────────────────────
    metrics = [
        ("3",  "總品項",   C_BLUE),
        ("1",  "安全",     C_GREEN),
        ("1",  "即將到期", C_YELLOW),
        ("1",  "已過期",   C_RED),
    ]
    kpi_x = canvas.width - 340
    kpi_y = 60
    for i, (val, lbl, color) in enumerate(metrics):
        mx = kpi_x + i * 82
        d.text((mx, kpi_y - 2), val, font=Fonts.kpi, fill=(*color, 255))
        d.text((mx, kpi_y + 22), lbl, font=Fonts.kpi_lbl, fill=(*C_MUTED, 200))


# ═══════════════════════════════════════════════════════════════════
# §5  EASING FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def ease_out_cubic(t: float) -> float:
    """Deceleration curve — mimics CSS cubic-bezier(.22,.61,.36,1)."""
    return 1.0 - (1.0 - min(max(t, 0), 1)) ** 3


def pulse_sin(t: float) -> float:
    """0 → 1 → 0 sine pulse (one full cycle at t=0..1)."""
    return 0.5 + 0.5 * math.sin(2 * math.pi * t - math.pi / 2)


# ═══════════════════════════════════════════════════════════════════
# §6  ANIMATION SEQUENCE
# ═══════════════════════════════════════════════════════════════════

def build_frames(width: int = 820, fps: int = 30) -> list[Image.Image]:
    """
    Animation timeline (at 30 fps):
      Phase A  0 – 0.4s  →  12 frames  hold (empty state)
      Phase B  0.4 – 1.6s → 36 frames  slide-up (3 cards × 12f each)
      Phase C  1.6 – 2.0s → 12 frames  hold (all visible)
      Phase D  2.0 – 5.9s → 118 frames pulse-red (2 full cycles ≈ 1.9s each)
      Total ≈ 178 frames, ~6 seconds per loop
    """
    H_CANVAS = 500
    CARD_W   = width - 60
    CARD_H   = 106
    CARD_X   = 30
    CARD_Y0  = 100   # y of first card top
    CARD_GAP = 14
    SLIDE_PX = 28    # slide-up offset pixels (matches CSS translateY(28px))

    frames: list[Image.Image] = []

    def card_y(idx: int) -> int:
        return CARD_Y0 + idx * (CARD_H + CARD_GAP)

    # ── timing (frames) ──────────────────────────────────────────
    HOLD_START  = int(fps * 0.4)   # hold before slide
    SLIDE_DUR   = int(fps * 0.42)  # duration of one card slide
    STAGGER     = int(fps * 0.12)  # delay between card starts
    HOLD_END    = int(fps * 0.4)
    PULSE_CYCLE = int(fps * 1.9)
    PULSE_REPS  = 3

    slide_starts = [HOLD_START + i * STAGGER for i in range(len(DEMO_CARDS))]
    all_done     = slide_starts[-1] + SLIDE_DUR
    pulse_start  = all_done + HOLD_END
    total_frames = pulse_start + PULSE_CYCLE * PULSE_REPS + HOLD_START

    for f in range(total_frames):

        # ── base canvas (RGBA for compositing) ───────────────────
        canvas = Image.new("RGBA", (width, H_CANVAS), (*C_BG0, 255))
        draw_header(canvas)

        # ── determine per-card state ──────────────────────────────
        for i, card in enumerate(DEMO_CARDS):
            ss     = slide_starts[i]
            se     = ss + SLIDE_DUR
            cy     = card_y(i)

            # slide-up progress
            if f < ss:
                progress = 0.0
            elif f < se:
                progress = (f - ss) / SLIDE_DUR
            else:
                progress = 1.0

            eased = ease_out_cubic(progress)
            card_alpha = eased
            offset_y   = int(SLIDE_PX * (1.0 - eased))

            # pulse intensity (only for expired/critical, after all cards visible)
            glow = 0.0
            if card["urgency"] == "pulse" and f >= pulse_start:
                pt    = (f - pulse_start) / PULSE_CYCLE
                glow  = pulse_sin(pt) * 0.85   # max 85% intensity

            draw_card(
                canvas,
                card,
                x=CARD_X,
                y=cy + offset_y,
                w=CARD_W,
                h=CARD_H,
                alpha=card_alpha,
                glow_intensity=glow,
            )

        # ── watermark ─────────────────────────────────────────────
        d = ImageDraw.Draw(canvas)
        d.text((width - 180, H_CANVAS - 22),
               "github.com/kada99192/smart-inventory",
               font=Fonts.kpi_lbl, fill=(*C_MUTED, 110))

        # Convert RGBA → RGB (GIF doesn't support full alpha)
        rgb_frame = Image.new("RGB", canvas.size, C_BG0)
        rgb_frame.paste(canvas, mask=canvas.split()[3])
        frames.append(rgb_frame)

    return frames


# ═══════════════════════════════════════════════════════════════════
# §7  GIF EXPORT
# ═══════════════════════════════════════════════════════════════════

def save_gif(frames: list[Image.Image], path: Path, fps: int) -> None:
    duration_ms = int(1000 / fps)
    print(f"  生成 {len(frames)} 幀，每幀 {duration_ms}ms …")

    # Quantize to 256 colours (required for GIF)
    quantized = []
    for img in frames:
        q = img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=0)
        quantized.append(q)

    quantized[0].save(
        path,
        save_all   = True,
        append_images = quantized[1:],
        loop       = 0,             # infinite loop
        duration   = duration_ms,
        optimize   = True,
    )
    size_kb = path.stat().st_size / 1024
    print(f"  輸出：{path.resolve()}")
    print(f"  大小：{size_kb:.0f} KB  （{size_kb/1024:.1f} MB）")


# ═══════════════════════════════════════════════════════════════════
# §8  MAIN
# ═══════════════════════════════════════════════════════════════════

def main() -> None:
    parser = argparse.ArgumentParser(
        description="智慧家居物資管理系統 — Demo GIF 產生器"
    )
    parser.add_argument("--output", default="demo_animation.gif",
                        help="輸出 GIF 路徑（預設：demo_animation.gif）")
    parser.add_argument("--fps",   type=int, default=30,
                        help="幀率（預設：30，建議 20-60）")
    parser.add_argument("--width", type=int, default=820,
                        help="畫布寬度（預設：820 px）")
    args = parser.parse_args()

    out_path = Path(args.output)

    print("\n" + "═" * 55)
    print("  智慧家居物資管理系統 — Demo GIF 產生器")
    print("═" * 55)
    print(f"\n  畫布：{args.width} × 500 px  ·  {args.fps} fps")
    print(f"  動畫：slide-up 入場  +  pulse-red ×3 循環")
    print(f"  字型：{_FONT_CANDIDATES[0] if Path(_FONT_CANDIDATES[0]).exists() else '系統預設'}\n")

    print("  [1/2] 建立動畫幀 …")
    frames = build_frames(width=args.width, fps=args.fps)
    print(f"        共 {len(frames)} 幀 ({len(frames)/args.fps:.1f} 秒/循環)")

    print("  [2/2] 匯出 GIF …")
    save_gif(frames, out_path, args.fps)

    print(f"\n  ✓ 完成！嵌入 README.md 使用以下語法：")
    print(f"    ![Demo]({out_path.name})\n")


if __name__ == "__main__":
    main()
