import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ExportPanel } from '@/features/editor/export-panel';
import { EditorWorkspace } from '@/features/editor/editor-workspace';
import { VideoPlayer } from '@/features/editor/video-player';
import type { EditSpecRevision } from '@/lib/api/edit-spec';
import type { RenderInput, RenderJob } from '@/lib/api/renders';
import { asset, json, page, project } from './fixtures';

const revision: EditSpecRevision = {
  id: 'b793155e-7047-482d-a140-ce384dff9bdd',
  project_id: project.id,
  revision_number: 2,
  created_at: project.created_at,
  schema_version: 1,
  source_media_id: asset.id,
  trim: { start_ms: 1000, end_ms: 13000 },
  canvas: { preset: '9:16', fit: 'cover', focal_x: 0.5, focal_y: 0.5 },
};
const jobId = 'a4aa2201-859b-4c37-8214-c73e57a3e8e7';
const olderId = 'a2386d10-7047-482d-a140-ce384dff9bdd';
const storageKey = `edivo:render:${project.id}`;
const selectedKey = `edivo:render-result:${project.id}`;
const pendingKey = `edivo:render-request:${project.id}`;
const base = `/api/v1/projects/${project.id}/renders`;

function job(status: RenderJob['status'] = 'queued', patch: Partial<RenderJob> = {}): RenderJob {
  const done = status === 'completed';
  const id = patch.id ?? jobId;
  return {
    id,
    project_id: project.id,
    revision_id: revision.id,
    revision_number: 2,
    profile: 'standard',
    preset: '9:16',
    render_engine_version: 'r05-1',
    status,
    stage: status === 'running' ? 'encoding' : status,
    attempts: status === 'queued' ? 0 : 1,
    max_attempts: 2,
    progress_encoded_ms: done ? 12000 : null,
    progress_percent: done ? 100 : null,
    cancel_requested_at: status === 'canceled' ? project.created_at : null,
    created_at: project.created_at,
    updated_at: project.updated_at,
    started_at: status === 'queued' ? null : project.created_at,
    finished_at: ['completed', 'failed', 'canceled'].includes(status) ? project.created_at : null,
    error_code: status === 'failed' ? 'render_timeout' : null,
    artifact_available: done,
    checksum_sha256: done ? 'c'.repeat(64) : null,
    byte_size: done ? 22000 : null,
    duration_ms: done ? 12000 : null,
    width: done ? 1080 : null,
    height: done ? 1920 : null,
    fps_num: done ? 30 : null,
    fps_den: done ? 1 : null,
    video_codec: done ? 'h264' : null,
    audio_codec: done ? 'aac' : null,
    status_url: `${base}/${id}`,
    file_url: done ? `${base}/${id}/file` : null,
    ...patch,
  };
}

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (
    this: HTMLMediaElement,
  ) {
    Object.defineProperty(this, 'paused', { configurable: true, value: true });
    fireEvent.pause(this);
  });
});
afterEach(() => vi.useRealTimers());

