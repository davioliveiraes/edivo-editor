import { describe, expect, it, vi } from 'vitest';
import { ApiError } from '@/lib/api/projects';
import {
  finalSizes,
  isRenderId,
  renderError,
  renderFailure,
  renderIsActive,
  rendersApi,
  type RenderInput,
  type RenderJob,
} from '@/lib/api/renders';
import { project } from './fixtures';

const jobId = '79a1e1e9-2692-4337-b6ad-45274f7d8231';
const revisionId = '0f4a1fa8-8006-4989-abde-35aa6b039328';
const anotherId = '638711da-fd7e-455b-8265-b9738c7a040b';
const base = `/api/v1/projects/${project.id}/renders/${jobId}`;
const input: RenderInput = {
  edit_spec_revision_id: revisionId,
  profile: 'standard',
  idempotency_key: '20eb02b4-4603-472d-9392-93529815663c',
};
const queued: RenderJob = {
  id: jobId,
  project_id: project.id,
  revision_id: revisionId,
  revision_number: 2,
  profile: 'standard',
  preset: '9:16',
  render_engine_version: 'r05-1',
  status: 'queued',
  stage: 'queued',
  attempts: 0,
  max_attempts: 2,
  progress_encoded_ms: null,
  progress_percent: null,
  cancel_requested_at: null,
  created_at: '2026-10-22T15:00:00Z',
  updated_at: '2026-10-22T15:00:00Z',
  started_at: null,
  finished_at: null,
  error_code: null,
  artifact_available: false,
  checksum_sha256: null,
  byte_size: null,
  duration_ms: null,
  width: null,
  height: null,
  fps_num: null,
  fps_den: null,
  video_codec: null,
  audio_codec: null,
  status_url: base,
  file_url: null,
};
const running: RenderJob = {
  ...queued,
  status: 'running',
  stage: 'encoding',
  attempts: 1,
  started_at: queued.created_at,
  progress_encoded_ms: 2400,
  progress_percent: 20,
};
const completed: RenderJob = {
  ...running,
  status: 'completed',
  stage: 'completed',
  progress_encoded_ms: 12000,
  progress_percent: 100,
  finished_at: '2026-10-22T15:00:20Z',
  artifact_available: true,
  checksum_sha256: 'e'.repeat(64),
  byte_size: 343652,
  duration_ms: 12000,
  width: 1080,
  height: 1920,
  fps_num: 30,
  fps_den: 1,
  video_codec: 'h264',
  audio_codec: 'aac',
  file_url: `${base}/file`,
};
const controller = () => new AbortController();
function respond(body: unknown, status = 200) {
  // Deliberately validate decoded untrusted values too: Number overflow in JSON
  // (e.g. 1e999) must not become a progress or size accepted by the client.
  const mock = vi
    .fn()
    .mockResolvedValue({ ok: status >= 200 && status < 300, status, json: async () => body });
  vi.stubGlobal('fetch', mock);
  return mock;
}
const fetchJob = () => rendersApi.get(project.id, jobId, controller().signal);

