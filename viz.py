"""音乐可视化 —— 拖拽生成工具

把「音频 + 封面 + 背景」拖到 生成.cmd 上即可。
封面、标题、艺人、歌词会自动从 MP3 标签读取；拖进来的图会覆盖它们。

    音频   .mp3 .wav .flac .m4a .ogg .aac   （必需）
    封面   第一张图（可选，MP3 自带封面时也能不拖）
    背景   第二张图（可选，不给就用封面模糊）
"""

import io
import math
import os
import re
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import platform_utils as PU

HERE = PU.HERE
FFMPEG = PU.FFMPEG

AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".ogg", ".aac", ".wma"}
IMG_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}

W, H, FPS = 1920, 1080, 25
S = W / 1280.0
CX, CY = int(400 * S), int(372 * S)
R = int(152 * S)
LABEL_R = int(146 * S)
SPIN_DEG_PER_SEC = 2.5
N_BARS = 96
FREQ_LO, FREQ_HI = 55.0, 15000.0

FONT_JP_B = r"C:\Windows\Fonts\msyhbd.ttc"
FONT_JP = r"C:\Windows\Fonts\msyh.ttc"
FONT_CN = r"C:\Windows\Fonts\msyh.ttc"


def pause(msg="\n  按回车关闭…"):
    try:
        input(msg)
    except EOFError:
        pass


def load_config():
    cfg = {}
    p = HERE / "config.txt"
    if p.exists():
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            cfg[k.strip()] = v.strip()
    return cfg


def read_tags(path):
    """从 MP3/FLAC/M4A 读标题、艺人、专辑、内嵌封面、带时间轴的歌词。"""
    info = {"title": "", "artist": "", "album": "", "cover": None, "lyrics": []}
    try:
        from mutagen import File as MFile
    except ImportError:
        return info
    try:
        f = MFile(str(path))
    except Exception:
        return info
    if f is None:
        return info

    tags = getattr(f, "tags", None)
    if tags is None:
        return info

    def pick(*keys):
        for k in keys:
            try:
                v = tags.get(k)
                if v:
                    return str(v[0]) if isinstance(v, list) else str(v)
            except Exception:
                pass
        return ""

    info["title"] = pick("title", "TIT2", "\xa9nam")
    info["artist"] = pick("artist", "TPE1", "\xa9ART")
    info["album"] = pick("album", "TALB", "\xa9alb")

    try:
        for k in list(tags.keys()):
            if str(k).startswith("APIC"):
                info["cover"] = bytes(tags[k].data)
                break
        if info["cover"] is None and getattr(f, "pictures", None):
            info["cover"] = bytes(f.pictures[0].data)
        if info["cover"] is None:
            covr = tags.get("covr")
            if covr:
                info["cover"] = bytes(covr[0])
    except Exception:
        pass

    raw = ""
    try:
        for k in list(tags.keys()):
            if str(k).startswith("USLT"):
                raw = str(tags[k].text)
                break
        if not raw:
            raw = pick("lyrics", "\xa9lyr")
    except Exception:
        pass

    lines = []
    for ln in raw.splitlines():
        m = re.match(r"\[(\d+):(\d+)(?:[.:](\d+))?\]\s*(.*)", ln.strip())
        if m:
            frac = 0.0
            if m.group(3):
                frac = int(m.group(3)) / (1000.0 if len(m.group(3)) >= 3 else 100.0)
            t = int(m.group(1)) * 60 + int(m.group(2)) + frac
            txt = m.group(4).strip()
            if not txt:
                continue
            tr = ""
            mm = re.match(r"^(.*?)\s*[(（]\s*翻译[:：]\s*[(（]?(.*?)[)）]?\s*[)）]\s*$", txt)
            if mm and mm.group(1).strip():
                txt, tr = mm.group(1).strip(), mm.group(2).strip().rstrip(")）")
            lines.append((t, txt, tr))
    info["lyrics"] = sorted(lines, key=lambda x: x[0])
    return info


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


