"""Rendert ein „Was passiert, wenn…“-Video (1080x1920) aus einem Themen-Eintrag.

Ablauf pro Szene: Sprachausgabe (edge-tts, mit Wort-Zeitstempeln) -> KI-Bild
(Pollinations, kostenlos) -> Ken-Burns-Bewegung. Danach alles zusammenfügen und
TikTok-typische Wort-für-Wort-Untertitel einbrennen.
"""
import asyncio
import json
import os
import random
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

W, H, FPS = 1080, 1920, 30
VOICE = os.environ.get("TTS_VOICE", "de-DE-ConradNeural")
RATE = os.environ.get("TTS_RATE", "+10%")
STYLE = (
    ", cinematic 3D render, hyper detailed, dramatic volumetric lighting, "
    "octane render, unreal engine, vivid colors, vertical composition, no text"
)
FONT = os.environ.get("SUB_FONT", "DejaVu Sans")


def run(cmd):
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def duration(path):
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)])
    return float(out.strip())


# ---------- Stimme ----------
async def _tts(text, mp3):
    import edge_tts
    try:
        com = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
    except TypeError:  # ältere edge-tts-Versionen
        com = edge_tts.Communicate(text, VOICE, rate=RATE)
    words = []
    with open(mp3, "wb") as f:
        async for chunk in com.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                words.append({
                    "w": chunk["text"],
                    "s": chunk["offset"] / 1e7,
                    "e": (chunk["offset"] + chunk["duration"]) / 1e7,
                })
    return words


def tts(text, mp3, fake=False):
    if fake:  # lokaler Test ohne Internet
        n = len(text.split())
        d = max(2.0, n * 0.36)
        run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=f=220:d={d}",
             "-q:a", "4", str(mp3)])
        return [], d
    for attempt in range(3):
        try:
            words = asyncio.run(_tts(text, mp3))
            return words, duration(mp3)
        except Exception as e:  # noqa: BLE001
            print("TTS-Fehler, neuer Versuch:", e)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("TTS fehlgeschlagen")


def even_words(text, total):
    toks = text.split()
    weights = [len(t) + 2 for t in toks]
    s, t, out = sum(weights), 0.0, []
    for tok, wgt in zip(toks, weights):
        d = total * wgt / s
        out.append({"w": tok, "s": t, "e": t + d})
        t += d
    return out


# ---------- Bilder ----------
def fallback_image(path, seed):
    rnd = random.Random(seed)
    c1 = [rnd.randint(0, 80) for _ in range(3)]
    c2 = [rnd.randint(60, 200) for _ in range(3)]
    img = Image.new("RGB", (W, H))
    d = ImageDraw.Draw(img)
    for y in range(H):
        k = y / H
        d.line([(0, y), (W, y)], fill=tuple(int(a + (b - a) * k) for a, b in zip(c1, c2)))
    for _ in range(40):
        x, y, r = rnd.randint(0, W), rnd.randint(0, H), rnd.randint(20, 220)
        d.ellipse([x - r, y - r, x + r, y + r],
                  fill=tuple(min(255, v + rnd.randint(20, 80)) for v in c2))
    img.filter(ImageFilter.GaussianBlur(40)).save(path, quality=92)


def fetch_image(prompt, path, seed, fake=False):
    if fake:
        return fallback_image(path, seed)
    q = urllib.parse.quote(prompt + STYLE)
    url = (f"https://image.pollinations.ai/prompt/{q}?width={W}&height={H}"
           f"&seed={seed}&nologo=true&model=flux")
    token = os.environ.get("POLLINATIONS_TOKEN")
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "tiktok-bot/1.0"})
            if token:
                req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=180) as r:
                data = r.read()
            if len(data) < 10_000:
                raise ValueError("Bild zu klein")
            with open(path, "wb") as f:
                f.write(data)
            Image.open(path).convert("RGB").resize((W, H)).save(path, quality=92)
            return
        except Exception as e:  # noqa: BLE001
            print(f"Bild-Fehler ({attempt + 1}/4):", e)
            time.sleep(15 * (attempt + 1))
    print("Nutze Ersatzbild")
    fallback_image(path, seed)


