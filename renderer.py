"""渲染引擎：把「布局参数 + 素材」渲染成帧或视频。

布局参数全部以 1280x720 为基准，渲染时按实际分辨率缩放。
"""

import math
import subprocess
import time
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

import platform_utils as PU

HERE = PU.HERE
FFMPEG = PU.FFMPEG

FONT_B = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_R = r"C:\Windows\Fonts\msyh.ttc"
FONT_N = r"C:\Windows\Fonts\NotoSansSC-VF.ttf"

# 可选字体：名字 → 平台层解析
FONTS = {k: k for k in ("微软雅黑", "思源黑体", "宋体", "楷体", "等线", "黑体")}


def font_pair(name):
    if name not in FONTS:
        name = "微软雅黑"
    return PU.font_pair(name)


def rgba(hex_str, alpha=255):
    try:
        h = str(hex_str).lstrip("#")
        return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(alpha))
    except Exception:
        return (255, 255, 255, alpha)

# ---------------- 默认布局 ----------------
DEFAULT_LAYOUT = {
    "preset": "黑胶经典",
    "w": 1920, "h": 1080, "fps": 25,
    # 唱片
    "disc_on": True,
    "disc_r": 152,          # 半径（基准 1280 宽下的像素）
    "disc_cx": 400,
    "disc_cy": 372,
    "spin": 2.5,            # 度/秒
    "rim": 6,               # 黑边厚度
    # 歌词
    "lyric_on": True,
    "lyric_size": 26,
    "lyric_lh": 92,
    "lyric_x": 630,         # 左侧起点（基准坐标）；<=0 表示水平居中
    "lyric_cy": -1,         # 歌词中心 Y；-1 = 跟随唱片圆心
    "lyric_tr_size": 17,
    # 字体与颜色
    "title_font": "微软雅黑",
    "lyric_font": "微软雅黑",
    "title_color": "#ffffff",
    "lyric_color": "#ffffff",
    "lyric_hi_color": "",        # 空 = 同 lyric_color
    "tr_color": "#b4aac8",
    "tag_color": "#ba8cf0",
    # 标题
    "title_on": True,
    "title_size": 31,
    "title_x": 630,
    "title_y": 120,
    "title_tag": True,      # NOW PLAYING
    "title_underline": True,
    # 音条
    "bars_on": True,
    "bars_count": 96,
    "bars_height": 62,
    "bars_bottom": 12,      # 距底边
    "bars_width": 1152,     # 总宽
    # 背景
    "bg_blur": 9,
    "bg_dark": 0.62,
    "bg_vignette": 0.55,
    # 时间
    "fade_in": 1.5,
    "fade_out": 3.0,
    "lyric_trans": 0.35,
}

PRESETS = {
    "黑胶经典": {
        "disc_on": True, "disc_r": 152, "disc_cx": 400, "disc_cy": 372,
        "lyric_x": 630, "lyric_size": 26, "lyric_lh": 92,
        "title_x": 630, "title_y": 120, "title_size": 31,
        "bars_on": True, "bars_count": 96, "bars_height": 62,
    },
    "居中歌词": {
        "disc_on": True, "disc_r": 96, "disc_cx": 170, "disc_cy": 150,
        "lyric_x": -1, "lyric_size": 34, "lyric_lh": 110,
        "title_x": -1, "title_y": 70, "title_size": 34,
        "bars_on": True, "bars_count": 64, "bars_height": 48,
    },
    "纯文字": {
        "disc_on": False, "disc_r": 90, "disc_cx": 640, "disc_cy": 150,
        "lyric_x": -1, "lyric_size": 30, "lyric_lh": 104,
        "title_x": -1, "title_y": 90, "title_size": 28,
        "bars_on": True, "bars_count": 48, "bars_height": 34,
    },
    "无音条": {
        "disc_on": True, "disc_r": 170, "disc_cx": 380, "disc_cy": 372,
        "lyric_x": 640, "lyric_size": 28, "lyric_lh": 96,
        "title_x": 640, "title_y": 130, "title_size": 32,
        "bars_on": False, "bars_count": 96, "bars_height": 62,
    },
}


def apply_preset(name):
    lay = dict(DEFAULT_LAYOUT)
    lay.update(PRESETS.get(name, {}))
    lay["preset"] = name
    return lay


# ---------------- 工具 ----------------
def duration_of(path):
    out = subprocess.run([FFMPEG, "-i", str(path)], capture_output=True,
                         text=True, errors="ignore").stderr
    for line in out.splitlines():
        if "Duration" in line:
            hms = line.split("Duration:")[1].split(",")[0].strip()
            h, m, s = hms.split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0