def band_energy(samples, sr, t, n=N_BARS, win=4096):
    st = max(0, int(t * sr) - win // 2)
    seg = samples[st:st + win]
    if len(seg) < win:
        seg = np.pad(seg, (0, win - len(seg)))
    spec = np.abs(np.fft.rfft(seg * np.hanning(win)))
    freqs = np.fft.rfftfreq(win, 1.0 / sr)
    edges = np.logspace(math.log10(FREQ_LO), math.log10(FREQ_HI), n + 1)
    out = np.zeros(n)
    for i in range(n):
        m = (freqs >= edges[i]) & (freqs < edges[i + 1])
        if m.any():
            out[i] = float(np.sqrt(np.mean(spec[m] ** 2)))
    return out


def precompute_bars(samples, sr, n_frames, window_sec=0.7, falloff=0.72):
    M = np.zeros((n_frames, N_BARS), dtype=np.float32)
    for i in range(n_frames):
        M[i] = band_energy(samples, sr, i / FPS)
    w = max(3, int(window_sec * FPS))
    pad = w // 2
    out = np.zeros_like(M)
    for j in range(N_BARS):
        col = np.pad(M[:, j], (pad, pad), mode="edge")
        sw = np.lib.stride_tricks.sliding_window_view(col, w)
        roll = sw.max(axis=1)[:n_frames]
        out[:, j] = M[:n_frames, j] / np.maximum(roll, 1e-9)
    out = np.clip(out, 0, 1) ** 1.15
    for i in range(1, n_frames):
        out[i] = np.maximum(out[i], out[i - 1] * falloff)
    return out


def prep_image(src, min_side=None):
    im = Image.open(io.BytesIO(src)) if isinstance(src, (bytes, bytearray)) else Image.open(src)
    im = im.convert("RGB")
    if min_side and min(im.size) < min_side:
        k = min_side / min(im.size)
        im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
        im = im.filter(ImageFilter.UnsharpMask(radius=1.5, percent=80, threshold=2))
    return im


def make_background(bg_img):
    """居中裁成 16:9 再放大铺满，整幅画面无空条。"""
    src = bg_img.convert("RGB")
    target = W / H
    r = src.width / src.height
    if r > target:                      # 太宽 → 裁左右
        nw = int(src.height * target)
        left = (src.width - nw) // 2
        box = (left, 0, left + nw, src.height)
    else:                               # 太高 → 裁上下
        nh = int(src.width / target)
        top = (src.height - nh) // 2
        box = (0, top, src.width, top + nh)
    bg = src.crop(box).resize((W, H), Image.LANCZOS)
    bg = bg.filter(ImageFilter.GaussianBlur(9 * S))

    arr = np.asarray(bg).astype(np.float32) * 0.62
    yy, xx = np.mgrid[0:H, 0:W]
    dist = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    vig = np.clip((dist - 0.40) / 0.90, 0, 1) ** 1.5 * 0.55
    arr *= (1.0 - vig)[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).convert("RGBA")


def make_disc():
    size = R * 2
    disc = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(disc)
    d.ellipse([0, 0, size - 1, size - 1], fill=(16, 15, 20, 255))
    d.ellipse([0, 0, size - 1, size - 1], outline=(58, 56, 64, 235), width=max(1, int(2 * S)))
    return disc


def make_label(cover):
    s = LABEL_R * 2
    cw, chh = cover.size
    side = min(cw, chh)
    left = int((cw - side) * 0.62)
    top = int((chh - side) * 0.30)
    img = cover.crop((left, top, left + side, top + side)).resize((s, s), Image.LANCZOS)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, s - 1, s - 1], fill=255)
    out = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    d = ImageDraw.Draw(out)
    d.ellipse([0, 0, s - 1, s - 1], outline=(14, 13, 18, 255), width=max(2, int(3 * S)))
    return out


def draw_title_block(d, tx, ty, title, f_tag, f_big, S, accent=(186, 140, 240)):
    """标题区：小标签 / 曲名大字 / 短强调线。"""
    d.text((tx, ty), "NOW PLAYING", font=f_tag, fill=(*accent, 235))
    d.text((tx, ty + int(22 * S)), title, font=f_big, fill=(255, 255, 255, 248))
    d.rounded_rectangle([tx, ty + int(22 * S) + int(46 * S),
                         tx + int(72 * S), ty + int(22 * S) + int(49 * S)],
                        max(1, int(1.5 * S)), fill=(*accent, 220))


