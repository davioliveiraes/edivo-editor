import { errorFromBody, projectRequest, type Page } from './projects';

export interface MediaAsset {
  id: string;
  project_id: string;
  kind: 'source_video';
  original_filename: string;
  checksum_sha256: string;
  byte_size: number;
  mime_detected: string;
  duration_ms: number;
  width: number;
  height: number;
  fps_num: number;
  fps_den: number;
  video_codec: string | null;
  has_audio: boolean;
  audio_codec: string | null;
  created_at: string;
}

export type UploadProgress =
  { stage: 'transferring'; percent: number | null } | { stage: 'analyzing' };

export const listMedia = (projectId: string, offset: number, signal: AbortSignal) =>
  projectRequest<Page<MediaAsset>>(`/${projectId}/media?limit=20&offset=${offset}`, { signal });

export function uploadAsset<T>(
  projectId: string,
  kind: 'media' | 'audio',
  file: File,
  signal: AbortSignal,
  onProgress: (progress: UploadProgress) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const abort = () => xhr.abort();
    const finish = () => signal.removeEventListener('abort', abort);
    xhr.open('POST', `/api/v1/projects/${projectId}/${kind}`);
    xhr.setRequestHeader('Accept', 'application/json');
    // A single request includes transfer and bounded server-side inspection.
    xhr.timeout = 30 * 60 * 1_000;
    xhr.upload.onprogress = (event) => {
      if (signal.aborted) return;
      onProgress({
        stage: 'transferring',
        percent:
          event.lengthComputable && event.total > 0
            ? Math.min(100, Math.round((event.loaded / event.total) * 100))
            : null,
      });
    };
    // Completion of transport is distinct from a successful import.
    xhr.upload.onload = () => {
      if (!signal.aborted) onProgress({ stage: 'analyzing' });
    };
    xhr.onload = () => {
      finish();
      let body: unknown;
      try {
        body = JSON.parse(xhr.responseText) as unknown;
      } catch {
        reject(new Error('INVALID_UPLOAD_RESPONSE'));
        return;
      }
      if (xhr.status !== 201) {
        reject(errorFromBody(xhr.status, body));
      } else if (typeof body === 'object' && body !== null && 'id' in body) {
        resolve(body as T);
      } else reject(new Error('INVALID_UPLOAD_RESPONSE'));
    };
    xhr.onerror = () => {
      finish();
      reject(new Error('UPLOAD_CONNECTION_FAILED'));
    };
    xhr.ontimeout = () => {
      finish();
      reject(new Error('UPLOAD_TIMEOUT'));
    };
    xhr.onabort = () => {
      finish();
      reject(new DOMException('Upload aborted', 'AbortError'));
    };
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) {
      finish();
      reject(new DOMException('Upload aborted', 'AbortError'));
      return;
    }
    const body = new FormData();
    body.append('file', file);
    xhr.send(body);
  });
}

export const uploadMedia = (
  projectId: string,
  file: File,
  signal: AbortSignal,
  onProgress: (progress: UploadProgress) => void,
) => uploadAsset<MediaAsset>(projectId, 'media', file, signal, onProgress);
