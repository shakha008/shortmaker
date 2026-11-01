import math
import os
import shutil
import subprocess
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import RequestEntityTooLarge
from yt_dlp.utils import DownloadError, download_range_func

BASE = Path(__file__).resolve().parent
WORK = BASE / "work"
WORK.mkdir(exist_ok=True)

MAX_SECONDS = 60                                        # Shorts uchun maksimal davomiylik
JOB_TTL = 60 * 60                                       # tayyor fayllar 1 soat saqlanadi
MAX_PARALLEL = int(os.environ.get("MAX_PARALLEL", 1))   # bir vaqtda ishlaydigan vazifalar
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", 100))
OUT_W = int(os.environ.get("OUT_W", 720))               # natija o'lchami (9:16)
OUT_H = int(os.environ.get("OUT_H", 1280))
COOKIES = os.environ.get("YTDLP_COOKIES")               # ixtiyoriy: cookies.txt yo'li

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".3gp"}
ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be", "www.youtu.be",
}

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = (MAX_UPLOAD_MB + 1) * 1024 * 1024
jobs: dict[str, dict] = {}
jobs_lock = threading.Lock()
slots = threading.Semaphore(MAX_PARALLEL)


# ---------- yordamchi funksiyalar ----------

def valid_youtube_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme in ("http", "https") and (p.hostname or "").lower() in ALLOWED_HOSTS


def base_opts() -> dict:
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if COOKIES and Path(COOKIES).is_file():
        opts["cookiefile"] = COOKIES
    return opts


def update(job_id: str, **kw) -> None:
    with jobs_lock:
        if job_id in jobs:
            jobs[job_id].update(kw)