describe('render client endpoints and saved identities', () => {
  it('creates only a saved revision/profile/key request, forwarding abort and no-store', async () => {
    const transport = respond(queued, 202),
      signal = controller().signal;
    expect(await rendersApi.create(project.id, input, signal)).toEqual(queued);
    const [url, options] = transport.mock.calls[0]!;
    expect(url).toBe(`/api/v1/projects/${project.id}/renders`);
    expect(options).toMatchObject({
      method: 'POST',
      signal,
      cache: 'no-store',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    });
    expect(JSON.parse(options.body)).toEqual(input);
    expect(options.body).not.toContain('storage_key');
    expect(options.body).not.toContain('source_media_id');
  });

  it('accepts replay of a completed immutable revision with HTTP200', async () => {
    respond(completed);
    expect(await rendersApi.create(project.id, input, controller().signal)).toEqual(completed);
  });

  it('fetches a single job with the exact relative URL and supplied signal', async () => {
    const transport = respond(running),
      signal = controller().signal;
    expect(await rendersApi.get(project.id, jobId, signal)).toEqual(running);
    expect(transport).toHaveBeenCalledWith(
      base,
      expect.objectContaining({ signal, cache: 'no-store' }),
    );
  });

  it('persists cancellation through POST without any process command', async () => {
    const canceled: RenderJob = {
      ...queued,
      status: 'canceled',
      stage: 'canceled',
      cancel_requested_at: queued.created_at,
      finished_at: queued.created_at,
    };
    const transport = respond(canceled),
      signal = controller().signal;
    expect(await rendersApi.cancel(project.id, jobId, signal)).toEqual(canceled);
    expect(transport).toHaveBeenCalledWith(
      `${base}/cancel`,
      expect.objectContaining({ method: 'POST', signal }),
    );
    expect(transport.mock.calls[0]![1].body).toBeUndefined();
  });

  it.each(['get', 'cancel'] as const)(
    'rejects another job identity returned to %s',
    async (method) => {
      respond({ ...queued, id: anotherId, status_url: base.replace(jobId, anotherId) });
      await expect(rendersApi[method](project.id, jobId, controller().signal)).rejects.toThrow(
        'INVALID_RENDER_IDENTITY',
      );
    },
  );

  it.each([{ revision_id: anotherId }, { profile: 'high' }])(
    'rejects create identity mismatch %j',
    async (patch) => {
      respond({ ...queued, ...patch });
      await expect(rendersApi.create(project.id, input, controller().signal)).rejects.toThrow(
        'INVALID_RENDER_IDENTITY',
      );
    },
  );

  it.each(['../storage', 'http://external.test/video.mp4', '/data/private', 'not-a-uuid'])(
    'blocks arbitrary IDs before fetching: %s',
    async (bad) => {
      const transport = respond(queued);
      await expect(rendersApi.get(project.id, bad, controller().signal)).rejects.toThrow(
        'INVALID_RENDER_IDENTITY',
      );
      await expect(rendersApi.cancel(bad, jobId, controller().signal)).rejects.toThrow(
        'INVALID_RENDER_IDENTITY',
      );
      await expect(
        rendersApi.create(project.id, { ...input, idempotency_key: bad }, controller().signal),
      ).rejects.toThrow('INVALID_RENDER_IDENTITY');
      expect(transport).not.toHaveBeenCalled();
    },
  );
});

