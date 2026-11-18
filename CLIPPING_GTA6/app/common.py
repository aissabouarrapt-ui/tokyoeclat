# -*- coding: utf-8 -*-
"""Shared plumbing: paths, log.txt, config.txt / streamers.txt / .env parsing,
STOP.txt watcher, single-instance lock, JSON state, child processes.

Everything lives inside the CLIPPING_GTA6 folder: nothing here reads or writes
outside of it (except the system temp folder used by heistclips while cutting)."""
import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app"
DATA = ROOT / "data"
CLIPS = ROOT / "clips"
WORK = CLIPS / "work"
READY = CLIPS / "ready"
POSTED = CLIPS / "posted"
MODELS = ROOT / "models"
FONTS = ROOT / "fonts"
TOOLS = ROOT / "tools"
HEISTCLIPS = TOOLS / "heistclips" / "cli" / "heistclips.mjs"

STOP_FILE = ROOT / "STOP.txt"
LOG_FILE = ROOT / "log.txt"
LINKS_CSV = ROOT / "links.csv"
DRYRUN_CSV = ROOT / "dry_run_posts.csv"
CONFIG_TXT = ROOT / "config.txt"
STREAMERS_TXT = ROOT / "streamers.txt"
ENV_FILE = ROOT / ".env"

IS_WINDOWS = os.name == "nt"

for d in (DATA, CLIPS, WORK, READY, POSTED):
    d.mkdir(parents=True, exist_ok=True)

# Console output in UTF-8 (Arabic text in a Windows console would otherwise crash).
for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------- log
log = logging.getLogger("clipping")


def setup_logging(verbose_console=True):
    if log.handlers:
        return log
    try:
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 5 * 1024 * 1024:
            old = ROOT / "log_old.txt"
            if old.exists():
                old.unlink()
            LOG_FILE.rename(old)
    except OSError:
        pass
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if verbose_console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(fmt)
        log.addHandler(ch)
    return log


def flush_logs():
    for h in log.handlers:
        try:
            h.flush()
        except Exception:
            pass


# ---------------------------------------------------------------------- STOP.txt
class Stopped(Exception):
    pass


_current_proc = {"p": None}


def stop_requested():
    return STOP_FILE.exists()


def check_stop():
    if stop_requested():
        raise Stopped()


def _kill_tree(p):
    try:
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.killpg(p.pid, 9)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass


def start_stop_watcher(interval=1.0):
    """Background thread: the moment STOP.txt appears, kill the running child
    (node / ffmpeg) and exit the whole program."""
    def loop():
        while True:
            if stop_requested():
                log.warning("STOP.txt موجود: إيقاف فوري للنظام (تم قطع العملية الجارية).")
                p = _current_proc.get("p")
                if p is not None and p.poll() is None:
                    _kill_tree(p)
                flush_logs()
                os._exit(3)
            time.sleep(interval)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def sleep_checking_stop(seconds):
    end = time.time() + seconds
    while time.time() < end:
        check_stop()
        time.sleep(min(2.0, max(0.0, end - time.time())))