def draw_lyrics(frame, tx, t, lyrics, f_ly, f_ly_b, f_tr, base_y, lh, S):
    """滚动歌词，画在独立图层上，再用上下渐隐遮罩淡出边缘。

    这样滑到边界会自动淡掉，不会突然截断，也不会压到标题。
    """
    if not lyrics:
        return

    TRANS = 0.35
    W, H = frame.size

    cur = -1
    for idx, item in enumerate(lyrics):
        if item[0] <= t:
            cur = idx

    dt = 1.0 if cur < 0 else t - lyrics[cur][0]
    ease = min(1.0, dt / TRANS)
    ease = ease * ease * (3 - 2 * ease)
    shift = 1.0 - ease
    bright = min(1.0, dt / 0.12)

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer, "RGBA")

    for idx in range(max(0, cur - 1), min(len(lyrics), cur + 3)):
        item = lyrics[idx]
        rel = (idx - cur) + shift
        y = base_y + int(rel * lh)

        if idx == cur:
            a = int(200 + 55 * bright)
            active = True
        elif idx == cur + 1:
            a = int(110 - 20 * ease)
            active = False
        else:
            a = 90
            active = False
        a = max(0, min(255, a))

        d.text((tx, y), item[1], anchor="lm",
               font=f_ly_b if active else f_ly,
               fill=(255, 255, 255, a) if active else (214, 206, 226, a))
        if len(item) > 2 and item[2]:
            d.text((tx, y + int(40 * S)), item[2], anchor="lm", font=f_tr,
                   fill=(180, 170, 202, int(a * 0.8)))

    # 上下渐隐遮罩：顶部从 0 渐变到 1，底部从 1 渐变到 0
    top = base_y - int(lh * 0.92)
    bot = base_y + int(lh * 2.35)
    band = int(lh * 0.88)
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    for y in range(max(0, top), min(H, bot)):
        if y < top + band:
            v = int(255 * (y - top) / max(1, band))
        elif y > bot - band:
            v = int(255 * (bot - y) / max(1, band))
        else:
            v = 255
        md.line([(0, y), (W, y)], fill=max(0, min(255, v)))

    from PIL import ImageChops
    alpha = ImageChops.multiply(layer.getchannel("A"), mask)
    layer.putalpha(alpha)
    frame.alpha_composite(layer)


