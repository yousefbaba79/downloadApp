import { File, Paths } from 'expo-file-system';

// Configure in mobile/.env (see .env.example). On a real phone "localhost" is the phone
// itself, so use your computer's LAN IP (e.g. http://192.168.1.20:8000) or a deployed URL.
export const API_URL = (process.env.EXPO_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/+$/, '');
const API_KEY = process.env.EXPO_PUBLIC_API_KEY;

export type Platform = 'youtube' | 'instagram' | 'facebook';

export type VideoInfo = {
  platform: Platform;
  title: string;
  thumbnail?: string | null;
  duration?: number | null;
  uploader?: string | null;
};

const PLATFORM_HOSTS: Record<string, Platform> = {
  'youtube.com': 'youtube',
  'youtu.be': 'youtube',
  'instagram.com': 'instagram',
  'facebook.com': 'facebook',
  'fb.watch': 'facebook',
  'fb.com': 'facebook',
};

/** Quick client-side check so obvious mistakes don't need a server round-trip. */
export function detectPlatform(input: string): Platform | null {
  const match = input.trim().match(/^https?:\/\/([^/?#:]+)/i);
  if (!match) return null;
  const host = match[1].toLowerCase();
  for (const [domain, platform] of Object.entries(PLATFORM_HOSTS)) {
    if (host === domain || host.endsWith('.' + domain)) return platform;
  }
  return null;
}

function headers(): Record<string, string> {
  return API_KEY ? { 'X-API-Key': API_KEY } : {};
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') return body.detail;
  } catch {
    // Response wasn't JSON; fall through to the generic message.
  }
  return `Server error (${res.status}). Please try again.`;
}

export async function fetchVideoInfo(url: string): Promise<VideoInfo> {
  let res: Response;
  try {
    res = await fetch(`${API_URL}/api/info`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...headers() },
      body: JSON.stringify({ url: url.trim() }),
    });
  } catch {
    throw new Error(`Can't reach the download server at ${API_URL}.`);
  }
  if (!res.ok) throw new Error(await errorMessage(res));
  return res.json();
}

/** Downloads the video to the app cache and returns the local file. */
export async function downloadVideo(
  url: string,
  onProgress: (fraction: number | null) => void,
  signal?: AbortSignal,
): Promise<File> {
  const destination = new File(Paths.cache, `video-${Date.now()}.mp4`);
  const task = File.createDownloadTask(
    `${API_URL}/api/download?url=${encodeURIComponent(url.trim())}`,
    destination,
    {
      headers: headers(),
      signal,
      onProgress: ({ bytesWritten, totalBytes }) =>
        onProgress(totalBytes > 0 ? bytesWritten / totalBytes : null),
    },
  );
  const file = await task.downloadAsync();
  if (!file) throw new Error('Download was interrupted.');

  // The server returns a JSON error body instead of a video when something goes wrong.
  if (file.size < 2048) {
    let message = 'Download failed. Please try again.';
    try {
      const body = JSON.parse(await file.text());
      if (typeof body?.detail === 'string') message = body.detail;
    } catch {
      // Tiny but valid file; unlikely for a video, treat as failure anyway.
    }
    file.delete();
    throw new Error(message);
  }
  return file;
}
