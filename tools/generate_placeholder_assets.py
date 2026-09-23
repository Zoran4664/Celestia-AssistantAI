"""
tools/generate_placeholder_assets.py — 生成占位资源

当 roles_img / roles_desktop / theme 为空目录时运行本脚本，
生成一套可直接使用的占位立绘、桌宠动图与主题背景，保证界面开箱即渲染。

用法：python tools/generate_placeholder_assets.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config_loader import ConfigLoader  # noqa: E402

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter  # type: ignore
except ImportError:
    print("[gen] Pillow 未安装，跳过资源生成。pip install Pillow")
    sys.exit(0)

EMOTIONS = {
    "neutral": ("#8e9aaf", "平静"),
    "happy": ("#e8a33d", "开心"),
    "sad": ("#5b7ee6", "难过"),
    "angry": ("#d64545", "生气"),
    "surprised": ("#9b59b6", "惊讶"),
}
ACTIONS = ("standing", "run", "say1", "say2", "hello", "sleep", "work", "angry")


def _font(size: int) -> "ImageFont.ImageFont":
    # 字体按系统目录拼（%WINDIR% / 环境变量），不写死盘符路径
    win_fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    candidates = [
        str(win_fonts / "msyh.ttc"),          # Windows 微软雅黑
        str(win_fonts / "simhei.ttf"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in candidates:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def gen_theme(cfg: ConfigLoader) -> None:
    d = cfg.theme_dir
    d.mkdir(parents=True, exist_ok=True)
    out = d / "default.png"
    if out.exists():
        return
    w, h = 900, 600
    img = Image.new("RGB", (w, h), "#eef1fb")
    draw = ImageDraw.Draw(img, "RGBA")
    for i in range(0, h, 8):
        ratio = i / h
        draw.line([(0, i), (w, i)], fill=(108, 142, 245, int(18 + 30 * ratio)))
    img = img.filter(ImageFilter.GaussianBlur(radius=6))
    img.save(out, "PNG")
    print(f"[gen] 主题背景 -> {out}")


def gen_portraits(cfg: ConfigLoader, role: str = "默认助手",
                  color: str = "#6c8ef5") -> None:
    base = cfg.roles_img_dir / role
    base.mkdir(parents=True, exist_ok=True)
    size = 300
    entries = list(EMOTIONS.items()) + [(None, None)]
    for name, pair in entries:
        suffix = f"-{name}" if name else "-"
        out = base / f"{role}{suffix}.png"
        if out.exists():
            continue
        ec, emoji = pair if pair else ("#8e9aaf", None)
        img = Image.new("RGB", (size, size), "#dfe6f5")
        draw = ImageDraw.Draw(img)
        draw.rounded_rectangle([8, 8, size - 8, size - 8], radius=24, fill=ec or color)
        if emoji:
            draw.text((size // 2 - 40, size // 2 - 55), emoji, fill="white",
                      font=_font(110), anchor="mm")
        draw.text((size // 2, size - 55), role, fill="white",
                  font=_font(30), anchor="mm")
        img.save(out, "PNG")
        print(f"[gen] 立绘 -> {out}")


def gen_pet_gifs(cfg: ConfigLoader, role: str = "默认助手",
                 color: str = "#6c8ef5") -> None:
    base = cfg.roles_desktop_dir / role
    base.mkdir(parents=True, exist_ok=True)
    for action in ACTIONS:
        out = base / f"{role}-{action}.gif"
        if out.exists():
            continue
        frames = []
        for i in range(8):
            img = Image.new("RGBA", (300, 400), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            x = 90 + (i * 7 if action in ("run",) else 0)
            y = 120 + (abs(3 - i) * 10 if action in ("standing", "run", "hello") else 0)
            if action == "sleep":
                y = 230
            draw.ellipse([x, y, x + 120, y + 120], fill=color)
            draw.text((x + 60, y + 60), _ACTION_GLYPH.get(action, ""),
                      fill="white", font=_font(44), anchor="mm")
            frames.append(img)
        frames[0].save(out, save_all=True, append_images=frames[1:],
                       duration=110, loop=0)
        print(f"[gen] 桌宠动图 -> {out}")


_ACTION_GLYPH = {
    "standing": "S", "run": "R", "say1": "…", "say2": "…",
    "hello": "Hi", "sleep": "Z", "work": "W", "angry": "!",
}


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="生成占位资源")
    parser.add_argument("--role", default="", help="为指定角色生成资源（默认仅默认助手）")
    args = parser.parse_args()
    cfg = ConfigLoader.instance()
    gen_theme(cfg)
    gen_portraits(cfg)
    gen_pet_gifs(cfg)
    if args.role:
        gen_portraits(cfg, role=args.role, color="#a855f7")
        gen_pet_gifs(cfg, role=args.role, color="#a855f7")
    print("[gen] 占位资源生成完成。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
