"""跨平台适配：ffmpeg 位置、字体查找、打开文件夹。

Windows / Linux 都能用；找不到就用安全回退。
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

HERE = Path(__file__).resolve().parent


# ---------------- ffmpeg ----------------
def find_ffmpeg():
    """优先用程序目录里的 ffmpeg(.exe)，其次系统 PATH。"""
    for name in ("ffmpeg.exe", "ffmpeg"):
        p = HERE / name
        if p.exists():
            return str(p)
    for name in ("ffmpeg", "ffmpeg.exe"):
        p = shutil.which(name)
        if p:
            return p
    return "ffmpeg"


FFMPEG = find_ffmpeg()


# ---------------- 字体 ----------------
# 显示名 → (Windows 粗体/常规, Linux fontconfig 家族名)
_FONT_SPECS = {
    "微软雅黑": ((r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc"),
             "Noto Sans CJK SC"),
    "思源黑体": ((r"C:\Windows\Fonts\NotoSansSC-VF.ttf", r"C:\Windows\Fonts\NotoSansSC-VF.ttf"),
             "Noto Sans CJK SC"),
    "宋体": ((r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\simsun.ttc"),
           "Noto Serif CJK SC"),
    "楷体": ((r"C:\Windows\Fonts\simkai.ttf", r"C:\Windows\Fonts\simkai.ttf"),
           "AR PL UKai CN"),
    "等线": ((r"C:\Windows\Fonts\Deng.ttf", r"C:\Windows\Fonts\Deng.ttf"),
           "Noto Sans CJK SC"),
    "黑体": ((r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\simhei.ttf"),
           "Noto Sans CJK SC"),
}

# Linux 上的备选顺序（fc-match 失败时逐个试）
_LINUX_FALLBACK = [
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/wenquanyi/wqy-microhei/wqy-microhei.ttc",
]

_cache = {}


def _fc_match(family, bold=False):
    if not shutil.which("fc-match"):
        return None
    pattern = f"{family}:bold" if bold else family
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", pattern],
                             capture_output=True, text=True, timeout=5)
        p = out.stdout.strip()
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    return None


def font_path(name, bold=False):
    """返回可用的字体文件路径。"""
    key = (name, bool(bold))
    if key in _cache:
        return _cache[key]

    win_pair, family = _FONT_SPECS.get(name, _FONT_SPECS["微软雅黑"])

    if IS_WIN:
        p = win_pair[0] if bold else win_pair[1]
        if os.path.exists(p):
            _cache[key] = p
            return p

    # Linux / macOS：先问 fontconfig
    p = _fc_match(family, bold)
    if p:
        _cache[key] = p
        return p

    # 再逐个试备选
    for cand in _LINUX_FALLBACK:
        if os.path.exists(cand):
            _cache[key] = cand
            return cand

    # 最后：Windows 路径（也许挂载了字体）
    for cand in win_pair:
        if os.path.exists(cand):
            _cache[key] = cand
            return cand

    raise RuntimeError(
        f"找不到中文字体（{name}）。\n"
        "Arch:  sudo pacman -S noto-fonts-cjk\n"
        "Debian: sudo apt install fonts-noto-cjk"
    )


def font_pair(name):
    return font_path(name, True), font_path(name, False)


# ---------------- 打开文件夹 ----------------
def open_path(path):
    """在文件管理器里打开目录（失败不抛异常）。"""
    path = str(path)
    try:
        if IS_WIN:
            os.startfile(path)  # noqa: S606
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def has_dragdrop():
    """拖拽需要 tkinterdnd2，缺了也能用「选择」按钮。"""
    try:
        import tkinterdnd2  # noqa: F401
        return True
    except Exception:
        return False
