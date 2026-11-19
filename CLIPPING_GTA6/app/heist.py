# -*- coding: utf-8 -*-
"""Thin wrapper around the heistclips CLI (tools/heistclips, pinned commit).

Why not just `heistclips auto <streamer> --n 5`? Tested, `auto`:
  * only skips music risk "high" (Twitch-muted audio); "medium" (chat talks about
    music = probably GTA radio, a Content ID claim) gets cut and posted;
  * has no memory: run every 2 hours it re-downloads and re-cuts the same top 5;
  * always uses the facecam in the top-right corner (Lacy's is top-left: the
    top half of the clip showed chat instead of his face);
  * burns the hook with a drawtext path that breaks on Windows drive letters.
So we do what `auto` does (moments -> cut, same 9:16 layouts) in two calls and
filter / de-duplicate / pick the layout in between."""
import json
import re
import shutil

from common import HEISTCLIPS, TOOLS, log, run

GTA_RE = re.compile(r"\bgta\b|gta\s?(6|vi|v|5)\b|gta6|gtavi|grand theft auto|vice city|leonida", re.I)


def node_bin():
    return shutil.which("node") or "node"


def available():
    return HEISTCLIPS.exists()


def moments(source, n):
    """-> (stream_title_line, [moment dicts]). Raises RuntimeError on failure."""
    code, out, err = run([node_bin(), HEISTCLIPS, "moments", source, "--n", str(n), "--json"],
                         cwd=TOOLS / "heistclips", timeout=600)
    if code != 0:
        msg = (err.strip().splitlines() or ["?"])[-1]
        raise RuntimeError(f"heistclips moments {source}: {msg}")
    m = re.search(r"reading chat: (.+)", err)
    title = m.group(1).strip() if m else ""
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"heistclips moments {source}: رد غير مفهوم: {out[:200]}")
    return title, data


def cut_source(moment):
    """What to pass to `heistclips cut`: the Twitch VOD id, or the Kick VOD URL."""
    vid = str(moment.get("vod_id", ""))
    if vid.isdigit():
        return vid
    return str(moment.get("url", "")).split("?")[0]


def cut(source, start, end, out_path, layout="split", cam="tr"):
    args = [node_bin(), HEISTCLIPS, "cut", source, "--start", f"{start:.2f}", "--end", f"{end:.2f}",
            "--layout", layout, "--out", str(out_path)]
    if layout == "split":
        args += ["--cam", cam]
    code, out, err = run(args, cwd=TOOLS / "heistclips", timeout=900)
    if code != 0 or not out_path.exists() or out_path.stat().st_size < 10000:
        msg = (err.strip().splitlines() or ["?"])[-1]
        raise RuntimeError(f"heistclips cut فشل: {msg}")
    return out_path


def campaigns_text(filter_="gta", n=50):
    code, out, err = run([node_bin(), HEISTCLIPS, "campaigns", "--filter", filter_, "--n", str(n)],
                         cwd=TOOLS / "heistclips", timeout=120)
    if code != 0:
        raise RuntimeError("heistclips campaigns: " + (err.strip().splitlines() or ["?"])[-1])
    return out


# Seen 19/11/2026: "Prepping For GTA 6 + Finishing 5 Huzz" was a dating game, not GTA.
PRE_GTA_RE = re.compile(r"prep+ing for|getting ready for|waiting for|before (gta|grand)|count ?down|until (gta|grand)", re.I)
NEUTRAL_GAMES = {"", "just chatting", "irl", "travel & outdoors", "special events", "none", "null", "undefined"}


def gta_check(title_line):
    """-> (is GTA?, reason). Trusts the stream's game category; the title is only used when
    the category is neutral (Just Chatting / IRL: many marathons start there)."""
    m = re.search(r"\[game: ([^\]]*)\]\s*$", title_line or "")
    game = (m.group(1).strip() if m else "")
    if GTA_RE.search(game):
        return True, f"الفئة: {game}"
    if game.lower() not in NEUTRAL_GAMES:
        return False, f"الفئة: {game} (ليست GTA)"
    if PRE_GTA_RE.search(title_line or ""):
        return False, "العنوان يقول إن GTA لم يبدأ بعد (prepping/waiting/countdown)"
    if GTA_RE.search(title_line or ""):
        return True, "العنوان يذكر GTA"
    return False, "لا الفئة ولا العنوان GTA"


def log_version():
    try:
        v = (TOOLS / "heistclips" / "VERSION.txt").read_text(encoding="utf-8").strip()
        log.info(f"heistclips: {v}")
    except OSError:
        pass
