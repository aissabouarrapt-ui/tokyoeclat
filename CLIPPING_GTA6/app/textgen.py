# -*- coding: utf-8 -*-
"""Titles, descriptions and hashtags, generated from what is actually in the clip.

Rules the GTA campaigns enforce (read on reachclipping.com, Nov 2026):
  * caption / overlay must mention the streamer's name  -> name is always in the title
  * a required hashtag (#LACY, #Clix, #LaurenHamden + @LaurenHamden)
                                                          -> streamers.txt column 3, always first
  * English captions, no "hate farming", no negative framing -> templates are neutral/positive,
                                                          slurs never reach a title
Best title = a real quote from the clip (campaigns ask for "memorable quotes"):
the shortest punchy line spoken around the moment chat exploded."""
import hashlib
import re

from subs import censor, has_slur, strip_emoji

EMOJI = {"funny": "😂", "hype": "🔥", "shock": "😳", "fail": "💀", "reaction": "😭"}
CONTEXT = {
    "funny": ["had chat CRYING", "can't stop laughing", "had the whole chat laughing"],
    "hype": ["just pulled THIS off", "went crazy", "made the cleanest play"],
    "shock": ["did NOT see this coming", "couldn't believe it", "was SHOCKED"],
    "fail": ["will never live this down", "did not plan for this", "had chat in tears"],
    "reaction": ["had the best reaction", "could not believe this", "reacted live"],
}
TEMPLATES = ["{name} {ctx} in GTA 6 {emoji}", "{name} {ctx} {emoji} | GTA 6", "GTA 6: {name} {ctx} {emoji}"]
BASE_TAGS = ["#GTA6", "#GTAVI", "#gaming", "#twitch", "#streamer", "#gta6clips"]


def _pick(seq, seed):
    h = int(hashlib.md5(seed.encode()).hexdigest(), 16)
    return seq[h % len(seq)]


def _tag(name):
    return "#" + re.sub(r"[^A-Za-z0-9_]", "", name)


FILLER = {"oh", "ah", "uh", "um", "yo", "bro", "bruh", "what", "the", "a", "an", "i", "it", "is", "was", "that",
          "this", "and", "to", "of", "okay", "ok", "yeah", "no", "yes", "hey", "man", "dude", "wow", "like", "so",
          "just", "my", "god", "me", "you", "we", "he", "she", "they", "in", "on", "at", "for", "be", "are", "do",
          "did", "it's", "i'm", "go", "get", "got", "here", "there", "up", "out", "all", "right", "now", "come", "on"}


def best_quote(words, onset_rel=24.0):
    """A short, complete, meaningful sentence spoken near the moment chat exploded,
    or None (then the title falls back to a template). Rejects stutters ("Big- Oh!"),
    fillers ("Oh! Oh!"), fragments and anything with a slur."""
    if not words or len(words) < 6:
        return None
    sents, cur = [], []
    for w in words:
        cur.append(w)
        if w[2][-1:] in ".!?" or len(cur) >= 12:
            sents.append(cur)
            cur = []
    if cur:
        sents.append(cur)
    best, best_score = None, 0.0
    for s in sents:
        text = " ".join(w[2] for w in s).strip(" ,-")
        toks = [t.lower().strip("!?.,'\"") for t in text.split()]
        content = [t for t in toks if t and t not in FILLER and len(t) > 2]
        if not (3 <= len(toks) <= 9) or len(content) < 2 or len(text) > 55:
            continue
        if "-" in text or "..." in text or has_slur(text) or len(set(toks)) < len(toks) - 1:
            continue
        mid = (s[0][0] + s[-1][1]) / 2
        if abs(mid - onset_rel) > 14:
            continue
        score = 3.0 - abs(mid - onset_rel) / 5
        score += 1.5 if text.endswith(("!", "?")) else 0
        score += 0.5 * min(3, len(content))
        if score > best_score:
            best, best_score = text, score
    if not best:
        return None
    return best[0].upper() + best[1:]


def make(meta, streamer, extra_hashtags="#GTA6 #GTAVI", censor_on=True):
    """meta: clip metadata (hook, event_type, words, id, login...). -> dict of texts."""
    name = streamer["name"]
    ev = meta.get("event_type") or "reaction"
    emoji = EMOJI.get(ev, "🔥")
    seed = meta["id"]
    hook = strip_emoji(meta.get("hook") or "").replace(meta.get("channel_name") or "\0", name)
    quote = best_quote(meta.get("words") or [], meta.get("onset_rel", 24.0))

    if quote:
        q = censor(quote, censor_on)
        title = f'{name}: "{q}" {emoji}'
    elif hook and name.lower() in hook.lower():
        title = f"{hook} {emoji}"
    else:
        ctx = _pick(CONTEXT.get(ev, CONTEXT["reaction"]), seed)
        title = _pick(TEMPLATES, seed + "t").format(name=name, ctx=ctx, emoji=emoji)
    title = title[:95]

    required = streamer.get("hashtags", [])
    mentions = streamer.get("mentions", [])
    extra = re.findall(r"#\w+", extra_hashtags or "")
    tags = []
    for t in required + [_tag(name)] + extra + BASE_TAGS:
        if t.lower() not in [x.lower() for x in tags]:
            tags.append(t)
    tiktok_tags = tags[:6]
    line2 = f"{name} {_pick(CONTEXT.get(ev, CONTEXT['reaction']), seed + 'x')} 🌴"
    caption = f"{title}\n\n{line2} {' '.join(mentions)}".rstrip() + "\n\n" + " ".join(tiktok_tags + ["#fyp"])

    src = (f"https://www.twitch.tv/{streamer['login']}" if streamer.get("platform") != "kick"
           else f"https://kick.com/{streamer['login']}")
    yt_title = title if len(title) <= 90 else title[:90]
    if "#shorts" not in yt_title.lower() and len(yt_title) <= 91:
        yt_title += " #shorts"
    yt_desc = (f"{title}\n\n{line2} {' '.join(mentions)}".rstrip()
               + f"\n\nFull stream: {src}\n\n" + " ".join(tags[:8] + ["#shorts"]))
    yt_tags = [t.lstrip("#") for t in tags[:10]] + [name, "GTA 6", "GTA VI", "Grand Theft Auto VI", "shorts"]
    return {
        "title": title, "caption": caption,
        "youtube_title": yt_title[:100], "youtube_description": yt_desc[:4900],
        "youtube_tags": list(dict.fromkeys(yt_tags))[:15],
        "quote": quote or "",
    }
