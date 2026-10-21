import type { MediaAsset } from './media';
import { projectRequest, type Project, type TargetPreset } from './projects';

interface SourceSpec {
  source_media_id: string;
  trim: { start_ms: number; end_ms: number };
  canvas: {
    preset: TargetPreset;
    fit: 'cover' | 'contain';
    focal_x: number;
    focal_y: number;
  };
}

export interface EditSpecV1 extends SourceSpec {
  schema_version: 1;
}

export interface TextOverlay {
  id: string;
  text: string;
  start_ms: number;
  end_ms: number;
  position: 'top' | 'center' | 'bottom';
  style: 'white_outline' | 'blue_outline' | 'white_plain';
  size: 'small' | 'medium' | 'large';
}

export interface AudioSpec {
  source_volume: number;
  background_audio_id: string | null;
  background_volume: number;
  background_loop: boolean;
  fade_in_ms: number;
  fade_out_ms: number;
}

export interface EditSpecV2 extends SourceSpec {
  schema_version: 2;
  text_overlays: TextOverlay[];
  audio: AudioSpec;
}

export type EditSpec = EditSpecV1 | EditSpecV2;

export type EditSpecRevision = EditSpec & {
  id: string;
  project_id: string;
  revision_number: number;
  created_at: string;
};

export const defaultAudio = (): AudioSpec => ({
  source_volume: 1,
  background_audio_id: null,
  background_volume: 0.15,
  background_loop: false,
  fade_in_ms: 0,
  fade_out_ms: 0,
});

export const editSpecApi = {
  get: (projectId: string, signal?: AbortSignal) =>
    projectRequest<EditSpecRevision>(`/${projectId}/edit-spec`, { signal }),
  save: (projectId: string, spec: EditSpec, expectedRevision: number, signal?: AbortSignal) =>
    projectRequest<EditSpecRevision>(`/${projectId}/edit-spec`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...spec, expected_revision: expectedRevision }),
      signal,
    }),
};

export function initialSpec(project: Project, asset: MediaAsset): EditSpecV2 {
  return {
    schema_version: 2,
    source_media_id: asset.id,
    trim: { start_ms: 0, end_ms: asset.duration_ms },
    canvas: {
      preset: project.target_preset ?? '9:16',
      fit: 'cover',
      focal_x: 0.5,
      focal_y: 0.5,
    },
    text_overlays: [],
    audio: defaultAudio(),
  };
}

export function revisionSpec(revision: EditSpecRevision): EditSpecV2 {
  return {
    // Normalize only the editable copy. GET snapshots and their version stay untouched.
    schema_version: 2,
    source_media_id: revision.source_media_id,
    trim: { ...revision.trim },
    canvas: { ...revision.canvas },
    text_overlays:
      revision.schema_version === 2 ? revision.text_overlays.map((text) => ({ ...text })) : [],
    audio: revision.schema_version === 2 ? { ...revision.audio } : defaultAudio(),
  };
}

export function textError(text: TextOverlay, trim: EditSpec['trim']): string {
  const value = text.text.trim();
  if (
    Array.from(value).length < 1 ||
    Array.from(value).length > 120 ||
    value.split('\n').length > 2
  )
    return 'use de 1 a 120 caracteres e até duas linhas.';
  if (/[\p{C}\p{Zl}\p{Zp}]/u.test(text.text.replaceAll('\n', '')))
    return 'remova os caracteres invisíveis ou de controle.';
  if (/<[^>]*>|%\{[^}]*\}/u.test(value)) return 'use texto simples, sem marcação ou expressões.';
  if (
    !Number.isSafeInteger(text.start_ms) ||
    !Number.isSafeInteger(text.end_ms) ||
    text.start_ms < trim.start_ms ||
    text.end_ms > trim.end_ms ||
    text.start_ms >= text.end_ms
  )
    return 'informe início e fim inteiros, dentro do trecho e com duração positiva.';
  return '';
}

export function editSpecError(spec: EditSpecV2): string {
  for (const [index, text] of spec.text_overlays.entries()) {
    const error = textError(text, spec.trim);
    if (error) return `Texto ${index + 1}: ${error}`;
  }
  const { source_volume, background_volume, fade_in_ms, fade_out_ms } = spec.audio;
  if (
    !Number.isFinite(source_volume) ||
    source_volume < 0 ||
    source_volume > 2 ||
    !Number.isFinite(background_volume) ||
    background_volume < 0 ||
    background_volume > 1
  )
    return 'Confira os volumes: original de 0 a 200% e música de 0 a 100%.';
  if (
    ![fade_in_ms, fade_out_ms].every(
      (value) => Number.isSafeInteger(value) && value >= 0 && value <= 3_000,
    ) ||
    fade_in_ms + fade_out_ms > spec.trim.end_ms - spec.trim.start_ms
  )
    return 'Use fades de 0 a 3000 ms cuja soma não ultrapasse a duração do trecho.';
  return '';
}
