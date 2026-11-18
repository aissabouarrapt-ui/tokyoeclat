# -*- coding: utf-8 -*-
"""Captions: faster-whisper word timestamps -> big, high-contrast "TikTok style"
subtitles (2-3 words at a time, current word in yellow, thick black outline),
plus the heistclips hook in a black banner, burned in one ffmpeg pass that also
normalises loudness to -14 LUFS (what TikTok / YouTube play at).

Text is rendered with libass from an .ass file referenced by a RELATIVE path, so
Windows drive letters ("C:") never reach the ffmpeg filter parser."""
import os
import re
import unicodedata

from common import FONTS, MODELS, ROOT, log, run

_model = {"name": None, "m": None}

# Positions on the 1080x1920 canvas, per heistclips layout:
#   split: facecam 0-730 on top, gameplay 730-1920 -> hook on the seam, subs mid-gameplay
#   fit:   16:9 picture at 656-1264 on a blurred fill -> hook above it, subs below it
#   crop:  full-height gameplay
POS = {
    "split": {"hook": 730, "subs": 1190},
    "fit": {"hook": 470, "subs": 1390},
    "crop": {"hook": 270, "subs": 1190},
}

STRONG = {
    "fuck": "f*ck", "fucking": "f*cking", "fucked": "f*cked", "fuckin": "f*ckin", "motherfucker": "m*therf*cker",
    "shit": "sh*t", "bitch": "b*tch", "bitches": "b*tches", "cunt": "c*nt", "pussy": "p*ssy",
    "dick": "d*ck", "asshole": "a**hole", "bullshit": "bullsh*t",
}
SLURS = re.compile(r"\b(n+i+g+(a|er|ga|gah|guh)?s?|f+a+g+(got)?s?|retard(ed)?s?)\b", re.I)


def censor(text, on=True):
    if not on:
        return text
    text = SLURS.sub(lambda m: m.group(0)[0] + "*" * (len(m.group(0)) - 1), text)

    def rep(m):
        w = m.group(0)
        r = STRONG.get(w.lower())
        if not r:
            return w
        return r.upper() if w.isupper() else (r.capitalize() if w[0].isupper() else r)
    return re.sub(r"[A-Za-z]+", rep, text)


def has_slur(text):
    return bool(SLURS.search(text or ""))


def strip_emoji(text):
    out = []
    for ch in text:
        cat = unicodedata.category(ch)
        if cat == "So" or ord(ch) >= 0x1F000 or ch in "‍️":
            continue
        out.append(ch)
    return re.sub(r"\s{2,}", " ", "".join(out)).strip()


def load_model(name):
    if _model["name"] == name and _model["m"] is not None:
        return _model["m"]
    from faster_whisper import WhisperModel
    MODELS.mkdir(parents=True, exist_ok=True)
    log.info(f"تحميل نموذج faster-whisper '{name}' (أول مرة قد يستغرق دقائق)…")
    m = WhisperModel(name, device="cpu", compute_type="int8", download_root=str(MODELS),
                     cpu_threads=max(1, (os.cpu_count() or 2) - 1))
    _model.update(name=name, m=m)
    return m


def load_audio(video):
    """16 kHz mono float32 via ffmpeg. (faster-whisper's own decoder breaks with
    PyAV >= 17: "open() got an unexpected keyword argument 'metadata_errors'")."""
    import numpy as np
    code, _, err = run(["ffmpeg", "-v", "error", "-nostdin", "-i", video, "-ac", "1", "-ar", "16000",
                        "-f", "s16le", "-acodec", "pcm_s16le", str(video) + ".pcm", "-y"], timeout=300)
    pcm = str(video) + ".pcm"
    try:
        if code != 0:
            raise RuntimeError("ffmpeg (صوت): " + (err.strip().splitlines() or ["?"])[-1])
        return np.fromfile(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    finally:
        try:
            os.remove(pcm)
        except OSError:
            pass


def transcribe(video, model_name="small", language=None):
    """-> (words [(start, end, text)], detected_language, full_text)"""
    m = load_model(model_name)
    audio = load_audio(video)
    segments, info = m.transcribe(
        audio, language=language or None, word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 400}, beam_size=3,
        condition_on_previous_text=False, temperature=0.0)
    words, texts = [], []
    for s in segments:
        if s.no_speech_prob > 0.6 and s.avg_logprob < -1.0:
            continue  # music / game noise transcribed as words
        texts.append(s.text.strip())
        for w in (s.words or []):
            t = w.word.strip()
            if t:
                words.append((float(w.start), float(w.end), t))
    return words, info.language, " ".join(texts).strip()