function server(options: { history?: RenderJob[]; post?: RenderJob; statuses?: RenderJob[] } = {}) {
  let history = options.history ?? [];
  let post = options.post ?? job();
  let statuses = options.statuses ?? [job('completed')];
  let getCount = 0;
  let createFailure: number | 'network' = 0;
  let getFailure = 0;
  let historyFailure = 0;
  let cancelResponse = job('canceled');
  let cancelFailure = 0;
  const posts: RenderInput[] = [];
  const cancels: string[] = [];
  const gets: string[] = [];
  const historyOffsets: number[] = [];
  const signals: AbortSignal[] = [];
  const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.signal) signals.push(init.signal);
    if (url.includes('/renders?')) {
      const offset = Number(new URL(url, 'http://localhost').searchParams.get('offset'));
      historyOffsets.push(offset);
      return historyFailure
        ? json({ code: 'unavailable', message: '/private/history' }, historyFailure)
        : json(page(history.slice(offset, offset + 20), history.length, offset));
    }
    if (url.endsWith('/cancel')) {
      cancels.push(url);
      return cancelFailure
        ? json({ code: 'render_not_cancelable', message: '/private/cancel' }, cancelFailure)
        : json(cancelResponse, cancelResponse.status === 'running' ? 202 : 200);
    }
    if (url === base && init?.method === 'POST') {
      const input = JSON.parse(init.body as string) as RenderInput;
      posts.push(input);
      if (createFailure === 'network') throw new TypeError('/private/network');
      if (createFailure)
        return json(
          {
            code: createFailure === 409 ? 'render_active_limit' : 'unavailable',
            message: '/private/post',
          },
          createFailure,
        );
      return json(
        { ...post, profile: input.profile, revision_id: input.edit_spec_revision_id },
        post.status === 'completed' ? 200 : 202,
      );
    }
    if (url === `${base}/${jobId}`) {
      gets.push(url);
      const next = statuses[Math.min(getCount++, statuses.length - 1)]!;
      return getFailure
        ? json({ code: 'unavailable', message: '/private/get' }, getFailure)
        : json(next);
    }
    const older = history.find((entry) => url === entry.status_url);
    if (older) {
      gets.push(url);
      return json(older);
    }
    if (url.endsWith('/edit-spec')) return json(revision);
    if (url.includes('/media?')) return json(page([{ ...asset, duration_ms: 14000 }]));
    if (url.includes('/audio?')) return json(page([]));
    if (url.endsWith(project.id)) return json(project);
    return json({}, 404);
  });
  vi.stubGlobal('fetch', fetchMock);
  return {
    posts,
    cancels,
    gets,
    historyOffsets,
    signals,
    fetchMock,
    createFailure: (value: number | 'network') => {
      createFailure = value;
    },
    getFailure: (value: number) => {
      getFailure = value;
    },
    historyFailure: (value: number) => {
      historyFailure = value;
    },
    statuses: (value: RenderJob[]) => {
      statuses = value;
      getCount = 0;
    },
    post: (value: RenderJob) => {
      post = value;
    },
    history: (value: RenderJob[]) => {
      history = value;
    },
    cancel: (value: RenderJob, failure = 0) => {
      cancelResponse = value;
      cancelFailure = failure;
    },
  };
}
function panel(props: Partial<Parameters<typeof ExportPanel>[0]> = {}) {
  return render(
    <ExportPanel
      projectId={project.id}
      projectName={project.name}
      revision={revision}
      dirty={false}
      busy={false}
      {...props}
    />,
  );
}
const generate = () => screen.getByRole('button', { name: 'Gerar vídeo' });
const selected = () => within(screen.getByRole('region', { name: 'Exportação selecionada' }));
const tracking = () => within(screen.getByRole('region', { name: 'Acompanhamento da exportação' }));
async function ready() {
  await waitFor(() => expect(screen.queryByText('Carregando histórico…')).not.toBeInTheDocument());
}
async function create() {
  await userEvent.click(generate());
}

