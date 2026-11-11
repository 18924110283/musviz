"""音乐可视化 —— 图形界面（可调排版）

拖入音频/图片，右侧选样式并微调参数，先预览再生成。
"""

import base64
import io
import json
import os
import queue
import sys
import threading
from pathlib import Path

import tkinter as tk
from tkinter import colorchooser, filedialog, messagebox
from tkinter import ttk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = PU.has_dragdrop()
except Exception:
    HAS_DND = False

import renderer as R
import viz as V
import platform_utils as PU
from ui_widgets import RoundedButton

HERE = Path(__file__).resolve().parent
CFG = HERE / "layout.json"
DEFAULT_CFG = HERE / "default.json"

BG = "#1b1a20"
CARD = "#26242c"
CARD2 = "#2f2c36"
FG = "#e9e6ef"
DIM = "#8d8799"
ACCENT = "#b98cf0"
ACCENT2 = "#7d5bb8"
OK = "#7ee0a8"

AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VID_EXT = {".mp4", ".webm", ".mov", ".mkv"}


class App:
    def __init__(self, root):
        self.root = root
        self.audio = None
        self.images = []
        self.tags = {}
        self.lyrics = []
        self.samples = None
        self.sr = None
        self.total = 0.0
        self.bars_cache = {}
        self.queue = queue.Queue()
        self.busy = False
        self.cancel_flag = False
        self.cover_override = False
        self.bg_video = None
        self._pct = 0
        self._img = None

        self.layout = R.apply_preset("黑胶经典")
        self._load_layout()

        root.title("音乐可视化")
        root.configure(bg=BG)
        root.geometry("1180x820")
        root.minsize(1040, 680)

        self.step = tk.StringVar(value="把音频拖进来开始")
        self.title_var = tk.StringVar()
        self.sub_var = tk.StringVar()
        self.out_var = tk.StringVar(value=str(HERE.parent / "voicevideo"))
        self.preset_var = tk.StringVar(value=self.layout.get("preset", "黑胶经典"))

        self._build()
        self._poll()

    # ---------------- 配置 ----------------
    def _load_layout(self):
        """优先恢复上次状态；没有就用个人默认。"""
        for f in (CFG, DEFAULT_CFG):
            if not f.exists():
                continue
            try:
                saved = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            base = R.apply_preset(saved.get("preset", self.layout.get("preset", "黑胶经典")))
            base.update({k: v for k, v in saved.items() if k in base})
            self.layout = base
            return

    def _save_default(self):
        try:
            DEFAULT_CFG.write_text(json.dumps(self.layout, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
            self.step.set("已设为个人默认")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("保存失败", str(exc))

    def _save_layout(self):
        try:
            CFG.write_text(json.dumps(self.layout, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        except Exception:
            pass

    # ---------------- UI ----------------
    def _build(self):
        wrap = tk.Frame(self.root, bg=BG)
        wrap.pack(fill="both", expand=True, padx=14, pady=12)

        left = tk.Frame(wrap, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        right = tk.Frame(wrap, bg=CARD, width=370)
        right.pack(side="right", fill="y", padx=(14, 0))
        right.pack_propagate(False)

        tk.Label(left, text="音乐可视化", bg=BG, fg=FG,
                 font=("Microsoft YaHei", 16, "bold")).pack(anchor="w")
        tk.Label(left, text="拖入 MP3 和图片 · 右侧调样式 · 先预览再生成", bg=BG, fg=DIM,
                 font=("Microsoft YaHei", 9)).pack(anchor="w", pady=(0, 8))

        self.drop = tk.Canvas(left, bg=CARD, height=96, highlightthickness=0)
        self.drop.pack(fill="x")
        self.drop.bind("<Configure>", lambda e: self._draw_drop())
        if HAS_DND:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<Drop>>", self._on_drop)
            self.drop.bind("<Button-1>", lambda e: self._pick_files())

        rows = tk.Frame(left, bg=BG)
        rows.pack(fill="x", pady=6)
        self.row_vars = {}
        for key, label in (("audio", "音频"), ("cover", "封面"), ("bg", "背景")):
            card = tk.Frame(rows, bg=CARD, height=38)
            card.pack(fill="x", pady=2)
            card.pack_propagate(False)
            tk.Label(card, text=label, bg=CARD, fg=ACCENT, width=4, anchor="w",
                     font=("Microsoft YaHei", 9, "bold")).pack(side="left", padx=(10, 0))
            v = tk.StringVar(value="—")
            lab = tk.Label(card, textvariable=v, bg=CARD, fg=DIM, anchor="w",
                           font=("Microsoft YaHei", 9))
            lab.pack(side="left", fill="x", expand=True, padx=4)
            RoundedButton(card, text="选择", command=lambda k=key: self._pick_one(k),
                          width=62, height=26, bg=CARD, fg=FG, fill=CARD2,
                          font=("Microsoft YaHei", 9)).pack(side="right", padx=8)
            self.row_vars[key] = (v, lab)

        opt = tk.Frame(left, bg=BG)
        opt.pack(fill="x", pady=2)
        for i, (label, var) in enumerate((("标题", self.title_var), ("副标题", self.sub_var),
                                          ("输出", self.out_var))):
            tk.Label(opt, text=label, bg=BG, fg=DIM, width=5, anchor="w",
                     font=("Microsoft YaHei", 9)).grid(row=i, column=0, sticky="w", pady=1)
            tk.Entry(opt, textvariable=var, bg=CARD, fg=FG, insertbackground=FG,
                     relief="flat", bd=0, font=("Microsoft YaHei", 9)).grid(
                row=i, column=1, sticky="we", ipady=4, pady=1)
        opt.columnconfigure(1, weight=1)

        # 先把底部固定元素 pack 好，再让预览吃掉剩余空间
        bottom = tk.Frame(left, bg=BG)
        bottom.pack(side="bottom", fill="x")

        self.preview = tk.Canvas(left, bg=CARD2, highlightthickness=0, height=220)
        self.preview.pack(fill="both", expand=True, pady=(8, 6))
        self.preview.bind("<Button-1>", self._drag_start)
        self.preview.bind("<B1-Motion>", self._drag_move)
        self.preview.bind("<ButtonRelease-1>", self._drag_end)
        self._disp = {"ratio": 0.0, "ox": 0, "oy": 0, "w": 0, "h": 0}
        self._drag_key = None

        btns = tk.Frame(bottom, bg=BG)
        btns.pack(fill="x")
        RoundedButton(btns, text="预览一帧", command=self._preview, width=104, height=34,
                      bg=BG, fg=FG, fill=CARD2, hover=ACCENT2,
                      font=("Microsoft YaHei", 10)).pack(side="left")
        self.go = RoundedButton(btns, text="生成视频", command=self._start, width=124, height=34,
                                bg=BG, fg="#1b1a20", fill=ACCENT, hover="#cba8ff",
                                font=("Microsoft YaHei", 10))
        self.go.pack(side="right")
        tk.Label(btns, textvariable=self.step, bg=BG, fg=DIM, anchor="w",
                 font=("Microsoft YaHei", 9)).pack(side="left", padx=12, fill="x", expand=True)

        self.bar = tk.Canvas(bottom, bg=BG, height=4, highlightthickness=0)
        self.bar.pack(fill="x", pady=(4, 0))
        self.bar.bind("<Configure>", lambda e: self._draw_bar(self._pct))

        self._build_style_panel(right)

    def _build_style_panel(self, p):
        tk.Label(p, text="样式与排版", bg=CARD, fg=FG,
                 font=("Microsoft YaHei", 12, "bold")).pack(anchor="w", padx=14, pady=(14, 6))

        row = tk.Frame(p, bg=CARD)
        row.pack(fill="x", padx=14)
        tk.Label(row, text="模板", bg=CARD, fg=DIM, width=5, anchor="w",
                 font=("Microsoft YaHei", 9)).pack(side="left")
        cb = ttk.Combobox(row, textvariable=self.preset_var, values=list(R.PRESETS.keys()),
                          state="readonly", width=14)
        cb.pack(side="left", fill="x", expand=True)
        cb.bind("<<ComboboxSelected>>", self._on_preset)

        specs = [
            ("唱片大小", "disc_r", 50, 280, 2),
            ("唱片 X", "disc_cx", 0, 1100, 5),
            ("唱片 Y", "disc_cy", 0, 700, 5),
            ("歌词字号", "lyric_size", 14, 44, 1),
            ("歌词行距", "lyric_lh", 50, 170, 2),
            ("歌词 X", "lyric_x", -1, 1100, 5),
            ("标题字号", "title_size", 16, 60, 1),
            ("标题 Y", "title_y", 0, 600, 5),
            ("音条数量", "bars_count", 16, 160, 4),
            ("音条高度", "bars_height", 10, 120, 2),
            ("背景模糊", "bg_blur", 0, 40, 1),
        ]
        self.sliders = {}
        box = tk.Frame(p, bg=CARD)
        box.pack(fill="both", expand=True, padx=14, pady=(6, 4))
        for name, key, lo, hi, step in specs:
            line = tk.Frame(box, bg=CARD)
            line.pack(fill="x")
            tk.Label(line, text=name, bg=CARD, fg=DIM, width=7, anchor="w",
                     font=("Microsoft YaHei", 8)).pack(side="left")
            var = tk.IntVar(value=int(self.layout.get(key, lo)))
            val = tk.Label(line, text=str(var.get()), bg=CARD, fg=ACCENT, width=4,
                           font=("Microsoft YaHei", 8))
            val.pack(side="right")
            s = tk.Scale(line, from_=lo, to=hi, resolution=step, orient="horizontal",
                         variable=var, showvalue=False, bg=CARD, fg=FG,
                         troughcolor=CARD2, highlightthickness=0, bd=0,
                         sliderrelief="flat", activebackground=ACCENT,
                         command=lambda v, k=key, lv=val: self._on_slide(k, v, lv))
            s.pack(side="left", fill="x", expand=True, padx=4)
            self.sliders[key] = var

        toggles = tk.Frame(box, bg=CARD)
        toggles.pack(fill="x", pady=(6, 0))
        self.t_disc = tk.IntVar(value=1 if self.layout["disc_on"] else 0)
        self.t_bars = tk.IntVar(value=1 if self.layout["bars_on"] else 0)
        self.t_tag = tk.IntVar(value=1 if self.layout["title_tag"] else 0)
        self.t_cover = tk.IntVar(value=0)
        tk.Checkbutton(toggles, text="用图片当封面", variable=self.t_cover, bg=CARD, fg=FG,
                       selectcolor=CARD2, activebackground=CARD, activeforeground=FG,
                       font=("Microsoft YaHei", 8), bd=0,
                       command=self._on_cover_override).pack(side="left", padx=(0, 6))
        for text, var, key in (("唱片", self.t_disc, "disc_on"),
                               ("音条", self.t_bars, "bars_on"),
                               ("NOW PLAYING", self.t_tag, "title_tag")):
            tk.Checkbutton(toggles, text=text, variable=var, bg=CARD, fg=FG,
                           selectcolor=CARD2, activebackground=CARD, activeforeground=FG,
                           font=("Microsoft YaHei", 8), bd=0,
                           command=lambda k=key: self._on_toggle(k)).pack(side="left", padx=(0, 6))

        # ---- 字体与颜色 ----
        fc = tk.Frame(p, bg=CARD)
        fc.pack(fill="x", padx=14, pady=(6, 2))
        tk.Label(fc, text="标题字体", bg=CARD, fg=DIM, width=7, anchor="w",
                 font=("Microsoft YaHei", 8)).grid(row=0, column=0, sticky="w", pady=1)
        self.title_font_var = tk.StringVar(value=self.layout.get("title_font", "微软雅黑"))
        ttk.Combobox(fc, textvariable=self.title_font_var, values=list(R.FONTS.keys()),
                     state="readonly", width=10).grid(row=0, column=1, sticky="we", padx=2)
        self.title_font_var.trace_add("write", lambda *_: self._on_font("title_font", self.title_font_var))

        tk.Label(fc, text="歌词字体", bg=CARD, fg=DIM, width=7, anchor="w",
                 font=("Microsoft YaHei", 8)).grid(row=1, column=0, sticky="w", pady=1)
        self.lyric_font_var = tk.StringVar(value=self.layout.get("lyric_font", "微软雅黑"))
        ttk.Combobox(fc, textvariable=self.lyric_font_var, values=list(R.FONTS.keys()),
                     state="readonly", width=10).grid(row=1, column=1, sticky="we", padx=2)
        self.lyric_font_var.trace_add("write", lambda *_: self._on_font("lyric_font", self.lyric_font_var))
        fc.columnconfigure(1, weight=1)

        colors = tk.Frame(p, bg=CARD)
        colors.pack(fill="x", padx=14, pady=(4, 2))
        self.swatches = {}
        for i, (label, key, default) in enumerate((
                ("标题色", "title_color", "#ffffff"),
                ("歌词色", "lyric_color", "#ffffff"),
                ("高亮色", "lyric_hi_color", ""),
                ("翻译色", "tr_color", "#b4aac8"))):
            tk.Label(colors, text=label, bg=CARD, fg=DIM,
                     font=("Microsoft YaHei", 8)).grid(row=0, column=i * 2, sticky="w", padx=(0, 3))
            sw = tk.Canvas(colors, width=26, height=16, bg=CARD, highlightthickness=0,
                           cursor="hand2")
            sw.grid(row=0, column=i * 2 + 1, padx=(0, 8))
            val = self.layout.get(key) or default or self.layout.get("lyric_color", "#ffffff")
            sw.configure(bg=val)
            sw.bind("<Button-1>", lambda e, k=key, c=sw: self._pick_color(k, c))
            self.swatches[key] = sw

        drow = tk.Frame(p, bg=CARD)
        drow.pack(fill="x", padx=14, pady=(6, 2))
        tk.Label(drow, text="预览中拖动", bg=CARD, fg=DIM, width=8, anchor="w",
                 font=("Microsoft YaHei", 8)).pack(side="left")
        self.drag_sel = tk.StringVar(value="唱片")
        for name in ("唱片", "歌词", "标题"):
            tk.Radiobutton(drow, text=name, value=name, variable=self.drag_sel, bg=CARD,
                           fg=FG, selectcolor=CARD2, activebackground=CARD,
                           activeforeground=FG, bd=0,
                           font=("Microsoft YaHei", 8)).pack(side="left")

        br = tk.Frame(p, bg=CARD)
        br.pack(fill="x", padx=14, pady=(0, 12))
        RoundedButton(br, text="设为默认", command=self._save_default, width=150, height=30,
                      bg=CARD, fg=FG, fill=CARD2, hover=ACCENT2,
                      font=("Microsoft YaHei", 9)).pack(side="left")
        RoundedButton(br, text="恢复默认", command=self._reset_preset, width=150, height=30,
                      bg=CARD, fg=FG, fill=CARD2, hover=ACCENT2,
                      font=("Microsoft YaHei", 9)).pack(side="right")

    # ---------------- 事件 ----------------
    def _on_preset(self, _=None):
        self.layout = R.apply_preset(self.preset_var.get())
        self._sync_ui()
        self.bars_cache.clear()
        self._save_layout()

    def _reset_preset(self):
        if DEFAULT_CFG.exists():
            try:
                saved = json.loads(DEFAULT_CFG.read_text(encoding="utf-8"))
                base = R.apply_preset(saved.get("preset", self.preset_var.get()))
                base.update({k: v for k, v in saved.items() if k in base})
                self.layout = base
                self._sync_ui()
                self.bars_cache.clear()
                self._save_layout()
                self._schedule_preview()
                self.step.set("已恢复个人默认")
                return
            except Exception:
                pass
        self.layout = R.apply_preset(self.preset_var.get())
        self._sync_ui()
        self.bars_cache.clear()
        self._save_layout()
        self._schedule_preview()

    def _sync_ui(self):
        for key, var in self.sliders.items():
            if key in self.layout:
                var.set(int(self.layout[key]))
        self.t_disc.set(1 if self.layout["disc_on"] else 0)
        self.t_bars.set(1 if self.layout["bars_on"] else 0)
        self.t_tag.set(1 if self.layout["title_tag"] else 0)

    def _on_slide(self, key, value, label):
        try:
            self.layout[key] = int(float(value))
        except Exception:
            return
        label.configure(text=str(self.layout[key]))
        if key == "bars_count":
            self.bars_cache.clear()
        self._save_layout()
        self._schedule_preview()

    def _on_font(self, key, var):
        name = var.get()
        if name in R.FONTS:
            self.layout[key] = name
            self._save_layout()
            self._schedule_preview()

    def _pick_color(self, key, sw):
        cur = self.layout.get(key, "#ffffff")
        rgb, hexv = colorchooser.askcolor(color=cur, title="选颜色")
        if hexv:
            self.layout[key] = hexv
            sw.configure(bg=hexv)
            self._save_layout()
            self._schedule_preview()

    def _schedule_preview(self):
        if not self.audio or self.busy:
            return
        if getattr(self, "_pv_job", None):
            try:
                self.root.after_cancel(self._pv_job)
            except Exception:
                pass
        self._pv_job = self.root.after(500, lambda: self._preview(quiet=True))

    def _on_cover_override(self):
        self.cover_override = bool(self.t_cover.get())
        self.bars_cache.clear()
        self._refresh()

    def _on_toggle(self, key):
        var = {"disc_on": self.t_disc, "bars_on": self.t_bars, "title_tag": self.t_tag}[key]
        self.layout[key] = bool(var.get())
        if key in ("disc_on", "bars_on"):
            self.bars_cache.clear()
        self._save_layout()

    def _draw_drop(self):
        c = self.drop
        c.delete("all")
        w, h = c.winfo_width(), c.winfo_height()
        c.create_rectangle(5, 5, w - 5, h - 5, outline="#4b4658", dash=(5, 4), width=1)
        if self.audio is None:
            c.create_text(w / 2, h / 2 - 8, text="拖文件到这里", fill=FG,
                          font=("Microsoft YaHei", 12, "bold"))
            c.create_text(w / 2, h / 2 + 14, text="音频 + 图片（音频必需）· 也可以点击选择",
                          fill=DIM, font=("Microsoft YaHei", 8))
        else:
            c.create_text(14, 24, text="就绪", anchor="w", fill=OK,
                          font=("Microsoft YaHei", 10, "bold"))
            c.create_text(14, 47, text=Path(self.audio).name[:44], anchor="w", fill=FG,
                          font=("Microsoft YaHei", 9))
            extra = f"{len(self.images)} 张图片" if self.images else "图片来自 MP3 标签"
            c.create_text(14, 69, text=f"{self.total:.0f} 秒  ·  {extra}", anchor="w",
                          fill=DIM, font=("Microsoft YaHei", 8))

    def _draw_bar(self, pct):
        c = self.bar
        c.delete("all")
        w = c.winfo_width()
        c.create_rectangle(0, 1, w, 3, fill="#2f2c36", outline="")
        if pct > 0:
            c.create_rectangle(0, 1, int(w * min(1.0, pct)), 3, fill=ACCENT, outline="")

    # ---------------- 文件 ----------------
    def _pick_files(self):
        paths = filedialog.askopenfilenames(
            title="选择音频和图片",
            filetypes=[("音频/图片/视频",
                        "*.mp3 *.wav *.flac *.m4a *.ogg *.aac *.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.webm *.mov"),
                       ("所有文件", "*.*")])
        if paths:
            self._accept([Path(p) for p in paths])

    def _pick_one(self, key):
        if key == "audio":
            p = filedialog.askopenfilename(
                title="选择音频",
                filetypes=[("音频", "*.mp3 *.wav *.flac *.m4a *.ogg *.aac"), ("所有文件", "*.*")])
        else:
            p = filedialog.askopenfilename(
                title="选择图片",
                filetypes=[("背景（图片或视频）",
                            "*.png *.jpg *.jpeg *.webp *.bmp *.mp4 *.webm *.mov"),
                           ("所有文件", "*.*")])
        if not p:
            return
        p = Path(p)
        if key == "audio":
            self.audio = p
            self._load_audio_meta()
        elif key == "cover":
            rest = self.images[1:] if len(self.images) > 1 else []
            self.images = [p] + rest
        else:
            if p.suffix.lower() in VID_EXT:
                self.bg_video = p
            else:
                self.bg_video = None
                self.images = ([self.images[0]] if self.images else []) + [p]
        self._refresh()

    def _on_drop(self, event):
        raw = event.data
        parts, buf, depth = [], "", 0
        for ch in raw:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
            elif ch == " " and depth == 0:
                if buf:
                    parts.append(buf)
                    buf = ""
            else:
                buf += ch
        if buf:
            parts.append(buf)
        self._accept([Path(p) for p in parts if p])

    def _accept(self, paths):
        imgs = []
        for p in paths:
            sfx = p.suffix.lower()
            if sfx in AUDIO_EXT:
                self.audio = p
            elif sfx in VID_EXT:
                self.bg_video = p
            elif sfx in IMG_EXT:
                imgs.append(p)
        if imgs:
            self.images = imgs
        if self.audio:
            self._load_audio_meta()
        self._refresh()

    def _load_audio_meta(self):
        self.step.set("读取标签…")
        try:
            self.tags = V.read_tags(self.audio) or {}
            self.lyrics = self.tags.get("lyrics") or []
            self.total = R.duration_of(self.audio)
            if self.tags.get("title") and not self.title_var.get():
                self.title_var.set(self.tags["title"])
            if not self.sub_var.get():
                parts = [x for x in (self.tags.get("artist"), self.tags.get("album")) if x]
                if parts:
                    self.sub_var.set(" — ".join(parts))
        except Exception as exc:  # noqa: BLE001
            self.tags = {}
            self.lyrics = []
            self.step.set(f"读标签失败: {exc}")
        self.samples = None
        self.bars_cache.clear()

    def _refresh(self):
        def set_row(key, text, color):
            v, lab = self.row_vars[key]
            v.set(text)
            lab.configure(fg=color)

        if self.audio:
            set_row("audio", Path(self.audio).name, FG)

        has_tag_cover = bool(self.tags.get("cover"))
        if has_tag_cover and not self.cover_override:
            set_row("cover", "MP3 内嵌封面（自动）", OK)
        elif self.images:
            set_row("cover", Path(self.images[0]).name, FG)
        elif has_tag_cover:
            set_row("cover", "MP3 内嵌封面（自动）", OK)
        else:
            set_row("cover", "—", DIM)

        if self.bg_video:
            set_row("bg", Path(self.bg_video).name + "（视频）", OK)
        elif len(self.images) >= 2:
            set_row("bg", Path(self.images[1]).name, FG)
        elif self.images and has_tag_cover and not self.cover_override:
            set_row("bg", Path(self.images[0]).name, FG)
        elif self.audio:
            set_row("bg", "封面羽化（自动）", OK)
        self._draw_drop()
        if self.audio:
            self.step.set("可以预览或生成了")

    # ---------------- 渲染 ----------------
    def _prepare(self):
        if self.audio is None:
            raise RuntimeError("先拖入音频")

        # 封面优先用 MP3 内嵌；没有内嵌时用第一张图
        if self.tags.get("cover") and not self.cover_override:
            cover = R.prep_image(self.tags["cover"], min_side=700)
        elif self.images:
            cover = R.prep_image(self.images[0], min_side=700)
        elif self.tags.get("cover"):
            cover = R.prep_image(self.tags["cover"], min_side=700)
        else:
            raise RuntimeError("没有封面：MP3 里没内嵌封面，也没选图")

        has_tag_cover = bool(self.tags.get("cover"))
        if len(self.images) >= 2:
            bg = R.prep_image(self.images[1], min_side=1100)
        elif self.images and has_tag_cover and not self.cover_override:
            bg = R.prep_image(self.images[0], min_side=1100)   # 唯一那张图当背景
        else:
            bg = cover

        if self.samples is None:
            (samples, sr), tmp = R.precache_audio(self.audio, HERE)
            self.samples, self.sr = samples, sr
            try:
                tmp.unlink()
            except OSError:
                pass
        if not self.total:
            self.total = R.duration_of(self.audio)

        tags = dict(self.tags)
        tags["title"] = self.title_var.get() or tags.get("title") or Path(self.audio).stem
        tags["sub"] = self.sub_var.get() or tags.get("sub", "")
        return cover, bg, self.samples, self.sr, tags, self.lyrics

    def _make_renderer(self, half=False):
        cover, bg, samples, sr, tags, lyrics = self._prepare()
        lay = dict(self.layout)
        if half:
            lay["w"] = 960
            lay["h"] = 540
        key = (str(self.audio), lay["bars_count"], lay["fps"])
        rd = R.Renderer(cover, bg, lay, self.total, samples, sr, tags, lyrics,
                        bars=self.bars_cache.get(key), bg_video=self.bg_video)
        if lay["bars_on"] and rd.bars is not None:
            self.bars_cache[key] = rd.bars
        return rd

    def _preview(self, quiet=False):
        if self.busy:
            return
        try:
            if not quiet:
                self.step.set("准备预览…")
                self.root.update_idletasks()
            rd = self._make_renderer(half=True)
            t = self.total * 0.42
            img = rd.frame(t, sequential=False).convert("RGB")
            cw = max(320, self.preview.winfo_width())
            ch = max(200, self.preview.winfo_height())
            scale = min(cw / img.width, ch / img.height)
            disp = img.resize((int(img.width * scale), int(img.height * scale)), 1)
            self._img = tk.PhotoImage(data=base64.b64encode(self._png(disp)))
            self.preview.delete("all")
            ox = (cw - disp.width) // 2
            oy = (ch - disp.height) // 2
            self.preview.create_image(ox, oy, anchor="nw", image=self._img, tags="img")
            # 基准坐标(1280 宽) → 屏幕像素
            self._disp = {"ratio": disp.width / 1280.0, "ox": ox, "oy": oy,
                          "w": disp.width, "h": disp.height}
            if not quiet:
                self.step.set(f"预览: 第 {t:.0f} 秒 · 可拖动唱片/歌词")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("预览失败", str(exc))
            self.step.set("预览失败")

    # ---------- 手拖定位 ----------
    def _to_base(self, sx, sy):
        d = self._disp
        if d["ratio"] <= 0:
            return None
        bx = (sx - d["ox"]) / d["ratio"]
        by = (sy - d["oy"]) / d["ratio"]
        return max(0, min(1280, bx)), max(0, min(720, by))

    def _drag_start(self, e):
        if self._disp["ratio"] <= 0:
            return
        self.drag_target = tk.StringVar if False else None
        target = self.drag_sel.get() if hasattr(self, "drag_sel") else "唱片"
        self._drag_key = target
        self.preview.delete("mark")
        self.preview.create_line(0, e.y, 9999, e.y, fill="#b98cf0", dash=(3, 3), tags="mark")
        self.preview.create_line(e.x, 0, e.x, 9999, fill="#b98cf0", dash=(3, 3), tags="mark")

    def _drag_move(self, e):
        if self._drag_key is None or self._disp["ratio"] <= 0:
            return
        self.preview.delete("mark")
        self.preview.create_line(0, e.y, 9999, e.y, fill="#b98cf0", dash=(3, 3), tags="mark")
        self.preview.create_line(e.x, 0, e.x, 9999, fill="#b98cf0", dash=(3, 3), tags="mark")
        p = self._to_base(e.x, e.y)
        if not p:
            return
        bx, by = int(p[0]), int(p[1])
        k = self._drag_key
        if k == "唱片":
            self.layout["disc_cx"] = bx
            self.layout["disc_cy"] = by
        elif k == "歌词":
            self.layout["lyric_x"] = bx
            self.layout["lyric_cy"] = by
        elif k == "标题":
            self.layout["title_x"] = bx
            self.layout["title_y"] = by
        self.preview.create_text(e.x + 12, e.y - 12, anchor="w", tags="mark",
                                 text=f"{bx}, {by}", fill="#e9e6ef",
                                 font=("Microsoft YaHei", 9))

    def _drag_end(self, _e):
        if self._drag_key is None:
            return
        self._drag_key = None
        self.preview.delete("mark")
        self._sync_ui()
        self._save_layout()
        self._preview(quiet=True)

    @staticmethod
    def _png(img):
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def _start(self):
        if self.busy:
            return
        if not self.audio:
            messagebox.showinfo("还差音频", "先把 MP3 拖进来。")
            return
        self.busy = True
        self.cancel_flag = False
        self.go.set_state("disabled")
        self.go.set_text("生成中…")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        try:
            rd = self._make_renderer(half=False)
            out_dir = Path(self.out_var.get().strip() or str(HERE.parent / "voicevideo"))
            out_dir.mkdir(parents=True, exist_ok=True)
            out = out_dir / (Path(self.audio).stem + "_可视化.mp4")

            def prog(i, n, eta=0):
                left = int(eta)
                if left >= 60:
                    txt = f"渲染 {i}/{n} 帧 · 剩余 {left // 60} 分 {left % 60} 秒"
                else:
                    txt = f"渲染 {i}/{n} 帧 · 剩余 {left} 秒"
                self.queue.put(("progress", (i / n, txt)))

            rd.render(self.audio, out, progress=prog, cancel=lambda: self.cancel_flag)
            self.queue.put(("done", str(out)))
        except Exception as exc:  # noqa: BLE001
            self.queue.put(("err", f"{type(exc).__name__}: {exc}"))
        finally:
            self.queue.put(("finish", None))

    def _poll(self):
        try:
            while True:
                kind, payload = self.queue.get_nowait()
                if kind == "progress":
                    pct, text = payload
                    self._pct = pct
                    self._draw_bar(pct)
                    self.step.set(text)
                elif kind == "done":
                    self._pct = 1
                    self._draw_bar(1)
                    self.step.set("完成")
                    if messagebox.askyesno("生成完成", "打开文件夹看看？"):
                        PU.open_path(Path(payload).parent)
                elif kind == "err":
                    messagebox.showerror("出错了", str(payload))
                    self.step.set("出错了")
                elif kind == "finish":
                    self.busy = False
                    self.go.set_state("normal")
                    self.go.set_text("生成视频")
        except queue.Empty:
            pass
        self.root.after(120, self._poll)


def main():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    App(root)
    if "--selftest" in sys.argv:
        root.after(1500, root.destroy)
    root.mainloop()


if __name__ == "__main__":
    main()
