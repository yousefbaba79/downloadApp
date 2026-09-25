import { StatusBar } from 'expo-status-bar';
import * as MediaLibrary from 'expo-media-library';
import * as Sharing from 'expo-sharing';
import { useRef, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  KeyboardAvoidingView,
  Platform as RNPlatform,
  Pressable,
  SafeAreaView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { detectPlatform, downloadVideo, fetchVideoInfo, VideoInfo } from './src/api';

type Status =
  | { kind: 'idle' }
  | { kind: 'finding' }
  | { kind: 'downloading'; progress: number | null }
  | { kind: 'saving' }
  | { kind: 'done'; message: string }
  | { kind: 'error'; message: string };

const PLATFORM_LABEL = { youtube: 'YouTube', instagram: 'Instagram', facebook: 'Facebook' };

export default function App() {
  const [url, setUrl] = useState('');
  const [info, setInfo] = useState<VideoInfo | null>(null);
  const [status, setStatus] = useState<Status>({ kind: 'idle' });
  const abortRef = useRef<AbortController | null>(null);

  const busy = status.kind === 'finding' || status.kind === 'downloading' || status.kind === 'saving';
  const detected = detectPlatform(url);

  async function handleDownload() {
    if (!url.trim()) {
      setStatus({ kind: 'error', message: 'Please paste a video link first.' });
      return;
    }
    if (!detected) {
      setStatus({ kind: 'error', message: 'Only YouTube, Instagram and Facebook links are supported.' });
      return;
    }

    const controller = new AbortController();
    abortRef.current = controller;
    setInfo(null);

    try {
      setStatus({ kind: 'finding' });
      const videoInfo = await fetchVideoInfo(url);
      setInfo(videoInfo);

      setStatus({ kind: 'downloading', progress: 0 });
      const file = await downloadVideo(
        url,
        (progress) => setStatus({ kind: 'downloading', progress }),
        controller.signal,
      );

      setStatus({ kind: 'saving' });
      const permission = await MediaLibrary.requestPermissionsAsync(true, ['video']);
      if (permission.granted) {
        await MediaLibrary.Asset.create(file.uri);
        file.delete();
        setStatus({ kind: 'done', message: 'Saved to your gallery.' });
      } else if (await Sharing.isAvailableAsync()) {
        // No gallery access: let the user save it via the share sheet ("Save to Files", etc).
        await Sharing.shareAsync(file.uri, { mimeType: 'video/mp4' });
        setStatus({ kind: 'done', message: 'Video downloaded.' });
      } else {
        setStatus({ kind: 'error', message: 'Allow photo library access to save videos.' });
      }
    } catch (err) {
      if (controller.signal.aborted) {
        setStatus({ kind: 'idle' });
      } else {
        setStatus({ kind: 'error', message: err instanceof Error ? err.message : String(err) });
      }
    } finally {
      abortRef.current = null;
    }
  }

  return (
    <SafeAreaView style={styles.safe}>
      <StatusBar style="dark" />
      <KeyboardAvoidingView
        style={styles.container}
        behavior={RNPlatform.OS === 'ios' ? 'padding' : undefined}
      >
        <Text style={styles.title}>Video Downloader</Text>
        <Text style={styles.subtitle}>YouTube · Instagram · Facebook</Text>

        <TextInput
          style={styles.input}
          value={url}
          onChangeText={(text) => {
            setUrl(text);
            if (status.kind === 'error' || status.kind === 'done') setStatus({ kind: 'idle' });
          }}
          placeholder="Paste video link here"
          placeholderTextColor="#8a8f98"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="url"
          returnKeyType="go"
          onSubmitEditing={handleDownload}
          editable={!busy}
          clearButtonMode="while-editing"
        />
        {detected && !busy ? (
          <Text style={styles.hint}>{PLATFORM_LABEL[detected]} link detected</Text>
        ) : null}

        {busy ? (
          <Pressable style={[styles.button, styles.cancel]} onPress={() => abortRef.current?.abort()}>
            <Text style={styles.buttonText}>Cancel</Text>
          </Pressable>
        ) : (
          <Pressable
            style={({ pressed }) => [styles.button, pressed && styles.pressed]}
            onPress={handleDownload}
          >
            <Text style={styles.buttonText}>Download</Text>
          </Pressable>
        )}

        {info ? (
          <View style={styles.card}>
            {info.thumbnail ? <Image source={{ uri: info.thumbnail }} style={styles.thumb} /> : null}
            <Text style={styles.cardTitle} numberOfLines={2}>
              {info.title}
            </Text>
            {info.uploader ? <Text style={styles.cardMeta}>{info.uploader}</Text> : null}
          </View>
        ) : null}

        <StatusView status={status} />

        <Text style={styles.disclaimer}>
          Only download videos you own or have permission to save.
        </Text>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function StatusView({ status }: { status: Status }) {
  switch (status.kind) {
    case 'idle':
      return null;
    case 'finding':
      return <Progress label="Finding video…" />;
    case 'saving':
      return <Progress label="Saving…" />;
    case 'downloading': {
      const pct = status.progress == null ? null : Math.round(status.progress * 100);
      return (
        <View style={styles.statusBox}>
          <Text style={styles.statusText}>
            {pct == null ? 'Downloading…' : `Downloading… ${pct}%`}
          </Text>
          <View style={styles.barTrack}>
            <View style={[styles.barFill, { width: `${pct ?? 100}%`, opacity: pct == null ? 0.4 : 1 }]} />
          </View>
        </View>
      );
    }
    case 'done':
      return <Text style={[styles.statusText, styles.success]}>✓ {status.message}</Text>;
    case 'error':
      return <Text style={[styles.statusText, styles.error]}>{status.message}</Text>;
  }
}

function Progress({ label }: { label: string }) {
  return (
    <View style={[styles.statusBox, styles.row]}>
      <ActivityIndicator color="#2563eb" />
      <Text style={styles.statusText}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  safe: { flex: 1, backgroundColor: '#f5f6f8' },
  container: { flex: 1, padding: 24, paddingTop: 48 },
  title: { fontSize: 28, fontWeight: '700', color: '#111827' },
  subtitle: { fontSize: 15, color: '#6b7280', marginTop: 4, marginBottom: 28 },
  input: {
    backgroundColor: '#fff',
    borderWidth: 1,
    borderColor: '#d1d5db',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 14,
    fontSize: 16,
    color: '#111827',
  },
  hint: { marginTop: 8, color: '#2563eb', fontSize: 13 },
  button: {
    marginTop: 16,
    backgroundColor: '#2563eb',
    borderRadius: 12,
    paddingVertical: 16,
    alignItems: 'center',
  },
  pressed: { opacity: 0.8 },
  cancel: { backgroundColor: '#6b7280' },
  buttonText: { color: '#fff', fontSize: 17, fontWeight: '600' },
  card: {
    marginTop: 24,
    backgroundColor: '#fff',
    borderRadius: 12,
    overflow: 'hidden',
    borderWidth: 1,
    borderColor: '#e5e7eb',
  },
  thumb: { width: '100%', aspectRatio: 16 / 9, backgroundColor: '#e5e7eb' },
  cardTitle: { fontSize: 16, fontWeight: '600', color: '#111827', padding: 12, paddingBottom: 4 },
  cardMeta: { fontSize: 13, color: '#6b7280', paddingHorizontal: 12, paddingBottom: 12 },
  statusBox: { marginTop: 20 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  statusText: { fontSize: 15, color: '#374151', marginTop: 20 },
  success: { color: '#15803d', fontWeight: '600' },
  error: { color: '#b91c1c' },
  barTrack: { height: 8, backgroundColor: '#e5e7eb', borderRadius: 4, marginTop: 10, overflow: 'hidden' },
  barFill: { height: 8, backgroundColor: '#2563eb' },
  disclaimer: { marginTop: 'auto', fontSize: 12, color: '#9ca3af', textAlign: 'center' },
});