# ------------------------------------------------------------------ child procs
def run(args, cwd=None, timeout=None):
    """Run a child process, tracked so STOP.txt can kill it. Returns (code, out, err)."""
    check_stop()
    kw = {}
    if IS_WINDOWS:
        kw["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        kw["start_new_session"] = True
    p = subprocess.Popen([str(a) for a in args], cwd=str(cwd) if cwd else None,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         stdin=subprocess.DEVNULL, **kw)
    _current_proc["p"] = p
    try:
        out, err = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(p)
        p.communicate()
        raise RuntimeError(f"انتهت المهلة ({timeout} ث) أثناء: {Path(str(args[0])).name} {args[1] if len(args) > 1 else ''}")
    finally:
        _current_proc["p"] = None
    check_stop()
    return p.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")


# -------------------------------------------------------------- single instance
class InstanceLock:
    """OS-level file lock: released automatically if the process dies."""

    def __init__(self, path=DATA / "run.lock"):
        self.path = path
        self.fh = None

    def acquire(self):
        self.fh = open(self.path, "a+")
        try:
            if IS_WINDOWS:
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False

    def release(self):
        if not self.fh:
            return
        try:
            if IS_WINDOWS:
                import msvcrt
                self.fh.seek(0)
                msvcrt.locking(self.fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        self.fh.close()
        self.fh = None


# ------------------------------------------------------------------- JSON state
def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except (json.JSONDecodeError, OSError) as e:
        bak = Path(str(path) + ".corrupt")
        try:
            Path(path).replace(bak)
        except OSError:
            pass
        log.error(f"ملف الحالة {Path(path).name} تالف ({e}); تم حفظه باسم {bak.name} والبدء من جديد.")
        return default


def save_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def now_utc():
    return datetime.now(timezone.utc)


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def local_str(dt=None):
    return (dt or now_utc()).astimezone().strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------ config.txt
DEFAULTS = {
    "DRY_RUN": "true",
    "CLIPS_PER_STREAMER": "5",
    "MAX_POSTS_PER_DAY": "6",
    "MIN_HOURS_BETWEEN_POSTS": "2",
    "MAX_WAIT_MINUTES": "25",
    "POST_HOURS": "",
    "MAX_READY_CLIPS": "30",
    "MAX_CLIP_AGE_HOURS": "48",
    "KEEP_POSTED_DAYS": "2",
    "ONLY_GTA_STREAMS": "true",
    "SKIP_MUSIC_WARNING": "true",
    "LAYOUT": "auto",
    "WHISPER_MODEL": "small",
    "SUBTITLE_FONT": "Arial Black",
    "CENSOR_SUBTITLES": "true",
    "EXTRA_HASHTAGS": "#GTA6 #GTAVI",
    "TIKTOK_PRIVACY": "public",
    "YOUTUBE_PRIVACY": "public",
    "LINK_WAIT_MINUTES": "12",
}

PLATFORMS = {"tiktok", "youtube"}


class Config:
    def __init__(self, settings, accounts, problems):
        self.s = settings
        self.accounts = accounts
        self.problems = problems

    def get(self, k):
        return self.s.get(k, DEFAULTS.get(k, ""))

    def bool(self, k):
        return str(self.get(k)).strip().lower() in ("1", "true", "yes", "oui", "on", "نعم")

    def int(self, k):
        try:
            return int(float(self.get(k)))
        except ValueError:
            return int(float(DEFAULTS[k]))

    def float(self, k):
        try:
            return float(self.get(k))
        except ValueError:
            return float(DEFAULTS[k])

    @property
    def dry_run(self):
        return self.bool("DRY_RUN")

    def post_hours(self):
        """'14-23' -> set of local hours allowed; '' -> all hours. '22-3' wraps midnight."""
        v = self.get("POST_HOURS").strip()
        if not v:
            return set(range(24))
        hours = set()
        for part in v.split(","):
            m = re.match(r"^\s*(\d{1,2})\s*-\s*(\d{1,2})\s*$", part)
            if m:
                a, b = int(m.group(1)) % 24, int(m.group(2)) % 24
                h = a
                while True:
                    hours.add(h)
                    if h == b:
                        break
                    h = (h + 1) % 24
            elif part.strip().isdigit():
                hours.add(int(part) % 24)
        return hours or set(range(24))


def _split_list(v):
    return [x.strip().lower().lstrip("@") for x in re.split(r"[,\s]+", v) if x.strip()]


def load_config(path=CONFIG_TXT):
    settings, accounts, problems = {}, [], []
    if not path.exists():
        problems.append("config.txt غير موجود")
        return Config(settings, accounts, problems)
    for n, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "|" in line:
            parts = [p.strip() for p in line.split("|")]
            platform = parts[0].lower()
            if platform not in PLATFORMS:
                problems.append(f"config.txt سطر {n}: المنصة '{parts[0]}' غير مدعومة (tiktok أو youtube فقط)")
                continue
            if len(parts) < 2 or not parts[1]:
                problems.append(f"config.txt سطر {n}: اسم الحساب ناقص")
                continue
            streamers = _split_list(parts[2]) if len(parts) > 2 and parts[2] else ["*"]
            accounts.append({"platform": platform, "user": parts[1].strip().lstrip("@"),
                             "streamers": streamers, "line": n})
        elif "=" in line:
            k, v = line.split("=", 1)
            k = k.strip().upper()
            if k not in DEFAULTS:
                problems.append(f"config.txt سطر {n}: إعداد غير معروف '{k}'")
            settings[k] = v.strip()
        else:
            problems.append(f"config.txt سطر {n}: سطر غير مفهوم: {raw.strip()[:60]}")
    return Config(settings, accounts, problems)


# --------------------------------------------------------------- streamers.txt
def load_streamers(path=STREAMERS_TXT):
    """Each line: login | name in captions | required #hashtags @mentions | facecam
    Only the first column is required. Kick channels: kick.com/name"""
    out, problems = [], []
    if not path.exists():
        return out, ["streamers.txt غير موجود (شغّل campaigns.bat لإنشائه)"]
    for n, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if raw.strip().startswith("#") or not raw.strip():
            continue
        parts = [p.strip() for p in raw.split("|")]
        src = parts[0].strip()
        src = re.sub(r"^https?://(www\.)?", "", src).rstrip("/")
        src = re.sub(r"^twitch\.tv/", "", src, flags=re.I)
        if not re.match(r"^(kick\.com/)?[A-Za-z0-9_-]{2,40}$", src, flags=re.I):
            problems.append(f"streamers.txt سطر {n}: اسم غير صالح '{parts[0]}'")
            continue
        platform = "kick" if src.lower().startswith("kick.com/") else "twitch"
        login = src.split("/")[-1].lower()
        name = parts[1] if len(parts) > 1 and parts[1] else login
        tags = parts[2] if len(parts) > 2 else ""
        cam = (parts[3] if len(parts) > 3 and parts[3] else "auto").lower()
        if cam not in ("auto", "tl", "tr", "bl", "br", "none"):
            problems.append(f"streamers.txt سطر {n}: facecam '{cam}' غير صالح (auto/tl/tr/bl/br/none) -> auto")
            cam = "auto"
        lang = (parts[4] if len(parts) > 4 and parts[4] else "auto").lower()
        out.append({
            "source": src if platform == "kick" else login,
            "login": login, "platform": platform, "name": name,
            "hashtags": re.findall(r"#\w+", tags), "mentions": re.findall(r"@[\w.]+", tags),
            "cam": cam, "lang": None if lang in ("auto", "") else lang,
        })
    return out, problems


# ------------------------------------------------------------------------- .env
def load_env(path=ENV_FILE):
    env = {}
    if path.exists():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def api_key():
    key = load_env().get("POSTFORME_API_KEY", "") or os.environ.get("POSTFORME_API_KEY", "")
    if key.lower().startswith("ضع") or key.lower() in ("", "your_key_here", "changeme"):
        return ""
    return key


def mask(s):
    return (s[:4] + "…" + s[-3:]) if s and len(s) > 10 else "***"