# ---------- Bewegung ----------
def ken_burns(img, dur, out, idx):
    frames = int(dur * FPS) + 2
    zin = idx % 2 == 0
    z = f"1+0.18*on/{frames}" if zin else f"1.18-0.18*on/{frames}"
    pans = [
        ("iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
        ("(iw-iw/zoom)*on/" + str(frames), "ih/2-(ih/zoom/2)"),
        ("iw/2-(iw/zoom/2)", "(ih-ih/zoom)*on/" + str(frames)),
        ("(iw-iw/zoom)*(1-on/" + str(frames) + ")", "ih/2-(ih/zoom/2)"),
    ]
    x, y = pans[idx % len(pans)]
    vf = (f"scale={W * 2}:{H * 2},zoompan=z='{z}':x='{x}':y='{y}'"
          f":d={frames}:s={W}x{H}:fps={FPS},format=yuv420p")
    run(["ffmpeg", "-y", "-loop", "1", "-i", str(img), "-vf", vf, "-t", f"{dur:.3f}",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(out)])


# ---------- Untertitel ----------
def ass_time(t):
    t = max(0, t)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def esc(s):
    return s.replace("{", "(").replace("}", ")").replace("\\", "")


def build_ass(words, title, path):
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,{FONT},92,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,3,5,60,60,0,1
Style: Title,{FONT},78,&H00FFFFFF,&H00FFFFFF,&H00000000,&HAA000000,-1,0,0,0,100,100,0,0,3,18,0,8,70,70,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    # Titel-Einblendung am Anfang (Hook)
    lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(2.8)},Title,,0,0,0,,"
                 f"{{\\fad(150,250)}}{esc(title.upper())}")
    # Wörter in Gruppen zu max. 3 Wörtern, aktives Wort gelb
    groups, cur = [], []
    for w in words:
        cur.append(w)
        if len(cur) == 3 or w["w"][-1:] in ".,!?:;":
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    for gi, g in enumerate(groups):
        g_end = groups[gi + 1][0]["s"] if gi + 1 < len(groups) else g[-1]["e"] + 0.4
        for i, w in enumerate(g):
            s = w["s"]
            e = g[i + 1]["s"] if i + 1 < len(g) else g_end
            txt = []
            for j, x in enumerate(g):
                word = esc(x["w"].upper())
                txt.append(f"{{\\c&H00E5FF&}}{word}{{\\c&HFFFFFF&}}" if j == i else word)
            pop = "{\\fscx108\\fscy108\\t(0,90,\\fscx100\\fscy100)}" if i == 0 else ""
            lines.append(f"Dialogue: 0,{ass_time(s)},{ass_time(e)},Sub,,0,0,0,,"
                         f"{{\\pos({W // 2},{int(H * 0.68)})}}{pop}{' '.join(txt)}")
    Path(path).write_text(head + "\n".join(lines) + "\n", encoding="utf-8")


# ---------- Gesamtablauf ----------
def render(topic, out_mp4, work, fake=False):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    seed_base = abs(hash(topic["id"])) % 100000
    all_words, clips, audios, t0 = [], [], [], 0.0
    for i, sc in enumerate(topic["scenes"]):
        mp3 = work / f"a{i}.mp3"
        words, d = tts(sc["text"], mp3, fake)
        if not words:
            words = even_words(sc["text"], d - 0.15)
        pad = 0.25  # kleine Pause zwischen Szenen
        d_total = d + pad
        for w in words:
            all_words.append({"w": w["w"], "s": w["s"] + t0, "e": w["e"] + t0})
        img = work / f"i{i}.jpg"
        fetch_image(sc["prompt"], img, seed_base + i, fake)
        if not fake and i < len(topic["scenes"]) - 1:
            time.sleep(4)  # Rate-Limit schonen
        clip = work / f"v{i}.mp4"
        ken_burns(img, d_total, clip, i)
        wav = work / f"a{i}.wav"
        run(["ffmpeg", "-y", "-i", str(mp3), "-af", f"apad=pad_dur={pad}",
             "-ar", "44100", "-ac", "2", "-t", f"{d_total:.3f}", str(wav)])
        clips.append(clip)
        audios.append(wav)
        t0 += d_total

    (work / "v.txt").write_text("".join(f"file '{c.resolve()}'\n" for c in clips))
    (work / "a.txt").write_text("".join(f"file '{a.resolve()}'\n" for a in audios))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "v.txt"),
         "-c", "copy", str(work / "video.mp4")])
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(work / "a.txt"),
         "-c:a", "pcm_s16le", str(work / "voice.wav")])

    ass = work / "subs.ass"
    build_ass(all_words, topic["title"], ass)
    # dezenter Ambient-Teppich (selbst erzeugt, keine Urheberrechtsprobleme)
    amb = (f"anoisesrc=d={t0:.2f}:c=brown:a=0.05,lowpass=f=400,"
           f"afade=t=in:d=1,afade=t=out:st={max(0, t0 - 1.5):.2f}:d=1.5")
    run(["ffmpeg", "-y", "-i", str(work / "video.mp4"), "-i", str(work / "voice.wav"),
         "-f", "lavfi", "-i", amb,
         "-filter_complex",
         f"[0:v]ass='{ass.resolve()}'[v];"
         "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[a1];[2:a]volume=0.6[a2];"
         "[a1][a2]amix=inputs=2:duration=first:normalize=0[a]",
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "21", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart", "-t", f"{t0:.3f}", str(out_mp4)])
    return t0


if __name__ == "__main__":
    import sys
    topics = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    t = topics[int(sys.argv[2]) if len(sys.argv) > 2 else 0]
    print(render(t, "test.mp4", "work_test", fake="--fake" in sys.argv))