def build_filter(mode: str) -> str:
    w, h = OUT_W, OUT_H
    if mode == "blur":
        # Orqa fon: kattalashtirilgan va xira, old tomon: asl video markazda
        return (
            "split[a][b];"
            f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
            f"scale={w // 8}:{h // 8},boxblur=3:2,scale={w}:{h}[bg];"
            f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    # "crop": markazdan kesib butun kadrni to'ldirish
    return f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"


def transcode(job_id: str, src: Path, out: Path, ss: float, dur: float,
              mode: str, p0: int = 30, p1: int = 95) -> None:
    """src dan [ss, ss+dur) qismini 9:16 MP4 ga o'tkazadi va foizni yangilab turadi."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-nostats", "-progress", "pipe:1"]
    if ss > 0:
        cmd += ["-ss", f"{ss:.3f}"]
    cmd += [
        "-i", str(src), "-t", f"{dur:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?",
        "-vf", build_filter(mode),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(out),
    ]
    errfile = out.with_suffix(".log")
    with open(errfile, "w") as ef:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=ef, text=True)
        timer = threading.Timer(900, proc.kill)   # 15 daqiqadan oshsa to'xtatamiz
        timer.start()
        try:
            for line in proc.stdout:
                if line.startswith(("out_time_us=", "out_time_ms=")):
                    try:
                        t = int(line.split("=", 1)[1]) / 1_000_000
                    except ValueError:
                        continue
                    frac = min(max(t / dur, 0.0), 1.0)
                    update(job_id, progress=int(p0 + (p1 - p0) * frac),
                           message=f"Konvertatsiya: {int(frac * 100)}%")
            proc.wait()
        finally:
            timer.cancel()
    if proc.returncode != 0 or not out.exists():
        err = errfile.read_text()[-300:] if errfile.exists() else ""
        raise RuntimeError("ffmpeg xatosi: " + err)


# ---------- vazifalar ----------

def run_url_job(job_id: str, url: str, start: float, end: float, mode: str) -> None:
    job_dir = WORK / job_id
    with slots:
        try:
            job_dir.mkdir(parents=True, exist_ok=True)
            update(job_id, status="downloading", progress=15,
                   message="Video qismi yuklanmoqda...")

            opts = base_opts() | {
                "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
                "outtmpl": str(job_dir / "raw.%(ext)s"),
                "download_ranges": download_range_func(None, [(start, end)]),
                "force_keyframes_at_cuts": True,
                "merge_output_format": "mp4",
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])

            raws = [p for p in job_dir.glob("raw.*") if p.suffix != ".part"]
            if not raws:
                raise RuntimeError("Yuklangan fayl topilmadi")
            raw = raws[0]

            update(job_id, status="converting", progress=60,
                   message="9:16 formatga o'tkazilmoqda...")
            out = job_dir / "short.mp4"
            transcode(job_id, raw, out, 0, end - start, mode, p0=60, p1=95)
            raw.unlink(missing_ok=True)
            update(job_id, status="done", progress=100,
                   message="Tayyor!", file=str(out))
        except DownloadError as exc:
            msg = str(exc)
            print("DOWNLOAD ERROR:", msg, flush=True)
            if "Sign in" in msg or "bot" in msg:
                text = "YouTube bu serverni bloklayapti (bot tekshiruvi)."
            else:
                text = ("Videoni yuklab bo'lmadi. U yopiq, yosh cheklovli yoki "
                        "hududingizda mavjud bo'lmasligi mumkin.")
            update(job_id, status="error", message=text)
        except Exception as exc:  # noqa: BLE001
            print("URL JOB ERROR:", exc, flush=True)
            update(job_id, status="error", message=f"Xatolik: {exc}")


def run_upload_job(job_id: str, src: Path, start: float, dur: float, mode: str) -> None:
    with slots:
        try:
            update(job_id, status="converting", progress=30,
                   message="Konvertatsiya boshlanmoqda...")
            out = src.parent / "short.mp4"
            transcode(job_id, src, out, start, dur, mode)
            update(job_id, status="done", progress=100,
                   message="Tayyor!", file=str(out))
        except Exception as exc:  # noqa: BLE001
            print("UPLOAD JOB ERROR:", exc, flush=True)
            update(job_id, status="error", message=f"Xatolik: {exc}")
        finally:
            src.unlink(missing_ok=True)   # katta asl faylni darhol o'chiramiz


def cleanup_loop() -> None:
    while True:
        time.sleep(600)
        now = time.time()
        with jobs_lock:
            old = [j for j, v in jobs.items() if now - v["created"] > JOB_TTL]
            for j in old:
                jobs.pop(j, None)
        for j in old:
            shutil.rmtree(WORK / j, ignore_errors=True)


def check_range(start_raw, end_raw):
    """(start, end) yoki (None, xato_matni) qaytaradi."""
    try:
        start = float(start_raw)
        end = float(end_raw)
    except (TypeError, ValueError):
        return None, "Vaqt noto'g'ri kiritilgan."
    if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end <= start:
        return None, "Boshlanish va tugash vaqtini to'g'ri tanlang."
    if end - start > MAX_SECONDS:
        return None, f"Davomiylik {MAX_SECONDS} soniyadan oshmasligi kerak."
    return (start, end), None


# ---------- yo'llar ----------

@app.errorhandler(RequestEntityTooLarge)
def too_large(_exc):
    return jsonify(error=f"Fayl juda katta (eng ko'pi {MAX_UPLOAD_MB} MB)."), 413


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/config")
def api_config():
    return jsonify(max_seconds=MAX_SECONDS, max_upload_mb=MAX_UPLOAD_MB)


@app.post("/api/upload")
def api_upload():
    f = request.files.get("file")
    mode = request.form.get("mode", "blur")
    if f is None or not f.filename:
        return jsonify(error="Video fayl tanlanmagan."), 400
    ext = Path(f.filename).suffix.lower()
    if ext not in VIDEO_EXTS:
        return jsonify(error="Faqat video fayl yuklang (mp4, mov, mkv, webm...)."), 400
    if mode not in ("blur", "crop"):
        return jsonify(error="Noto'g'ri format rejimi."), 400
    rng, err = check_range(request.form.get("start"), request.form.get("end"))
    if err:
        return jsonify(error=err), 400
    start, end = rng

    job_id = uuid.uuid4().hex
    job_dir = WORK / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    src = job_dir / f"input{ext}"      # foydalanuvchi bergan nomni ishlatmaymiz
    f.save(src)

    with jobs_lock:
        jobs[job_id] = {"status": "queued", "progress": 30,
                        "message": "Navbatda...", "created": time.time()}
    threading.Thread(target=run_upload_job, args=(job_id, src, start, end - start, mode),
                     daemon=True).start()
    return jsonify(id=job_id)


@app.post("/api/info")
def api_info():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not valid_youtube_url(url):
        return jsonify(error="Faqat YouTube havolasini kiriting."), 400
    try:
        with yt_dlp.YoutubeDL(base_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError as exc:
        msg = str(exc)
        print("INFO ERROR:", msg, flush=True)
        if "Sign in" in msg or "bot" in msg:
            return jsonify(error="YouTube bu serverni bloklayapti (bot tekshiruvi)."), 400
        return jsonify(error="Video topilmadi yoki unga kirib bo'lmaydi."), 400

    if info.get("is_live"):
        return jsonify(error="Jonli efirni qisqartirib bo'lmaydi."), 400
    duration = info.get("duration") or 0
    if duration < 1:
        return jsonify(error="Video davomiyligini aniqlab bo'lmadi."), 400
    return jsonify(
        title=info.get("title", ""),
        duration=duration,
        thumbnail=info.get("thumbnail", ""),
        max_seconds=MAX_SECONDS,
    )


@app.post("/api/shorts")
def api_shorts():
    data = request.get_json(silent=True) or {}
    url = str(data.get("url", "")).strip()
    mode = data.get("mode", "blur")
    if not valid_youtube_url(url):
        return jsonify(error="Faqat YouTube havolasini kiriting."), 400
    if mode not in ("blur", "crop"):
        return jsonify(error="Noto'g'ri format rejimi."), 400
    rng, err = check_range(data.get("start"), data.get("end"))
    if err:
        return jsonify(error=err), 400
    start, end = rng

    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"status": "queued", "progress": 5,
                        "message": "Navbatda...", "created": time.time()}
    threading.Thread(target=run_url_job, args=(job_id, url, start, end, mode),
                     daemon=True).start()
    return jsonify(id=job_id)


@app.get("/api/status/<job_id>")
def api_status(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            return jsonify(error="Vazifa topilmadi."), 404
        return jsonify(status=job["status"], progress=job["progress"],
                       message=job["message"])


@app.get("/api/download/<job_id>")
def api_download(job_id):
    with jobs_lock:
        job = jobs.get(job_id)
        file = job.get("file") if job else None
    if not file or not Path(file).is_file():
        return jsonify(error="Fayl tayyor emas."), 404
    inline = request.args.get("inline") == "1"
    return send_file(file, mimetype="video/mp4", as_attachment=not inline,
                     download_name="short.mp4", conditional=True)


# Tozalash oqimi gunicorn ostida ham ishlashi uchun import paytida ishga tushadi
threading.Thread(target=cleanup_loop, daemon=True).start()

if __name__ == "__main__":
    if not shutil.which("ffmpeg"):
        print("OGOHLANTIRISH: ffmpeg topilmadi. Uni o'rnating (README ga qarang).")
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", 5000)))
