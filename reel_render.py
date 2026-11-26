"""Turns a Reel script (see reels.py) into a finished 1080x1920 MP4, with free tools only:

  voice     edge-tts (Microsoft's natural neural voices, incl. Indian English and Hindi, with
            word timings for the captions); Kokoro (open-source, Apache-2.0, runs offline) is
            the backup if edge-tts is down
  b-roll    Pexels or Pixabay free stock video via their free APIs, only when a key is set
            (PEXELS_API_KEY / PIXABAY_API_KEY); otherwise every scene is motion graphics
  music     any track in assets/music/ (e.g. downloaded from Pixabay Music, no attribution
            needed), else a soft background beat generated here, so there is never a licence issue
  sound fx  whooshes and pops generated here
  visuals   templates/reel.html in a headless Chromium, stepped one frame at a time and piped
            into ffmpeg (same Playwright setup as the carousels)
"""
import asyncio
import json
import os
import pathlib
import random
import re
import shutil
import subprocess
import wave

import numpy as np
import requests

ROOT = pathlib.Path(__file__).parent
CFG = json.loads((ROOT / "config.json").read_text())
SR = 48000  # audio sample rate for the final mix
FPS = 30
W, H = 1080, 1920
GAP = 0.18  # silence between scenes (seconds)
TAIL = 0.9  # the last scene stays on screen this long after the voice ends

THEMES = {"hook": "dark", "counter": "accent", "bars": "light", "compare": "dark", "list": "light", "cta": "dark"}


def settings():
    from reels import settings as s
    return s()


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError(f"{cmd[0]} failed: {r.stderr[-800:]}")
    return r


