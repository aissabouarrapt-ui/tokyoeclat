# -*- coding: utf-8 -*-
"""Post for Me REST client — only the calls this system needs, read from the
official OpenAPI document (https://api.postforme.dev/swagger-json, v1):

  GET  /v1/social-accounts                     list connected accounts (read only)
  POST /v1/media/create-upload-url             -> {upload_url, media_url}; PUT the file to upload_url
  POST /v1/social-posts                        create + publish now (scheduled_at omitted)
  GET  /v1/social-post-results?post_id=        per-account outcome: success, error, platform_data{id,url}
  GET  /v1/social-account-feeds/{id}?social_post_id=   published post with its platform_url

It never deletes, disconnects or edits anything, and only posts to the account ids
it is given (the ones listed in config.txt).

Found in Post for Me's open-source server (trigger/posting/platforms/tiktok-post-client.ts):
for TikTok, platform_data.url is the PROFILE url (https://www.tiktok.com/@user), not the
video. The video id is platform_data.id (TikTok's public post id once the video is
public; a "v_pub_..." publish id before that). links.csv needs the video link, so we
build https://www.tiktok.com/@user/video/<id>, or ask the account feed for it."""
import os
import re
import time

from common import check_stop, log

BASE = os.environ.get("POSTFORME_BASE_URL", "https://api.postforme.dev").rstrip("/")


class ApiError(Exception):
    def __init__(self, status, body, what):
        self.status, self.body = status, body
        hint = {401: "مفتاح API غير صالح (راجع .env)", 403: "المفتاح لا يملك صلاحية", 404: "غير موجود",
                429: "تجاوز حد الطلبات", 400: "طلب مرفوض"}.get(status, "")
        super().__init__(f"Post for Me {what}: HTTP {status} {hint} {str(body)[:300]}")


class PostForMe:
    def __init__(self, key, base=BASE):
        import requests
        self.r = requests.Session()
        self.r.headers.update({"Authorization": f"Bearer {key}", "Accept": "application/json",
                               "User-Agent": "clipping-gta6/1.0"})
        self.base = base

    def _req(self, method, path, what, params=None, json=None, tries=4):
        import requests
        delay = 5
        for attempt in range(1, tries + 1):
            check_stop()
            try:
                resp = self.r.request(method, self.base + path, params=params, json=json, timeout=60)
            except requests.RequestException as e:
                if attempt == tries:
                    raise ApiError(0, str(e), what)
                log.warning(f"  Post for Me: خطأ شبكة ({e.__class__.__name__}), إعادة المحاولة بعد {delay}ث")
                time.sleep(delay)
                delay *= 2
                continue
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < tries:
                wait = int(resp.headers.get("Retry-After", delay) or delay)
                log.warning(f"  Post for Me: HTTP {resp.status_code}, إعادة المحاولة بعد {wait}ث")
                time.sleep(min(wait, 120))
                delay *= 2
                continue
            if resp.status_code >= 400:
                try:
                    body = resp.json()
                except ValueError:
                    body = resp.text
                raise ApiError(resp.status_code, body, what)
            return resp.json() if resp.content else {}
        raise ApiError(0, "?", what)

    # ---- accounts
    def accounts(self, platforms=("tiktok", "youtube")):
        out, offset = [], 0
        while True:
            d = self._req("GET", "/v1/social-accounts", "قائمة الحسابات",
                          params={"platform": list(platforms), "limit": 100, "offset": offset})
            data = d.get("data", [])
            out += data
            total = (d.get("meta") or {}).get("total", len(out))
            offset += len(data)
            if not data or offset >= total:
                return out

    # ---- media
    def upload(self, path):
        d = self._req("POST", "/v1/media/create-upload-url", "رابط الرفع")
        upload_url, media_url = d["upload_url"], d["media_url"]
        size = path.stat().st_size
        for attempt in range(1, 4):
            check_stop()
            with open(path, "rb") as f:
                resp = self.r.put(upload_url, data=f, timeout=900,
                                  headers={"Content-Type": "video/mp4", "Content-Length": str(size),
                                           "Authorization": None})
            if resp.status_code < 300:
                return media_url
            log.warning(f"  رفع الفيديو: HTTP {resp.status_code} (محاولة {attempt}/3)")
            time.sleep(10 * attempt)
        raise ApiError(resp.status_code, resp.text[:300], "رفع الفيديو")

    # ---- posts
    def create_post(self, body):
        return self._req("POST", "/v1/social-posts", "إنشاء منشور", json=body, tries=2)

    def results(self, post_id):
        return self._req("GET", "/v1/social-post-results", "نتيجة المنشور",
                         params={"post_id": [post_id], "limit": 50}).get("data", [])

    def feed_post(self, account_id, post_id):
        try:
            d = self._req("GET", f"/v1/social-account-feeds/{account_id}", "feed الحساب",
                          params={"social_post_id": [post_id], "limit": 10}, tries=2)
        except ApiError as e:
            log.warning(f"  {e}")
            return None
        for p in d.get("data", []):
            if p.get("platform_url"):
                return p
        return None


def build_body(platform, account_id, media_url, texts, cfg, clip):
    """POST /v1/social-posts body for ONE account (CreateSocialPostDto)."""
    body = {
        "caption": texts["caption"] if platform == "tiktok" else texts["youtube_description"],
        "social_accounts": [account_id],
        "media": [{"url": media_url, "thumbnail_timestamp_ms": int(clip.get("thumb_ms", 3000))}],
        "external_id": clip["id"][:120],
    }
    if platform == "tiktok":
        body["platform_configurations"] = {"tiktok": {
            "privacy_status": cfg.get("TIKTOK_PRIVACY") or "public",
            "allow_comment": True, "allow_duet": True, "allow_stitch": True,
            "is_ai_generated": False, "disclose_your_brand": False, "disclose_branded_content": False,
        }}
    else:
        body["platform_configurations"] = {"youtube": {
            "title": texts["youtube_title"][:100],
            "description": texts["youtube_description"],
            "tags": texts["youtube_tags"],
            "category_id": "20",
            "privacy_status": cfg.get("YOUTUBE_PRIVACY") or "public",
            "made_for_kids": False,
            "contains_synthetic_media": False,
        }}
    return body


def video_link(platform, result, username=""):
    """Turn a post result into the link a campaign accepts, or None if not known yet."""
    pdata = result.get("platform_data") or {}
    url, pid = str(pdata.get("url") or ""), str(pdata.get("id") or "")
    if platform == "youtube":
        m = re.search(r"(?:v=|shorts/|youtu\.be/)([\w-]{6,})", url)
        vid = m.group(1) if m else (pid if re.fullmatch(r"[\w-]{6,}", pid or "") else "")
        return f"https://www.youtube.com/shorts/{vid}" if vid else None
    if "/video/" in url:
        return url.split("?")[0]
    m = re.search(r"tiktok\.com/@([\w.]+)", url)
    user = (m.group(1) if m else username or "").lstrip("@")
    if pid.isdigit() and len(pid) >= 15 and user:
        return f"https://www.tiktok.com/@{user}/video/{pid}"
    return None