describe('Explicit export requests', () => {
  it('shows the export panel and requires an explicitly saved clean revision', async () => {
    const api = server();
    const view = panel({ revision: null });
    await ready();
    expect(screen.getByRole('heading', { name: 'Exportar vídeo' })).toBeVisible();
    expect(generate()).toBeDisabled();
    view.rerender(
      <ExportPanel
        projectId={project.id}
        projectName={project.name}
        revision={revision}
        dirty
        busy={false}
      />,
    );
    expect(generate()).toBeDisabled();
    expect(
      screen.getByText(/As alterações locais não serão enviadas automaticamente/),
    ).toBeVisible();
    fireEvent.click(generate());
    expect(api.posts).toHaveLength(0);
    expect(api.fetchMock.mock.calls.every(([, init]) => init?.method !== 'PUT')).toBe(true);
  });

  it.each([
    ['9:16', '1080 × 1920'],
    ['1:1', '1080 × 1080'],
    ['4:5', '1080 × 1350'],
    ['16:9', '1920 × 1080'],
  ] as const)('shows final dimensions from saved preset %s', async (preset, dimensions) => {
    server();
    panel({ revision: { ...revision, canvas: { ...revision.canvas, preset } } });
    await ready();
    expect(screen.getByText(dimensions)).toBeVisible();
    expect(screen.getByText(/H.264 · 30 FPS/)).toBeVisible();
  });

  it('submits only saved identity, selected profile and UUID key, without autosaving', async () => {
    const api = server({ statuses: [job('running', { profile: 'high' })] });
    panel();
    await ready();
    await userEvent.selectOptions(screen.getByLabelText('Perfil de qualidade'), 'high');
    await create();
    await waitFor(() => expect(api.posts).toHaveLength(1));
    expect(api.posts[0]).toEqual({
      edit_spec_revision_id: revision.id,
      profile: 'high',
      idempotency_key: expect.stringMatching(/^[a-f0-9-]{36}$/),
    });
    expect(api.posts[0]).not.toHaveProperty('trim');
    expect(localStorage.getItem(storageKey)).toBe(jobId);
  });

  it('preserves an uncertain POST identity across an explicit retry', async () => {
    const api = server();
    api.createFailure('network');
    panel();
    await ready();
    await create();
    expect(await screen.findByRole('alert')).toHaveTextContent('Não foi possível confirmar');
    const persisted = JSON.parse(localStorage.getItem(pendingKey)!);
    expect(persisted.input).toEqual(api.posts[0]);
    api.createFailure(0);
    await userEvent.click(screen.getByRole('button', { name: 'Consultar solicitação novamente' }));
    await waitFor(() => expect(api.posts).toHaveLength(2));
    expect(api.posts[1]).toEqual(api.posts[0]);
    expect(localStorage.getItem(pendingKey)).toBeNull();
    expect(screen.queryByText('/private/network')).not.toBeInTheDocument();
  });

  it('restores an uncertain request after reload without automatically repeating POST', async () => {
    const input = {
      edit_spec_revision_id: revision.id,
      profile: 'standard',
      idempotency_key: olderId,
    };
    localStorage.setItem(pendingKey, JSON.stringify({ input, revision_number: 2 }));
    const api = server();
    panel();
    await ready();
    expect(api.posts).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: 'Consultar solicitação novamente' }));
    expect(api.posts).toEqual([input]);
  });

  it('keeps monitoring active B while an uncertain older request A waits for explicit resumption', async () => {
    vi.useFakeTimers();
    const input = {
      edit_spec_revision_id: asset.id,
      profile: 'standard',
      idempotency_key: olderId,
    };
    localStorage.setItem(pendingKey, JSON.stringify({ input, revision_number: 1 }));
    const old = job('completed', { id: olderId, revision_id: asset.id, revision_number: 1 });
    const api = server({ history: [job('running'), old], post: old, statuses: [job('running')] });
    panel();
    await act(async () => {});
    const resume = screen.getByRole('button', { name: 'Consultar solicitação novamente' });
    expect(resume).toBeDisabled();
    fireEvent.click(resume);
    expect(api.posts).toHaveLength(0);
    expect(localStorage.getItem(storageKey)).toBe(jobId);
    expect(screen.getByRole('button', { name: 'Cancelar renderização' })).toBeEnabled();
    api.statuses([job('completed')]);
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(resume).toBeEnabled();
    await act(async () => fireEvent.click(resume));
    expect(api.posts).toEqual([input]);
    expect(selected().getByText('Vídeo da revisão 1')).toBeVisible();
  });

  it('reports the active limit without silently retrying or replacing an identity', async () => {
    const api = server();
    api.createFailure(409);
    panel();
    await ready();
    await create();
    expect(await screen.findByRole('alert')).toHaveTextContent('já tem uma exportação');
    expect(api.posts).toHaveLength(1);
    expect(localStorage.getItem(pendingKey)).toBeNull();
    expect(screen.queryByText('/private/post')).not.toBeInTheDocument();
  });

  it('integrates into the existing editor and dirty state without a second editing flow', async () => {
    const api = server();
    render(<EditorWorkspace projectId={project.id} />);
    await screen.findByRole('heading', { name: 'Editor do projeto.' });
    await ready();
    expect(generate()).toBeEnabled();
    await userEvent.click(screen.getByRole('button', { name: '1:1' }));
    expect(generate()).toBeDisabled();
    expect(screen.getByText('1080 × 1920')).toBeVisible();
    expect(api.posts).toHaveLength(0);
  });
});

