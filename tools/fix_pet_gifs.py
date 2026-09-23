"""修复 roles_desktop 中过小(无效)的 GIF：为所有角色重新生成有效的 say1/say2 动画。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from utils.utf8 import force_utf8_stdio
force_utf8_stdio()

from config_loader import ConfigLoader
from PIL import Image, ImageDraw, ImageFont

MIN_SIZE = 4000   # 与 pet_manager._pet_gif 的阈值一致

# 字体按系统目录拼（%WINDIR% / 环境变量），不写死盘符路径
_FONT_DIR = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
FONT_CANDS = [
    str(_FONT_DIR / "msyh.ttc"),      # Windows 微软雅黑
    str(_FONT_DIR / "simhei.ttf"),
]


def _font(size):
    for p in FONT_CANDS:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def gen_gif(out, color, glyph):
    frames = []
    for i in range(8):
        img = Image.new("RGBA", (300, 400), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        x = 90
        y = 120 + (abs(3 - i) * 10)
        draw.ellipse([x, y, x + 120, y + 120], fill=color)
        draw.text((x + 60, y + 60), glyph, fill="white", font=_font(44),
                  anchor="mm")
        frames.append(img)
    frames[0].save(out, save_all=True, append_images=frames[1:],
                   duration=110, loop=0)


def main():
    cfg = ConfigLoader.instance()
    base = cfg.roles_desktop_dir
    if not base.exists():
        print("roles_desktop 不存在，跳过")
        return
    for role_dir in sorted(base.iterdir()):
        if not role_dir.is_dir():
            continue
        for gif in sorted(role_dir.glob("*.gif")):
            try:
                too_small = gif.stat().st_size < MIN_SIZE
            except OSError:
                too_small = True
            if not too_small:
                continue
            # 只有 say1/say2 等需要重建（保留 standing 等无效的也重建，保证可用）
            name = gif.stem.split("-")[-1]
            glyph = {"say1": "…", "say2": "…", "standing": "S", "run": "R",
                     "hello": "Hi", "sleep": "Z", "work": "W",
                     "angry": "!"}.get(name, "!")
            color = "#6c8ef5"
            gif.unlink(missing_ok=True)
            try:
                gen_gif(gif, color, glyph)
                print(f"[fix] 重建 {gif.name} -> {gif.stat().st_size} 字节")
            except Exception as exc:
                print(f"[fix] 失败 {gif.name}: {exc}")
    print("完成")


if __name__ == "__main__":
    main()
