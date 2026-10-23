import { ApiError, projectRequest, type Page, type TargetPreset } from './projects';

export type RenderProfile = 'standard' | 'high';
export type RenderStatus = 'queued' | 'running' | 'completed' | 'failed' | 'canceled';
export interface RenderInput {
  edit_spec_revision_id: string;
  profile: RenderProfile;
  idempotency_key: string;
}
export interface RenderJob {
  id: string;
  project_id: string;
  revision_id: string;
  revision_number: number;
  profile: RenderProfile;
  preset: TargetPreset;
  render_engine_version: string;
  status: RenderStatus;
  stage:
    | 'queued'
    | 'preparing'
    | 'encoding'
    | 'validating'
    | 'publishing'
    | 'completed'
    | 'failed'
    | 'canceled';
  attempts: number;
  max_attempts: number;
  progress_encoded_ms: number | null;
  progress_percent: number | null;
  cancel_requested_at: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
  error_code: string | null;
  artifact_available: boolean;
  checksum_sha256: string | null;
  byte_size: number | null;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  fps_num: number | null;
  fps_den: number | null;
  video_codec: string | null;
  audio_codec: string | null;
  status_url: string;
  file_url: string | null;
}

export const finalSizes: Record<TargetPreset, readonly [number, number]> = {
  '9:16': [1080, 1920],
  '1:1': [1080, 1080],
  '4:5': [1080, 1350],
  '16:9': [1920, 1080],
};
export const isRenderId = (value: string): boolean =>
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value);
export const renderIsActive = (job: RenderJob): boolean =>
  job.status === 'queued' || job.status === 'running';
const integer = (value: unknown, minimum = 0): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum;
const timestamp = (value: unknown): boolean =>
  typeof value === 'string' && Number.isFinite(Date.parse(value));
function validIds(...values: string[]) {
  if (!values.every(isRenderId)) throw new Error('INVALID_RENDER_IDENTITY');
}

function checkedJob(job: RenderJob, projectId: string): RenderJob {
  const base = `/api/v1/projects/${projectId}/renders/${job.id}`;
  const size = finalSizes[job.preset];
  const completed = job.status === 'completed';
  if (
    !isRenderId(job.id ?? '') ||
    job.project_id !== projectId ||
    !isRenderId(job.revision_id ?? '') ||
    !integer(job.revision_number, 1) ||
    !['standard', 'high'].includes(job.profile) ||
    !Object.hasOwn(finalSizes, job.preset) ||
    !size ||
    typeof job.render_engine_version !== 'string' ||
    !job.render_engine_version.length ||
    !['queued', 'running', 'completed', 'failed', 'canceled'].includes(job.status) ||
    (job.status === 'running'
      ? !['preparing', 'encoding', 'validating', 'publishing'].includes(job.stage)
      : job.stage !== job.status) ||
    !integer(job.attempts) ||
    !integer(job.max_attempts, 1) ||
    job.max_attempts > 2 ||
    job.attempts > job.max_attempts ||
    (job.progress_encoded_ms !== null && !integer(job.progress_encoded_ms)) ||
    (completed
      ? job.progress_percent !== 100
      : job.progress_percent !== null &&
        (!integer(job.progress_percent) ||
          job.progress_percent > 99 ||
          job.stage !== 'encoding')) ||
    !timestamp(job.created_at) ||
    !timestamp(job.updated_at) ||
    ![job.started_at, job.finished_at, job.cancel_requested_at].every(
      (value) => value === null || timestamp(value),
    ) ||
    (job.error_code !== null &&
      (typeof job.error_code !== 'string' || !/^render_[a-z0-9_]{1,57}$/.test(job.error_code))) ||
    typeof job.artifact_available !== 'boolean' ||
    job.status_url !== base ||
    (job.artifact_available
      ? !completed || job.file_url !== `${base}/file`
      : job.file_url !== null) ||
    (completed
      ? !/^[a-f0-9]{64}$/.test(job.checksum_sha256 ?? '') ||
        !integer(job.byte_size, 1) ||
        job.byte_size > 2_147_483_648 ||
        !integer(job.duration_ms, 1) ||
        job.width !== size?.[0] ||
        job.height !== size?.[1] ||
        job.fps_num !== 30 ||
        job.fps_den !== 1 ||
        job.video_codec !== 'h264' ||
        ![null, 'aac'].includes(job.audio_codec)
      : [
          job.checksum_sha256,
          job.byte_size,
          job.duration_ms,
          job.width,
          job.height,
          job.fps_num,
          job.fps_den,
          job.video_codec,
          job.audio_codec,
        ].some((value) => value !== null))
  )
    throw new Error('INVALID_RENDER_RESPONSE');
  return job;
}

