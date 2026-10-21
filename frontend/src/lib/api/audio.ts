import { uploadAsset, type UploadProgress } from './media';
import { ApiError, publicError } from './projects';

export interface AudioAsset {
  id: string;
  project_id: string;
  original_filename: string;
  checksum_sha256: string;
  byte_size: number;
  mime_detected: 'audio/mpeg';
  duration_ms: number;
  codec: 'mp3';
  sample_rate: number;
  channels: number;
  created_at: string;
}

export const uploadAudio = (
  projectId: string,
  file: File,
  signal: AbortSignal,
  onProgress: (progress: UploadProgress) => void,
) => uploadAsset<AudioAsset>(projectId, 'audio', file, signal, onProgress);

export function audioError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'audio_duration_exceeded')
      return 'A música ultrapassa a duração permitida. Escolha um MP3 mais curto.';
    if (error.code === 'probe_timeout')
      return 'A análise do áudio demorou mais que o permitido. Tente novamente com outro MP3.';
    if (error.status === 400) return 'Selecione um único arquivo MP3 e tente novamente.';
    if (error.status === 404) return 'Este projeto ou música não está mais disponível.';
    if (error.status === 415) return 'O arquivo precisa ser um MP3 válido, sem stream de vídeo.';
    if (error.status === 422)
      return 'Não foi possível validar o MP3. Confira formato, conteúdo e duração.';
  }
  return publicError(error);
}
