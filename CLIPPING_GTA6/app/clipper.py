# -*- coding: utf-8 -*-
"""CLIPPING_GTA6 — entry point.

  python app/clipper.py run         one full cycle (what the scheduled task runs)
  python app/clipper.py campaigns   GTA campaigns ranked + allowed streamers -> campaigns.txt
  python app/clipper.py accounts    accounts connected in Post for Me (to fill config.txt)
  python app/clipper.py status      queue, posts per account, next slots, pending links
  python app/clipper.py doctor      checks every piece of the installation
"""
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (FONTS, HEISTCLIPS, LINKS_CSV, MODELS, ROOT, STOP_FILE, InstanceLock, Stopped, api_key, load_config,
                    load_json, load_streamers, local_str, log, mask, now_utc, parse_iso, run, setup_logging,
                    start_stop_watcher)


def cmd_run():
    setup_logging()
    if STOP_FILE.exists():
        log.warning("STOP.txt موجود: النظام متوقف. احذف STOP.txt (أو شغّل resume.bat) لإعادة التشغيل.")
        return 3
    lock = InstanceLock()
    if not lock.acquire():
        log.warning("تشغيل آخر ما زال يعمل؛ هذا التشغيل لن يبدأ (لا تكرار).")
        return 0
    start_stop_watcher()
    t0 = time.time()
    try:
        housekeeping()
        import heist
        import producer
        import publisher
        cfg = load_config()
        streamers, sproblems = load_streamers()
        mode = "🧪 تجريبي DRY_RUN (لا نشر حقيقي)" if cfg.dry_run else "🔴 حقيقي (نشر فعلي)"
        log.info("=" * 70)
        log.info(f"بدء التشغيل — الوضع: {mode} — {len(streamers)} ستريمر، {len(cfg.accounts)} حساب")
        for p in cfg.problems + sproblems:
            log.error(p)
        if not heist.available():
            log.error("heistclips غير مثبت (tools/heistclips). شغّل setup.bat.")
            return 2
        heist.log_version()
        n1 = publisher.publish_phase(cfg)          # first: keeps the 2-hour rhythm steady
        made = producer.produce(cfg, streamers) if streamers else 0
        n2 = publisher.publish_phase(cfg)          # then: anything due that now has a clip
        tidy = publisher.cleanup(cfg)
        log.info(f"انتهى التشغيل في {int(time.time() - t0)}ث: {made} كليب جديد، {n1 + n2} منشور"
                 f"{' (تجريبي)' if cfg.dry_run else ''}، {tidy} تنظيف.")
        return 0
    except Stopped:
        log.warning("STOP.txt: توقف النظام.")
        return 3
    except Exception as e:
        log.exception(f"خطأ غير متوقع أوقف التشغيل: {e}")
        return 1
    finally:
        lock.release()


def housekeeping():
    """Leftovers of a run that was killed (STOP.txt, PC shutdown): half-cut clips and
    heistclips temp folders. Safe: we hold the single-instance lock."""
    import tempfile
    import publisher
    from common import WORK, load_json, save_json
    shutil.rmtree(WORK, ignore_errors=True)
    WORK.mkdir(parents=True, exist_ok=True)
    for d in Path(tempfile.gettempdir()).glob("heistclips-*"):
        try:
            if time.time() - d.stat().st_mtime > 3600:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass
    hist = load_json(publisher.HISTORY, {"clips": {}, "facecam": {}})
    stale = [c for c in hist["clips"].values() if c.get("status") == "cutting"]
    for c in stale:
        c.update(status="failed", error="interrupted")
    if stale:
        save_json(publisher.HISTORY, hist)
        log.info(f"تنظيف: {len(stale)} قص غير مكتمل من تشغيل سابق (سيُعاد مرة واحدة).")


def cmd_campaigns():
    setup_logging(verbose_console=False)
    import campaigns
    campaigns.report()
    return 0


def cmd_accounts():
    setup_logging(verbose_console=False)
    import pfm
    key = api_key()
    if not key:
        print("❌ لا يوجد POSTFORME_API_KEY في ملف .env")
        return 2
    try:
        accs = pfm.PostForMe(key).accounts()
    except pfm.ApiError as e:
        print(f"❌ {e}")
        return 1
    print(f"الحسابات المتصلة في Post for Me ({len(accs)}) — انسخ اسم المستخدم إلى config.txt:\n")
    for a in accs:
        print(f"  {a.get('platform'):8} | {a.get('username') or '?':30} | {a.get('id')} | {a.get('status')}")
    print("\nمثال سطر في config.txt:\n  tiktok | <username> | lacy, clix")
    return 0