def read_audio(path):
    """Any audio file -> mono float32 at SR, via ffmpeg."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "f32le", "-ac", "1", "-ar", str(SR), "-"],
                       capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode()[-500:])
    return np.frombuffer(r.stdout, dtype=np.float32).copy()


def write_wav(path, x):
    x = np.clip(x, -1, 1)
    pcm = (x * 32767).astype("<i2")
    if pcm.ndim == 1:
        pcm = np.stack([pcm, pcm], axis=1)
    with wave.open(str(path), "wb") as f:
        f.setnchannels(2)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes(pcm.tobytes())


# ---------------------------------------------------------------- voice

def edge_say(text, voice, rate, out):
    """edge-tts: audio file + word timings [(word, start s, end s)]."""
    import edge_tts

    async def go():
        kw = {"rate": rate}
        if os.environ.get("HTTPS_PROXY"):
            kw["proxy"] = os.environ["HTTPS_PROXY"]
        try:
            comm = edge_tts.Communicate(text, voice, boundary="WordBoundary", **kw)
        except TypeError:  # older edge-tts sends word boundaries by default
            comm = edge_tts.Communicate(text, voice, **kw)
        marks = []
        with open(out, "wb") as f:
            async for chunk in comm.stream():
                if chunk["type"] == "audio":
                    f.write(chunk["data"])
                elif chunk["type"] == "WordBoundary":
                    t0 = chunk["offset"] / 1e7
                    marks.append((chunk["text"], t0, t0 + chunk["duration"] / 1e7))
        return marks

    marks = asyncio.run(go())
    if not marks or os.path.getsize(out) < 2000:
        raise RuntimeError("edge-tts returned no audio")
    return marks


_kokoro = None


def kokoro_model():
    """Kokoro (Apache-2.0) model files, downloaded once into ~/.cache/kokoro (cached in Actions)."""
    global _kokoro
    if _kokoro:
        return _kokoro
    from kokoro_onnx import Kokoro
    d = pathlib.Path(os.environ.get("KOKORO_DIR", pathlib.Path.home() / ".cache" / "kokoro"))
    d.mkdir(parents=True, exist_ok=True)
    base = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
    for name in ("kokoro-v1.0.onnx", "voices-v1.0.bin"):
        if not (d / name).exists():
            print("Downloading", name)
            with requests.get(base + name, stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(d / (name + ".part"), "wb") as f:
                    shutil.copyfileobj(r.raw, f)
            (d / (name + ".part")).rename(d / name)
    _kokoro = Kokoro(str(d / "kokoro-v1.0.onnx"), str(d / "voices-v1.0.bin"))
    return _kokoro


def speakable(text):
    """Kokoro reads digits well but not the rupee sign or Indian digit grouping."""
    text = re.sub(r"₹\s?([\d,]+(?:\.\d+)?)\s*(lakh|crore|k)?", lambda m: f"{m.group(1).replace(',', '')} {m.group(2) or ''} rupees", text)
    return text.replace("%", " percent").replace("&", " and ")


def kokoro_say(text, voice, speed, out):
    import soundfile as sf
    samples, sr = kokoro_model().create(speakable(text), voice=voice, speed=speed, lang="en-us")
    sf.write(out, samples, sr)
    return estimate_marks(text, samples, sr)


def estimate_marks(text, samples, sr):
    """No word timings from the engine: spread the words over the speech, weighted by length,
    with a little extra time at punctuation. Close enough for 2-3 word caption groups."""
    x = np.abs(np.asarray(samples, dtype=np.float32))
    on = np.where(x > 0.02)[0]
    start, end = (on[0] / sr, on[-1] / sr) if len(on) else (0, len(x) / sr)
    ws = text.split()
    weights = [len(re.sub(r"\W", "", w)) + 2 + (3 if re.search(r"[,.?!:;]$", w) else 0) for w in ws]
    total = sum(weights) or 1
    t, marks = start, []
    for w, k in zip(ws, weights):
        d = (end - start) * k / total
        marks.append((w, t, t + d * 0.9))
        t += d
    return marks


def voice_scenes(scenes, outdir):
    """Voice every scene; returns (voice track, scene timings, caption groups, engine used)."""
    s = settings()
    engines = [e.strip() for e in os.environ.get("REEL_TTS", s["tts"]).split(",") if e.strip()]
    lang = s["language"].lower()
    edge_voice = s["voice"]["edge_hindi" if lang == "hindi" else "edge"]
    last_err = None
    for engine in engines:
        try:
            parts = []
            for i, sc in enumerate(scenes):
                f = outdir / f"voice_{i}.{'mp3' if engine == 'edge' else 'wav'}"
                if engine == "edge":
                    marks = edge_say(sc["say"], edge_voice, s["voice"]["rate"], f)
                elif engine == "kokoro":
                    marks = kokoro_say(sc["say"], s["voice"]["kokoro"], s["voice"]["kokoro_speed"], f)
                else:
                    raise RuntimeError(f"unknown TTS engine {engine}")
                parts.append((read_audio(f), marks))
            print("Voice:", engine)
            return assemble(scenes, parts) + (engine,)
        except Exception as e:  # network down, service changed, model missing
            print(f"TTS {engine} failed: {e}")
            last_err = e
    raise RuntimeError(f"no TTS engine worked: {last_err}")


def assemble(scenes, parts):
    track, t, timings, words = [], 0.0, [], []
    for i, (audio, marks) in enumerate(parts):
        # trim leading/trailing silence so the pacing stays tight
        on = np.where(np.abs(audio) > 0.01)[0]
        lead = max(0, on[0] - int(0.03 * SR)) if len(on) else 0
        trail = min(len(audio), on[-1] + int(0.08 * SR)) if len(on) else len(audio)
        audio = audio[lead:trail]
        shift = t - lead / SR
        dur = len(audio) / SR
        start = 0.0 if i == 0 else t
        track.append(audio)
        for w, a, b in marks:
            words.append((w, max(t, a + shift), b + shift, i))
        t += dur
        gap = TAIL if i == len(parts) - 1 else GAP
        track.append(np.zeros(int(gap * SR), dtype=np.float32))
        t += gap
        timings.append((start, t))
    voice = np.concatenate(track)
    # scene i ends where i+1 starts
    timings = [(a, timings[k + 1][0] if k + 1 < len(timings) else b) for k, (a, b) in enumerate(timings)]
    return voice, timings, caption_groups(words)


def caption_groups(words, max_words=3, max_chars=18):
    """Word timings -> short caption groups (2-3 words), split at punctuation and scene cuts."""
    groups, cur = [], []

    def flush():
        if cur:
            groups.append({"t0": cur[0]["t0"], "t1": cur[-1]["t1"], "scene": cur[0]["scene"],
                           "words": [{"w": x["w"], "t0": x["t0"]} for x in cur]})
            cur.clear()

    for w, a, b, sc in words:
        clean = re.sub(r"[\"“”()]", "", w).strip().rstrip(",.;:")
        if not clean:
            continue
        if cur and (len(cur) >= max_words or sc != cur[0]["scene"]
                    or len(" ".join(x["w"] for x in cur)) + len(clean) > max_chars):
            flush()
        cur.append({"w": clean, "t0": a, "t1": b, "scene": sc})
        if re.search(r"[,.?!:;]$", w):
            flush()
    flush()
    for k, g in enumerate(groups):  # hold each group until the next one starts (no flicker)
        nxt = groups[k + 1]["t0"] if k + 1 < len(groups) else g["t1"] + 0.4
        g["t1"] = nxt if nxt - g["t1"] < 0.6 else g["t1"] + 0.25
    return groups


# ---------------------------------------------------------------- music and sound effects

def synth_music(seconds, seed):
    """A soft, royalty-free background loop made from scratch: warm chord pad, plucked
    arpeggio, light kick and hat at ~100 BPM. Quiet and unobtrusive under the voice."""
    rng = np.random.default_rng(seed)
    bpm = rng.choice([92, 96, 100, 104])
    beat = 60 / bpm
    n = int(seconds * SR)
    t = np.arange(n) / SR
    root = rng.choice([57, 60, 62, 55])  # A, C, D, G
    progs = [[0, 7, 9, 5], [0, 5, 9, 7], [9, 5, 0, 7], [0, 9, 5, 7]]  # I-V-vi-IV and friends
    prog = progs[rng.integers(len(progs))]
    out = np.zeros(n, dtype=np.float32)
    bar = beat * 4
    hz = lambda m: 440 * 2 ** ((m - 69) / 12)

    def chord(deg):
        major = deg in (0, 5, 7)
        return [root + deg, root + deg + (4 if major else 3), root + deg + 7, root + deg + 11 if major else root + deg + 10]

    k = 0
    while k * bar < seconds:
        notes = chord(prog[k % 4])
        a, b = int(k * bar * SR), min(n, int((k + 1) * bar * SR))
        tt = t[a:b] - k * bar
        env = np.minimum(1, tt / 0.4) * np.minimum(1, (bar - tt) / 0.3)
        for m in notes:  # pad: two slightly detuned soft tones per note
            f = hz(m - 12)
            out[a:b] += 0.05 * env * (np.sin(2 * np.pi * f * tt) + 0.6 * np.sin(2 * np.pi * f * 1.003 * tt + 1)
                                      + 0.2 * np.sin(2 * np.pi * 2 * f * tt))
        for j in range(8):  # pluck arpeggio on eighth notes
            m = notes[[0, 1, 2, 3, 2, 1, 2, 3][j]] + 12
            s0 = a + int(j * beat / 2 * SR)
            L = min(int(0.5 * SR), n - s0)
            if L <= 0:
                continue
            tl = np.arange(L) / SR
            out[s0:s0 + L] += 0.06 * np.exp(-tl * 7) * np.sin(2 * np.pi * hz(m) * tl) * (1 + 0.3 * np.sin(2 * np.pi * hz(m) * 2 * tl))
        for j in range(4):  # kick on 1 and 3, hat on the off-beats
            s0 = a + int(j * beat * SR)
            if j % 2 == 0:
                L = min(int(0.3 * SR), n - s0)
                if L > 0:
                    tl = np.arange(L) / SR
                    out[s0:s0 + L] += 0.35 * np.exp(-tl * 14) * np.sin(2 * np.pi * (45 + 90 * np.exp(-tl * 30)) * tl)
            h0 = s0 + int(beat / 2 * SR)
            L = min(int(0.05 * SR), n - h0)
            if L > 0:
                noise = rng.standard_normal(L).astype(np.float32)
                noise = noise - np.convolve(noise, np.ones(4) / 4, mode="same")  # crude high-pass
                out[h0:h0 + L] += 0.05 * np.exp(-np.arange(L) / SR * 80) * noise
        k += 1
    out *= np.minimum(1, t / 1.0) * np.minimum(1, (seconds - t) / 1.5)  # fade in / out
    return out / (np.max(np.abs(out)) + 1e-9) * 0.8


def music_bed(seconds, seed):
    tracks = sorted(p for p in (ROOT / "assets" / "music").glob("*") if p.suffix.lower() in (".mp3", ".wav", ".m4a", ".ogg"))
    if tracks:
        pick = random.Random(seed).choice(tracks)
        x = read_audio(pick)
        if len(x) < seconds * SR:  # loop short tracks
            x = np.tile(x, int(seconds * SR / max(1, len(x))) + 1)
        x = x[:int(seconds * SR)]
        t = np.arange(len(x)) / SR
        x = x * np.minimum(1, t / 0.5) * np.minimum(1, (seconds - t) / 1.5)
        print("Music:", pick.name)
        return x / (np.max(np.abs(x)) + 1e-9) * 0.8, pick.name
    print("Music: generated beat")
    return synth_music(seconds, seed), "generated"


def sfx(kind, rng):
    if kind == "whoosh":  # filtered noise sweeping up then down
        L = int(0.42 * SR)
        tl = np.arange(L) / SR
        noise = rng.standard_normal(L).astype(np.float32)
        out = np.zeros(L, dtype=np.float32)
        y = 0.0
        for i in range(L):  # one-pole low-pass with a moving cutoff
            a = 0.02 + 0.25 * np.sin(np.pi * i / L)
            y += a * (noise[i] - y)
            out[i] = y
        return 0.5 * out * np.sin(np.pi * tl / tl[-1]) ** 2
    if kind == "pop":
        L = int(0.12 * SR)
        tl = np.arange(L) / SR
        return 0.35 * np.exp(-tl * 40) * np.sin(2 * np.pi * (900 - 2500 * tl) * tl)
    if kind == "ding":
        L = int(0.9 * SR)
        tl = np.arange(L) / SR
        return 0.22 * np.exp(-tl * 5) * (np.sin(2 * np.pi * 1318.5 * tl) + 0.5 * np.sin(2 * np.pi * 1975.5 * tl))
    return np.zeros(1, dtype=np.float32)


def sfx_track(scenes, n, seed):
    rng = np.random.default_rng(seed)
    out = np.zeros(n, dtype=np.float32)
    cues = []
    for i, sc in enumerate(scenes):
        a, b = sc["start"], sc["end"]
        if i:
            cues.append((a - 0.21, "whoosh"))
        if sc["type"] == "list":
            span = max(.4, ((b - a) * .72 - .3) / max(1, len(sc["items"])))
            cues += [(a + .3 + k * span, "pop") for k in range(len(sc["items"]))]
        elif sc["type"] == "bars":
            cues += [(a + .3 + k * .28, "pop") for k in range(len(sc["items"]))]
        elif sc["type"] == "compare":
            cues += [(a + .25, "pop"), (a + .7, "pop")]
        elif sc["type"] == "counter":
            cues.append((a + .1 + min(1.6, (b - a) * .5) * .6, "ding"))
    for at, kind in cues:
        x = sfx(kind, rng)
        s0 = max(0, int(at * SR))
        L = min(len(x), n - s0)
        if L > 0:
            out[s0:s0 + L] += x[:L]
    return out


def mix(voice, scenes, seed, outdir):
    s = settings()
    n = len(voice)
    music, track = music_bed(n / SR, seed)
    music = music[:n] if len(music) >= n else np.pad(music, (0, n - len(music)))
    # duck the music under the voice (smoothed voice loudness)
    env = np.convolve(np.abs(voice), np.ones(int(0.12 * SR)) / int(0.12 * SR), mode="same")
    speaking = np.clip(env / 0.03, 0, 1)
    gain = s["music_volume"] * (1 - 0.55 * speaking)
    out = voice * 1.0 + music * gain + sfx_track(scenes, n, seed) * s["sfx_volume"]
    out = out / (np.max(np.abs(out)) + 1e-9) * 0.95
    path = outdir / "mix.wav"
    write_wav(path, out)
    return path, track


# ---------------------------------------------------------------- stock b-roll

def pexels(query):
    key = os.environ.get("PEXELS_API_KEY")
    if not key:
        return None
    r = requests.get("https://api.pexels.com/videos/search", headers={"Authorization": key},
                     params={"query": query, "orientation": "portrait", "size": "medium", "per_page": 8}, timeout=30)
    r.raise_for_status()
    for v in r.json().get("videos", []):
        files = [f for f in v.get("video_files", []) if f.get("file_type") == "video/mp4"
                 and (f.get("height") or 0) >= 1280 and (f.get("height") or 0) > (f.get("width") or 0)]
        if files and v.get("duration", 0) >= 4:
            f = min(files, key=lambda f: abs((f.get("height") or 0) - 1920))
            return {"url": f["link"], "source": "Pexels", "page": v.get("url")}
    return None


def pixabay(query):
    key = os.environ.get("PIXABAY_API_KEY")
    if not key:
        return None
    r = requests.get("https://pixabay.com/api/videos/", params={"key": key, "q": query, "safesearch": "true", "per_page": 10},
                     timeout=30)
    r.raise_for_status()
    for v in r.json().get("hits", []):
        for size in ("large", "medium"):
            f = (v.get("videos") or {}).get(size) or {}
            if f.get("url") and (f.get("height") or 0) >= 1080 and v.get("duration", 0) >= 4:
                return {"url": f["url"], "source": "Pixabay", "page": v.get("pageURL")}
    return None


def broll(scenes, outdir):
    """Background footage for the scenes that asked for it, as frame images the renderer
    steps through. Any failure just leaves that scene as motion graphics."""
    s = settings()
    if not (os.environ.get("PEXELS_API_KEY") or os.environ.get("PIXABAY_API_KEY")):
        return []
    credits, used = [], 0
    for i, sc in enumerate(scenes):
        q = (sc.get("broll") or "").strip()
        if not q or sc["type"] not in ("hook", "statement") or used >= s["max_broll_scenes"]:
            continue
        try:
            hit = pexels(q) or pixabay(q)
            if not hit:
                continue
            src = outdir / f"broll_{i}.mp4"
            with requests.get(hit["url"], stream=True, timeout=120) as r:
                r.raise_for_status()
                with open(src, "wb") as f:
                    shutil.copyfileobj(r.raw, f)
            frames = outdir / "bg" / str(i)
            frames.mkdir(parents=True, exist_ok=True)
            dur = sc["end"] - sc["start"] + 0.1
            run(["ffmpeg", "-v", "error", "-y", "-ss", "0.5", "-t", f"{dur:.2f}", "-i", str(src),
                 "-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},eq=saturation=0.85",
                 "-q:v", "4", str(frames / "%04d.jpg")])
            count = len(list(frames.glob("*.jpg")))
            if count:
                sc["bg"] = {"dir": f"bg/{i}", "count": count}
                credits.append(hit)
                used += 1
                print(f"B-roll for scene {i}: {hit['source']} ({q})")
            src.unlink(missing_ok=True)
        except Exception as e:
            print(f"B-roll for scene {i} failed, using motion graphics: {e}")
    return credits


# ---------------------------------------------------------------- the whole video

def theme_scenes(scenes):
    """Alternate dark / light backgrounds so every cut is a visible change."""
    last = None
    for sc in scenes:
        th = THEMES.get(sc["type"]) or ("light" if last == "dark" else "dark")
        if th == last and sc["type"] not in ("hook", "cta"):
            th = {"dark": "light", "light": "dark", "accent": "dark"}[th]
        sc["theme"] = last = th


def render(script, outdir, seed=None):
    """script -> outdir/reel.mp4 (+ cover.jpg). Returns info for the post log."""
    outdir = pathlib.Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    seed = seed if seed is not None else random.randrange(1 << 30)
    scenes = [dict(sc) for sc in script["scenes"]]
    voice, timings, captions, engine = voice_scenes(scenes, outdir)
    for sc, (a, b) in zip(scenes, timings):
        sc["start"], sc["end"] = round(a, 3), round(b, 3)
    duration = scenes[-1]["end"]
    theme_scenes(scenes)
    credits = broll(scenes, outdir)
    audio, track = mix(voice, scenes, seed, outdir)

    c = CFG["colors"]
    reel = {"scenes": scenes, "captions": captions, "duration": duration, "fps": FPS,
            "colors": c, "brand": CFG["brand"], "handle": CFG["handle"],
            "halftone": (ROOT / "assets" / "halftone.svg").read_text()}
    page_html = (ROOT / "templates" / "reel.html").read_text().replace(
        "<script>\nconst R = window.REEL;",
        "<script>window.REEL = " + json.dumps(reel, ensure_ascii=False).replace("</", "<\\/") + ";</script>\n<script>\nconst R = window.REEL;", 1)
    page_file = outdir / "reel.html"
    page_file.write_text(page_html)

    video = outdir / "reel.mp4"
    frames = int(duration * FPS)
    from playwright.sync_api import sync_playwright
    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "image2pipe", "-c:v", "mjpeg", "-framerate", str(FPS), "-i", "-",
         "-i", str(audio), "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-preset", os.environ.get("X264_PRESET", "medium"), "-crf", "19", "-pix_fmt", "yuv420p",
         "-profile:v", "high", "-r", str(FPS), "-g", str(FPS * 2),
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-movflags", "+faststart", "-shortest", str(video)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None,
                                    args=["--allow-file-access-from-files"])
        page = browser.new_page(viewport={"width": W, "height": H})
        page.goto(page_file.as_uri(), wait_until="networkidle")
        page.evaluate("window.ready")
        cover_at = min(1.2, duration / 3)
        for f in range(frames):
            t = f / FPS
            page.evaluate(f"seek({t})")
            shot = page.screenshot(type="jpeg", quality=90)
            enc.stdin.write(shot)
            if abs(t - cover_at) < 0.5 / FPS:
                (outdir / "cover.jpg").write_bytes(shot)
        browser.close()
    enc.stdin.close()
    err = enc.stderr.read().decode()
    if enc.wait():
        raise RuntimeError("ffmpeg failed: " + err[-800:])
    # keep the folder small: frames and stems are not needed after the render
    shutil.rmtree(outdir / "bg", ignore_errors=True)
    for f in outdir.glob("voice_*"):
        f.unlink()
    print(f"Rendered {video} ({duration:.1f}s, {video.stat().st_size / 1e6:.1f} MB)")
    return {"duration": round(duration, 1), "tts": engine, "music": track, "cover_ms": int(cover_at * 1000),
            "broll": [{"source": c["source"], "page": c.get("page")} for c in credits], "seed": seed,
            "timeline": [{k: sc.get(k) for k in ("type", "start", "end", "theme")} for sc in scenes]}
