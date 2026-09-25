# Video Downloader (web app)

A website that downloads videos from **YouTube, Instagram and Facebook**. You paste a link, tap
**Download**, and the video is saved to your device. It works in any browser on Android, iPhone
or a computer. No app store is needed.

```
┌──────────────┐   1. POST /api/jobs {url}          ┌───────────────────────────┐
│   Browser    │ ─────────────────────────────────▶ │  Server (FastAPI)         │
│  (phone or   │   2. GET /api/jobs/{id}  (poll)    │  yt-dlp downloads the     │
│   computer)  │   3. GET /api/jobs/{id}/file       │  video in the background  │
└──────────────┘ ◀──────────── video.mp4 ────────── └───────────────────────────┘
```

The server does the hard part. It uses [yt-dlp](https://github.com/yt-dlp/yt-dlp) to find the
real video file behind a link. The same server also hosts the web page (`server/static/`).
Downloads run in the background while the page shows progress, so no single request takes more
than a few seconds. That matters behind Cloudflare, which cuts off slow requests after about
100 seconds.

## Run it on your computer

```bash
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
```

Open http://localhost:8000. For best quality, install **ffmpeg**, which yt-dlp needs to combine
YouTube's separate video and audio streams. The Docker setup below already includes it.

Run the tests with `pip install -r requirements-dev.txt && pytest`.

## Deploy on Render (free)

Render builds the site from `server/Dockerfile` using the settings in `render.yaml`, and gives
it a free `https://….onrender.com` address.

1. Sign up at [render.com](https://render.com) with your GitHub account.
2. Click **New → Blueprint**, then connect the `downloadApp` repository. If Render asks which
   repositories it may access, allow this one.
3. Pick the branch that has the code, if asked. Render reads `render.yaml` and shows one web
   service called **video-downloader** on the **Free** plan.
4. It asks for a value for **`API_KEY`**. Type an access code you'll share with the people
   allowed to use the site, for example `family2026`.
5. Click **Apply** (or **Deploy Blueprint**). The first build takes a few minutes.
6. When it says **Live**, open the `https://video-downloader-….onrender.com` address shown at the
   top of the service page. Enter your access code the first time.

Every push to the branch redeploys automatically.

**Free plan limits:**
- After about 15 minutes with no visitors the site goes to sleep. The next visit takes about a
  minute to load while it wakes up.
- It has 512 MB of memory, so `render.yaml` limits videos to 300 MB and 2 downloads at a time.
  You can change `MAX_FILESIZE_MB` and `MAX_PARALLEL_DOWNLOADS` under **Environment** in Render.
- If YouTube downloads fail with "Could not get the video", YouTube is probably blocking
  Render's servers. Instagram and Facebook usually still work.

## Put it online with Cloudflare Tunnel (runs on your own computer)

Cloudflare Tunnel gives the server on your computer a public **HTTPS** address. You don't need
to open ports on your router. You need [Docker](https://docs.docker.com/get-docker/), and the
computer must stay on while people use the site.

```bash
cp .env.example .env      # optional: set API_KEY to make the site private
```

### Option A: quick test, no account needed

```bash
docker compose --profile quick up -d --build
docker compose logs tunnel-quick | grep trycloudflare.com
```

Open the `https://….trycloudflare.com` address it prints, on your phone or anywhere else. The
address **changes every time** the tunnel restarts, so use this only for testing.

### Option B: your own domain, permanent address

This needs a free Cloudflare account and a domain that uses Cloudflare for DNS.

1. In the Cloudflare dashboard, go to **Zero Trust → Networks → Tunnels → Create a tunnel**
   and choose **Cloudflared**. The menu names may be slightly different.
2. Copy the **token** it shows into `.env` as `TUNNEL_TOKEN=…`.
3. Add a **Public Hostname**, for example `video.yourdomain.com`, with service **HTTP** and
   URL `server:8000`.
4. Start it:

   ```bash
   docker compose --profile named up -d --build
   ```

Your site is now at `https://video.yourdomain.com`.

## Settings (`.env`)

| Setting | What it does |
|---------|--------------|
| `API_KEY` | An access code. When set, visitors must enter it once. The browser remembers it. Leave it empty for an open site. |
| `MAX_FILESIZE_MB` | The largest video the server will download. Default `500`. |
| `TUNNEL_TOKEN` | Cloudflare Tunnel token, used only by Option B. |

If other people can find the link, **set `API_KEY`**. Otherwise anyone can use your server and
your internet connection to download videos.

## Saving on phones

- **Android:** the video goes to **Downloads** and usually shows up in the Gallery.
- **iPhone:** Safari saves it to the **Files** app → Downloads. Open it and tap
  **Share → Save Video** to move it to Photos. The site shows this tip on iPhones.
- **App icon:** you can add the site to your home screen (Share → Add to Home Screen) so it opens
  like an app.

## Important

- **Downloading breaks the terms of service** of YouTube, and likely Instagram and Facebook too.
  Only download videos you own or have permission to save. The page shows this notice.
- A **public** downloader site is more likely to get complaints from rights holders than a
  private one. Keep it private with `API_KEY`, or drop YouTube from `ALLOWED_HOSTS` in
  `server/app.py` (and `PLATFORMS` in `server/static/index.html`).
- Cloudflare's terms limit using its free network mainly to serve large amounts of video. That's
  fine for personal use. Check their terms before you grow it.
- Sites change often. If downloads start failing, update yt-dlp: rebuild with
  `docker compose build --pull --no-cache server`.
