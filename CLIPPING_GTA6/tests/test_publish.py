# -*- coding: utf-8 -*-
"""Offline test of the REAL publishing path (DRY_RUN=false) against a local mock of
the Post for Me API (same routes and JSON shapes as the official OpenAPI v1 spec,
including the TikTok quirk: platform_data.url = profile URL, video URL only via the
account feed). Runs in a throw-away copy of the project: your files are not touched.

    python tests/test_publish.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PROJECT = Path(__file__).resolve().parent.parent
KEY = "pfm_test_key_123456"
STATE = {"posts": {}, "uploads": {}, "polls": {}, "auth_on_upload": [], "throttled": False}
ACCOUNTS = [
    {"id": "spc_tt1", "platform": "tiktok", "username": "lacy.clips.test", "status": "connected"},
    {"id": "spc_yt1", "platform": "youtube", "username": "LacyClipsTest", "status": "connected"},
    {"id": "spc_other", "platform": "tiktok", "username": "republication_account", "status": "connected"},
]


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth(self):
        if self.headers.get("Authorization") != f"Bearer {KEY}":
            self._json(401, {"message": "Unauthorized"})
            return False
        return True

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if not self._auth():
            return
        if u.path == "/v1/social-accounts":
            plats = q.get("platform") or [a["platform"] for a in ACCOUNTS]
            data = [a for a in ACCOUNTS if a["platform"] in plats]
            return self._json(200, {"data": data, "meta": {"total": len(data), "offset": 0, "limit": 100}})
        if u.path == "/v1/social-post-results":
            pid = q["post_id"][0]
            post = STATE["posts"][pid]
            STATE["polls"][pid] = STATE["polls"].get(pid, 0) + 1
            if STATE["polls"][pid] == 1:          # first poll: still processing
                return self._json(200, {"data": [], "meta": {"total": 0}})
            acc = post["social_accounts"][0]
            if acc == "spc_tt1":
                pdata = {"id": "v_pub_url~v2.7412345", "url": "https://www.tiktok.com/@lacy.clips.test"}
            else:
                pdata = {"id": "dQw4w9WgXcQ", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
            return self._json(200, {"data": [{"id": "spr_1", "social_account_id": acc, "post_id": pid,
                                              "success": True, "error": None, "details": {},
                                              "platform_data": pdata, "media": []}], "meta": {"total": 1}})
        if u.path.startswith("/v1/social-account-feeds/"):
            pid = q["social_post_id"][0]
            return self._json(200, {"data": [{"platform": "tiktok", "social_post_id": pid,
                                              "platform_post_id": "7412345678901234567",
                                              "platform_url": "https://www.tiktok.com/@lacy.clips.test/video/7412345678901234567"}]})
        self._json(404, {"message": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        if not self._auth():
            return
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        if u.path == "/v1/media/create-upload-url":
            if not STATE["throttled"]:
                STATE["throttled"] = True       # one 429 to exercise the retry path
                return self._json(429, {"message": "Too Many Requests"})
            mid = f"m{len(STATE['uploads']) + 1}"
            port = self.server.server_address[1]
            return self._json(200, {"upload_url": f"http://127.0.0.1:{port}/upload/{mid}",
                                    "media_url": f"http://127.0.0.1:{port}/media/{mid}"})
        if u.path == "/v1/social-posts":
            errs = []
            if not body.get("caption"):
                errs.append("caption is required")
            known = {a["id"] for a in ACCOUNTS}
            if not body.get("social_accounts") or not set(body["social_accounts"]) <= known:
                errs.append("invalid social accounts, not owned by user")
            for m in body.get("media") or []:
                if m["url"].rsplit("/", 1)[-1] not in STATE["uploads"]:
                    errs.append("media not uploaded")
            if errs:
                return self._json(400, {"error": errs})
            pid = f"sp_{len(STATE['posts']) + 1}"
            STATE["posts"][pid] = body
            return self._json(200, {**body, "id": pid, "status": "processing"})
        self._json(404, {"message": "not found"})

    def do_PUT(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n)
        STATE["auth_on_upload"].append(self.headers.get("Authorization"))
        STATE["uploads"][u.path.rsplit("/", 1)[-1]] = len(data)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()


def check(cond, msg):
    print(("  ✅ " if cond else "  ❌ ") + msg)
    if not cond:
        raise SystemExit(1)


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.environ["POSTFORME_BASE_URL"] = f"http://127.0.0.1:{srv.server_address[1]}"

    box = Path(tempfile.mkdtemp(prefix="clipping_test_")) / "CLIPPING_GTA6"
    shutil.copytree(PROJECT / "app", box / "app", ignore=shutil.ignore_patterns("__pycache__"))
    (box / "clips" / "ready").mkdir(parents=True)
    (box / "data").mkdir()
    (box / ".env").write_text(f"POSTFORME_API_KEY={KEY}\n", encoding="utf-8")
    (box / "config.txt").write_text(
        "DRY_RUN=false\nLINK_WAIT_MINUTES=2\nMAX_WAIT_MINUTES=0\n"
        "tiktok | lacy.clips.test | lacy\nyoutube | LacyClipsTest | lacy\n", encoding="utf-8")
    sys.path.insert(0, str(box / "app"))
    import common
    common.setup_logging(verbose_console=False)
    import publisher
    common_now = publisher.now_utc()

    clips = {}
    for i in range(3):
        cid = f"lacy-123-{1000 + i}"
        f = box / "clips" / "ready" / f"{cid}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=s=1080x1920:d=2",
                        "-f", "lavfi", "-i", "sine=d=2", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        str(f)], check=True)
        clips[cid] = {"id": cid, "login": "lacy", "name": "LACY", "status": "ready", "score": 9 - i,
                      "file": f"clips/ready/{cid}.mp4", "created_at": common.iso(common_now), "thumb_ms": 1000,
                      "texts": {"title": f"LACY clip {i} 😂", "caption": f"LACY clip {i} 😂\n\n#LACY #GTA6",
                                "youtube_title": f"LACY clip {i} #shorts", "youtube_description": "desc #LACY",
                                "youtube_tags": ["LACY", "GTA6"]}}
    common.save_json(publisher.HISTORY, {"clips": clips, "facecam": {}})
    cfg = common.load_config(box / "config.txt")

    print("Run 1 (DRY_RUN=false, mock Post for Me):")
    n = publisher.publish_phase(cfg)
    led = common.load_json(publisher.ledger_path(False), {"posts": []})
    check(n == 2, f"2 posts created (TikTok + YouTube): {n}")
    used = {p["social_accounts"][0] for p in STATE["posts"].values()}
    check(used == {"spc_tt1", "spc_yt1"}, f"only config.txt accounts used, never spc_other: {sorted(used)}")
    check(all(a is None for a in STATE["auth_on_upload"]), "API key NOT sent to the storage upload URL")
    check(all(v > 1000 for v in STATE["uploads"].values()), f"video bytes uploaded: {list(STATE['uploads'].values())}")
    bodies = list(STATE["posts"].values())
    tt = [b for b in bodies if b["social_accounts"] == ["spc_tt1"]][0]
    yt = [b for b in bodies if b["social_accounts"] == ["spc_yt1"]][0]
    check(tt["platform_configurations"]["tiktok"]["privacy_status"] == "public" and "#LACY" in tt["caption"],
          "TikTok body: public + required hashtag in caption")
    check(yt["platform_configurations"]["youtube"]["title"].endswith("#shorts")
          and yt["platform_configurations"]["youtube"]["made_for_kids"] is False, "YouTube body: title, made_for_kids=false")
    check(tt["media"][0]["url"] != yt["media"][0]["url"], "one fresh upload per post (Post for Me deletes media once published)")
    check(len({p["clip"] for p in led["posts"]}) == 1, "same (best) clip went to TikTok AND YouTube")
    rows = (box / "links.csv").read_text(encoding="utf-8-sig").strip().splitlines()
    check(rows[0] == "date,streamer,platform,link", f"links.csv header: {rows[0]}")
    check(any("tiktok.com/@lacy.clips.test/video/7412345678901234567" in r for r in rows),
          "TikTok VIDEO link (not the profile link) resolved via the account feed")
    check(any("youtube.com/shorts/dQw4w9WgXcQ" in r for r in rows), "YouTube Shorts link")
    check(all(p["status"] == "published" for p in led["posts"]), "ledger: both published")

    print("Run 2 (immediately after): 2-hour gap must block")
    n = publisher.publish_phase(cfg)
    check(n == 0, f"no post before 2 hours: {n}")

    print("Run 3 (pretend 2h05 passed):")
    for p in led["posts"]:
        p["created_at"] = common.iso(common.parse_iso(p["created_at"]) - publisher.timedelta(hours=2, minutes=5))
    common.save_json(publisher.ledger_path(False), led)
    n = publisher.publish_phase(cfg)
    led = common.load_json(publisher.ledger_path(False), {"posts": []})
    check(n == 2, f"posts again after the gap: {n}")
    tt_clips = [p["clip"] for p in led["posts"] if p["platform"] == "tiktok"]
    check(len(tt_clips) == len(set(tt_clips)), f"never the same clip twice on TikTok: {tt_clips}")

    print("Run 4 (6 posts already in 24h):")
    fake = [{"account": "tiktok:lacy.clips.test", "account_id": "spc_tt1", "platform": "tiktok", "clip": f"x{i}",
             "streamer": "lacy", "status": "published",
             "created_at": common.iso(common.now_utc() - publisher.timedelta(hours=3 + i))} for i in range(6)]
    led["posts"] = fake
    common.save_json(publisher.ledger_path(False), led)
    wait, reason, due = publisher.next_slot("tiktok:lacy.clips.test", led, cfg)
    check(wait > 3600 * 9, f"daily cap 6/24h blocks the account ({reason}, next in {wait / 3600:.1f}h)")

    print("Run 5 (STOP.txt):")
    led["posts"] = []
    common.save_json(publisher.ledger_path(False), led)
    (box / "STOP.txt").write_text("stop")
    try:
        publisher.publish_phase(cfg)
        stopped = False
    except common.Stopped:
        stopped = True
    check(stopped, "STOP.txt stops publishing before any post")
    (box / "STOP.txt").unlink()

    print("Run 6 (wrong API key):")
    (box / ".env").write_text("POSTFORME_API_KEY=wrong_key_000000\n")
    n = publisher.publish_phase(cfg)
    check(n == 0, "invalid key -> nothing posted, clear error in log")
    log = (box / "log.txt").read_text(encoding="utf-8")
    check("401" in log and "مفتاح API غير صالح" in log, "log.txt explains the 401")
    srv.shutdown()
    shutil.rmtree(box.parent, ignore_errors=True)
    print("\nALL PUBLISH TESTS PASSED")


if __name__ == "__main__":
    main()
