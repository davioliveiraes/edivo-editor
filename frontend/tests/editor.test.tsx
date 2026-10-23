import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EditorWorkspace } from '@/features/editor/editor-workspace';
import { VideoPlayer } from '@/features/editor/video-player';
import { ProjectsWorkspace } from '@/features/projects/projects-workspace';
import type { EditSpecRevision } from '@/lib/api/edit-spec';
import { asset, json, page, project } from './fixtures';

const source = { ...asset, duration_ms: 10_000 };
const second = {
  ...source,
  id: '981f1234-afb0-40d2-bd24-dc484ac0c71e',
  original_filename: 'segundo.mp4',
  has_audio: false,
  audio_codec: null,
};
const revision: EditSpecRevision = {
  schema_version: 1,
  source_media_id: source.id,
  trim: { start_ms: 1_000, end_ms: 8_500 },
  canvas: { preset: '9:16', fit: 'cover', focal_x: 0.5, focal_y: 0.5 },
  id: 'd0513d24-3990-47c6-b61d-2081f6ee631d',
  project_id: project.id,
  revision_number: 1,
  created_at: project.created_at,
};

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (
    this: HTMLMediaElement,
  ) {
    Object.defineProperty(this, 'paused', { configurable: true, value: false });
    fireEvent.play(this);
    fireEvent.playing(this);
    return Promise.resolve();
  });
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(function (
    this: HTMLMediaElement,
  ) {
    Object.defineProperty(this, 'paused', { configurable: true, value: true });
    fireEvent.pause(this);
  });
  vi.stubGlobal(
    'requestAnimationFrame',
    vi.fn(() => 1),
  );
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
});

function mockEditor({ media = [source], saved = null as EditSpecRevision | null } = {}) {
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes('/renders?')) return json(page([]));
    if (options?.method === 'HEAD') return new Response(null, { status: 200 });
    if (url.endsWith('/edit-spec'))
      return saved ? json(saved) : json({ code: 'edit_spec_not_found' }, 404);
    if (url.includes('/media?')) return json(page(media));
    if (url.endsWith(`/media/${source.id}`)) return json(source);
    if (url.endsWith(project.id)) return json(project);
    return json(page([project]));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

async function openEditor() {
  render(<EditorWorkspace projectId={project.id} />);
  await screen.findByRole('heading', { name: 'Editor do projeto.' });
  return screen.getByLabelText('Vídeo do projeto') as HTMLVideoElement;
}

function metadata(video: HTMLVideoElement) {
  Object.defineProperty(video, 'duration', { configurable: true, value: 10 });
  Object.defineProperty(video, 'videoWidth', { configurable: true, value: 1920 });
  Object.defineProperty(video, 'videoHeight', { configurable: true, value: 1080 });
  fireEvent.loadedMetadata(video);
}

