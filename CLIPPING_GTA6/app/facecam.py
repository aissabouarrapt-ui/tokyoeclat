# -*- coding: utf-8 -*-
"""Find which corner of the stream holds the streamer's webcam, for each clip.

heistclips' 9:16 "split" layout puts a corner of the stream (the facecam) on top
and the gameplay below, but it must be told the corner and defaults to top-right.
Measured on Lacy's GTA VI marathon: his cam is top-LEFT (default gave a top half
full of chat), Kai Cenat's sits mid-left (no corner), Clix was on an IRL camera.
Campaigns reject clips where the streamer is not the focus, so for every clip we
cut a 3-second probe in the "fit" layout (whole 16:9 frame), look for a face in
each corner region with OpenCV, and pick the corner where it keeps appearing.
No webcam in a corner -> "fit" layout (whole picture on a blurred fill).

Tuning (15 frames of Lacy, see git history): per-corner search found the cam in
14/15 frames with no false corner; whole-frame search and 2x upscaling did worse."""
import shutil

from common import WORK, log, run

# "fit" layout puts the 1080x608 16:9 frame at y=656 of the 1080x1920 canvas.
FRAME_W, FRAME_H, FRAME_Y = 1080, 608, 656
REGION_W, REGION_H = 0.40, 0.45
_casc = []


def _cascades():
    if not _casc:
        import cv2
        for name in ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml"):
            _casc.append(cv2.CascadeClassifier(cv2.data.haarcascades + name))
    return _casc


def corners_with_face(png):
    import cv2
    img = cv2.imread(str(png))
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    rw, rh = int(w * REGION_W), int(h * REGION_H)
    regions = {"tl": (0, 0), "tr": (w - rw, 0), "bl": (0, h - rh), "br": (w - rw, h - rh)}
    hits = set()
    for name, (x, y) in regions.items():
        roi = gray[y:y + rh, x:x + rw]
        for cc in _cascades():
            if len(cc.detectMultiScale(roi, scaleFactor=1.08, minNeighbors=6, minSize=(26, 26))):
                hits.add(name)
                break
    return hits


def detect(cut_fn, source, t, key, prior=None):
    """Probe 3 s at VOD time t. Returns 'tl'|'tr'|'bl'|'br' or None (-> 'fit').
    prior: the corner this streamer used before (one hit there is enough)."""
    tmp = WORK / f"probe_{key}"
    tmp.mkdir(parents=True, exist_ok=True)
    votes, frames = {}, 0
    try:
        probe = tmp / "p.mp4"
        try:
            cut_fn(source, t, t + 3.0, probe, layout="fit")
        except Exception as e:
            log.warning(f"  كشف الكاميرا: تعذر قص عينة ({e})")
            return prior
        for j, ss in enumerate((0.2, 1.4, 2.6)):
            png = tmp / f"f{j}.png"
            code, _, _ = run(["ffmpeg", "-v", "error", "-y", "-ss", str(ss), "-i", probe, "-frames:v", "1",
                              "-vf", f"crop={FRAME_W}:{FRAME_H}:0:{FRAME_Y}", png], timeout=60)
            if code != 0 or not png.exists():
                continue
            hits = corners_with_face(png)
            if hits is None:
                continue
            frames += 1
            for c in hits:
                votes[c] = votes.get(c, 0) + 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not frames:
        return prior
    ranked = sorted(votes.items(), key=lambda kv: -kv[1])
    if ranked and ranked[0][1] >= 2 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
        return ranked[0][0]
    if prior and votes.get(prior, 0) >= 1 and all(v <= votes[prior] for v in votes.values()):
        return prior
    return None
