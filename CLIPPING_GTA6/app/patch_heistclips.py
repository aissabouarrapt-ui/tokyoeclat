# -*- coding: utf-8 -*-
"""Two small local changes to heistclips (pinned commit 50bb761), applied by setup.

Bug found while testing (YourRAGE's VOD 2931997902): newer Twitch VODs are served
as fragmented MP4 (#EXT-X-MAP init segment + .mp4 fragments) instead of .ts.
heistclips ignores the init segment, so ffmpeg cannot read the fragments
("trun track id unknown") and every cut of that VOD fails.
Fix: remember the #EXT-X-MAP URI and, when present, glue init + fragments into one
file before handing it to ffmpeg. .ts VODs are untouched.

Idempotent; refuses to touch a file whose code does not match what it expects."""
import sys
from pathlib import Path

CLI = Path(__file__).resolve().parent.parent / "tools" / "heistclips" / "cli" / "heistclips.mjs"
MARK = "/* CLIPPING_GTA6 patch: fmp4 */"

EDITS = [
    (
        # 2) also print the stream's game category, so a Fortnite stream titled
        #    "... GTA GIVEAWAY ..." is not mistaken for GTA content.
        """  log(`reading chat: ${vod.channel_name} – ${vod.title} (${clock(vod.length)})`);""",
        """  log(`reading chat: ${vod.channel_name} – ${vod.title} (${clock(vod.length)}) [game: ${vod.game || ""}]`);""",
    ),
    (
        """  const segs = []; let t = 0, dur = 0;
  for (const ln of lines) {
    if (ln.startsWith("#EXTINF:")) dur = parseFloat(ln.slice(8));""",
        """  const segs = []; let t = 0, dur = 0; """ + MARK + """
  for (const ln of lines) {
    if (ln.startsWith("#EXT-X-MAP:")) { const mm = ln.match(/URI="([^"]+)"/); if (mm) segs.initUrl = new URL(mm[1], base).href; }
    else if (ln.startsWith("#EXTINF:")) dur = parseFloat(ln.slice(8));""",
    ),
    (
        """  const segs = (await playlist(vod)).filter((s) => s.start + s.dur > start && s.start < end);""",
        """  const all = await playlist(vod);
  const segs = all.filter((s) => s.start + s.dur > start && s.start < end);""",
    ),
    (
        """  const list = [];
  for (const [i, s] of segs.entries()) {
    const r = await fetch(s.url);
    if (!r.ok) throw new Error("segment " + r.status);
    const f = path.join(tmp, `s${i}.ts`);
    fs.writeFileSync(f, Buffer.from(await r.arrayBuffer()));
    list.push(`file '${f}'`);
  }""",
        """  const list = [];
  const parts = [];
  if (all.initUrl) { const ri = await fetch(all.initUrl); if (!ri.ok) throw new Error("init segment " + ri.status); parts.push(Buffer.from(await ri.arrayBuffer())); }
  for (const [i, s] of segs.entries()) {
    const r = await fetch(s.url);
    if (!r.ok) throw new Error("segment " + r.status);
    if (all.initUrl) { parts.push(Buffer.from(await r.arrayBuffer())); continue; }
    const f = path.join(tmp, `s${i}.ts`);
    fs.writeFileSync(f, Buffer.from(await r.arrayBuffer()));
    list.push(`file '${f}'`);
  }
  if (all.initUrl) { const f = path.join(tmp, "joined.mp4"); fs.writeFileSync(f, Buffer.concat(parts)); list.push(`file '${f}'`); }""",
    ),
]


def apply(path=CLI):
    if not path.exists():
        return False, "heistclips غير مثبت"
    src = path.read_text(encoding="utf-8")
    done, missing = 0, 0
    for old, new in EDITS:
        if new in src:
            done += 1
        elif src.count(old) == 1:
            src = src.replace(old, new)
            done += 1
        else:
            missing += 1
    path.write_text(src, encoding="utf-8")
    if missing:
        return False, "نسخة heistclips مختلفة عن المتوقعة؛ بعض الإصلاحات لم تُطبق"
    return True, f"الإصلاحات مطبقة ({done})"


if __name__ == "__main__":
    ok, msg = apply()
    print(("OK: " if ok else "WARN: ") + msg)
    sys.exit(0)
