#!/usr/bin/env bash
# 安装依赖（Arch / Debian / Fedora 通用写法）
set -e

echo "== 音乐可视化 · 依赖安装 =="

if command -v pacman >/dev/null 2>&1; then
    echo "检测到 Arch Linux"
    sudo pacman -S --needed --noconfirm python python-pillow python-numpy tk ffmpeg noto-fonts-cjk
    sudo pacman -S --needed --noconfirm python-pip
    pip install --user --break-system-packages tkinterdnd2 2>/dev/null || pip install --user tkinterdnd2
elif command -v apt >/dev/null 2>&1; then
    echo "检测到 Debian/Ubuntu"
    sudo apt update
    sudo apt install -y python3 python3-pil python3-numpy python3-tk ffmpeg fonts-noto-cjk python3-pip
    pip3 install --user --break-system-packages tkinterdnd2 2>/dev/null || pip3 install --user tkinterdnd2
elif command -v dnf >/dev/null 2>&1; then
    echo "检测到 Fedora"
    sudo dnf install -y python3 python3-pillow python3-numpy python3-tkinter ffmpeg google-noto-sans-cjk-fonts
    pip install --user tkinterdnd2
else
    echo "未识别的发行版，请手动安装：python3, Pillow, numpy, tkinter, ffmpeg, 中文字体"
    exit 1
fi

echo
echo "完成。运行： ./run.sh"
echo "如果拖拽不可用，装一下 tkinterdnd2（可选），用「选择」按钮也能用。"
