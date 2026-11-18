# -*- coding: utf-8 -*-
"""campaigns.bat: `heistclips campaigns --filter gta`, enriched.

The CLI prints one line per campaign. To answer "which streamers may I clip, and
does this campaign fit an automated TikTok + YouTube Shorts pipeline?" we also read
the same JSON the CLI reads (heistclips.com/api/campaigns), pull streamer handles from
each brief (twitch.tv/…, kick.com/…, else the campaign title), read the linked rules
page for the required hashtags / mentions / minimum views, and flag what cannot be
automated (pre-approval, music-sound campaigns, YouTube-only streamers, no TikTok/YT).

Writes campaigns.txt, and creates streamers.txt the first time (never overwrites it)."""
import html
import json
import re

from common import ROOT, STREAMERS_TXT, local_str, log
import heist

API = "https://heistclips.com/api/campaigns?filter=gta&limit=50"
MCP = "https://heistclips.com/mcp"
OUT = ROOT / "campaigns.txt"


def _get(url, timeout=20):
    import requests
    r = requests.get(url, timeout=timeout, headers={"User-Agent": "clipping-gta6/1.0"})
    r.raise_for_status()
    return r


def live_gta_logins():
    """Who streams GTA right now (heistclips' public MCP tool live_gta_streams)."""
    import requests
    try:
        r = requests.post(MCP, timeout=30, json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                                  "params": {"name": "live_gta_streams", "arguments": {"limit": 40}}},
                          headers={"Accept": "application/json, text/event-stream"})
        txt = r.json()["result"]["content"][0]["text"]
        return {(s["platform"], s["login"].lower()): s for s in json.loads(txt)["streams"]}
    except Exception:
        return {}


def handles(c):
    d = c.get("description") or ""
    out = []
    for m in re.finditer(r"twitch\.tv/([A-Za-z0-9_]{3,25})", d):
        out.append(("twitch", m.group(1).lower(), "من الرابط"))
    for m in re.finditer(r"kick\.com/([A-Za-z0-9_-]{3,25})", d):
        out.append(("kick", m.group(1).lower(), "من الرابط"))
    yt = re.findall(r"youtube\.com/@([\w.-]+)", d)
    if out:
        return out, yt
    name = title_name(c["title"])
    cats = {x.lower() for x in c.get("categories") or []}
    looks_streamer = bool(cats & {"streamer", "personal brand", "content creator"}) or bool(
        re.search(r"\b(streams?|live|twitch|kick|marathon|playthrough|clips?)\b", d, re.I))
    if re.search(r"storytelling|\bmusic\b|\bsound\b", f"{c['title']} {d}", re.I) or "music" in cats:
        looks_streamer = False
    if name and not yt and looks_streamer:
        plat = "kick" if re.search(r"\bon kick\b|\bkick\b", d, re.I) else "twitch"
        out.append((plat, re.sub(r"[^a-z0-9_]", "", name.lower()), "تخمين من العنوان"))
    return out, yt


def title_name(title):
    t = re.sub(r"\$[\d,.]+k?", " ", title)
    t = re.split(r"\s(?:GTA|gta|Gta)|\s[-–|×x]\s|/|\bClips?\b|\bClipping\b|\bCampaign\b|\bPool\b", t)[0]
    t = re.sub(r"\s+", " ", t).strip(" -–|×")
    return t if 2 <= len(t) <= 30 else ""