def main():
    files = [Path(a) for a in sys.argv[1:] if not a.startswith("--")]
    audios = [f for f in files if f.suffix.lower() in AUDIO_EXT]
    images = [f for f in files if f.suffix.lower() in IMG_EXT]

    if not audios:
        print("\n  没找到音频文件。把 mp3/wav 拖到 生成.cmd 上。")
        pause()
        return

    audio = audios[0]
    tags = read_tags(audio)
    cfg = load_config()

    # 封面：MP3 内嵌优先（拖进来的图默认当背景）
    if tags["cover"]:
        cover = prep_image(tags["cover"], min_side=700)
        cover_src = "MP3 内嵌封面"
    elif images:
        cover = prep_image(images[0], min_side=700)
        cover_src = images[0].name + "（封面）"
    else:
        print("\n  没有封面：MP3 里没有内嵌封面，也没拖图。\n  请再拖一张图当封面。")
        pause()
        return

    # 背景：拖进来的图 > config 指定 > 封面模糊
    cfg_bg = (cfg.get("背景") or "").strip()
    if images:
        bg_img = prep_image(images[-1], min_side=1100)
        bg_src = images[-1].name + "（背景）"
    elif cfg_bg:
        bg_path = Path(cfg_bg)
        if not bg_path.is_absolute():
            bg_path = HERE / bg_path
        if bg_path.exists():
            bg_img = prep_image(bg_path, min_side=1100)
            bg_src = bg_path.name + "（config 指定）"
        else:
            bg_img = cover
            bg_src = "封面模糊（config 里的背景不存在）"
    else:
        bg_img = cover
        bg_src = "封面模糊"

    title = cfg.get("标题") or tags["title"] or audio.stem
    sub = (cfg.get("副标题") or "").strip()
    if not sub:
        parts = [x for x in (tags["artist"], tags["album"]) if x]
        sub = " — ".join(parts) if parts else ""

    lyrics = tags["lyrics"]
    use_lyrics = len(lyrics) >= 3

    out_dir = (cfg.get("输出目录") or "").strip()
    out_dir = Path(out_dir) if out_dir else audio.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (audio.stem + "_可视化.mp4")
    total = duration_of(audio)
    if total <= 0:
        print("\n  读不出音频时长，文件可能损坏。")
        pause()
        return

    print(f"\n  音频   {audio.name}  ({total:.0f} 秒)")
    print(f"  封面   {cover_src}")
    print(f"  背景   {bg_src}")
    print(f"  标题   {title}")
    print(f"  副标题 {sub or '（无）'}")
    if use_lyrics:
        print(f"  歌词   {len(lyrics)} 行，滚动显示")
    elif lyrics:
        print(f"  歌词   {len(lyrics)} 行（太少，按纯音乐处理）")
    else:
        print("  歌词   无")
    print(f"  输出   {out_path.name}")
    print("\n  渲染中，可以最小化这个窗口…\n")

    tmp_wav = HERE / "_tmp.wav"
    subprocess.run([FFMPEG, "-y", "-i", str(audio), "-ac", "1", "-ar", "44100", str(tmp_wav)],
                   capture_output=True)
    samples, sr = load_audio_mono(tmp_wav)

    n_frames = int(total * FPS)
    print("  分析频谱…", flush=True)
    BARMAT = precompute_bars(samples, sr, n_frames)

    bg = make_background(bg_img)
    disc = make_disc()
    label = make_label(cover)

    f_title = ImageFont.truetype(FONT_JP_B, int(42 * S))
    f_sub = ImageFont.truetype(FONT_JP, int(21 * S))
    f_tag = ImageFont.truetype(FONT_CN, int(13 * S))
    f_big = ImageFont.truetype(FONT_JP_B, int(31 * S))
    f_ly = ImageFont.truetype(FONT_JP, int(26 * S))
    f_ly_b = ImageFont.truetype(FONT_JP_B, int(26 * S))

    proc = subprocess.Popen(
        [FFMPEG, "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
         "-r", str(FPS), "-i", "-", "-i", str(audio),
         "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-shortest", str(out_path)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    for i in range(n_frames):
        t = i / FPS
        frame = bg.copy()
        frame.alpha_composite(disc, (CX - R, CY - R))
        ang = (t * SPIN_DEG_PER_SEC) % 360
        lab = label.rotate(-ang, resample=Image.BICUBIC, center=(LABEL_R, LABEL_R))
        frame.alpha_composite(lab, (CX - LABEL_R, CY - LABEL_R))

        d = ImageDraw.Draw(frame, "RGBA")
        tx = CX + R + int(78 * S)

        if use_lyrics:
            draw_title_block(d, tx, int(120 * S), title, f_tag, f_big, S)
            draw_lyrics(frame, tx, t, lyrics, f_ly, f_ly_b, f_tr,
                        CY, int(92 * S), S)
        else:
            if title:
                d.text((tx, int(250 * S)), title, font=f_title, fill=(255, 255, 255, 245))
            if sub:
                d.text((tx + int(2 * S), int(310 * S)), sub, font=f_sub,
                       fill=(212, 200, 222, 195))

        bars = BARMAT[i]
        bw, gap = int(8 * S), int(4 * S)
        x0 = (W - (bw + gap) * N_BARS) // 2
        base_y = H - int(12 * S)
        for b in range(N_BARS):
            v = float(bars[b])
            hgt = int(4 * S + v * 62 * S)
            x = x0 + b * (bw + gap)
            d.rounded_rectangle([x, base_y - hgt, x + bw, base_y], max(2, int(3 * S)),
                                fill=(232, 216, 246, int(70 + 150 * v)))

        fade = min(1.0, t / 1.5, max(0.0, (total - t) / 3.0))
        if fade < 1.0:
            black = Image.new("RGBA", (W, H), (0, 0, 0, int((1 - fade) * 255)))
            frame = Image.alpha_composite(frame, black)

        proc.stdin.write(frame.convert("RGB").tobytes())
        if i % 500 == 0 and i:
            print(f"  {i}/{n_frames} 帧", flush=True)

    proc.stdin.close()
    proc.wait()
    try:
        tmp_wav.unlink()
    except OSError:
        pass

    if out_path.exists():
        print(f"\n  完成！{out_path}")
        print(f"  {out_path.stat().st_size/1e6:.1f} MB")
        os.startfile(out_path.parent)
    else:
        print("\n  渲染失败，检查 ffmpeg.exe 是否在本目录。")

    pause()


if __name__ == "__main__":
    main()