describe('render DTO trust boundary', () => {
  it.each([
    { project_id: anotherId },
    { revision_id: 'invalid' },
    { revision_number: 0 },
    { revision_number: 1.5 },
    { revision_number: '2' },
    { profile: 'ultra' },
    { preset: '__proto__' },
    { preset: 'constructor' },
    { preset: '4:3' },
    { render_engine_version: '' },
    { render_engine_version: null },
    { attempts: -1 },
    { attempts: 3 },
    { max_attempts: 3 },
    { max_attempts: 0 },
    { attempts: 0.5 },
    { created_at: 'not-a-date' },
    { updated_at: null },
    { started_at: false },
    { cancel_requested_at: '/private/time' },
    { artifact_available: 'false' },
    { status: 'exporting' },
    { stage: 'running' },
    { error_code: '/data/storage/private.mp4' },
    { error_code: 'raw database password' },
    { status_url: 'http://external.test/job' },
    { status_url: `/api/v1/projects/${anotherId}/renders/${jobId}` },
    { status_url: `${base}/../file` },
    { file_url: '/data/storage/final.mp4' },
    { artifact_available: true, file_url: `${base}/file` },
    { byte_size: 100 },
  ])('rejects malformed or cross-project queued metadata %j', async (patch) => {
    respond({ ...queued, ...patch });
    await expect(fetchJob()).rejects.toThrow('INVALID_RENDER_RESPONSE');
  });

  it.each([
    { progress_percent: -1 },
    { progress_percent: 100 },
    { progress_percent: 1.2 },
    { progress_percent: Number.NaN },
    { progress_percent: Number.POSITIVE_INFINITY },
    { progress_percent: '20' },
    { progress_encoded_ms: -1 },
    { progress_encoded_ms: Number.POSITIVE_INFINITY },
    { progress_encoded_ms: 1.2 },
    { stage: 'validating', progress_percent: 99 },
    { stage: 'publishing', progress_percent: 99 },
    { stage: 'completed', progress_percent: 100 },
  ])('rejects invalid or premature progress %j', async (patch) => {
    respond({ ...running, ...patch });
    await expect(fetchJob()).rejects.toThrow('INVALID_RENDER_RESPONSE');
  });

  it.each([0, 99, null])('accepts real encoding progress %s', async (progress_percent) => {
    respond({ ...running, progress_percent });
    expect((await fetchJob()).progress_percent).toBe(progress_percent);
  });

  it.each(['preparing', 'validating', 'publishing'] as const)(
    'keeps %s indeterminate with measured time retained',
    async (stage) => {
      respond({ ...running, stage, progress_percent: null });
      expect(await fetchJob()).toMatchObject({
        stage,
        progress_percent: null,
        progress_encoded_ms: 2400,
      });
    },
  );

  it.each([
    { checksum_sha256: null },
    { checksum_sha256: 'UPPERCASE'.repeat(8) },
    { checksum_sha256: 'e'.repeat(63) },
    { byte_size: 0 },
    { byte_size: 2147483649 },
    { byte_size: Number.POSITIVE_INFINITY },
    { duration_ms: 0 },
    { duration_ms: 12000.5 },
    { width: 540 },
    { height: 960 },
    { fps_num: 24 },
    { fps_den: 0 },
    { video_codec: 'mpeg4' },
    { audio_codec: 'mp3' },
    { progress_percent: 99 },
    { file_url: 'https://external.test/final.mp4' },
    { file_url: `${base}/file?path=private` },
    { file_url: `/api/v1/projects/${anotherId}/renders/${jobId}/file` },
  ])('rejects invalid completed artifact metadata %j', async (patch) => {
    respond({ ...completed, ...patch });
    await expect(fetchJob()).rejects.toThrow('INVALID_RENDER_RESPONSE');
  });

  it.each(Object.entries(finalSizes))(
    'accepts final dimensions %s for 12s MP4',
    async (preset, size) => {
      respond({ ...completed, preset, width: size[0], height: size[1] });
      expect(await fetchJob()).toMatchObject({ preset, duration_ms: 12000, fps_num: 30 });
    },
  );

  it('accepts a silent export with no AAC stream', async () => {
    respond({ ...completed, audio_codec: null });
    expect((await fetchJob()).audio_codec).toBeNull();
  });

  it('preserves completed history metadata when the artifact is unavailable', async () => {
    respond({
      ...completed,
      artifact_available: false,
      file_url: null,
      error_code: 'render_artifact_unavailable',
    });
    expect(await fetchJob()).toMatchObject({
      status: 'completed',
      progress_percent: 100,
      checksum_sha256: completed.checksum_sha256,
      artifact_available: false,
      file_url: null,
    });
  });

  it.each(['failed', 'canceled'] as const)(
    'accepts terminal %s without an artifact or fake percent',
    async (status) => {
      respond({
        ...running,
        status,
        stage: status,
        progress_percent: null,
        finished_at: queued.created_at,
        ...(status === 'canceled'
          ? { cancel_requested_at: queued.created_at }
          : { error_code: 'render_timeout' }),
      });
      expect(await fetchJob()).toMatchObject({ status, artifact_available: false, file_url: null });
    },
  );

  it.each([null, '<html>proxy failure</html>', false])(
    'rejects non-object response %j',
    async (body) => {
      respond(body);
      await expect(fetchJob()).rejects.toThrow();
    },
  );
});