describe('Monitoring, recovery and cancellation', () => {
  it('polls serially at two seconds and shows only measured encoding progress', async () => {
    vi.useFakeTimers();
    const api = server({
      statuses: [
        job(),
        job('running', { progress_encoded_ms: 4500, progress_percent: 37 }),
        job('running', { stage: 'validating', progress_encoded_ms: 12000 }),
        job('completed'),
      ],
    });
    panel();
    await act(async () => {});
    await act(async () => fireEvent.click(generate()));
    expect(api.gets).toHaveLength(1);
    expect(tracking().getByText('Na fila')).toBeVisible();
    await act(() => vi.advanceTimersByTimeAsync(1999));
    expect(api.gets).toHaveLength(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(screen.getByRole('progressbar', { name: 'Progresso da renderização' })).toHaveAttribute(
      'value',
      '37',
    );
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(tracking().getByText('Validando arquivo')).toBeVisible();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(selected().getByRole('link', { name: 'Baixar MP4' })).toBeVisible();
    await act(() => vi.advanceTimersByTimeAsync(10000));
    expect(api.gets).toHaveLength(4);
  });

  it('does not overlap a pending GET and aborts both status and history on unmount', async () => {
    vi.useFakeTimers();
    localStorage.setItem(storageKey, jobId);
    const signals: AbortSignal[] = [];
    const mock = vi.fn((_url: string, init: RequestInit) => {
      signals.push(init.signal!);
      return new Promise<Response>(() => {});
    });
    vi.stubGlobal('fetch', mock);
    const view = panel();
    await act(() => vi.advanceTimersByTimeAsync(10000));
    expect(mock).toHaveBeenCalledTimes(2);
    view.unmount();
    expect(signals.every((signal) => signal.aborted)).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(10000));
    expect(mock).toHaveBeenCalledTimes(2);
  });

  it('waits for server history before resuming an uncertain request that could hide another active job', () => {
    localStorage.setItem(
      pendingKey,
      JSON.stringify({
        input: {
          edit_spec_revision_id: revision.id,
          profile: 'standard',
          idempotency_key: olderId,
        },
        revision_number: 2,
      }),
    );
    vi.stubGlobal(
      'fetch',
      vi.fn(() => new Promise<Response>(() => {})),
    );
    panel();
    expect(screen.getByRole('button', { name: 'Consultar solicitação novamente' })).toBeDisabled();
  });

  it('restores a remembered job by GET and validates it before displaying the MP4', async () => {
    localStorage.setItem(storageKey, jobId);
    localStorage.setItem(selectedKey, jobId);
    const api = server();
    panel();
    const video = await screen.findByLabelText('Vídeo exportado');
    expect(video).toHaveAttribute('src', `${base}/${jobId}/file`);
    expect(api.posts).toHaveLength(0);
    expect(api.gets).toHaveLength(1);
  });

  it('recovers an active export from server history when browser storage is absent', async () => {
    const api = server({ history: [job('running')], statuses: [job('running')] });
    panel();
    expect(await screen.findByRole('button', { name: 'Cancelar renderização' })).toBeEnabled();
    expect(api.gets).toHaveLength(1);
    expect(api.posts).toHaveLength(0);
  });

  it.each(['queued', 'running'] as const)(
    'confirms and requests cancellation for %s',
    async (status) => {
      const current = job(status);
      const api = server({ history: [current], statuses: [current] });
      panel();
      await userEvent.click(await screen.findByRole('button', { name: 'Cancelar renderização' }));
      const dialog = screen.getByRole('dialog', { name: 'Confirmar cancelamento' });
      expect(api.cancels).toHaveLength(0);
      api.statuses([job('canceled')]);
      await userEvent.click(within(dialog).getByRole('button', { name: 'Confirmar cancelamento' }));
      await waitFor(() => expect(tracking().getByText('Cancelado')).toBeVisible());
      expect(api.cancels).toEqual([`${base}/${jobId}/cancel`]);
      expect(screen.queryByRole('link', { name: 'Baixar MP4' })).not.toBeInTheDocument();
    },
  );

  it('distinguishes durable cancel intention from a confirmed canceled result', async () => {
    const api = server({ history: [job('running')], statuses: [job('running')] });
    const requested = job('running', { cancel_requested_at: project.created_at });
    api.cancel(requested);
    panel();
    await userEvent.click(await screen.findByRole('button', { name: 'Cancelar renderização' }));
    api.statuses([requested]);
    await userEvent.click(screen.getByRole('button', { name: 'Confirmar cancelamento' }));
    expect(await tracking().findByText('Cancelamento solicitado')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Cancelar renderização' })).toBeDisabled();
    expect(screen.getByText(/interrupção não é instantânea/)).toBeVisible();
  });

  it('allows dismissing cancellation without sending a request', async () => {
    const api = server({ history: [job('running')], statuses: [job('running')] });
    panel();
    await userEvent.click(await screen.findByRole('button', { name: 'Cancelar renderização' }));
    await userEvent.click(screen.getByRole('button', { name: 'Continuar renderizando' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(api.cancels).toHaveLength(0);
  });

  it('reports a completion/cancel conflict without pretending cancellation succeeded', async () => {
    const api = server({ history: [job('running')], statuses: [job('running')] });
    panel();
    await userEvent.click(await screen.findByRole('button', { name: 'Cancelar renderização' }));
    api.cancel(job('completed'), 409);
    api.statuses([job('completed')]);
    await userEvent.click(screen.getByRole('button', { name: 'Confirmar cancelamento' }));
    expect(await tracking().findByText('Concluído')).toBeVisible();
    expect(screen.queryByText('Cancelado')).not.toBeInTheDocument();
    expect(api.cancels).toHaveLength(1);
  });

  it('keeps an offline lookup separate from job failure and supports explicit recovery', async () => {
    localStorage.setItem(storageKey, jobId);
    const api = server({ statuses: [job('running')] });
    api.getFailure(503);
    panel();
    expect(await screen.findByRole('alert')).toHaveTextContent('temporariamente indisponível');
    expect(screen.queryByText('Falhou')).not.toBeInTheDocument();
    expect(localStorage.getItem(storageKey)).toBe(jobId);
    api.getFailure(0);
    api.statuses([job('completed')]);
    await userEvent.click(screen.getByRole('button', { name: 'Verificar exportações novamente' }));
    expect(await tracking().findByText('Concluído')).toBeVisible();
    expect(api.posts).toHaveLength(0);
  });

  it('shows a processing failure without turning it into an automatic retry', async () => {
    const api = server({ history: [job('failed')], statuses: [job('failed')] });
    panel();
    expect(await screen.findByRole('alert')).toHaveTextContent('ultrapassou o tempo permitido');
    expect(screen.queryByLabelText('Vídeo exportado')).not.toBeInTheDocument();
    expect(api.posts).toHaveLength(0);
  });
});

describe('Private results and persistent history', () => {
  it('shows native final playback and private download only after completion', async () => {
    server({ post: job('completed') });
    panel();
    await ready();
    await create();
    const video = (await screen.findByLabelText('Vídeo exportado')) as HTMLVideoElement;
    expect(video.controls).toBe(true);
    expect(video.autoplay).toBe(false);
    expect(video).toHaveAttribute('preload', 'metadata');
    expect(selected().getByRole('link', { name: 'Baixar MP4' })).toHaveAttribute(
      'href',
      `${base}/${jobId}/file`,
    );
    expect(selected().getByRole('link', { name: 'Baixar MP4' })).toHaveAttribute('download');
    expect(selected().getByText('1080 × 1920 · 30 FPS')).toBeVisible();
    expect(selected().getByText('21,5 KiB')).toBeVisible();
  });

  it('preserves unavailable completed history without offering a broken download or video', async () => {
    const unavailable = job('completed', {
      artifact_available: false,
      file_url: null,
      error_code: 'render_artifact_unavailable',
    });
    server({ history: [unavailable], statuses: [unavailable] });
    panel();
    expect(await screen.findByRole('alert')).toHaveTextContent('conferência de integridade');
    expect(screen.queryByRole('link', { name: 'Baixar MP4' })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Vídeo exportado')).not.toBeInTheDocument();
    expect(await screen.findByText('Arquivo indisponível; histórico preservado.')).toBeVisible();
  });

  it('selects an old completed result without interrupting monitoring of an active export', async () => {
    vi.useFakeTimers();
    const old = job('completed', { id: olderId, revision_number: 1, revision_id: asset.id });
    const api = server({ history: [job('running'), old], statuses: [job('running')] });
    panel();
    await act(async () => {});
    await act(async () =>
      fireEvent.click(
        screen.getByRole('button', { name: `Ver exportação ${olderId.slice(0, 8)}` }),
      ),
    );
    expect(selected().getByLabelText('Vídeo exportado')).toHaveAttribute(
      'src',
      `${base}/${olderId}/file`,
    );
    expect(
      selected().getByText('Esta exportação mantém a revisão 1; a edição está na revisão 2.'),
    ).toBeVisible();
    expect(screen.getByRole('button', { name: 'Cancelar renderização' })).toBeEnabled();
    const before = api.gets.filter((url) => url.endsWith(jobId)).length;
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(api.gets.filter((url) => url.endsWith(jobId))).toHaveLength(before + 1);
    expect(localStorage.getItem(selectedKey)).toBe(olderId);
  });

  it('retains terminal history and makes retries explicit with a new key and original revision', async () => {
    const old = job('canceled', {
      id: olderId,
      revision_number: 1,
      revision_id: asset.id,
      profile: 'high',
    });
    const api = server({ history: [old] });
    panel();
    await ready();
    expect(api.posts).toHaveLength(0);
    await waitFor(() =>
      expect(
        screen.getByRole('button', {
          name: `Tentar novamente exportação ${olderId.slice(0, 8)}`,
        }),
      ).toBeEnabled(),
    );
    await userEvent.click(
      screen.getByRole('button', { name: `Tentar novamente exportação ${olderId.slice(0, 8)}` }),
    );
    expect(api.posts[0]).toMatchObject({ edit_spec_revision_id: asset.id, profile: 'high' });
    expect(api.posts[0]!.idempotency_key).not.toBe(olderId);
    expect(await screen.findByRole('article', { name: `Exportação ${olderId}` })).toBeVisible();
  });

  it('blocks historical retry with dirty changes while preserving access to a result', async () => {
    const old = job('canceled', { id: olderId });
    const api = server({ history: [old] });
    panel({ dirty: true });
    await ready();
    expect(
      screen.getByRole('button', { name: `Tentar novamente exportação ${olderId.slice(0, 8)}` }),
    ).toBeDisabled();
    expect(
      screen.getByRole('button', { name: `Ver exportação ${olderId.slice(0, 8)}` }),
    ).toBeEnabled();
    expect(api.posts).toHaveLength(0);
  });

  it('revalidates a previously selected historical file again without stopping the active job', async () => {
    const old = job('completed', { id: olderId, revision_number: 1, revision_id: asset.id });
    const api = server({ history: [job('running'), old], statuses: [job('running')] });
    panel();
    await ready();
    const view = screen.getByRole('button', { name: `Ver exportação ${olderId.slice(0, 8)}` });
    await userEvent.click(view);
    await screen.findByLabelText('Vídeo exportado');
    const before = api.gets.filter((url) => url.endsWith(olderId)).length;
    await userEvent.click(view);
    expect(await screen.findByLabelText('Vídeo exportado')).toBeVisible();
    expect(api.gets.filter((url) => url.endsWith(olderId))).toHaveLength(before + 1);
    expect(screen.getByRole('button', { name: 'Cancelar renderização' })).toBeEnabled();
  });

  it('removes inaccessible remembered IDs only after an authoritative404', async () => {
    localStorage.setItem(storageKey, jobId);
    localStorage.setItem(selectedKey, jobId);
    const api = server();
    api.getFailure(404);
    panel();
    expect(await screen.findByRole('alert')).toHaveTextContent('não está mais disponível');
    expect(localStorage.getItem(storageKey)).toBeNull();
    expect(localStorage.getItem(selectedKey)).toBeNull();
    expect(screen.queryByLabelText('Vídeo exportado')).not.toBeInTheDocument();
  });

  it('paginates history deterministically using twenty-row pages', async () => {
    const history = Array.from({ length: 21 }, (_, index) =>
      job('canceled', { id: `a2386d10-7047-482d-a140-${String(index).padStart(12, '0')}` }),
    );
    const api = server({ history });
    panel();
    await ready();
    expect(screen.getAllByRole('article')).toHaveLength(20);
    await userEvent.click(screen.getByRole('button', { name: 'Próxima página de exportações' }));
    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(1));
    expect(api.historyOffsets).toContain(20);
    await userEvent.click(screen.getByRole('button', { name: 'Página anterior de exportações' }));
    await waitFor(() => expect(screen.getAllByRole('article')).toHaveLength(20));
  });

  it('reports and recovers a history connection failure without deleting references', async () => {
    const api = server();
    api.historyFailure(503);
    panel();
    expect(await screen.findByRole('alert')).toHaveTextContent('temporariamente indisponível');
    api.historyFailure(0);
    await userEvent.click(screen.getByRole('button', { name: 'Recarregar histórico' }));
    expect(await screen.findByText('Nenhuma exportação solicitada neste projeto.')).toBeVisible();
  });

  it('ignores invalid persisted references and operates when localStorage writes are blocked', async () => {
    localStorage.setItem(storageKey, '../../private');
    localStorage.setItem(selectedKey, 'https://external.invalid');
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    const api = server();
    panel();
    await ready();
    await create();
    expect(await screen.findByLabelText('Vídeo exportado')).toBeVisible();
    expect(api.posts).toHaveLength(1);
  });

  it.each([
    [4, 'decodificar'],
    [2, 'conexão'],
  ])('handles native playback error %s without changing completed state', async (code, text) => {
    server({ post: job('completed') });
    panel();
    await ready();
    await create();
    const video = await screen.findByLabelText('Vídeo exportado');
    Object.defineProperty(video, 'error', { configurable: true, value: { code } });
    fireEvent.error(video);
    expect(screen.getByRole('alert')).toHaveTextContent(text);
    expect(selected().getByRole('link', { name: 'Baixar MP4' })).toBeEnabled();
    const reload = vi.spyOn(video as HTMLVideoElement, 'load').mockImplementation(() => {});
    await userEvent.click(screen.getByRole('button', { name: 'Recarregar vídeo exportado' }));
    expect(reload).toHaveBeenCalledOnce();
    fireEvent.loadedMetadata(video);
    expect(screen.queryByText('Carregando vídeo exportado…')).not.toBeInTheDocument();
  });

  it('coordinates native playback with the original source and pauses on unmount', async () => {
    server();
    const view = render(
      <div className="editor-workspace">
        <VideoPlayer
          projectId={project.id}
          asset={{ ...asset, duration_ms: 14000 }}
          trim={revision.trim}
        />
        <ExportPanel
          projectId={project.id}
          projectName={project.name}
          revision={revision}
          dirty={false}
          busy={false}
        />
      </div>,
    );
    await ready();
    await create();
    const output = await screen.findByLabelText('Vídeo exportado');
    const source = screen.getByLabelText('Vídeo do projeto');
    Object.defineProperty(source, 'paused', { configurable: true, value: false });
    fireEvent.play(output);
    expect((source as HTMLVideoElement).paused).toBe(true);
    const pause = vi.spyOn(output as HTMLVideoElement, 'pause');
    view.unmount();
    expect(pause).toHaveBeenCalled();
  });

  it('supports keyboard activation and keeps result semantics in dark and narrow layouts', async () => {
    document.documentElement.setAttribute('data-theme', 'dark');
    window.innerWidth = 375;
    server({ post: job('completed') });
    panel();
    await ready();
    generate().focus();
    await userEvent.keyboard('{Enter}');
    const download = await selected().findByRole('link', { name: 'Baixar MP4' });
    download.focus();
    expect(download).toHaveFocus();
    expect(screen.getByRole('region', { name: 'Histórico de exportações' })).toBeVisible();
  });
});
