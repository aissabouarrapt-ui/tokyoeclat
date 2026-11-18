# -*- coding: utf-8 -*-
"""Cutting: for each streamer in streamers.txt, find moments with heistclips, keep the
best NEW ones without a music warning, cut them 9:16, add captions, write metadata.

"auto <streamer> --n 5" semantics, made safe for a run every 2 hours:
  * asks heistclips for up to 3x more candidates than needed, then keeps the top
    CLIPS_PER_STREAMER that were never cut before -> each run brings NEW moments
    instead of re-finding the same top 5 of a 24/7 marathon;
  * skips every moment with a music warning (Twitch-muted audio = "high", chat talking
    about music = "medium"), before downloading anything;
  * skips streams whose title is not GTA (campaigns pay for GTA content only);
  * stops producing when MAX_READY_CLIPS are waiting (6 posts/day/account can never use
    5 clips x N streamers x 12 runs/day: that would fill the disk for nothing)."""
import shutil

from common import READY, ROOT, WORK, check_stop, iso, load_json, log, now_utc, save_json
import facecam
import heist
import subs
import textgen
from publisher import HISTORY


def overlaps(a_start, a_end, b_start, b_end):
    inter = min(a_end, b_end) - max(a_start, b_start)
    return inter > 0.3 * min(a_end - a_start, b_end - b_start) or abs(a_start - b_start) < 15


def already_done(m, history):
    for c in history["clips"].values():
        if str(c.get("vod_id")) != str(m.get("vod_id")):
            continue
        if overlaps(c["t_start"], c["t_end"], m["t_start"], m["t_end"]):
            if c.get("status") == "failed" and c.get("attempts", 0) < 2:
                continue  # one more try
            return True
    return False


def ready_count(history):
    return sum(1 for c in history["clips"].values()
               if c.get("status") == "ready" and (ROOT / c["file"]).exists())


def choose_layout(s, m, history, cfg):
    """Per clip: forced layout / streamers.txt corner / webcam detection on this moment."""
    forced = (cfg.get("LAYOUT") or "auto").lower()
    if forced in ("fit", "crop"):
        return forced, None
    if s["cam"] in ("tl", "tr", "bl", "br"):
        return "split", s["cam"]
    if s["cam"] == "none":
        return "fit", None
    priors = history.setdefault("facecam", {})
    prior = (priors.get(s["login"]) or {}).get("cam")
    cam = facecam.detect(heist.cut, heist.cut_source(m), m["t_start"] + 20, f"{s['login']}_{int(m['t_start'])}", prior)
    if cam:
        priors[s["login"]] = {"cam": cam, "at": iso(now_utc())}
    log.info(f"  الكاميرا: {('زاوية ' + cam + ' -> تخطيط split (الكاميرا فوق، اللعب تحت)') if cam else 'لا كاميرا في زاوية -> تخطيط fit'}")
    return ("split", cam) if cam else ("fit", None)