describe('Editor navigation and sources', () => {
  it('recognizes the editor hash without breaking the project workspace', async () => {
    mockEditor();
    window.history.replaceState(null, '', `/#/projects/${project.id}/editor`);
    const mode = vi.fn();
    render(<ProjectsWorkspace onEditorModeChange={mode} />);
    expect(await screen.findByRole('heading', { name: 'Editor do projeto.' })).toBeVisible();
    expect(mode).toHaveBeenLastCalledWith(true);
    expect(screen.getByRole('link', { name: 'Voltar à biblioteca' })).toHaveAttribute(
      'href',
      `#/projects/${project.id}`,
    );
    expect(screen.queryByRole('heading', { name: 'Seus projetos.' })).not.toBeInTheDocument();
  });

  it('shows a real empty state without a fake player', async () => {
    mockEditor({ media: [] });
    render(<EditorWorkspace projectId={project.id} />);
    expect(
      await screen.findByRole('heading', { name: 'Este projeto ainda não tem vídeos.' }),
    ).toBeVisible();
    expect(document.querySelector('video')).toBeNull();
    expect(screen.getByRole('link', { name: 'Importar primeiro vídeo' })).toHaveAttribute(
      'href',
      `#/projects/${project.id}`,
    );
  });

  it('allows selecting one source and uses the private relative streaming URL', async () => {
    mockEditor({ media: [source, second] });
    const video = await openEditor();
    expect(video).toHaveAttribute(
      'src',
      `/api/v1/projects/${project.id}/media/${source.id}/stream`,
    );
    expect(video).toHaveAttribute('preload', 'metadata');
    expect(video).not.toHaveAttribute('autoplay');
    await userEvent.selectOptions(screen.getByLabelText('Vídeo de origem'), second.id);
    expect(screen.getByLabelText('Vídeo do projeto')).toHaveAttribute(
      'src',
      `/api/v1/projects/${project.id}/media/${second.id}/stream`,
    );
    expect(screen.getByText(/Sem áudio/)).toBeVisible();
  });

  it('restores a saved source even outside the loaded library page', async () => {
    const fetchMock = mockEditor({ media: [second], saved: revision });
    const video = await openEditor();
    expect(screen.getByLabelText('Vídeo de origem')).toHaveValue(source.id);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/projects/${project.id}/media/${source.id}`,
      expect.any(Object),
    );
    metadata(video);
    expect(video.currentTime).toBe(1);
    expect(video).toHaveStyle({ objectFit: 'cover', objectPosition: '50% 50%' });
  });

  it('shows sanitized load failure and retries', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('private-storage-path')));
    render(<EditorWorkspace projectId={project.id} />);
    expect(screen.getByText('Carregando editor…')).toBeVisible();
    expect(await screen.findByRole('alert')).toHaveTextContent('Verifique a conexão');
    expect(screen.queryByText('private-storage-path')).not.toBeInTheDocument();
    mockEditor();
    await userEvent.click(screen.getByRole('button', { name: 'Recarregar editor' }));
    expect(await screen.findByRole('heading', { name: 'Editor do projeto.' })).toBeVisible();
  });

  it('aborts pending reads when leaving the editor', async () => {
    const fetchMock = vi.fn(() => new Promise<Response>(() => {}));
    vi.stubGlobal('fetch', fetchMock);
    const { unmount } = render(<EditorWorkspace projectId={project.id} />);
    const signal = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1].signal;
    unmount();
    expect(signal?.aborted).toBe(true);
  });
});

describe('Native video controls', () => {
  function player(trim = { start_ms: 0, end_ms: source.duration_ms }) {
    render(<VideoPlayer projectId={project.id} asset={source} trim={trim} />);
    return screen.getByLabelText('Vídeo do projeto') as HTMLVideoElement;
  }

  it('waits for metadata and reflects native play and pause events', async () => {
    const video = player();
    expect(screen.getByText('Carregando vídeo…')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Reproduzir vídeo' })).toBeDisabled();
    metadata(video);
    expect(screen.queryByText('Carregando vídeo…')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Reproduzir vídeo' }));
    expect(video.play).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Pausar vídeo' })).toBeEnabled();
    await userEvent.click(screen.getByRole('button', { name: 'Pausar vídeo' }));
    expect(video.pause).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: 'Reproduzir vídeo' })).toBeEnabled();
  });

  it('seeks in seconds at the media boundary and reports integer milliseconds', () => {
    const video = player();
    metadata(video);
    fireEvent.change(screen.getByRole('slider', { name: 'Buscar no vídeo' }), {
      target: { value: '3456' },
    });
    expect(video.currentTime).toBe(3.456);
    expect(screen.getByLabelText('Tempo atual')).toHaveTextContent('00:03,456');
    video.currentTime = 7.12345;
    fireEvent.timeUpdate(video);
    expect(screen.getByRole('slider', { name: 'Buscar no vídeo' })).toHaveValue('7123');
  });

  it('starts at the selected region and pauses at its end', async () => {
    const video = player({ start_ms: 1_000, end_ms: 3_000 });
    metadata(video);
    expect(video.currentTime).toBe(1);
    video.currentTime = 7;
    await userEvent.click(screen.getByRole('button', { name: 'Reproduzir vídeo' }));
    expect(video.currentTime).toBe(1);
    video.currentTime = 3.05;
    fireEvent.timeUpdate(video);
    expect(video.pause).toHaveBeenCalledOnce();
    expect(video.currentTime).toBe(3);
  });

  it('supports keyboard seeking and playback on the video', async () => {
    const video = player();
    metadata(video);
    fireEvent.keyDown(video, { key: 'ArrowRight' });
    expect(video.currentTime).toBe(1);
    await act(async () => fireEvent.keyDown(video, { key: ' ' }));
    expect(video.play).toHaveBeenCalledOnce();
  });

  it('shows native buffering and clears it after seeking', () => {
    const video = player();
    metadata(video);
    fireEvent.waiting(video);
    expect(screen.getByText('Carregando vídeo…')).toBeVisible();
    fireEvent.seeked(video);
    expect(screen.queryByText('Carregando vídeo…')).not.toBeInTheDocument();
  });

  it('reports unsupported codec honestly without fetching the entire source', async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 200 }));
    vi.stubGlobal('fetch', fetchMock);
    const video = player();
    Object.defineProperty(video, 'error', { configurable: true, value: { code: 4 } });
    fireEvent.error(video);
    expect(await screen.findByRole('alert')).toHaveTextContent('codec pode não ser compatível');
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        video.getAttribute('src'),
        expect.objectContaining({ method: 'HEAD' }),
      ),
    );
    expect(screen.getByRole('button', { name: 'Reproduzir vídeo' })).toBeDisabled();
  });

  it('distinguishes network failure and unavailable storage', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 503 })));
    const video = player();
    Object.defineProperty(video, 'error', { configurable: true, value: { code: 2 } });
    fireEvent.error(video);
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('não está disponível no momento'),
    );
  });

  it('does not claim playback if the native play promise fails', async () => {
    const video = player();
    metadata(video);
    vi.mocked(video.play).mockRejectedValue(
      new DOMException('private-media-detail', 'NotSupportedError'),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Reproduzir vídeo' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('não conseguiu iniciar');
    expect(screen.queryByText('private-media-detail')).not.toBeInTheDocument();
  });

  it('pauses and cancels animation when unmounted', async () => {
    const { unmount } = render(
      <VideoPlayer
        projectId={project.id}
        asset={source}
        trim={{ start_ms: 0, end_ms: source.duration_ms }}
      />,
    );
    metadata(screen.getByLabelText('Vídeo do projeto') as HTMLVideoElement);
    await userEvent.click(screen.getByRole('button', { name: 'Reproduzir vídeo' }));
    unmount();
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledOnce();
    expect(cancelAnimationFrame).toHaveBeenCalled();
  });
});
