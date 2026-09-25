import sys
import threading
import time
from pathlib import Path

import pytest
import yt_dlp
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app as server  # noqa: E402

client = TestClient(server.app)


class FakeYDL:
    """Stands in for yt_dlp.YoutubeDL so tests don't hit the network."""

    error: Exception | None = None

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if FakeYDL.error:
            raise FakeYDL.error
        if download:
            for hook in self.opts.get("progress_hooks", []):
                hook({"status": "downloading", "downloaded_bytes": 5, "total_bytes": 10})
                hook({"status": "finished"})
            out = Path(self.opts["outtmpl"].replace("%(ext)s", "mp4"))
            out.write_bytes(b"fake-video-bytes")
        return {"title": "My: Clip/1", "thumbnail": "https://img/x.jpg", "duration": 12}


@pytest.fixture(autouse=True)
def fake_ydl(monkeypatch):
    FakeYDL.error = None
    monkeypatch.setattr(server.yt_dlp, "YoutubeDL", FakeYDL)
    monkeypatch.setattr(server, "API_KEY", None)


@pytest.mark.parametrize(
    "url,platform",
    [
        ("https://www.youtube.com/watch?v=abc", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://m.youtube.com/shorts/abc", "youtube"),
        ("https://www.instagram.com/reel/abc/", "instagram"),
        ("https://www.facebook.com/watch/?v=123", "facebook"),
        ("https://fb.watch/abc/", "facebook"),
    ],
)
def test_detect_platform(url, platform):
    assert server.detect_platform(url) == platform


@pytest.mark.parametrize(
    "url",
    ["", "not a url", "ftp://youtube.com/x", "https://evil.com/youtube.com", "https://notyoutube.com/x"],
)
def test_rejects_unsupported_urls(url):
    res = client.post("/api/info", json={"url": url})
    assert res.status_code == 400


def test_info():
    res = client.post("/api/info", json={"url": "https://youtu.be/abc"})
    assert res.status_code == 200
    body = res.json()
    assert body["platform"] == "youtube"
    assert body["title"] == "My: Clip/1"


def test_info_private_video_message():
    FakeYDL.error = yt_dlp.utils.DownloadError("This video is private")
    res = client.post("/api/info", json={"url": "https://www.instagram.com/p/x/"})
    assert res.status_code == 422
    assert "private" in res.json()["detail"]


class SyncExecutor:
    def submit(self, fn, *args):
        fn(*args)


@pytest.fixture
def sync_jobs(monkeypatch):
    monkeypatch.setattr(server, "executor", SyncExecutor())
    server.jobs.clear()


def test_job_downloads_file_then_cleans_up(sync_jobs):
    res = client.post("/api/jobs", json={"url": "https://youtu.be/abc"})
    assert res.status_code == 200
    job_id = res.json()["id"]
    workdir = server.jobs[job_id].workdir

    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "ready"
    assert status["progress"] == 1.0

    file = client.get(f"/api/jobs/{job_id}/file")
    assert file.status_code == 200
    assert file.content == b"fake-video-bytes"
    assert file.headers["content-type"] == "video/mp4"
    assert "My%20Clip1.mp4" in file.headers["content-disposition"]

    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    assert not workdir.exists()
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_job_reports_friendly_error(sync_jobs):
    FakeYDL.error = yt_dlp.utils.DownloadError("Sign in to confirm your age")
    job_id = client.post("/api/jobs", json={"url": "https://youtu.be/abc"}).json()["id"]
    status = client.get(f"/api/jobs/{job_id}").json()
    assert status["status"] == "error"
    assert "private or requires login" in status["error"]
    assert client.get(f"/api/jobs/{job_id}/file").status_code == 409


def test_job_rejects_unsupported_url(sync_jobs):
    assert client.post("/api/jobs", json={"url": "https://example.com/v"}).status_code == 400


def test_cancel_stops_running_download(monkeypatch):
    server.jobs.clear()
    started, release = threading.Event(), threading.Event()

    class SlowYDL(FakeYDL):
        def extract_info(self, url, download=False):
            started.set()
            release.wait(5)
            for hook in self.opts["progress_hooks"]:
                hook({"status": "downloading", "downloaded_bytes": 1, "total_bytes": 10})
            raise AssertionError("progress hook should have cancelled the download")

    monkeypatch.setattr(server.yt_dlp, "YoutubeDL", SlowYDL)
    job_id = client.post("/api/jobs", json={"url": "https://youtu.be/abc"}).json()["id"]
    job = server.jobs[job_id]
    assert started.wait(5)
    assert client.delete(f"/api/jobs/{job_id}").status_code == 204
    release.set()
    for _ in range(50):
        if not job.workdir.exists():
            break
        time.sleep(0.05)
    assert job.cancelled
    assert job.status != "error"
    assert not job.workdir.exists()


def test_expired_jobs_are_removed(sync_jobs, monkeypatch):
    job_id = client.post("/api/jobs", json={"url": "https://youtu.be/abc"}).json()["id"]
    workdir = server.jobs[job_id].workdir
    monkeypatch.setattr(server, "JOB_TTL_SECONDS", -1)
    server.sweep_expired_jobs()
    assert job_id not in server.jobs
    assert not workdir.exists()


def test_api_key_required_when_configured(monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "secret")
    url = "https://youtu.be/abc"
    assert client.post("/api/info", json={"url": url}).status_code == 401
    ok = client.post("/api/info", json={"url": url}, headers={"X-API-Key": "secret"})
    assert ok.status_code == 200
