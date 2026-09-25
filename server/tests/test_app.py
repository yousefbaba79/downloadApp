import sys
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


def test_download_streams_file_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(server.tempfile, "mkdtemp", lambda prefix: str(tmp_path / "work"))
    (tmp_path / "work").mkdir()
    res = client.get("/api/download", params={"url": "https://youtu.be/abc"})
    assert res.status_code == 200
    assert res.content == b"fake-video-bytes"
    assert res.headers["content-type"] == "video/mp4"
    assert "My%20Clip1.mp4" in res.headers["content-disposition"]
    assert not (tmp_path / "work").exists()


def test_api_key_required_when_configured(monkeypatch):
    monkeypatch.setattr(server, "API_KEY", "secret")
    url = "https://youtu.be/abc"
    assert client.post("/api/info", json={"url": url}).status_code == 401
    ok = client.post("/api/info", json={"url": url}, headers={"X-API-Key": "secret"})
    assert ok.status_code == 200