def chunk_words(words, max_words=3, max_chars=14, max_gap=0.6):
    chunks, cur = [], []
    for w in words:
        if cur:
            text_len = len(" ".join(x[2] for x in cur + [w]))
            gap = w[0] - cur[-1][1]
            ends_sentence = cur[-1][2][-1:] in ".!?,"
            if len(cur) >= max_words or text_len > max_chars or gap > max_gap or ends_sentence:
                chunks.append(cur)
                cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def _ass_time(t):
    t = max(0.0, t)
    h = int(t // 3600)
    m = int(t % 3600 // 60)
    s = t % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _esc(t):
    return t.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def wrap(text, width=22, max_lines=3):
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = (cur + " " + w).strip()
    if cur:
        lines.append(cur)
    return lines[:max_lines]


def build_ass(path, words, hook, duration, layout, font, censor_on=True):
    pos = POS.get(layout, POS["split"])
    lines = [
        "[Script Info]", "ScriptType: v4.00+", "PlayResX: 1080", "PlayResY: 1920",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Sub,{font},84,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,-1,0,0,0,100,100,1,0,1,7,3,5,70,70,0,1",
        f"Style: Hook,{font},58,&H00FFFFFF,&H00FFFFFF,&H1A000000,&H00000000,-1,0,0,0,100,100,0,0,3,16,0,5,60,60,0,1",
        "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    if hook:
        hook_lines = wrap(_esc(strip_emoji(censor(hook, censor_on))), 24, 2)
        if hook_lines:
            lines.append(f"Dialogue: 1,{_ass_time(0)},{_ass_time(duration)},Hook,,0,0,0,,"
                         f"{{\\an5\\pos(540,{pos['hook']})}}" + "\\N".join(hook_lines))
    chunks = chunk_words(words)
    for ci, ch in enumerate(chunks):
        c_end = ch[-1][1]
        nxt = chunks[ci + 1][0][0] if ci + 1 < len(chunks) else duration
        c_end = min(max(c_end + 0.25, c_end), nxt, duration)  # hold briefly, never overlap the next chunk
        toks = [_esc(strip_emoji(censor(w[2], censor_on))).upper() for w in ch]
        for wi, w in enumerate(ch):
            st = ch[0][0] if wi == 0 else w[0]
            en = ch[wi + 1][0] if wi + 1 < len(ch) else c_end
            if en - st < 0.05:
                continue
            txt = " ".join((f"{{\\c&H00F2FF&}}{t}{{\\c&HFFFFFF&}}" if k == wi else t) for k, t in enumerate(toks))
            lines.append(f"Dialogue: 0,{_ass_time(st)},{_ass_time(en)},Sub,,0,0,0,,{{\\an5\\pos(540,{pos['subs']})}}{txt}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(chunks)


_filters = {}


def text_filter():
    """'ass' or 'subtitles' if this ffmpeg has libass, else None."""
    if "v" not in _filters:
        code, out, _ = run(["ffmpeg", "-hide_banner", "-filters"], timeout=30)
        names = set(re.findall(r"^\s*[TSC.|]{2,3}\s+(\w+)\s", out, re.M))
        _filters["v"] = "ass" if "ass" in names else ("subtitles" if "subtitles" in names else None)
    return _filters["v"]


def render(raw, ass_file, out, fontsdir=FONTS):
    """Burn subtitles + hook, normalise loudness. Paths passed relative to ROOT."""
    rel = lambda p: os.path.relpath(p, ROOT).replace("\\", "/")
    flt = text_filter()
    vf = []
    if flt and ass_file and ass_file.exists():
        fd = f":fontsdir={rel(fontsdir)}" if fontsdir.exists() else ""
        vf.append(f"{flt}={rel(ass_file)}{fd}")
    elif ass_file:
        log.error("ffmpeg هذا بدون libass: لا يمكن كتابة الترجمة على الفيديو. ثبّت نسخة ffmpeg الكاملة (setup.bat يفعل ذلك).")
    args = ["ffmpeg", "-y", "-v", "error", "-i", rel(raw)]
    if vf:
        args += ["-vf", ",".join(vf)]
    args += ["-c:v", "libx264", "-preset", "medium", "-crf", "21", "-maxrate", "9M", "-bufsize", "18M",
             "-pix_fmt", "yuv420p", "-r", "30",
             "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
             "-map_metadata", "-1", "-movflags", "+faststart", rel(out)]
    code, _, err = run(args, cwd=ROOT, timeout=900)
    if code != 0 or not out.exists():
        raise RuntimeError("ffmpeg (ترجمة): " + (err.strip().splitlines() or ["?"])[-1])
    return bool(vf)
