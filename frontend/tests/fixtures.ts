import { vi } from 'vitest';
import type { Project } from '@/lib/api/projects';
import type { MediaAsset } from '@/lib/api/media';

export const project: Project = {
  id: 'b85f4c32-1171-41bb-9d52-fd83b8d91db4',
  name: 'Campanha de primavera',
  purpose: 'Apresentar a nova coleção.',
  target_preset: '9:16',
  created_at: '2026-10-17T10:00:00Z',
  updated_at: '2026-10-17T10:00:00Z',
};

export const asset: MediaAsset = {
  id: '01e9125d-c053-45bb-afd3-e5e5618e7158',
  project_id: project.id,
  kind: 'source_video',
  original_filename: 'colecao.mp4',
  checksum_sha256: 'a'.repeat(64),
  byte_size: 1_048_576,
  mime_detected: 'video/mp4',
  duration_ms: 1_501,
  width: 1920,
  height: 1080,
  fps_num: 30_000,
  fps_den: 1_001,
  video_codec: 'h264',
  has_audio: true,
  audio_codec: 'aac',
  created_at: '2026-10-17T10:10:00Z',
};

export const page = <T>(items: T[], total = items.length, offset = 0) => ({
  items,
  total,
  limit: 20,
  offset,
});
export const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });

export class FakeXhr {
  static instances: FakeXhr[] = [];
  upload: { onprogress: ((event: ProgressEvent) => void) | null; onload: (() => void) | null } = {
    onprogress: null,
    onload: null,
  };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  onabort: (() => void) | null = null;
  status = 0;
  responseText = '';
  timeout = 0;
  open = vi.fn();
  setRequestHeader = vi.fn();
  send = vi.fn();
  abort = vi.fn(() => this.onabort?.());

  constructor() {
    FakeXhr.instances.push(this);
  }
  respond(body: unknown, status = 201) {
    this.status = status;
    this.responseText = JSON.stringify(body);
    this.onload?.();
  }
}
