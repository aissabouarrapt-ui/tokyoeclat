# -*- coding: utf-8 -*-
"""Publishing: which clip goes to which account, when, and recording the link.

Anti-spam rules (per account, config.txt):
  * at most MAX_POSTS_PER_DAY posts in any rolling 24 h (stricter than "per calendar day":
    no burst of 6 at 23:00 + 6 at 00:00);
  * at least MIN_HOURS_BETWEEN_POSTS between two posts;
  * optional POST_HOURS window (local time).
The scheduler fires every 2 h, so a slot is usually due a few minutes after the run
starts: if the next slot is less than MAX_WAIT_MINUTES away we wait for it instead of
losing 2 h (otherwise 2 h + a few seconds of drift would halve the daily volume).

A clip goes once per platform (one TikTok account + one YouTube account), never twice
on the same platform: re-uploading the same video on several accounts is "reposting",
which the Reach campaigns ban, and TikTok suppresses duplicates anyway.

The link is fetched right after posting (polling up to LINK_WAIT_MINUTES) because the
Reach campaigns want links submitted within 30 minutes of posting."""
import csv
from datetime import timedelta

from common import (DATA, DRYRUN_CSV, LINKS_CSV, POSTED, ROOT, api_key, check_stop, iso, local_str, log,
                    load_json, now_utc, parse_iso, save_json, sleep_checking_stop)
import pfm

HISTORY = DATA / "history.json"


def ledger_path(dry):
    return DATA / ("ledger_dryrun.json" if dry else "ledger.json")


def acc_key(a):
    return f"{a['platform']}:{a['user'].lower()}"


