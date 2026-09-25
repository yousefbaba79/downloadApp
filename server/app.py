"""Backend for the video downloader app.

The web page sends a video page URL (YouTube, Instagram or Facebook). This server uses
yt-dlp to resolve and download the actual video file, then hands it to the browser.
Doing the extraction on a server keeps the app simple and lets you update yt-dlp (which
changes often as the sites change) without changing the web page.

Downloads run as background jobs: the page starts a job, polls its progress, then fetches
the finished file. Every request returns quickly, which matters behind proxies such as
Cloudflare that cut off requests that take longer than ~100 seconds to respond.
"""

import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import yt_dlp
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

ALLOWED_HOSTS = {
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "fb.watch": "facebook",
    "fb.com": "facebook",
}

# Set API_KEY in the environment to make the site private: visitors must enter this access
# code once (it is sent as the X-API-Key header, or ?key= for file downloads).
API_KEY = os.environ.get("API_KEY")
MAX_FILESIZE_MB = int(os.environ.get("MAX_FILESIZE_MB", "500"))
MAX_PARALLEL_DOWNLOADS = int(os.environ.get("MAX_PARALLEL_DOWNLOADS", "3"))
# Finished files are deleted after this long. The browser saves the file itself, so the
# server can't tell when it's done; keep this long enough for slow connections.
JOB_TTL_SECONDS = int(os.environ.get("JOB_TTL_MINUTES", "30")) * 60

app = FastAPI(title="Video Downloader API")


class UrlRequest(BaseModel):
    url: str


class VideoInfo(BaseModel):
    platform: str
    title: str
    thumbnail: str | None = None
    duration: float | None = None
    uploader: str | None = None


JobStatus = Literal["queued", "downloading", "processing", "ready", "error"]


class JobView(BaseModel):
    id: str
    status: JobStatus
    progress: float | None = None
    error: str | None = None
    title: str | None = None


class Cancelled(Exception):
    pass


@dataclass
class Job:
    id: str
    url: str
    workdir: Path
    status: JobStatus = "queued"
    progress: float | None = None
    error: str | None = None
    title: str | None = None
    file: Path | None = None
    cancelled: bool = False
    created_at: float = field(default_factory=time.monotonic)

    def view(self) -> JobView:
        return JobView(
            id=self.id, status=self.status, progress=self.progress, error=self.error, title=self.title
        )


jobs: dict[str, Job] = {}
jobs_lock = threading.Lock()
executor = ThreadPoolExecutor(max_workers=MAX_PARALLEL_DOWNLOADS)


STATIC_DIR = Path(__file__).resolve().parent / "static"


def require_api_key(
    x_api_key: str | None = Header(default=None),
    key: str | None = Query(default=None, include_in_schema=False),
) -> None:
    # Browsers can't add headers to a plain file download link, so ?key= is accepted too.
    if API_KEY and API_KEY not in (x_api_key, key):
        raise HTTPException(status_code=401, detail="Access code required.")


