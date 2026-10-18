import type { MediaAsset } from './media';
import { projectRequest, type Project, type TargetPreset } from './projects';

export interface EditSpec {
  schema_version: 1;
  source_media_id: string;
  trim: { start_ms: number; end_ms: number };
  canvas: {
    preset: TargetPreset;
    fit: 'cover' | 'contain';
    focal_x: number;
    focal_y: number;
  };
}

export interface EditSpecRevision extends EditSpec {
  id: string;
  project_id: string;
  revision_number: number;
  created_at: string;
}

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

export function initialSpec(project: Project, asset: MediaAsset): EditSpec {
  return {
    schema_version: 1,
    source_media_id: asset.id,
    trim: { start_ms: 0, end_ms: asset.duration_ms },
    canvas: {
      preset: project.target_preset ?? '9:16',
      fit: 'cover',
      focal_x: 0.5,
      focal_y: 0.5,
    },
  };
}

export function revisionSpec(revision: EditSpecRevision): EditSpec {
  return {
    schema_version: revision.schema_version,
    source_media_id: revision.source_media_id,
    trim: { ...revision.trim },
    canvas: { ...revision.canvas },
  };
}