def _append_csv(path, header, row):
    new = not path.exists() or path.stat().st_size == 0
    with open(path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(header)
        w.writerow(row)


def write_link(streamer_name, platform, link):
    _append_csv(LINKS_CSV, ["date", "streamer", "platform", "link"],
                [local_str(), streamer_name, {"tiktok": "TikTok", "youtube": "YouTube Shorts"}[platform], link])
    log.info(f"  ✅ رابط جديد في links.csv: {link}  (أرسله للحملة خلال 30 دقيقة)")


# ------------------------------------------------------------------- schedule
def next_slot(key, ledger, cfg, now=None):
    """-> (seconds to wait (0 = now) or None if not today, reason)"""
    now = now or now_utc()
    mine = sorted(parse_iso(p["created_at"]) for p in ledger["posts"]
                  if p["account"] == key and p["status"] != "upload_failed")
    day = [t for t in mine if t > now - timedelta(hours=24)]
    due = now
    reason = ""
    if len(day) >= cfg.int("MAX_POSTS_PER_DAY"):
        due = max(due, day[-cfg.int("MAX_POSTS_PER_DAY")] + timedelta(hours=24))
        reason = f"الحد اليومي ({cfg.int('MAX_POSTS_PER_DAY')}/24س) مكتمل"
    if mine:
        gap_due = mine[-1] + timedelta(hours=cfg.float("MIN_HOURS_BETWEEN_POSTS"))
        if gap_due > due:
            due = gap_due
            reason = reason or f"آخر منشور قبل أقل من {cfg.get('MIN_HOURS_BETWEEN_POSTS')} ساعة"
    hours = cfg.post_hours()
    if len(hours) < 24:
        t = due.astimezone()
        for _ in range(48):
            if t.hour in hours:
                break
            t = (t + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        if t > due.astimezone():
            due = t
            reason = reason or f"خارج ساعات النشر POST_HOURS={cfg.get('POST_HOURS')}"
    wait = (due - now).total_seconds()
    return max(0.0, wait), (reason or "جاهز"), due


# -------------------------------------------------------------------- picking
def posted_platforms(clip_id, ledger):
    return {p["platform"] for p in ledger["posts"]
            if p["clip"] == clip_id and p["status"] in ("pending", "published", "dry_run")}


def pick_clip(account, history, ledger, cfg, now=None):
    now = now or now_utc()
    key = acc_key(account)
    wanted = set(account["streamers"])
    last = [p for p in ledger["posts"] if p["account"] == key]
    last_streamer = last[-1]["streamer"] if last else None
    tried_here = {p["clip"] for p in last}
    best, best_score = None, -1
    for c in history["clips"].values():
        if c.get("status") != "ready" or not (ROOT / c["file"]).exists():
            continue
        if "*" not in wanted and c["login"] not in wanted:
            continue
        if c["id"] in tried_here or account["platform"] in posted_platforms(c["id"], ledger):
            continue
        age_h = (now - parse_iso(c["created_at"])).total_seconds() / 3600
        if age_h > cfg.float("MAX_CLIP_AGE_HOURS"):
            continue
        score = max(0.5, float(c.get("score") or 1)) * 0.5 ** (age_h / 12)  # fresh beats old
        if c["login"] == last_streamer and "*" in wanted:
            score *= 0.6  # mix streamers on a shared account
        if score > best_score:
            best, best_score = c, score
    return best


# ------------------------------------------------------------------- publishing
def resolve_accounts(cfg, client):
    """config.txt names -> Post for Me account ids. Only those accounts, nothing else."""
    remote = client.accounts() if client else []
    out = []
    for a in cfg.accounts:
        match = None
        for r in remote:
            if r.get("platform") != a["platform"]:
                continue
            if a["user"].startswith("spc_") and r.get("id") == a["user"]:
                match = r
            elif (r.get("username") or "").lower().lstrip("@") == a["user"].lower():
                match = r
            elif ((r.get("metadata") or {}).get("display_name") or "").lower() == a["user"].lower():
                match = r
            if match:
                break
        if client and not match:
            log.error(f"الحساب '{a['user']}' ({a['platform']}) من config.txt سطر {a['line']} غير موجود في Post for Me. "
                      f"شغّل accounts.bat لرؤية الأسماء الصحيحة. لن يُنشر عليه شيء.")
            continue
        if match and match.get("status") == "disconnected":
            log.error(f"الحساب '{a['user']}' ({a['platform']}) غير متصل في Post for Me (disconnected): أعد ربطه من لوحة Post for Me.")
            continue
        out.append({**a, "id": match["id"] if match else f"dry-{acc_key(a)}",
                    "username": (match or {}).get("username") or a["user"]})
    return out


def _poll_link(client, post, account, streamer_name, minutes, ledger, lpath):
    """Wait for this post's result; record the link. Returns True when resolved (ok or failed)."""
    deadline = now_utc() + timedelta(minutes=minutes)
    first = True
    while True:
        if not first:
            sleep_checking_stop(20)
        first = False
        try:
            results = client.results(post["post_id"])
        except pfm.ApiError as e:
            log.warning(f"  {e}")
            results = []
        mine = [r for r in results if r.get("social_account_id") == account["id"]] or results
        if mine:
            r = mine[0]
            if not r.get("success"):
                post["status"] = "failed"
                post["error"] = str(r.get("error") or r.get("details") or "?")[:500]
                save_json(lpath, ledger)
                log.error(f"  ❌ {account['platform']} @{account['username']} رفض المنشور: {post['error']}")
                return True
            link = pfm.video_link(account["platform"], r, account.get("username", ""))
            if not link:
                fp = client.feed_post(account["id"], post["post_id"])
                if fp:
                    link = pfm.video_link(account["platform"], {"platform_data": {"url": fp.get("platform_url"),
                                                                                  "id": fp.get("platform_post_id")}},
                                          account.get("username", ""))
            if link:
                post.update(status="published", link=link, published_at=iso(now_utc()))
                save_json(lpath, ledger)
                write_link(streamer_name, account["platform"], link)
                return True
        if now_utc() > deadline:
            log.warning(f"  ⏳ المنشور {post['post_id']} لم يُعطِ رابط الفيديو بعد؛ سأعيد المحاولة في التشغيل القادم.")
            return False


def resolve_pending(client, accounts, ledger, lpath, history):
    by_id = {a["id"]: a for a in accounts}
    for p in ledger["posts"]:
        if p["status"] != "pending" or p["account_id"] not in by_id:
            continue
        if now_utc() - parse_iso(p["created_at"]) > timedelta(days=3):
            p["status"] = "unknown"
            log.warning(f"  المنشور {p['post_id']} بلا رابط منذ 3 أيام: تحقق منه يدوياً في Post for Me.")
            continue
        c = history["clips"].get(p["clip"], {})
        _poll_link(client, p, by_id[p["account_id"]], c.get("name", p["streamer"]), 0, ledger, lpath)
    save_json(lpath, ledger)


def publish_phase(cfg, label=""):
    """Post every account that is due (or due within MAX_WAIT_MINUTES)."""
    if not cfg.accounts:
        log.info("النشر: لا حسابات في config.txt (أضف سطر tiktok | ... أو youtube | ...).")
        return 0
    dry = cfg.dry_run
    key = api_key()
    client = None
    if key:
        client = pfm.PostForMe(key)
    elif not dry:
        log.error("DRY_RUN=false لكن POSTFORME_API_KEY غير موجود في .env: لا نشر.")
        return 0
    try:
        accounts = resolve_accounts(cfg, client)
    except pfm.ApiError as e:
        log.error(f"تعذر قراءة الحسابات من Post for Me: {e}")
        if not dry:
            return 0
        accounts = resolve_accounts(cfg, None)
    if dry and not client:
        log.info("DRY_RUN: لا مفتاح API، أتحقق من الجدولة فقط (أسماء الحسابات لم تُفحص).")

    lpath = ledger_path(dry)
    ledger = load_json(lpath, {"posts": []})
    history = load_json(HISTORY, {"clips": {}, "facecam": {}})
    if client and not dry:
        resolve_pending(client, accounts, ledger, lpath, history)

    posted = 0
    handled = set()
    max_wait = cfg.float("MAX_WAIT_MINUTES") * 60
    while True:
        check_stop()
        todo = []
        for a in accounts:
            if acc_key(a) in handled:
                continue
            wait, reason, due = next_slot(acc_key(a), ledger, cfg)
            todo.append((wait, a, reason, due))
        if not todo:
            break
        todo.sort(key=lambda x: x[0])
        wait, a, reason, due = todo[0]
        handled.add(acc_key(a))
        if wait > max_wait:
            log.info(f"النشر {a['platform']} @{a['user']}: {reason}؛ الموعد القادم {local_str(due)}")
            continue
        clip = pick_clip(a, history, ledger, cfg)
        if not clip:
            log.info(f"النشر {a['platform']} @{a['user']}: لا يوجد كليب جاهز مناسب الآن.")
            continue
        if wait > 0:
            log.info(f"النشر {a['platform']} @{a['user']}: انتظار {int(wait // 60)} د {int(wait % 60)} ث حتى يكتمل فاصل الساعتين…")
            sleep_checking_stop(wait)
        posted += post_one(cfg, client, a, clip, ledger, lpath, dry)
    return posted


def post_one(cfg, client, account, clip, ledger, lpath, dry):
    texts = clip["texts"]
    path = ROOT / clip["file"]
    entry = {"account": acc_key(account), "account_id": account["id"], "platform": account["platform"],
             "clip": clip["id"], "streamer": clip["login"], "created_at": iso(now_utc()), "status": "pending"}
    shown = texts["title"] if account["platform"] == "tiktok" else texts["youtube_title"]
    if dry:
        entry["status"] = "dry_run"
        ledger["posts"].append(entry)
        save_json(lpath, ledger)
        _append_csv(DRYRUN_CSV, ["date", "streamer", "platform", "account", "file", "title", "caption"],
                    [local_str(), clip["name"], account["platform"], account["user"], clip["file"], shown,
                     (texts["caption"] if account["platform"] == "tiktok" else texts["youtube_description"]).replace("\n", " ⏎ ")])
        log.info(f"🧪 DRY_RUN: كان سيُنشر على {account['platform']} @{account['user']}: «{shown}» ({clip['file']})")
        return 1
    log.info(f"🚀 نشر على {account['platform']} @{account['user']}: «{shown}»")
    try:
        media_url = client.upload(path)
        body = pfm.build_body(account["platform"], account["id"], media_url, texts, cfg, clip)
        post = client.create_post(body)
    except pfm.ApiError as e:
        entry.update(status="upload_failed", error=str(e)[:500])
        ledger["posts"].append(entry)
        save_json(lpath, ledger)
        log.error(f"  ❌ فشل النشر: {e}")
        return 0
    entry["post_id"] = post.get("id")
    ledger["posts"].append(entry)
    save_json(lpath, ledger)
    log.info(f"  المنشور أُنشئ ({entry['post_id']})، أنتظر رابط الفيديو…")
    _poll_link(client, entry, account, clip["name"], cfg.float("LINK_WAIT_MINUTES"), ledger, lpath)
    return 1


# ---------------------------------------------------------------------- tidy
def cleanup(cfg):
    history = load_json(HISTORY, {"clips": {}, "facecam": {}})
    dry = cfg.dry_run
    ledger = load_json(ledger_path(False), {"posts": []})
    platforms_with_accounts = {a["platform"] for a in cfg.accounts}
    now = now_utc()
    changed = 0
    for c in history["clips"].values():
        f = ROOT / c["file"] if c.get("file") else None
        age_h = (now - parse_iso(c["created_at"])).total_seconds() / 3600
        if c.get("status") == "ready":
            done = platforms_with_accounts and platforms_with_accounts <= posted_platforms(c["id"], ledger)
            if done and not dry and f and f.exists():
                dest = POSTED / f.name
                f.replace(dest)
                c.update(status="done", file=str(dest.relative_to(ROOT)).replace("\\", "/"))
                changed += 1
            elif age_h > cfg.float("MAX_CLIP_AGE_HOURS"):
                if f and f.exists():
                    f.unlink()
                c["status"] = "expired"
                changed += 1
        elif c.get("status") == "done" and f and f.exists() and age_h > 24 * cfg.float("KEEP_POSTED_DAYS"):
            f.unlink()
            changed += 1
    # forget very old records but keep enough to never re-cut a moment of a live VOD
    for cid in [k for k, c in history["clips"].items()
                if (now - parse_iso(c["created_at"])).days > 30]:
        del history["clips"][cid]
    if changed:
        save_json(HISTORY, history)
    return changed