def load_audio_mono(wav_path):
    with wave.open(str(wav_path), "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        raw = w.readframes(w.getnframes())
    d = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if ch == 2:
        d = d.reshape(-1, 2).mean(axis=1)
    return d, sr


def precache_audio(audio_path, workdir):
    tmp = Path(workdir) / "_render_tmp.wav"
    subprocess.run([FFMPEG, "-y", "-i", str(audio_path), "-ac", "1", "-ar", "44100", str(tmp)],
                   capture_output=True)
    return load_audio_mono(tmp), tmp


def precompute_bars(samples, sr, n_frames, n, fps, lo=55.0, hi=15000.0,
                    window_sec=0.7, falloff=0.72):
    win = 4096
    M = np.zeros((n_frames, n), dtype=np.float32)
    freqs = None
    edges = np.logspace(math.log10(lo), math.log10(hi), n + 1)
    for i in range(n_frames):
        st = max(0, int(i / fps * sr) - win // 2)
        seg = samples[st:st + win]
        if len(seg) < win:
            seg = np.pad(seg, (0, win - len(seg)))
        spec = np.abs(np.fft.rfft(seg * np.hanning(win)))
        if freqs is None:
            freqs = np.fft.rfftfreq(win, 1.0 / sr)
        for j in range(n):
            m = (freqs >= edges[j]) & (freqs < edges[j + 1])
            if m.any():
                M[i, j] = float(np.sqrt(np.mean(spec[m] ** 2)))
    w = max(3, int(window_sec * fps))
    pad = w // 2
    out = np.zeros_like(M)
    for j in range(n):
        col = np.pad(M[:, j], (pad, pad), mode="edge")
        sw = np.lib.stride_tricks.sliding_window_view(col, w)
        roll = sw.max(axis=1)[:n_frames]
        out[:, j] = M[:n_frames, j] / np.maximum(roll, 1e-9)
    out = np.clip(out, 0, 1) ** 1.15
    for i in range(1, n_frames):
        out[i] = np.maximum(out[i], out[i - 1] * falloff)
    return out


def prep_image(src, min_side=None):
    import io
    im = Image.open(io.BytesIO(src)) if isinstance(src, (bytes, bytearray)) else Image.open(src)
    im = im.convert("RGB")
    if min_side and min(im.size) < min_side:
        k = min_side / min(im.size)
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
        im = im.filter(ImageFilter.UnsharpMask(radius=1.5, percent=80, threshold=2))
    return im


# ---------------- 渲染器 ----------------
class Renderer:
    def __init__(self, cover, bg_img, layout, total, samples, sr, tags, lyrics,
                 bars=None, bg_video=None):
        self.lay = layout
        self.W = int(layout["w"])
        self.H = int(layout["h"])
        self.S = self.W / 1280.0
        self.total = total
        self.cover = cover
        self.lyrics = lyrics or []
        self.tags = tags or {}
        self.S_ = self.S

        self.bg_video = str(bg_video) if bg_video else None
        self._pipe = None
        self.bg = self._make_bg(bg_img) if not self.bg_video else None
        self.disc = self._make_disc() if layout["disc_on"] else None
        self.label = self._make_label() if layout["disc_on"] else None
        self.fps = int(layout["fps"])
        # 音条按当前布局的音条数量缓存
        self._bars_key = (layout["bars_count"], layout["fps"])
        if not layout["bars_on"]:
            self.bars = None
        elif bars is not None:
            self.bars = bars
        else:
            n = int(total * self.fps)
            self.bars = precompute_bars(samples, sr, n, int(layout["bars_count"]), self.fps)

    # ---- 视频背景 ----
    def _video_cmd(self, ss=None):
        cmd = [FFMPEG, "-loglevel", "error"]
        if ss is not None:
            cmd += ["-ss", f"{ss:.3f}"]
        cmd += ["-i", self.bg_video,
                "-vf", f"scale={self.W}:{self.H}:force_original_aspect_ratio=increase,"
                       f"crop={self.W}:{self.H}",
                "-f", "rawvideo", "-pix_fmt", "rgb24", "-r", str(int(self.lay["fps"])),
                "-an"]
        if ss is not None:
            cmd += ["-frames:v", "1"]
        return cmd + ["-"]

    def _open_pipe(self):
        self._close_pipe()
        self._pipe = subprocess.Popen(self._video_cmd(), stdout=subprocess.PIPE,
                                      stderr=subprocess.DEVNULL)

    def _close_pipe(self):
        if self._pipe:
            try:
                self._pipe.stdout.close()
                self._pipe.kill()
            except Exception:
                pass
            self._pipe = None

    def _read_video_frame(self, rescan=True):
        need = self.W * self.H * 3
        if self._pipe is None:
            self._open_pipe()
        data = self._pipe.stdout.read(need)
        if len(data) < need:
            if not rescan:
                return None
            self._open_pipe()
            data = self._pipe.stdout.read(need)
            if len(data) < need:
                return None
        return Image.frombytes("RGB", (self.W, self.H), data)

    def _video_frame_at(self, t):
        out = subprocess.run(self._video_cmd(ss=t), capture_output=True)
        need = self.W * self.H * 3
        if len(out.stdout) < need:
            return None
        return Image.frombytes("RGB", (self.W, self.H), out.stdout[:need])

    def _treat_bg(self, img):
        """模糊 + 压暗 + 暗角。先缩到 1/4 再模糊，速度差 20 倍。"""
        W, H, S = self.W, self.H, self.S
        small = img.resize((max(2, W // 4), max(2, H // 4)), Image.BILINEAR)
        blur = float(self.lay["bg_blur"])
        if blur > 0:
            small = small.filter(ImageFilter.GaussianBlur(blur * S / 4))
        small = small.resize((W, H), Image.BILINEAR)
        arr = np.asarray(small).astype(np.float32) * float(self.lay["bg_dark"])
        yy, xx = np.mgrid[0:H, 0:W]
        dist = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        vig = np.clip((dist - 0.40) / 0.90, 0, 1) ** 1.5 * float(self.lay["bg_vignette"])
        arr *= (1.0 - vig)[..., None]
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")

    # ---- 素材预处理 ----
    def _make_bg(self, bg_img):
        W, H, S = self.W, self.H, self.S
        src = bg_img.convert("RGB")
        target = W / H
        r = src.width / src.height
        if r > target:
            nw = int(src.height * target)
            left = (src.width - nw) // 2
            box = (left, 0, left + nw, src.height)
        else:
            nh = int(src.width / target)
            top = (src.height - nh) // 2
            box = (0, top, src.width, top + nh)
        bg = src.crop(box).resize((W, H), Image.LANCZOS)
        blur = float(self.lay["bg_blur"])
        if blur > 0:
            bg = bg.filter(ImageFilter.GaussianBlur(blur * S))
        arr = np.asarray(bg).astype(np.float32) * float(self.lay["bg_dark"])
        yy, xx = np.mgrid[0:H, 0:W]
        dist = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
        vig = np.clip((dist - 0.40) / 0.90, 0, 1) ** 1.5 * float(self.lay["bg_vignette"])
        arr *= (1.0 - vig)[..., None]
        return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")

    def _disc_pos(self):
        S = self.S
        R = int(float(self.lay["disc_r"]) * S)
        cx = int(float(self.lay["disc_cx"]) * S)
        cy = int(float(self.lay["disc_cy"]) * S)
        return cx, cy, R

    def _make_disc(self):
        cx, cy, R = self._disc_pos()
        rim = max(1, int(float(self.lay["rim"]) * self.S))
        size = R * 2
        disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(disc)
        d.ellipse([0, 0, size - 1, size - 1], fill=(16, 15, 20, 255))
        d.ellipse([0, 0, size - 1, size - 1], outline=(58, 56, 64, 235), width=rim)
        return disc

    def _make_label(self):
        cx, cy, R = self._disc_pos()
        rim = max(1, int(float(self.lay["rim"]) * self.S))
        s = max(8, (R - rim) * 2)
        cw, chh = self.cover.size
        side = min(cw, chh)
        left = int((cw - side) * 0.62)
        top = int((chh - side) * 0.30)
        img = self.cover.crop((left, top, left + side, top + side)).resize((s, s), Image.LANCZOS)
        mask = Image.new("L", (s, s), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, s - 1, s - 1], fill=255)
        out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        out.paste(img, (0, 0), mask)
        ImageDraw.Draw(out).ellipse([0, 0, s - 1, s - 1], outline=(14, 13, 18, 255),
                                    width=max(2, int(2 * self.S)))
        return out

    def _fonts(self):
        S = self.S
        L = self.lay
        tb, tr_ = font_pair(L.get("title_font", "微软雅黑"))
        lb, lr = font_pair(L.get("lyric_font", "微软雅黑"))
        return {
            "tag": ImageFont.truetype(FONT_N, max(8, int(13 * S))),
            "title": ImageFont.truetype(tb, max(10, int(float(L["title_size"]) * S))),
            "lyric": ImageFont.truetype(lr, max(10, int(float(L["lyric_size"]) * S))),
            "lyric_b": ImageFont.truetype(lb, max(10, int(float(L["lyric_size"]) * S))),
            "tr": ImageFont.truetype(lr, max(8, int(float(L["lyric_tr_size"]) * S))),
        }

    def _x_of(self, key):
        """返回 (x, 是否居中)。x <= 0 表示水平居中。"""
        v = int(float(self.lay.get(key, 0)))
        if v <= 0:
            return int(self.W * 0.5), True
        return int(v * self.S), False

    def lyric_x(self):
        return self._x_of("lyric_x")

    def title_x(self):
        return self._x_of("title_x")

    # ---- 单帧 ----
    def frame(self, t, sequential=True):
        S, lay = self.S, self.lay
        W, H = self.W, self.H
        if self.bg_video:
            img = (self._read_video_frame() if sequential else self._video_frame_at(t))
            base = self._treat_bg(img if img is not None else Image.new("RGB", (W, H), (20, 18, 26)))
        else:
            base = self.bg
        frame = base.copy()

        if self.disc:
            cx, cy, R = self._disc_pos()
            frame.alpha_composite(self.disc, (cx - R, cy - R))
            ang = (t * float(lay["spin"])) % 360
            lab = self.label.rotate(-ang, resample=Image.BICUBIC, center=(self.label.width / 2,
                                                                          self.label.height / 2))
            frame.alpha_composite(lab, (cx - self.label.width // 2, cy - self.label.height // 2))
            disc_cy = cy
        else:
            disc_cy = int(370 * S)

        d = ImageDraw.Draw(frame, "RGBA")
        fonts = self._fonts()

        # 标题
        if lay["title_on"] and self.tags.get("title"):
            tx, centered = self.title_x()
            ty = int(float(lay["title_y"]) * S)
            anchor = "ma" if centered else "la"
            title = self.tags["title"]
            if lay["title_tag"]:
                d.text((tx, ty), "NOW PLAYING", anchor=anchor, font=fonts["tag"],
                       fill=rgba(lay.get("tag_color", "#ba8cf0"), 235))
            d.text((tx, ty + int(22 * S)), title, anchor=anchor, font=fonts["title"],
                   fill=rgba(lay.get("title_color", "#ffffff"), 248))
            if lay["title_underline"]:
                tw = d.textlength(title, font=fonts["title"])
                y2 = ty + int(22 * S) + int(float(lay["title_size"]) * S) + int(10 * S)
                x0 = tx - tw / 2 if centered else tx
                d.rounded_rectangle([x0, y2, x0 + int(72 * S), y2 + max(2, int(1.5 * S))],
                                    max(1, int(1.5 * S)),
                                    fill=rgba(lay.get("tag_color", "#ba8cf0"), 220))

        # 歌词（Y 可独立指定，默认跟唱片圆心对齐）
        lcy = int(float(lay.get("lyric_cy", -1)))
        lcy = disc_cy if lcy <= 0 else int(lcy * S)
        if lay["lyric_on"] and self.lyrics:
            self._draw_lyrics(frame, d, fonts, t, lcy)
        elif lay["title_on"] and self.tags.get("sub"):
            _sx, _sc = self.title_x()
            d.text((_sx, lcy), self.tags["sub"], anchor="mm" if _sc else "lm",
                   font=fonts["lyric"], fill=(212, 200, 222, 200))

        # 音条
        if lay["bars_on"] and self.bars is not None:
            idx = min(len(self.bars) - 1, max(0, int(t * self.fps)))
            row = self.bars[idx]
            n = len(row)
            total_w = float(lay["bars_width"]) * S
            gap = max(1, int(4 * S))
            bw = max(1, int((total_w - gap * (n - 1)) / n))
            x0 = (W - (bw * n + gap * (n - 1))) // 2
            base_y = H - int(float(lay["bars_bottom"]) * S)
            maxh = float(lay["bars_height"]) * S
            for b in range(n):
                v = float(row[b])
                hgt = int(4 * S + v * maxh)
                x = x0 + b * (bw + gap)
                d.rounded_rectangle([x, base_y - hgt, x + bw, base_y], max(2, int(2 * S)),
                                    fill=(232, 216, 246, int(70 + 150 * v)))

        # 淡入淡出
        fi = float(lay["fade_in"])
        fo = float(lay["fade_out"])
        fade = min(1.0, t / fi if fi > 0 else 1.0,
                   max(0.0, (self.total - t) / fo) if fo > 0 else 1.0)
        if fade < 1.0:
            black = Image.new("RGBA", (W, H), (0, 0, 0, int((1 - fade) * 255)))
            frame = Image.alpha_composite(frame, black)
        return frame

    def _draw_lyrics(self, frame, d, fonts, t, center_y):
        S, lay = self.S, self.lay
        W, H = frame.size
        lyrics = self.lyrics
        TRANS = float(lay["lyric_trans"])
        lh = int(float(lay["lyric_lh"]) * S)

        cur = -1
        for idx, item in enumerate(lyrics):
            if item[0] <= t:
                cur = idx
        dt = 1.0 if cur < 0 else t - lyrics[cur][0]
        ease = min(1.0, dt / TRANS) if TRANS > 0 else 1.0
        ease = ease * ease * (3 - 2 * ease)
        shift = 1.0 - ease
        bright = min(1.0, dt / 0.12)
        tx, centered = self.lyric_x()
        anchor = "mm" if centered else "lm"

        layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer, "RGBA")

        hi_hex = (lay.get("lyric_hi_color") or "").strip() or lay.get("lyric_color", "#ffffff")
        hi = rgba(hi_hex)
        base = rgba(lay.get("lyric_color", "#ffffff"))
        tr_col = rgba(lay.get("tr_color", "#b4aac8"))
        accent = rgba(lay.get("tag_color", "#ba8cf0"))
        stroke = max(0, int(round(0.7 * S)))

        for idx in range(max(0, cur - 1), min(len(lyrics), cur + 3)):
            item = lyrics[idx]
            rel = (idx - cur) + shift
            y = center_y + int(rel * lh)
            active = idx == cur
            if active:
                a = int(225 + 30 * bright)
            elif idx == cur + 1:
                a = int(95 - 15 * ease)
            else:
                a = 80
            a = max(0, min(255, a))

            if active:
                # 描边加粗：任何字体都能看出"重"
                ld.text((tx, y), item[1], anchor=anchor, font=fonts["lyric_b"],
                        fill=(hi[0], hi[1], hi[2], 255),
                        stroke_width=stroke, stroke_fill=(hi[0], hi[1], hi[2], 255))
                # 左侧竖条：正在播放指示
                wlen = ld.textlength(item[1], font=fonts["lyric_b"])
                x0 = tx - wlen / 2 if centered else tx
                bar_x = x0 - int(16 * S)
                ld.rounded_rectangle([bar_x, y - int(20 * S), bar_x + max(2, int(3 * S)),
                                      y + int(20 * S)], max(1, int(1.5 * S)),
                                     fill=(accent[0], accent[1], accent[2], 220))
            else:
                ld.text((tx, y), item[1], anchor=anchor, font=fonts["lyric"],
                        fill=(base[0], base[1], base[2], a))

            if len(item) > 2 and item[2]:
                ld.text((tx, y + int(40 * S)), item[2], anchor=anchor, font=fonts["tr"],
                        fill=(tr_col[0], tr_col[1], tr_col[2], int(a * 0.9)))

        top = center_y - int(lh * 0.92)
        bot = center_y + int(lh * 2.35)
        band = max(1, int(lh * 0.88))
        mask = Image.new("L", (W, H), 0)
        md = ImageDraw.Draw(mask)
        for y in range(max(0, top), min(H, bot)):
            if y < top + band:
                v = int(255 * (y - top) / band)
            elif y > bot - band:
                v = int(255 * (bot - y) / band)
            else:
                v = 255
            md.line([(0, y), (W, y)], fill=max(0, min(255, v)))
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))
        frame.alpha_composite(layer)

    # ---- 整段 ----
    def render(self, audio_path, out_path, progress=None, cancel=None):
        n = int(self.total * self.fps)
        t0 = time.time()
        proc = subprocess.Popen(
            [FFMPEG, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
             "-s", f"{self.W}x{self.H}", "-r", str(self.fps), "-i", "-",
             "-i", str(audio_path),
             "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_path)],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for i in range(n):
                if cancel is not None and cancel():
                    break
                proc.stdin.write(self.frame(i / self.fps).convert("RGB").tobytes())
                if progress is not None and i % 10 == 0:
                    el = time.time() - t0
                    eta = el / max(1, i) * (n - i) if i else 0
                    progress(i, n, eta)
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
            proc.wait()
            self._close_pipe()
        return Path(out_path)