def make_clip(m, s, layout, cam, cfg, history, title_line):
    vod_id = str(m["vod_id"])
    cid = f"{s['login']}-{vod_id[:12]}-{int(m['t_start'])}"
    work = WORK / cid
    work.mkdir(parents=True, exist_ok=True)
    rec = {
        "id": cid, "login": s["login"], "name": s["name"], "platform": s["platform"],
        "vod_id": vod_id, "t_start": m["t_start"], "t_end": m["t_end"], "url": m.get("url"),
        "score": m.get("score"), "chat_ratio": m.get("chat_ratio"), "event_type": m.get("event_type"),
        "music": (m.get("music_risk") or {}).get("level"), "layout": layout, "cam": cam,
        "stream_title": title_line[:200], "created_at": iso(now_utc()), "status": "cutting",
        "attempts": history["clips"].get(cid, {}).get("attempts", 0) + 1,
    }
    history["clips"][cid] = rec
    save_json(HISTORY, history)
    try:
        raw = work / "raw.mp4"
        heist.cut(heist.cut_source(m), m["t_start"], m["t_end"], raw, layout=layout, cam=cam or "tr")
        check_stop()
        words, lang, text = subs.transcribe(raw, cfg.get("WHISPER_MODEL"), s.get("lang"))
        check_stop()
        duration = m["t_end"] - m["t_start"]
        hook = (m.get("hooks") or [""])[0]
        hook = _rename(hook, s)
        ass = work / "subs.ass"
        chunks = subs.build_ass(ass, words, hook, duration, layout, cfg.get("SUBTITLE_FONT"),
                                cfg.bool("CENSOR_SUBTITLES"))
        final = READY / f"{cid}.mp4"
        burned = subs.render(raw, ass, final)
        onset_rel = max(0.0, (m.get("t_peak") or (m["t_start"] + 24)) - m["t_start"] - 6)
        meta = {"id": cid, "hook": hook, "event_type": m.get("event_type"), "words": words,
                "onset_rel": onset_rel, "channel_name": s["login"]}
        texts = textgen.make(meta, s, cfg.get("EXTRA_HASHTAGS"), cfg.bool("CENSOR_SUBTITLES"))
        rec.update(status="ready", file=f"clips/ready/{cid}.mp4", hook=hook, language=lang,
                   transcript=text[:1500], subtitle_chunks=chunks, subtitles_burned=burned,
                   thumb_ms=int(min(duration - 1, onset_rel + 4) * 1000), texts=texts)
        save_json(HISTORY, history)
        log.info(f"  ✂️  كليب جاهز: {final.name} | «{texts['title']}» | {chunks} سطر ترجمة ({lang})")
        return rec
    except Exception as e:
        rec.update(status="failed", error=str(e)[:400])
        save_json(HISTORY, history)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _rename(hook, s):
    import re
    return re.sub(re.escape(s["login"]), s["name"], hook, flags=re.I) if hook else hook


def produce(cfg, streamers):
    history = load_json(HISTORY, {"clips": {}, "facecam": {}})
    history.setdefault("facecam", {})
    per = max(0, cfg.int("CLIPS_PER_STREAMER"))
    cap = cfg.int("MAX_READY_CLIPS")
    made = 0
    for s in streamers:
        check_stop()
        waiting = ready_count(history)
        if waiting >= cap:
            log.info(f"القص: {waiting} كليب ينتظر النشر (الحد MAX_READY_CLIPS={cap})؛ لا قص جديد هذه المرة.")
            break
        want = min(per, cap - waiting)
        if want <= 0:
            break
        log.info(f"🎮 {s['name']} ({s['source']}): البحث عن اللحظات…")
        try:
            title_line, moms = heist.moments(s["source"], min(15, max(want * 3, want + 3)))
        except Exception as e:
            log.error(f"  تعذر قراءة بث {s['name']}: {e}")
            continue
        if title_line:
            log.info(f"  البث: {title_line[:150]}")
        if cfg.bool("ONLY_GTA_STREAMS") and title_line and not heist.is_gta(title_line):
            log.info(f"  تخطي: آخر بث لـ {s['name']} ليس GTA (الحملة تدفع على محتوى GTA فقط).")
            continue
        chosen, skipped_music, skipped_dup = [], 0, 0
        for m in moms:
            lvl = (m.get("music_risk") or {}).get("level", "low")
            if cfg.bool("SKIP_MUSIC_WARNING") and lvl in ("medium", "high"):
                skipped_music += 1
                log.info(f"  🎵 حذف لحظة {int(m['t_start'])}ث: تحذير موسيقى ({lvl}) - {(m.get('music_risk') or {}).get('why', '')}")
                continue
            if already_done(m, history):
                skipped_dup += 1
                continue
            chosen.append(m)
            if len(chosen) >= want:
                break
        log.info(f"  لحظات: {len(moms)} وُجدت، {skipped_music} حُذفت (موسيقى)، {skipped_dup} سبق قصها، {len(chosen)} للقص")
        if not chosen:
            continue
        for m in chosen:
            check_stop()
            try:
                layout, cam = choose_layout(s, m, history, cfg)
                make_clip(m, s, layout, cam, cfg, history, title_line)
                made += 1
            except Exception as e:
                if e.__class__.__name__ == "Stopped":
                    raise
                log.error(f"  فشل قص اللحظة {int(m['t_start'])}ث من {s['name']}: {e}")
    return made
