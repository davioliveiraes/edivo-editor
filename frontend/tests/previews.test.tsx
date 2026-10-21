import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PreviewPanel } from '@/features/editor/preview-panel';
import { EditorWorkspace } from '@/features/editor/editor-workspace';
import { VideoPlayer } from '@/features/editor/video-player';
import type { EditSpecRevision } from '@/lib/api/edit-spec';
import { previewsApi, type PreviewJob } from '@/lib/api/previews';
import { asset, json, page, project } from './fixtures';

const source = { ...asset, duration_ms: 12_000 };
const revision: EditSpecRevision = {
  id: 'b793155e-7047-482d-a140-ce384dff9bdd',
  project_id: project.id,
  revision_number: 2,
  created_at: project.created_at,
  schema_version: 1,
  source_media_id: asset.id,
  trim: { start_ms: 1_000, end_ms: 11_000 },
  canvas: { preset: '9:16', fit: 'cover', focal_x: 0.5, focal_y: 0.5 },
};
const jobId = 'a4aa2201-859b-4c37-8214-c73e57a3e8e7';
const statusUrl = `/api/v1/projects/${project.id}/previews/${jobId}`;
const storageKey = `edivo:preview:${project.id}`;
const queued: PreviewJob = {
  id: jobId,
  project_id: project.id,
  revision_id: revision.id,
  revision_number: revision.revision_number,
  preview_start_ms: 1_000,
  duration_ms: 8_000,
  status: 'queued',
  stage: 'queued',
  attempts: 0,
  created_at: project.created_at,
  started_at: null,
  finished_at: null,
  error_code: null,
  artifact_available: false,
  checksum_sha256: null,
  byte_size: null,
  status_url: statusUrl,
  file_url: null,
};
const running: PreviewJob = { ...queued, status: 'running', stage: 'encoding', attempts: 1 };
const completed: PreviewJob = {
  ...running,
  status: 'completed',
  stage: 'completed',
  finished_at: project.created_at,
  artifact_available: true,
  checksum_sha256: 'c'.repeat(64),
  byte_size: 22_000,
  file_url: `${statusUrl}/file`,
};
const failed: PreviewJob = {
  ...running,
  status: 'failed',
  stage: 'failed',
  error_code: 'preview_timeout',
  finished_at: project.created_at,
};

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (
    this: HTMLMediaElement,
  ) {
    Object.defineProperty(this, 'paused', { configurable: true, value: true });
    fireEvent.pause(this);
  });
});
afterEach(() => vi.useRealTimers());

function server(post: PreviewJob = queued, statuses: PreviewJob[] = [completed]) {
  let currentRevision = revision;
  let createStatus = post.status === 'completed' ? 200 : 202;
  let createCode = '';
  let getStatus = 200;
  let getCode = '';
  const posts: unknown[] = [];
  const puts: unknown[] = [];
  let gets = 0;
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/previews')) {
      posts.push(JSON.parse(options?.body as string));
      return createStatus < 300
        ? json(post, createStatus)
        : json({ code: createCode, message: '/private/unsafe' }, createStatus);
    }
    if (url === statusUrl) {
      const response = statuses[Math.min(gets, statuses.length - 1)]!;
      gets += 1;
      return getStatus === 200
        ? json(response)
        : json({ code: getCode, message: '/private/unsafe' }, getStatus);
    }
    if (url.endsWith('/edit-spec')) {
      if (options?.method === 'PUT') {
        const body = JSON.parse(options.body as string);
        puts.push(body);
        delete body.expected_revision;
        currentRevision = {
          ...revision,
          ...body,
          revision_number: 3,
          id: 'fe260b22-a8ca-4aa1-b3dd-6a2b4461d479',
        };
      }
      return json(currentRevision);
    }
    if (url.includes('/audio?')) return json(page([]));
    if (url.includes('/media?')) return json(page([source]));
    if (url.endsWith(project.id)) return json(project);
    return json({}, 404);
  });
  vi.stubGlobal('fetch', fetchMock);
  return {
    fetchMock,
    posts,
    puts,
    get count() {
      return gets;
    },
    failCreate(status: number, code = '') {
      createStatus = status;
      createCode = code;
    },
    failGet(status: number, code = '') {
      getStatus = status;
      getCode = code;
    },
  };
}

