import { ApiError, projectRequest } from './projects';

export interface PreviewInput {
  edit_spec_revision_id: string;
  preview_start_ms: number;
  duration_ms: number;
}

export interface PreviewJob {
  id: string;
  project_id: string;
  revision_id: string;
  revision_number: number;
  preview_start_ms: number;
  duration_ms: number;
  status: 'queued' | 'running' | 'completed' | 'failed';
  stage: 'queued' | 'preparing' | 'encoding' | 'validating' | 'publishing' | 'completed' | 'failed';
  attempts: number;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  artifact_available: boolean;
  checksum_sha256: string | null;
  byte_size: number | null;
  status_url: string;
  file_url: string | null;
}

const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isPreviewId = (id: string): boolean => uuid.test(id);
export const previewIsActive = (job: PreviewJob): boolean =>
  job.status === 'queued' || job.status === 'running';

function checkedJob(value: PreviewJob, projectId: string): PreviewJob {
  const expectedUrl = `/api/v1/projects/${projectId}/previews/${value.id}`;
  if (
    !isPreviewId(value.id ?? '') ||
    value.project_id !== projectId ||
    !isPreviewId(value.revision_id ?? '') ||
    !['queued', 'running', 'completed', 'failed'].includes(value.status) ||
    ![
      'queued',
      'preparing',
      'encoding',
      'validating',
      'publishing',
      'completed',
      'failed',
    ].includes(value.stage) ||
    !Number.isSafeInteger(value.revision_number) ||
    value.revision_number < 1 ||
    !Number.isSafeInteger(value.preview_start_ms) ||
    value.preview_start_ms < 0 ||
    !Number.isSafeInteger(value.duration_ms) ||
    value.duration_ms < 1 ||
    value.duration_ms > 8_000 ||
    value.status_url !== expectedUrl ||
    typeof value.artifact_available !== 'boolean' ||
    (value.artifact_available
      ? value.status !== 'completed' ||
        value.file_url !== `${expectedUrl}/file` ||
        !/^[a-f0-9]{64}$/.test(value.checksum_sha256 ?? '') ||
        !Number.isSafeInteger(value.byte_size) ||
        (value.byte_size ?? 0) < 1
      : value.file_url !== null)
  )
    throw new Error('INVALID_PREVIEW_RESPONSE');
  return value;
}

export const previewsApi = {
  create: async (projectId: string, input: PreviewInput, signal: AbortSignal) =>
    checkedJob(
      await projectRequest<PreviewJob>(`/${projectId}/previews`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
        signal,
      }),
      projectId,
    ),
  get: async (projectId: string, id: string, signal: AbortSignal) => {
    const job = checkedJob(
      await projectRequest<PreviewJob>(`/${projectId}/previews/${id}`, { signal }),
      projectId,
    );
    if (job.id !== id) throw new Error('INVALID_PREVIEW_RESPONSE');
    return job;
  },
};

export function previewError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'preview_active_limit')
      return 'Este projeto atingiu o limite de prévias na fila ou em processamento. Aguarde uma delas terminar.';
    if (error.code === 'preview_not_ready')
      return 'A prévia ainda não está pronta. Consulte o estado novamente.';
    if (error.status === 404) return 'Esta prévia ou projeto não está mais disponível.';
    if (error.status === 422)
      return 'Não foi possível validar a janela ou a revisão. Confira os tempos e salve a edição.';
    if (error.status === 503)
      return 'A prévia está temporariamente indisponível. Verifique novamente em instantes.';
  }
  return 'Não foi possível consultar ou solicitar a prévia. Verifique a conexão e tente novamente.';
}

export function previewFailure(code: string | null): string {
  if (code === 'preview_text_unsupported')
    return 'A fonte local não possui todos os caracteres deste texto. Ajuste os símbolos e salve uma nova revisão. Nenhum texto foi removido silenciosamente.';
  if (code === 'preview_font_unavailable')
    return 'A fonte local necessária para os textos está indisponível. Verifique o ambiente antes de tentar novamente. A prévia não foi publicada sem os textos.';
  if (code === 'preview_timeout')
    return 'O processamento ultrapassou o tempo permitido. Tente uma janela menor.';
  if (code === 'preview_source_unavailable')
    return 'Uma fonte da composição está indisponível. Confira a biblioteca.';
  return 'Não foi possível concluir esta prévia. Os originais foram preservados. Você pode solicitar uma nova tentativa.';
}