def rules_from_page(url):
    """Required hashtags / mentions / minimum views from a campaign rules page."""
    try:
        txt = _get(url, 15).text
    except Exception:
        return None
    txt = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    txt = re.sub(r"\s+", " ", txt)
    if not re.search(r"hashtag|requirements|rules", txt, re.I):
        return None
    tags, ments = [], []
    for sent in re.split(r"(?<=[.!])\s|\*", txt):
        if re.search(r"hashtag|tagging|\btag\b", sent, re.I) and len(sent) < 400:
            tags += [t for t in re.findall(r"#[A-Za-z0-9_]+", sent)
                     if re.search(r"[G-Zg-z_]", t) and not re.fullmatch(r"#[0-9A-Fa-f]{3,8}", t)]
            ments += [m for m in re.findall(r"@[A-Za-z0-9_.]+[A-Za-z0-9_]", sent)
                      if m.lower() not in ("@media", "@import", "@font", "@keyframes", "@supports", "@charset")]
    mvr = re.search(r"Min(?:imum)? Views?(?: Requirement)?\s*\(?MVR\)?\s*([\d,]+)", txt, re.I)
    plats = re.search(r"can only be posted on ([^.]+)\.", txt, re.I)
    return {"hashtags": list(dict.fromkeys(tags)), "mentions": list(dict.fromkeys(ments)),
            "mvr": mvr.group(1) if mvr else None, "platforms": plats.group(1).strip() if plats else None,
            "dedicated": bool(re.search(r"(MUST|must) be branded|dedicated accounts are required", txt))}


def verdict(c, hs, yt, rules=None):
    text = f"{c['title']} {c.get('description') or ''}"
    plats = set(c.get("platforms") or [])
    if rules and rules.get("platforms"):
        for p, word in (("tiktok", "tiktok"), ("youtube", "youtube")):
            if word in rules["platforms"].lower():
                plats.add(p)  # the rules page is more complete than the feed (Lauren Hamden: YouTube Shorts)
    if re.search(r"sound campaign|official sound|\bsong\b|\bEP\b|using .{0,30} by ", text, re.I):
        return "❌", "حملة موسيقى/صوت (تحتاج الأغنية، والنظام يحذف الموسيقى)"
    if not hs:
        return "❌", ("الستريمر على YouTube Live فقط (heistclips يقرأ Twitch و Kick فقط)" if yt
                     else "ليست حملة كليبات ستريمر")
    if c.get("budget_left", 0) <= 0:
        return "❌", "الميزانية انتهت"
    if not ({"tiktok", "youtube"} & plats):
        return "❌", "لا تدفع على TikTok ولا YouTube"
    notes = []
    if c.get("approval_required") or re.search(r"pre-approval|require[sd]? (pre-)?approval", text, re.I):
        notes.append("تتطلب موافقة/طلب انضمام قبل النشر")
    missing = [p for p in ("tiktok", "youtube") if p not in plats]
    if missing:
        notes.append(f"لا تدفع على {', '.join(missing)}")
    if re.search(r"scheduled for|submissions begin only", text, re.I):
        notes.append("لم تبدأ بعد (اقرأ الموعد)")
    if re.search(r"1win|casino|bet\b|betting", text, re.I):
        notes.append("راعي مراهنات: خطر حظر على TikTok")
    return ("⚠️" if notes else "✅"), ("; ".join(notes) or "مناسبة للنظام الآلي")