function panel(props: Partial<Parameters<typeof PreviewPanel>[0]> = {}) {
  return render(
    <PreviewPanel
      projectId={project.id}
      revision={revision}
      dirty={false}
      busy={false}
      {...props}
    />,
  );
}
const generate = () => screen.getByRole('button', { name: 'Gerar prévia' });
function number(label: string, value: number | string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value: String(value) } });
}

describe('Explicit preview requests and revision ownership', () => {
  it('requires a saved clean revision and never saves a draft implicitly', async () => {
    const api = server();
    const view = panel({ revision: null });
    expect(generate()).toBeDisabled();
    expect(screen.getByText(/Salve uma revisão/)).toBeVisible();
    view.rerender(<PreviewPanel projectId={project.id} revision={revision} dirty busy={false} />);
    expect(generate()).toBeDisabled();
    expect(screen.getByText('Salve a edição antes de gerar uma nova prévia.')).toBeVisible();
    fireEvent.click(generate());
    expect(api.fetchMock).not.toHaveBeenCalled();
    expect(api.puts).toHaveLength(0);
  });

  it('posts only the immutable revision and absolute window, without PUT or a draft payload', async () => {
    const result = { ...completed, preview_start_ms: 9_000, duration_ms: 2_000 };
    const api = server(result, [result]);
    panel();
    number('Início da prévia (ms)', 9_000);
    number('Duração da prévia (ms)', 8_000);
    expect(screen.getByText(/9000–11000 ms/)).toBeVisible();
    await userEvent.click(generate());
    expect(await screen.findByLabelText('Prévia gerada')).toHaveAttribute(
      'src',
      `${statusUrl}/file`,
    );
    expect(api.posts).toEqual([
      { edit_spec_revision_id: revision.id, preview_start_ms: 9_000, duration_ms: 8_000 },
    ]);
    expect(api.puts).toHaveLength(0);
    expect(localStorage.getItem(storageKey)).toBe(jobId);
  });

  it.each([
    [999, 1_000],
    [11_000, 1_000],
    [1_000.5, 1_000],
    [1_000, 0],
    [1_000, 8_001],
    [1_000, 1.5],
    ['', 1_000],
  ])('blocks invalid absolute window start=%s duration=%s', (start, duration) => {
    const api = server();
    panel();
    number('Início da prévia (ms)', start);
    number('Duração da prévia (ms)', duration);
    expect(generate()).toBeDisabled();
    expect(screen.getByText(/Informe início inteiro/)).toBeVisible();
    expect(api.posts).toHaveLength(0);
  });

  it('keeps a completed result labelled with its old revision after editing and saving a newer one', async () => {
    server(completed);
    const view = panel();
    await userEvent.click(generate());
    await screen.findByLabelText('Prévia gerada');
    view.rerender(
      <PreviewPanel
        projectId={project.id}
        revision={{ ...revision, id: '712a6a61-207b-43bd-92cd-c7607b58f97b', revision_number: 3 }}
        dirty
        busy={false}
      />,
    );
    expect(screen.getByText('Prévia da revisão 2')).toBeVisible();
    expect(screen.getByText(/edição está na revisão 3/)).toBeVisible();
    expect(screen.getByText('As alterações locais ainda não estão nesta prévia.')).toBeVisible();
    expect(generate()).toBeDisabled();
    expect(screen.getByLabelText('Prévia gerada')).toHaveAttribute('src', `${statusUrl}/file`);
  });

  it('uses the explicitly saved new revision after the editor dirty state is cleared', async () => {
    const api = server(completed);
    render(<EditorWorkspace projectId={project.id} />);
    await screen.findByRole('heading', { name: 'Editor do projeto.' });
    await userEvent.click(screen.getByRole('button', { name: 'Adicionar texto' }));
    expect(generate()).toBeDisabled();
    expect(api.posts).toHaveLength(0);
    await userEvent.click(screen.getByRole('button', { name: 'Salvar edição' }));
    await screen.findByText('Edição salva · Revisão 3');
    expect(generate()).toBeEnabled();
    expect(api.puts).toHaveLength(1);
    expect(screen.getByText('Gerar a partir da revisão salva 3')).toBeVisible();
  });
});