export const rendersApi = {
  create: async (projectId: string, input: RenderInput, signal: AbortSignal) => {
    validIds(projectId, input.edit_spec_revision_id, input.idempotency_key);
    const job = checkedJob(
      await projectRequest<RenderJob>(`/${projectId}/renders`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
        signal,
      }),
      projectId,
    );
    if (job.revision_id !== input.edit_spec_revision_id || job.profile !== input.profile)
      throw new Error('INVALID_RENDER_IDENTITY');
    return job;
  },
  get: async (projectId: string, id: string, signal: AbortSignal) => {
    validIds(projectId, id);
    const job = checkedJob(
      await projectRequest<RenderJob>(`/${projectId}/renders/${id}`, { signal }),
      projectId,
    );
    if (job.id !== id) throw new Error('INVALID_RENDER_IDENTITY');
    return job;
  },
  cancel: async (projectId: string, id: string, signal: AbortSignal) => {
    validIds(projectId, id);
    const job = checkedJob(
      await projectRequest<RenderJob>(`/${projectId}/renders/${id}/cancel`, {
        method: 'POST',
        signal,
      }),
      projectId,
    );
    if (job.id !== id) throw new Error('INVALID_RENDER_IDENTITY');
    return job;
  },
  list: async (projectId: string, offset: number, signal: AbortSignal) => {
    validIds(projectId);
    const page = await projectRequest<Page<RenderJob>>(
      `/${projectId}/renders?limit=20&offset=${offset}`,
      { signal },
    );
    if (
      !Array.isArray(page.items) ||
      !integer(page.total) ||
      page.limit !== 20 ||
      page.offset !== offset ||
      page.items.length > 20 ||
      page.items.length > Math.max(0, page.total - offset) ||
      new Set(page.items.map((job) => job.id)).size !== page.items.length
    )
      throw new Error('INVALID_RENDER_PAGE');
    return { ...page, items: page.items.map((job) => checkedJob(job, projectId)) };
  },
};

export function renderError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'render_queue_full')
      return 'A fila local de exportações atingiu o limite. Aguarde os trabalhos terminarem e confira se o worker está ativo antes de tentar novamente.';
    if (error.code === 'render_active_limit')
      return 'Este projeto já tem uma exportação na fila ou em execução. Consulte o histórico e aguarde ou cancele esse trabalho.';
    if (error.code === 'render_not_cancelable')
      return 'A exportação já terminou e não pode ser cancelada. Consulte o estado atualizado.';
    if (error.code === 'idempotency_conflict' || error.code === 'render_engine_conflict')
      return 'A solicitação pertence a outros parâmetros ou a outra versão do motor. Consulte o histórico antes de solicitar outra exportação.';
    if (error.status === 404) return 'Esta exportação ou projeto não está mais disponível.';
    if (error.status === 422)
      return 'Não foi possível validar a revisão ou o perfil. Confira a edição salva.';
    if (error.status === 503)
      return 'A exportação está temporariamente indisponível. Verifique novamente em instantes.';
  }
  return 'Não foi possível confirmar a solicitação. Verifique a conexão e consulte novamente; o estado do trabalho foi preservado.';
}

export function renderFailure(code: string | null): string {
  if (code === 'render_timeout')
    return 'A renderização ultrapassou o tempo permitido. Os originais foram preservados.';
  if (code === 'render_text_unsupported')
    return 'A fonte local não possui todos os caracteres do texto. Ajuste os símbolos e salve uma nova revisão.';
  if (code === 'render_font_unavailable')
    return 'A fonte local dos textos está indisponível. Confira o ambiente antes de tentar novamente.';
  if (code === 'render_artifact_unavailable')
    return 'O resultado consta no histórico, mas o arquivo está indisponível ou não passou na conferência de integridade.';
  if (code === 'render_storage_unavailable' || code === 'render_storage_insufficient')
    return 'O armazenamento está indisponível ou sem espaço suficiente. Nenhum arquivo existente foi removido.';
  return 'Não foi possível concluir esta exportação. Os originais foram preservados. Uma nova tentativa depende de sua solicitação.';
}
