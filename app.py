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
from yt_dlp.utils import DownloadError, download_range_func

BASE = Path(__file__).resolve().parent
WORK = BASE / "work"
WORK.mkdir(exist_ok=True)

MAX_SECONDS = 60          # Shorts uchun maksimal davomiylik
JOB_TTL = 60 * 60         # tayyor fayllar 1 soat saqlanadi
MAX_PARALLEL = 2          # bir vaqtda ishlaydigan vazifalar soni
COOKIES = os.environ.get("YTDLP_COOKIES")  # ixtiyoriy: cookies.txt yo'li

ALLOWED_HOSTS = {
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be", "www.youtu.be",
}

app = Flask(__name__, static_folder="static", static_url_path="")
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
    if mode == "blur":
        # Orqa fon: xira va to'ldirilgan, old tomon: asl video markazda
        return (
            "split[a][b];"
            "[a]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=25:5[bg];"
            "[b]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    # "crop": markazdan kesib to'ldirish
    return "scale=-2:1920,crop=1080:1920,setsar=1"


def run_job(job_id: str, url: str, start: float, end: float, mode: str) -> None:
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
            cmd = [
                "ffmpeg", "-y", "-i", str(raw),
                "-vf", build_filter(mode),
                "-t", str(end - start),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k",
                "-movflags", "+faststart",
                str(out),
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
            if proc.returncode != 0 or not out.exists():
                raise RuntimeError("ffmpeg xatosi: " + proc.stderr[-300:])

            raw.unlink(missing_ok=True)
            update(job_id, status="done", progress=100,
                   message="Tayyor!", file=str(out))
        except DownloadError:
            update(job_id, status="error",
                   message="Videoni yuklab bo'lmadi. U yopiq, yosh cheklovli yoki "
                           "hududingizda mavjud bo'lmasligi mumkin.")
        except Exception as exc:  # noqa: BLE001
            update(job_id, status="error", message=f"Xatolik: {exc}")


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


# ---------- yo'llar ----------

@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.post("/api/info")
def api_info():
    url = (request.get_json(silent=True) or {}).get("url", "").strip()
    if not valid_youtube_url(url):
        return jsonify(error="Faqat YouTube havolasini kiriting."), 400
    try:
        with yt_dlp.YoutubeDL(base_opts()) as ydl:
            info = ydl.extract_info(url, download=False)
    except DownloadError:
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
    try:
        start = float(data.get("start"))
        end = float(data.get("end"))
    except (TypeError, ValueError):
        return jsonify(error="Vaqt noto'g'ri kiritilgan."), 400

    if not valid_youtube_url(url):
        return jsonify(error="Faqat YouTube havolasini kiriting."), 400
    if mode not in ("blur", "crop"):
        return jsonify(error="Noto'g'ri format rejimi."), 400
    if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end <= start:
        return jsonify(error="Boshlanish va tugash vaqtini to'g'ri tanlang."), 400
    if end - start > MAX_SECONDS:
        return jsonify(error=f"Davomiylik {MAX_SECONDS} soniyadan oshmasligi kerak."), 400

    job_id = uuid.uuid4().hex
    with jobs_lock:
        jobs[job_id] = {"status": "queued", "progress": 5,
                        "message": "Navbatda...", "created": time.time()}
    threading.Thread(target=run_job, args=(job_id, url, start, end, mode),
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