describe('Persistent preview monitoring and real terminal states', () => {
  it('polls at two seconds through real stages and stops immediately at completion', async () => {
    vi.useFakeTimers();
    const api = server(queued, [queued, running, completed]);
    panel();
    await act(async () => fireEvent.click(generate()));
    expect(api.count).toBe(1);
    expect(screen.getByText('Na fila')).toBeVisible();
    expect(generate()).toBeDisabled();
    await act(() => vi.advanceTimersByTimeAsync(1_999));
    expect(api.count).toBe(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(screen.getByText('Codificando prévia')).toBeVisible();
    await act(() => vi.advanceTimersByTimeAsync(2_000));
    expect(screen.getByText('Prévia concluída')).toBeVisible();
    expect(screen.getByLabelText('Prévia gerada')).toHaveAttribute('preload', 'metadata');
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(api.count).toBe(3);
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('does not overlap pending status requests and aborts on leaving the screen', async () => {
    vi.useFakeTimers();
    localStorage.setItem(storageKey, jobId);
    let statusSignal: AbortSignal | undefined;
    const fetchMock = vi.fn((_url: string, options?: RequestInit) => {
      statusSignal = options?.signal ?? undefined;
      return new Promise<Response>(() => {});
    });
    vi.stubGlobal('fetch', fetchMock);
    const view = panel();
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(fetchMock).toHaveBeenCalledTimes(1);
    view.unmount();
    expect(statusSignal?.aborted).toBe(true);
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('restores only the remembered job ID and revalidates it by GET before displaying its video', async () => {
    localStorage.setItem(storageKey, jobId);
    const api = server();
    panel();
    expect(screen.getByText('Recuperando prévia do projeto…')).toBeVisible();
    expect(screen.queryByLabelText('Prévia gerada')).not.toBeInTheDocument();
    expect(await screen.findByLabelText('Prévia gerada')).toHaveAttribute(
      'src',
      `${statusUrl}/file`,
    );
    expect(api.posts).toHaveLength(0);
    expect(api.count).toBe(1);
  });

  it('discards an inaccessible remembered job without trusting browser storage as authority', async () => {
    localStorage.setItem(storageKey, jobId);
    const api = server();
    api.failGet(404);
    panel();
    expect(await screen.findByRole('alert')).toHaveTextContent('não está mais disponível');
    expect(localStorage.getItem(storageKey)).toBeNull();
    expect(screen.queryByLabelText('Prévia gerada')).not.toBeInTheDocument();
    expect(generate()).toBeEnabled();
  });

  it('retries a failed status lookup explicitly, never exposing the server error message', async () => {
    localStorage.setItem(storageKey, jobId);
    const api = server();
    api.failGet(503);
    panel();
    expect(await screen.findByRole('alert')).toHaveTextContent('temporariamente indisponível');
    expect(screen.queryByText('/private/unsafe')).not.toBeInTheDocument();
    api.failGet(200);
    await userEvent.click(screen.getByRole('button', { name: 'Verificar prévia novamente' }));
    expect(await screen.findByLabelText('Prévia gerada')).toBeVisible();
    expect(api.posts).toHaveLength(0);
  });

  it('shows terminal processing failure and requires another explicit POST to retry', async () => {
    const api = server(failed, [failed]);
    panel();
    await userEvent.click(generate());
    expect(await screen.findByText('Falha no processamento')).toBeVisible();
    expect(screen.getByRole('alert')).toHaveTextContent('ultrapassou o tempo permitido');
    expect(screen.queryByLabelText('Prévia gerada')).not.toBeInTheDocument();
    expect(api.posts).toHaveLength(1);
    await userEvent.click(screen.getByRole('button', { name: 'Tentar nova prévia' }));
    await waitFor(() => expect(api.posts).toHaveLength(2));
  });

  it.each([
    [
      'preview_text_unsupported',
      'não possui todos os caracteres',
      'Nenhum texto foi removido silenciosamente',
    ],
    ['preview_font_unavailable', 'fonte local necessária', 'não foi publicada sem os textos'],
  ])(
    'explains text rendering failure %s without pretending the overlay was applied',
    async (code, cause, integrity) => {
      server({ ...failed, error_code: code }, [{ ...failed, error_code: code }]);
      panel();
      await userEvent.click(generate());
      const message = await screen.findByRole('alert');
      expect(message).toHaveTextContent(cause);
      expect(message).toHaveTextContent(integrity);
      expect(screen.queryByLabelText('Prévia gerada')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Tentar nova prévia' })).toBeEnabled();
    },
  );

  it('reports the project active limit without overwriting a result or saving an edit', async () => {
    const api = server();
    api.failCreate(409, 'preview_active_limit');
    panel();
    await userEvent.click(generate());
    expect(await screen.findByRole('alert')).toHaveTextContent('limite de prévias');
    expect(api.puts).toHaveLength(0);
    expect(api.posts).toHaveLength(1);
    expect(localStorage.getItem(storageKey)).toBeNull();
  });

  it('does not follow a foreign or external file URL from an invalid API response', async () => {
    server({ ...completed, file_url: 'https://external.invalid/private.mp4' });
    panel();
    await userEvent.click(generate());
    expect(await screen.findByRole('alert')).toHaveTextContent('Não foi possível');
    expect(screen.queryByLabelText('Prévia gerada')).not.toBeInTheDocument();
    expect(localStorage.getItem(storageKey)).toBeNull();
  });

  it.each([
    { revision_id: '28b40d46-13b6-42f4-8d6d-a9a026c91d28' },
    { preview_start_ms: 2_000 },
    { duration_ms: 4_000 },
  ])(
    'refuses to attribute a mismatched result to the requested revision/window: %j',
    async (patch) => {
      server({ ...completed, ...patch });
      panel();
      await userEvent.click(generate());
      expect(await screen.findByRole('alert')).toHaveTextContent('Não foi possível');
      expect(screen.queryByLabelText('Prévia gerada')).not.toBeInTheDocument();
      expect(localStorage.getItem(storageKey)).toBeNull();
    },
  );

  it('ignores malformed stored IDs and still works when localStorage writes are blocked', async () => {
    localStorage.setItem(storageKey, '../../private');
    const api = server(completed);
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked');
    });
    panel();
    expect(api.count).toBe(0);
    await userEvent.click(generate());
    expect(await screen.findByLabelText('Prévia gerada')).toBeVisible();
  });
});

describe('Native preview media and playback coordination', () => {
  it.each([
    [4, 'decodificar'],
    [2, 'conexão'],
  ])('exposes native controls and handles media error %s', async (code, message) => {
    server(completed);
    panel();
    await userEvent.click(generate());
    const video = (await screen.findByLabelText('Prévia gerada')) as HTMLVideoElement;
    expect(video.controls).toBe(true);
    expect(video.autoplay).toBe(false);
    expect(video).toHaveAttribute('controlslist', 'nodownload');
    Object.defineProperty(video, 'error', { configurable: true, value: { code } });
    fireEvent.error(video);
    expect(screen.getByRole('alert')).toHaveTextContent(message);
    expect(screen.getByText('Prévia concluída')).toBeVisible();
    const load = vi.spyOn(video, 'load').mockImplementation(() => {});
    await userEvent.click(screen.getByRole('button', { name: 'Recarregar vídeo de prévia' }));
    expect(load).toHaveBeenCalledOnce();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    fireEvent.loadedMetadata(video);
    expect(screen.queryByText('Carregando prévia…')).not.toBeInTheDocument();
  });

  it('pauses the other native video when either source or encoded preview really starts', async () => {
    server(completed);
    render(
      <div className="editor-workspace">
        <VideoPlayer projectId={project.id} asset={source} trim={revision.trim} />
        <PreviewPanel projectId={project.id} revision={revision} dirty={false} busy={false} />
      </div>,
    );
    await userEvent.click(generate());
    const output = (await screen.findByLabelText('Prévia gerada')) as HTMLVideoElement;
    const input = screen.getByLabelText('Vídeo do projeto') as HTMLVideoElement;
    Object.defineProperty(input, 'paused', { configurable: true, value: false });
    fireEvent.play(output);
    expect(input.paused).toBe(true);
    Object.defineProperty(output, 'paused', { configurable: true, value: false });
    fireEvent.play(input);
    expect(output.paused).toBe(true);
  });

  it('accepts both API success codes without treating completed reuse as a new queued job', async () => {
    const api = server(completed);
    const result = await previewsApi.create(
      project.id,
      { edit_spec_revision_id: revision.id, preview_start_ms: 1_000, duration_ms: 8_000 },
      new AbortController().signal,
    );
    expect(result.status).toBe('completed');
    expect(result.artifact_available).toBe(true);
    expect(api.posts).toHaveLength(1);
  });
});