def cmd_status():
    setup_logging(verbose_console=False)
    import publisher
    cfg = load_config()
    hist = load_json(publisher.HISTORY, {"clips": {}})
    clips = hist["clips"].values()
    ready = [c for c in clips if c.get("status") == "ready" and (ROOT / c["file"]).exists()]
    print(f"الوضع: {'🧪 تجريبي (DRY_RUN=true)' if cfg.dry_run else '🔴 حقيقي'} | STOP.txt: {'موجود ⛔' if STOP_FILE.exists() else 'لا'}")
    print(f"كليبات جاهزة للنشر: {len(ready)} | مقصوصة إجمالاً: {sum(1 for c in clips if c.get('status') in ('ready', 'done'))}"
          f" | فاشلة: {sum(1 for c in clips if c.get('status') == 'failed')}")
    for dry in (False, True):
        led = load_json(publisher.ledger_path(dry), {"posts": []})
        if not led["posts"] and dry != cfg.dry_run:
            continue
        print(f"\n{'سجل التجريبي' if dry else 'سجل النشر الحقيقي'}:")
        for a in cfg.accounts:
            k = publisher.acc_key(a)
            day = [p for p in led["posts"] if p["account"] == k
                   and (now_utc() - parse_iso(p["created_at"])).total_seconds() < 86400]
            wait, reason, due = publisher.next_slot(k, led, cfg)
            print(f"  {a['platform']:7} @{a['user']:24} آخر 24س: {len(day)}/{cfg.int('MAX_POSTS_PER_DAY')} | "
                  f"القادم: {'الآن' if wait == 0 else local_str(due)}")
        pend = [p for p in led["posts"] if p["status"] == "pending"]
        if pend:
            print(f"  روابط معلّقة: {len(pend)}")
    if LINKS_CSV.exists():
        rows = LINKS_CSV.read_text(encoding="utf-8-sig").strip().splitlines()[1:]
        print(f"\nlinks.csv: {len(rows)} رابط. آخرها:")
        for r in rows[-5:]:
            print("  " + r)
    return 0


def cmd_doctor():
    setup_logging(verbose_console=False)
    ok_all = True

    def check(name, ok, detail=""):
        nonlocal ok_all
        ok_all &= bool(ok)
        print(f"  {'✅' if ok else '❌'} {name}{(' — ' + detail) if detail else ''}")

    print("فحص التثبيت:")
    check("Python", sys.version_info >= (3, 9), sys.version.split()[0])
    node = shutil.which("node")
    nv = run([node, "--version"], timeout=20)[1].strip() if node else ""
    check("Node 20+", node and int((nv.lstrip("v").split(".") or ["0"])[0] or 0) >= 20, nv or "غير موجود")
    ff = shutil.which("ffmpeg")
    check("ffmpeg", ff, ff or "غير موجود")
    if ff:
        import subs
        flt = subs.text_filter()
        check("ffmpeg + libass (ترجمة مكتوبة)", flt, flt or "هذه النسخة بدون libass")
        enc = run(["ffmpeg", "-hide_banner", "-encoders"], timeout=30)[1]
        check("ffmpeg + libx264", "libx264" in enc)
    check("heistclips", HEISTCLIPS.exists(), (ROOT / "tools/heistclips/VERSION.txt").read_text().strip()
          if (ROOT / "tools/heistclips/VERSION.txt").exists() else "غير مثبت")
    if HEISTCLIPS.exists():
        check("إصلاح fMP4 في heistclips", "CLIPPING_GTA6 patch" in HEISTCLIPS.read_text(encoding="utf-8"))
    for mod in ("faster_whisper", "cv2", "requests", "numpy"):
        try:
            __import__(mod)
            check(f"مكتبة {mod}", True)
        except Exception as e:
            check(f"مكتبة {mod}", False, str(e))
    cfg = load_config()
    check("نموذج faster-whisper محمّل", any(MODELS.glob(f"*{cfg.get('WHISPER_MODEL')}*")),
          f"{cfg.get('WHISPER_MODEL')} في models/ (يُحمّل تلقائياً عند أول قص إن لم يوجد)")
    check("خطوط الترجمة", any(FONTS.glob("*.tt*")), ", ".join(p.name for p in FONTS.glob("*.tt*")) or "fonts/ فارغ")
    check("config.txt", not cfg.problems, "; ".join(cfg.problems) or
          f"DRY_RUN={cfg.get('DRY_RUN')}, {len(cfg.accounts)} حساب")
    streamers, sp = load_streamers()
    check("streamers.txt", streamers and not sp, "; ".join(sp) or ", ".join(s["name"] for s in streamers))
    key = api_key()
    check(".env: POSTFORME_API_KEY", key or cfg.dry_run, mask(key) if key else "غير موجود (مطلوب للنشر الحقيقي)")
    if key:
        import pfm
        import publisher
        try:
            accs = publisher.resolve_accounts(cfg, pfm.PostForMe(key))
            check("حسابات config.txt موجودة في Post for Me", len(accs) == len(cfg.accounts),
                  ", ".join(f"{a['platform']}:@{a['username']}" for a in accs) or "لا شيء")
        except Exception as e:
            check("اتصال Post for Me", False, str(e))
    free = shutil.disk_usage(ROOT).free / 1e9
    check("مساحة القرص", free > 5, f"{free:.1f} GB متاحة")
    check("STOP.txt غير موجود", not STOP_FILE.exists(), "موجود: النظام متوقف" if STOP_FILE.exists() else "")
    print("\nالنتيجة:", "✅ كل شيء جاهز" if ok_all else "❌ يوجد ما يجب إصلاحه (انظر أعلاه)")
    return 0 if ok_all else 1


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "run"
    fn = {"run": cmd_run, "campaigns": cmd_campaigns, "accounts": cmd_accounts,
          "status": cmd_status, "doctor": cmd_doctor}.get(cmd)
    if not fn:
        print(__doc__)
        return 2
    return fn()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