def detect_platform(url: str) -> str:
    """Return the platform name for a supported URL, or raise a 400 error."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(status_code=400, detail="Please enter a valid http(s) link.")
    host = parsed.hostname.lower()
    for domain, platform in ALLOWED_HOSTS.items():
        if host == domain or host.endswith("." + domain):
            return platform
    raise HTTPException(
        status_code=400,
        detail="Only YouTube, Instagram and Facebook links are supported.",
    )


def ydl_options(**extra) -> dict:
    has_ffmpeg = shutil.which("ffmpeg") is not None
    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        # Prefer MP4 so the file plays in the iOS/Android gallery. Merging separate
        # video+audio streams needs ffmpeg; without it fall back to a single-file format.
        "format": (
            "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/b"
            if has_ffmpeg
            else "b[ext=mp4][vcodec!=none][acodec!=none]/b[vcodec!=none][acodec!=none]/b"
        ),
        "max_filesize": MAX_FILESIZE_MB * 1024 * 1024,
    }
    if has_ffmpeg:
        opts["merge_output_format"] = "mp4"
    opts.update(extra)
    return opts


def friendly_error(err: Exception) -> str:
    message = str(err)
    if re.search(r"private|login|sign in|cookies|authentication", message, re.IGNORECASE):
        return "This video is private or requires login, so it can't be downloaded."
    if re.search(r"unsupported url|no video", message, re.IGNORECASE):
        return "No video was found at this link."
    return "Could not get the video from this link. Please check it and try again."


def safe_filename(title: str, ext: str) -> str:
    name = re.sub(r"[^\w\- ]+", "", title).strip()[:80] or "video"
    return f"{name}.{ext}"


def remove_job(job_id: str) -> None:
    with jobs_lock:
        job = jobs.pop(job_id, None)
    if job:
        job.cancelled = True  # stops a download that is still running
        shutil.rmtree(job.workdir, ignore_errors=True)


def sweep_expired_jobs() -> None:
    now = time.monotonic()
    with jobs_lock:
        expired = [j.id for j in jobs.values() if now - j.created_at > JOB_TTL_SECONDS]
    for job_id in expired:
        remove_job(job_id)


def run_job(job: Job) -> None:
    def on_progress(d: dict) -> None:
        if job.cancelled:
            raise Cancelled()
        if d.get("status") == "downloading":
            job.status = "downloading"
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if total:
                # With separate video + audio streams yt-dlp reports each one; this shows
                # per-stream progress, which is still a useful "it's moving" signal.
                job.progress = min(d.get("downloaded_bytes", 0) / total, 1.0)
        elif d.get("status") == "finished":
            job.status = "processing"
            job.progress = None

    try:
        opts = ydl_options(outtmpl=str(job.workdir / "video.%(ext)s"), progress_hooks=[on_progress])
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(job.url, download=True)
        files = [p for p in job.workdir.iterdir() if p.is_file() and not p.name.endswith(".part")]
        if not files:
            raise yt_dlp.utils.DownloadError("The video is too large or unavailable.")
        job.title = data.get("title") or "video"
        job.file = max(files, key=lambda p: p.stat().st_size)
        job.progress = 1.0
        job.status = "ready"
    except Cancelled:
        shutil.rmtree(job.workdir, ignore_errors=True)
    except Exception as err:  # noqa: BLE001 - any failure is reported to the page
        exc_info = getattr(err, "exc_info", None) or (None, None)
        cancelled = job.cancelled or isinstance(exc_info[1], Cancelled)
        if not cancelled:
            too_large = "too large" in str(err).lower()
            job.error = "The video is too large to download." if too_large else friendly_error(err)
            job.status = "error"
        shutil.rmtree(job.workdir, ignore_errors=True)


def get_job(job_id: str) -> Job:
    with jobs_lock:
        job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Download not found or expired. Please try again.")
    return job


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "ffmpeg": shutil.which("ffmpeg") is not None}


@app.post("/api/info", response_model=VideoInfo, dependencies=[Depends(require_api_key)])
def info(req: UrlRequest) -> VideoInfo:
    platform = detect_platform(req.url)
    try:
        with yt_dlp.YoutubeDL(ydl_options()) as ydl:
            data = ydl.extract_info(req.url.strip(), download=False)
    except yt_dlp.utils.DownloadError as err:
        raise HTTPException(status_code=422, detail=friendly_error(err)) from err
    return VideoInfo(
        platform=platform,
        title=data.get("title") or "Video",
        thumbnail=data.get("thumbnail"),
        duration=data.get("duration"),
        uploader=data.get("uploader"),
    )


@app.post("/api/jobs", response_model=JobView, dependencies=[Depends(require_api_key)])
def create_job(req: UrlRequest) -> JobView:
    """Start downloading a video on the server. Poll GET /api/jobs/{id} for progress."""
    detect_platform(req.url)
    sweep_expired_jobs()
    job = Job(id=uuid.uuid4().hex, url=req.url.strip(), workdir=Path(tempfile.mkdtemp(prefix="vdl-")))
    with jobs_lock:
        jobs[job.id] = job
    executor.submit(run_job, job)
    return job.view()


@app.get("/api/jobs/{job_id}", response_model=JobView, dependencies=[Depends(require_api_key)])
def job_status(job_id: str) -> JobView:
    return get_job(job_id).view()


@app.get("/api/jobs/{job_id}/file", dependencies=[Depends(require_api_key)])
def job_file(job_id: str) -> FileResponse:
    job = get_job(job_id)
    if job.status != "ready" or not job.file:
        raise HTTPException(status_code=409, detail="The video is not ready yet.")
    ext = job.file.suffix.lstrip(".") or "mp4"
    return FileResponse(
        job.file,
        media_type="video/mp4" if ext == "mp4" else "application/octet-stream",
        filename=safe_filename(job.title or "video", ext),
    )


@app.delete("/api/jobs/{job_id}", status_code=204, dependencies=[Depends(require_api_key)])
def delete_job(job_id: str) -> None:
    """Cancel a running download or free its file early."""
    remove_job(job_id)


# Serve the web page. Mounted last so the /api routes above take priority.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
