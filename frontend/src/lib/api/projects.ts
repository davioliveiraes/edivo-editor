export type TargetPreset = '9:16' | '1:1' | '4:5' | '16:9';

export interface Project {
  id: string;
  name: string;
  purpose: string | null;
  target_preset: TargetPreset | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectInput {
  name: string;
  purpose: string | null;
  target_preset: TargetPreset | null;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code = '',
  ) {
    super('EDIVO_REQUEST_FAILED');
  }
}

export function publicError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === 'media_duration_exceeded')
      return 'O vídeo ultrapassa a duração permitida. Escolha um arquivo mais curto.';
    if (error.code === 'probe_timeout')
      return 'A análise do vídeo demorou mais que o permitido. Tente um arquivo menor.';
    if (error.status === 404) return 'Este projeto ou vídeo não está mais disponível.';
    if (error.status === 413) return 'O arquivo ultrapassa o limite de upload do servidor.';
    if (error.status === 415) return 'O arquivo precisa ser um vídeo MP4 válido.';
    if (error.status === 422)
      return 'Não foi possível validar os dados ou o vídeo. Confira as informações e tente novamente.';
    if (error.status === 400)
      return 'O envio não pôde ser lido. Selecione um único MP4 e tente novamente.';
    if (error.status === 503)
      return 'Um serviço necessário está temporariamente indisponível. Tente novamente em instantes.';
  }
  return 'Não foi possível concluir a solicitação. Verifique a conexão e tente novamente.';
}

export function errorFromBody(status: number, body: unknown): ApiError {
  const code =
    typeof body === 'object' && body !== null && 'code' in body && typeof body.code === 'string'
      ? body.code
      : '';
  return new ApiError(status, code);
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`/api/v1/projects${path}`, {
    ...options,
    cache: 'no-store',
    signal: options.signal ?? AbortSignal.timeout(15_000),
    headers: { Accept: 'application/json', ...options.headers },
  });
  if (response.status === 204 && response.ok) return undefined as T;
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) throw errorFromBody(response.status, body);
  if (body === null || typeof body !== 'object') throw new Error('INVALID_RESPONSE');
  return body as T;
}

export const projectsApi = {
  list: (offset: number, signal: AbortSignal) =>
    request<Page<Project>>(`?limit=20&offset=${offset}`, { signal }),
  get: (id: string, signal: AbortSignal) => request<Project>(`/${id}`, { signal }),
  create: (input: ProjectInput) =>
    request<Project>('', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    }),
  update: (id: string, input: ProjectInput) =>
    request<Project>(`/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(input),
    }),
  remove: (id: string) => request<void>(`/${id}`, { method: 'DELETE' }),
};

export { request as projectRequest };
