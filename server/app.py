"""Backend for the video downloader app.

The mobile app sends a video page URL (YouTube, Instagram or Facebook). This server uses
yt-dlp to resolve and download the actual video file, then streams it back to the phone.
Doing the extraction on a server keeps the app simple and lets you update yt-dlp (which
changes often as the sites change) without shipping a new app release.
"""

import os
import re
import shutil
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yt_dlp
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

ALLOWED_HOSTS = {
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "fb.watch": "facebook",
    "fb.com": "facebook",
}

# Set API_KEY in the environment to require the app to send a matching X-API-Key header.
API_KEY = os.environ.get("API_KEY")
MAX_FILESIZE_MB = int(os.environ.get("MAX_FILESIZE_MB", "500"))

app = FastAPI(title="Video Downloader API")


class InfoRequest(BaseModel):
    url: str


class VideoInfo(BaseModel):
    platform: str
    title: str
    thumbnail: str | None = None
    duration: float | None = None
    uploader: str | None = None


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


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


def friendly_error(err: Exception) -> HTTPException:
    message = str(err)
    if re.search(r"private|login|sign in|cookies|authentication", message, re.IGNORECASE):
        detail = "This video is private or requires login, so it can't be downloaded."
    elif re.search(r"unsupported url|no video", message, re.IGNORECASE):
        detail = "No video was found at this link."
    else:
        detail = "Could not get the video from this link. Please check it and try again."
    return HTTPException(status_code=422, detail=detail)


def safe_filename(title: str, ext: str) -> str:
    name = re.sub(r"[^\w\- ]+", "", title).strip()[:80] or "video"
    return f"{name}.{ext}"


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "ffmpeg": shutil.which("ffmpeg") is not None}


@app.post("/api/info", response_model=VideoInfo, dependencies=[Depends(require_api_key)])
def info(req: InfoRequest) -> VideoInfo:
    platform = detect_platform(req.url)
    try:
        with yt_dlp.YoutubeDL(ydl_options()) as ydl:
            data = ydl.extract_info(req.url.strip(), download=False)
    except yt_dlp.utils.DownloadError as err:
        raise friendly_error(err) from err
    return VideoInfo(
        platform=platform,
        title=data.get("title") or "Video",
        thumbnail=data.get("thumbnail"),
        duration=data.get("duration"),
        uploader=data.get("uploader"),
    )


@app.get("/api/download", dependencies=[Depends(require_api_key)])
def download(url: str = Query(..., description="Video page URL")) -> FileResponse:
    detect_platform(url)
    workdir = Path(tempfile.mkdtemp(prefix="vdl-"))
    cleanup = BackgroundTask(shutil.rmtree, workdir, ignore_errors=True)
    try:
        opts = ydl_options(outtmpl=str(workdir / "video.%(ext)s"))
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url.strip(), download=True)
        files = [p for p in workdir.iterdir() if p.is_file() and not p.name.endswith(".part")]
        if not files:
            raise HTTPException(status_code=422, detail="The video is too large or unavailable.")
        path = max(files, key=lambda p: p.stat().st_size)
    except yt_dlp.utils.DownloadError as err:
        shutil.rmtree(workdir, ignore_errors=True)
        raise friendly_error(err) from err
    except BaseException:
        shutil.rmtree(workdir, ignore_errors=True)
        raise

    ext = path.suffix.lstrip(".") or "mp4"
    return FileResponse(
        path,
        media_type="video/mp4" if ext == "mp4" else "application/octet-stream",
        filename=safe_filename(data.get("title") or "video", ext),
        background=cleanup,
    )