def report():
    lines = [f"حملات GTA — {local_str()}  (المصدر: heistclips campaigns --filter gta)", ""]
    try:
        raw = heist.campaigns_text("gta", 50)
        lines += ["--- مخرجات heistclips الأصلية ---", raw.strip(), ""]
    except Exception as e:
        log.error(f"heistclips campaigns: {e}")
    data = _get(API).json()
    live = live_gta_logins()
    suggestions = []
    lines.append("--- التحليل (مرتبة حسب ربح الكليب المقبول المتوقع) ---")
    for c in data["campaigns"]:
        hs, yt = handles(c)
        rules = None
        for u in re.findall(r"https?://[^\s)]+", c.get("description") or ""):
            if re.search(r"twitch\.tv|kick\.com|youtube\.com|youtu\.be|contentrewards|whop\.com|discord|instagram\.com"
                         r"|tiktok\.com|x\.com|twitter\.com|linktr\.ee|spotify|apple\.com", u):
                continue
            rules = rules_from_page(u)
            if rules:
                rules["url"] = u
                break
        v, why = verdict(c, hs, yt, rules)
        per = (f"${c['expected_per_clip']:.2f}/كليب مقبول" if c.get("expected_per_clip") is not None
               else "جديدة (لا سجل بعد)")
        if c.get("payout_type") == "cpm":
            pay = f"${c['rate_per_1k']}/1000 مشاهدة، حد ${c.get('cap_per_clip')}/كليب"
        elif c.get("payout_type") == "pool":
            pay = "مسبح أرباح (يُقسم حسب المشاهدات)"
        else:
            pay = f"${c.get('per_post')}/منشور" if c.get("per_post") else str(c.get("payout_type"))
        streamer_txt = ", ".join(
            f"{('kick.com/' if p == 'kick' else 'twitch.tv/')}{h} ({how}"
            f"{', 🔴 يبث GTA الآن' if (p, h) in live else ''})" for p, h, how in hs) or (
            ", ".join(f"youtube.com/@{y}" for y in yt) or "—")
        lines += [
            "",
            f"{c['rank']}. {v} {c['title']}",
            f"   الربح: {per} | {pay} | متبقي ${round(c.get('budget_left') or 0):,}"
            + (f" (~{c['days_left']} يوم)" if c.get("days_left") is not None else ""),
            f"   المنصات: {', '.join(c.get('platforms') or [])} | كليبات مقبولة: {c.get('approved_clips', 0)}",
            f"   الستريمر المسموح: {streamer_txt}",
            f"   الحكم: {why}",
        ]
        if rules:
            if rules.get("platforms"):
                lines.append(f"   صفحة القواعد: النشر مسموح على {rules['platforms']}")
            lines.append(f"   القواعد ({rules['url']}): هاشتاغ إلزامي {' '.join(rules['hashtags']) or '—'}"
                         f" {' '.join(rules['mentions'])} | حد أدنى للمشاهدات {rules['mvr'] or '—'}"
                         f"{' | حساب مخصص للستريمر إلزامي' if rules['dedicated'] else ''}")
        lines.append(f"   الصفحة: {c['url']}")
        if v in ("✅", "⚠️") and hs:
            p, h, _ = hs[0]
            name = title_name(c["title"]) or h
            tags = " ".join((rules or {}).get("hashtags", []) + (rules or {}).get("mentions", []))
            suggestions.append((p, h, name, tags, c["title"], v))
    lines += ["", "ملاحظة: حملات «المسبح» (pool) لا تدفع إلا إذا بلغ مجموع مشاهداتك الحد الأدنى (MVR) قبل نهاية الحملة.",
              "تحقق دائماً من القواعد في صفحة الحملة قبل النشر."]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")
    print("\n".join(lines))
    if not STREAMERS_TXT.exists():
        write_streamers(suggestions)
        print(f"\n✅ أُنشئ streamers.txt بـ {len(suggestions)} ستريمر — عدّله بحرية.")
    else:
        print("\n(streamers.txt موجود؛ لم يُعدَّل. الاقتراحات أعلاه.)")
    return suggestions


def write_streamers(sugg):
    seen, rows = set(), []
    for p, h, name, tags, title, v in sugg:
        if h in seen:
            continue
        seen.add(h)
        src = f"kick.com/{h}" if p == "kick" else h
        prefix = "" if v == "✅" else "# "
        rows.append(f"# {title} ({v})\n{prefix}{src} | {name} | {tags} | auto | auto")
    STREAMERS_TXT.write_text(STREAMERS_HEADER + "\n\n".join(rows) + "\n", encoding="utf-8")


STREAMERS_HEADER = """# ============================ streamers.txt ============================
# سطر لكل ستريمر. السطر الذي يبدأ بـ # مُعطّل (احذف # لتفعيله).
#
#  الستريمر          | الاسم في العناوين | هاشتاغات/منشن إلزامية | الكاميرا | اللغة
#  lacy              | LACY              | #LACY                | auto     | en
#  kick.com/gattouz  | Gattouz           |                      | auto     | fr
#
#  الكاميرا: auto (كشف تلقائي) أو tl / tr / bl / br (زاوية الكاميرا) أو none (بدون كاميرا)
#  اللغة: auto أو en / fr / es ... (لغة الترجمة المكتوبة على الفيديو)
#  الهاشتاغ الإلزامي للحملة يوضع أولاً في كل وصف (بدونه يُرفض الكليب).
# ======================================================================

"""
