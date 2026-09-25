FROM python:3.12-slim

# ffmpeg lets yt-dlp merge separate video + audio streams (needed for most YouTube quality levels).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static ./static

EXPOSE 8000
# Hosts like Render tell the app which port to use via $PORT; default to 8000 elsewhere.
CMD ["sh", "-c", "exec uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
