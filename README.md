# Video Downloader (Android + iOS)

A mobile app that downloads videos from **YouTube, Instagram and Facebook**. The user pastes a
link, taps **Download**, and the video is saved to the phone's gallery.

```
┌──────────────┐  1. POST /api/info {url}        ┌────────────────────────┐
│  Mobile app  │ ──────────────────────────────▶ │  Server (FastAPI)      │
│  (Expo /     │  2. GET /api/download?url=…     │  yt-dlp finds the real │
│ React Native)│ ◀────────── video.mp4 ───────── │  video file & streams  │
└──────┬───────┘                                 └────────────────────────┘
       │ 3. save to Photos / Gallery
```

Finding the real video file behind a YouTube, Instagram or Facebook link is hard, and the sites
change often. The server uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) for this. You can
update it on the server at any time, so you don't need to ship a new app version when a site
changes.

| Folder    | What it is |
|-----------|------------|
| `mobile/` | Expo (React Native + TypeScript) app for Android and iOS from one codebase |
| `server/` | Python FastAPI backend using yt-dlp |

## 1. Run the server

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Or use Docker. This includes ffmpeg, which yt-dlp needs to merge YouTube's separate video and
audio streams into higher-quality MP4s:

```bash
docker build -t video-downloader-server server
docker run -p 8000:8000 video-downloader-server
```

Optional environment variables:

- `API_KEY`: when set, the app must send the same key in the `X-API-Key` header.
- `MAX_FILESIZE_MB`: the largest video the server will download. Default is `500`.

Run the tests with `pip install -r requirements-dev.txt && pytest`.

## 2. Run the app

```bash
cd mobile
npm install
cp .env.example .env   # set EXPO_PUBLIC_API_URL to your server
npx expo start
```

- On a real phone, `localhost` means the phone itself. Set `EXPO_PUBLIC_API_URL` to your
  computer's LAN IP address (for example `http://192.168.1.20:8000`) or to a deployed HTTPS URL.
- Saving to the gallery uses `expo-media-library`. It works best in a development build
  (`npx expo run:android` / `npx expo run:ios`, or `npx eas-cli build --profile development`)
  rather than Expo Go.
- Production builds: `npx eas-cli build -p android` and `npx eas-cli build -p ios`.
- For production, host the server behind **HTTPS**. iOS and Android block plain `http://` by
  default in release builds.

## How the app works

1. The user pastes a link. The app detects which platform it is from.
2. **Download** → the app calls `/api/info` and shows the title and thumbnail.
3. The app downloads the MP4 from `/api/download` and shows a progress bar. The user can cancel.
4. The app saves the video to Photos/Gallery. If the user denies permission, the share sheet
   opens instead, so they can still save it (for example with "Save to Files").
5. Private or login-only videos, and links from other sites, show a clear error message.

## Important: legal and app store rules

- **YouTube's Terms of Service forbid downloading videos** except through YouTube's own features.
  Instagram and Facebook have similar terms. Users should only download videos they own or have
  permission to save. The app shows a notice saying this.
- **Google Play and the Apple App Store usually reject apps that download YouTube videos.** Many
  Instagram and Facebook downloaders are rejected too. Common alternatives:
  - Android: distribute the APK yourself, or through F-Droid or another store.
  - Remove YouTube support (from `ALLOWED_HOSTS` in `server/app.py` and `PLATFORM_HOSTS` in
    `mobile/src/api.ts`) before submitting to a store.
- Sites like YouTube often block or rate-limit requests from cloud server IPs. If that happens
  in production, see yt-dlp's documentation on cookies and PO tokens.