describe('paginated render history', () => {
  it('requests limit20/offset20 and preserves the supplied cancellation signal', async () => {
    const transport = respond({ items: [completed], total: 21, limit: 20, offset: 20 });
    const signal = controller().signal;
    expect(await rendersApi.list(project.id, 20, signal)).toEqual({
      items: [completed],
      total: 21,
      limit: 20,
      offset: 20,
    });
    expect(transport).toHaveBeenCalledWith(
      `/api/v1/projects/${project.id}/renders?limit=20&offset=20`,
      expect.objectContaining({ signal, cache: 'no-store' }),
    );
  });

  it('accepts an empty project history', async () => {
    respond({ items: [], total: 0, limit: 20, offset: 0 });
    expect((await rendersApi.list(project.id, 0, controller().signal)).items).toEqual([]);
  });

  it.each([
    { items: null },
    { total: -1 },
    { total: 1.2 },
    { total: '2' },
    { total: Number.POSITIVE_INFINITY },
    { limit: 19 },
    { offset: 1 },
    { items: [queued, queued], total: 2 },
    { items: [queued], total: 0 },
  ])('rejects malformed pagination or duplicate IDs %j', async (patch) => {
    respond({ items: [queued], total: 1, limit: 20, offset: 0, ...patch });
    await expect(rendersApi.list(project.id, 0, controller().signal)).rejects.toThrow(
      'INVALID_RENDER_PAGE',
    );
  });

  it('rejects more than twenty unique history records in a page', async () => {
    const items = Array.from({ length: 21 }, (_, index) => {
      const id = `${index.toString(16).padStart(8, '0')}-2692-4337-b6ad-45274f7d8231`;
      return { ...queued, id, status_url: `/api/v1/projects/${project.id}/renders/${id}` };
    });
    respond({ items, total: 21, limit: 20, offset: 0 });
    await expect(rendersApi.list(project.id, 0, controller().signal)).rejects.toThrow(
      'INVALID_RENDER_PAGE',
    );
  });

  it('checks every history item for project ownership and URL safety', async () => {
    respond({ items: [{ ...queued, project_id: anotherId }], total: 1, limit: 20, offset: 0 });
    await expect(rendersApi.list(project.id, 0, controller().signal)).rejects.toThrow(
      'INVALID_RENDER_RESPONSE',
    );
  });
});

describe('aborts and sanitized public errors', () => {
  it('forwards abort without converting it into a failed job or new request', async () => {
    const aborter = controller();
    const transport = vi.fn(
      (_url: string, options: RequestInit) =>
        new Promise((_resolve, reject) => {
          options.signal!.addEventListener('abort', () => reject(options.signal!.reason), {
            once: true,
          });
        }),
    );
    vi.stubGlobal('fetch', transport);
    const pending = rendersApi.get(project.id, jobId, aborter.signal);
    aborter.abort();
    await expect(pending).rejects.toMatchObject({ name: 'AbortError' });
    expect(transport).toHaveBeenCalledTimes(1);
  });

  it.each([
    [409, 'render_active_limit'],
    [409, 'render_queue_full'],
    [409, 'render_not_cancelable'],
    [409, 'idempotency_conflict'],
    [409, 'render_engine_conflict'],
    [404, 'render_not_found'],
    [422, 'validation_error'],
    [503, 'database_unavailable'],
  ])('keeps HTTP%s/%s but never displays raw server message', async (status, code) => {
    respond(
      {
        code,
        message: '/private/path postgresql://admin:secret@private/db',
        request_id: 'qa-safe',
      },
      status,
    );
    const error = await fetchJob().catch((value: unknown) => value);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status, code });
    const message = renderError(error);
    expect(message).not.toMatch(/private|postgresql|secret|admin|stack/i);
    expect(message.length).toBeGreaterThan(20);
    if (code === 'render_queue_full')
      expect(message).toContain('fila local de exportações atingiu o limite');
  });

  it.each([
    'render_timeout',
    'render_text_unsupported',
    'render_font_unavailable',
    'render_artifact_unavailable',
    'render_storage_unavailable',
    'render_storage_insufficient',
    '/private/raw-stderr',
    null,
  ])('maps worker code %s to controlled Portuguese feedback', (code) => {
    const message = renderFailure(code);
    expect(message.length).toBeGreaterThan(20);
    expect(message).not.toMatch(/private|stderr|postgresql|stack/i);
  });

  it('does not expose raw network exception text', () => {
    expect(renderError(new TypeError('/private/token=secret'))).not.toMatch(
      /private|secret|token=/i,
    );
  });
});

describe('render status helpers', () => {
  it.each([
    [queued, true],
    [running, true],
    [completed, false],
    [{ ...queued, status: 'failed' }, false],
    [{ ...queued, status: 'canceled' }, false],
  ] as const)('distinguishes active status $0.status', (job, active) => {
    expect(renderIsActive(job as RenderJob)).toBe(active);
  });
  it('recognizes UUID identity without accepting filesystem paths', () => {
    expect(isRenderId(jobId)).toBe(true);
    expect(isRenderId(jobId.toUpperCase())).toBe(true);
    expect(isRenderId('../renders/final.mp4')).toBe(false);
  });
});
