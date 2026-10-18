import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EditorWorkspace } from '@/features/editor/editor-workspace';
import { Timeline } from '@/features/editor/timeline';
import { VideoPlayer } from '@/features/editor/video-player';
import { ProjectsWorkspace } from '@/features/projects/projects-workspace';
import type { EditSpecRevision } from '@/lib/api/edit-spec';
import { asset, json, page, project } from './fixtures';

const source = { ...asset, duration_ms: 10_000 };
const second = {
  ...source,
  id: '981f1234-afb0-40d2-bd24-dc484ac0c71e',
  original_filename: 'segundo.mp4',
  duration_ms: 5_000,
};
const saved: EditSpecRevision = {
  id: 'd0513d24-3990-47c6-b61d-2081f6ee631d',
  project_id: project.id,
  revision_number: 3,
  created_at: project.created_at,
  schema_version: 1,
  source_media_id: source.id,
  trim: { start_ms: 1_000, end_ms: 8_000 },
  canvas: { preset: '1:1', fit: 'contain', focal_x: 0.2, focal_y: 0.8 },
};

beforeEach(() => {
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(function (
    this: HTMLMediaElement,
  ) {
    Object.defineProperty(this, 'paused', { configurable: true, value: false });
    fireEvent.play(this);
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

function server(initial: EditSpecRevision | null = null) {
  let revision = initial;
  let failure = 0;
  const puts: Record<string, unknown>[] = [];
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.endsWith('/edit-spec')) {
      if (options?.method === 'PUT') {
        const body = JSON.parse(options.body as string) as Record<string, unknown>;
        puts.push(body);
        if (failure)
          return json(
            {
              code: failure === 409 ? 'edit_spec_conflict' : 'unavailable',
              message: '/private/do-not-expose',
            },
            failure,
          );
        revision = {
          ...saved,
          ...body,
          revision_number: (revision?.revision_number ?? 0) + 1,
        } as EditSpecRevision;
      }
      return revision ? json(revision) : json({ code: 'edit_spec_not_found' }, 404);
    }
    if (url.includes('/media?')) return json(page([source, second]));
    if (url.endsWith(`/media/${second.id}`)) return json(second);
    if (url.endsWith(project.id)) return json(project);
    return json(page([project]));
  });
  vi.stubGlobal('fetch', fetchMock);
  return {
    puts,
    fetchMock,
    fail: (status: number) => {
      failure = status;
    },
    external: (value: EditSpecRevision) => {
      revision = value;
    },
  };
}
async function open() {
  render(<EditorWorkspace projectId={project.id} />);
  await screen.findByRole('heading', { name: 'Editor do projeto.' });
  return screen.getByLabelText('Vídeo do projeto') as HTMLVideoElement;
}
function metadata(video: HTMLVideoElement) {
  Object.defineProperty(video, 'duration', { configurable: true, value: 10 });
  fireEvent.loadedMetadata(video);
}
function setNumber(name: string, value: number) {
  fireEvent.change(screen.getByLabelText(name), { target: { value: String(value) } });
}

describe('Non-destructive timeline', () => {
  it('represents one source with full duration, playhead and selected region', async () => {
    server();
    const video = await open();
    metadata(video);
    expect(screen.getByRole('region', { name: 'Timeline de uma faixa' })).toBeVisible();
    expect(screen.getByLabelText('Duração selecionada')).toHaveTextContent('00:10');
    setNumber('Entrada (ms)', 1_234);
    setNumber('Saída (ms)', 8_765);
    expect(screen.getByLabelText('Duração selecionada')).toHaveTextContent('00:07,531');
    expect(screen.getByRole('slider', { name: 'Marcador de entrada' })).toHaveAttribute(
      'aria-valuenow',
      '1234',
    );
    expect(screen.getByText('Alterações não salvas')).toBeVisible();
    expect(document.querySelectorAll('.timeline-selection')).toHaveLength(1);
  });

  it('bounds integer trims to duration and a minimum of 100 milliseconds', async () => {
    server();
    await open();
    setNumber('Saída (ms)', 11_000);
    expect(screen.getByLabelText('Saída (ms)')).toHaveValue(10_000);
    setNumber('Entrada (ms)', 9_999);
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(9_900);
    setNumber('Saída (ms)', 1);
    expect(screen.getByLabelText('Saída (ms)')).toHaveValue(10_000);
    setNumber('Entrada (ms)', -1);
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(0);
    setNumber('Entrada (ms)', 123.7);
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(124);
  });

  it('supports keyboard markers, shift steps, Home and End', async () => {
    server();
    await open();
    const start = screen.getByRole('slider', { name: 'Marcador de entrada' });
    const end = screen.getByRole('slider', { name: 'Marcador de saída' });
    fireEvent.keyDown(start, { key: 'ArrowRight' });
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(100);
    fireEvent.keyDown(start, { key: 'ArrowRight', shiftKey: true });
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(1_100);
    fireEvent.keyDown(start, { key: 'End' });
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(9_900);
    fireEvent.keyDown(start, { key: 'Home' });
    fireEvent.keyDown(end, { key: 'ArrowLeft' });
    expect(screen.getByLabelText('Saída (ms)')).toHaveValue(9_900);
    fireEvent.keyDown(end, { key: 'End' });
    expect(screen.getByLabelText('Saída (ms)')).toHaveValue(10_000);
  });

  it('seeks on the timeline and marks the real native playhead', async () => {
    const api = server();
    const video = await open();
    metadata(video);
    fireEvent.change(screen.getByLabelText('Posição na timeline'), { target: { value: '2500' } });
    expect(video.currentTime).toBe(2.5);
    await userEvent.click(screen.getByRole('button', { name: 'Marcar entrada' }));
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(2_500);
    video.currentTime = 7.125;
    fireEvent.timeUpdate(video);
    await userEvent.click(screen.getByRole('button', { name: 'Marcar saída' }));
    expect(screen.getByLabelText('Saída (ms)')).toHaveValue(7_125);
    expect(api.puts).toHaveLength(0);
  });

  it('handles pointer seeking and marker dragging including pointer cancellation', () => {
    const onTrim = vi.fn();
    const onSeek = vi.fn();
    render(
      <Timeline
        duration={10_000}
        trim={{ start_ms: 0, end_ms: 10_000 }}
        playhead={0}
        filename="source.mp4"
        disabled={false}
        onTrim={onTrim}
        onSeek={onSeek}
      />,
    );
    const track = document.querySelector('.timeline-track')!;
    vi.spyOn(track, 'getBoundingClientRect').mockReturnValue({ left: 0, width: 200 } as DOMRect);
    const marker = screen.getByRole('slider', { name: 'Marcador de entrada' });
    marker.setPointerCapture = vi.fn();
    fireEvent.pointerDown(track, { clientX: 100, button: 0, pointerId: 1 });
    expect(onSeek).toHaveBeenCalledWith(5_000);
    fireEvent.pointerDown(marker, { clientX: 0, button: 0, pointerId: 2, pointerType: 'touch' });
    fireEvent.pointerMove(marker, { clientX: 50, pointerId: 2, pointerType: 'touch' });
    expect(onTrim).toHaveBeenCalledWith({ start_ms: 2_500, end_ms: 10_000 });
    fireEvent.pointerCancel(marker, { pointerId: 2 });
    onTrim.mockClear();
    fireEvent.pointerMove(marker, { clientX: 80, pointerId: 2 });
    expect(onTrim).not.toHaveBeenCalled();
  });

  it('preserves the full interval for a source shorter than 100ms', () => {
    const onTrim = vi.fn();
    render(
      <Timeline
        duration={34}
        trim={{ start_ms: 0, end_ms: 34 }}
        playhead={0}
        filename="short.mp4"
        disabled={false}
        onTrim={onTrim}
        onSeek={vi.fn()}
      />,
    );
    setNumber('Entrada (ms)', 33);
    setNumber('Saída (ms)', 1);
    expect(onTrim.mock.calls).toEqual([
      [{ start_ms: 0, end_ms: 34 }],
      [{ start_ms: 0, end_ms: 34 }],
    ]);
  });
});

describe('Canvas presets and framing', () => {
  it.each(['9:16', '1:1', '4:5', '16:9'])(
    'selects %s with a real CSS aspect ratio',
    async (preset) => {
      server();
      await open();
      const control = screen.getByRole('button', { name: preset });
      await userEvent.click(control);
      expect(control).toHaveAttribute('aria-pressed', 'true');
      const [width, height] = preset.split(':').map(Number);
      expect(document.querySelector('.composition-frame')).toHaveStyle({
        '--canvas-ratio': String(width! / height!),
      });
    },
  );

  it('persists focus in cover and centers contain without discarding focus', async () => {
    server();
    const video = await open();
    setNumber('Ponto focal horizontal', 0.25);
    setNumber('Ponto focal vertical', 0.75);
    expect(video).toHaveStyle({ objectFit: 'cover', objectPosition: '25% 75%' });
    await userEvent.click(screen.getByLabelText('Ajustar (contain)'));
    expect(video).toHaveStyle({ objectFit: 'contain', objectPosition: '50% 50%' });
    expect(screen.getByLabelText('Ponto focal horizontal')).toBeDisabled();
    await userEvent.click(screen.getByLabelText('Preencher (cover)'));
    expect(video).toHaveStyle({ objectFit: 'cover', objectPosition: '25% 75%' });
  });

  it('uses the saved preset instead of the project suggestion', async () => {
    server(saved);
    await open();
    expect(screen.getByRole('button', { name: '1:1' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('Edição salva · Revisão 3')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Salvar edição' })).toBeDisabled();
  });
});

describe('Explicit saving and conflicts', () => {
  it('saves an initial complete plan with expected_revision0 and then a new version', async () => {
    const api = server();
    await open();
    setNumber('Entrada (ms)', 1_000);
    await userEvent.click(screen.getByRole('button', { name: 'Salvar edição' }));
    expect(await screen.findByText('Edição salva · Revisão 1')).toBeVisible();
    expect(api.puts[0]).toEqual({
      schema_version: 1,
      source_media_id: source.id,
      trim: { start_ms: 1_000, end_ms: 10_000 },
      canvas: { preset: '9:16', fit: 'cover', focal_x: 0.5, focal_y: 0.5 },
      expected_revision: 0,
    });
    setNumber('Saída (ms)', 9_000);
    await userEvent.click(screen.getByRole('button', { name: 'Salvar edição' }));
    expect(await screen.findByText('Edição salva · Revisão 2')).toBeVisible();
    expect(api.puts[1]?.expected_revision).toBe(1);
  });

  it('does not mark playhead movements dirty or send transient playback data', async () => {
    const api = server(saved);
    const video = await open();
    metadata(video);
    fireEvent.change(screen.getByLabelText('Posição na timeline'), { target: { value: '2500' } });
    video.currentTime = 3.25;
    fireEvent.timeUpdate(video);
    expect(screen.getByText('Edição salva · Revisão 3')).toBeVisible();
    expect(api.puts).toHaveLength(0);
    expect(screen.getByRole('button', { name: 'Salvar edição' })).toBeDisabled();
  });

  it('clears dirty when changes return to the saved plan', async () => {
    server(saved);
    await open();
    setNumber('Entrada (ms)', 1_500);
    expect(screen.getByText('Alterações não salvas')).toBeVisible();
    setNumber('Entrada (ms)', 1_000);
    expect(screen.getByText('Edição salva · Revisão 3')).toBeVisible();
  });

  it('retains local edits on503, shows sanitized error and permits retry', async () => {
    const api = server(saved);
    api.fail(503);
    await open();
    setNumber('Entrada (ms)', 2_000);
    await userEvent.click(screen.getByRole('button', { name: 'Salvar edição' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('temporariamente indisponível');
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(2_000);
    expect(screen.queryByText('/private/do-not-expose')).not.toBeInTheDocument();
    api.fail(0);
    await userEvent.click(screen.getByRole('button', { name: 'Salvar edição' }));
    expect(await screen.findByText('Edição salva · Revisão 4')).toBeVisible();
  });

  it('never overwrites a409, preserves local controls and safely reloads the latest revision', async () => {
    const api = server(saved);
    api.fail(409);
    await open();
    setNumber('Entrada (ms)', 2_000);
    await userEvent.click(screen.getByRole('button', { name: 'Salvar edição' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Outra revisão');
    setNumber('Entrada (ms)', 2_500);
    expect(screen.getByRole('button', { name: 'Recarregar revisão atual' })).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Salvar edição' })).toBeDisabled();
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await userEvent.click(screen.getByRole('button', { name: 'Recarregar revisão atual' }));
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(2_500);
    api.external({ ...saved, revision_number: 4, trim: { start_ms: 500, end_ms: 5_000 } });
    await userEvent.click(screen.getByRole('button', { name: 'Recarregar revisão atual' }));
    expect(await screen.findByText('Edição salva · Revisão 4')).toBeVisible();
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(500);
    expect(api.puts).toHaveLength(1);
  });

  it('shows saving state, locks controls and protects navigation until completion', async () => {
    server(saved);
    await open();
    setNumber('Entrada (ms)', 2_000);
    let finish: (value: Response) => void = () => {};
    vi.mocked(fetch).mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          finish = resolve;
        }),
    );
    await userEvent.click(screen.getByRole('button', { name: 'Salvar edição' }));
    expect(screen.getByText('Salvando edição…')).toBeVisible();
    expect(screen.getByLabelText('Entrada (ms)')).toBeDisabled();
    expect(window.dispatchEvent(new Event('edivo:before-navigate', { cancelable: true }))).toBe(
      false,
    );
    await act(async () =>
      finish(json({ ...saved, revision_number: 4, trim: { start_ms: 2_000, end_ms: 8_000 } })),
    );
    expect(await screen.findByText('Edição salva · Revisão 4')).toBeVisible();
  });
});

describe('Unsaved work protection', () => {
  it('cancels source changes safely and resets trim only after confirmation', async () => {
    server(saved);
    const video = await open();
    setNumber('Entrada (ms)', 2_000);
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await userEvent.selectOptions(screen.getByLabelText('Vídeo de origem'), second.id);
    expect(screen.getByLabelText('Vídeo de origem')).toHaveValue(source.id);
    expect(screen.getByLabelText('Vídeo do projeto')).toBe(video);
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(2_000);
    await userEvent.selectOptions(screen.getByLabelText('Vídeo de origem'), second.id);
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(0);
    expect(screen.getByLabelText('Saída (ms)')).toHaveValue(5_000);
    expect(screen.getByLabelText('Vídeo do projeto')).not.toBe(video);
  });

  it('protects hash navigation and preserves the exact mounted editor on cancellation', async () => {
    server(saved);
    window.history.replaceState(null, '', `/#/projects/${project.id}/editor`);
    render(<ProjectsWorkspace />);
    await screen.findByRole('heading', { name: 'Editor do projeto.' });
    setNumber('Entrada (ms)', 2_000);
    const video = screen.getByLabelText('Vídeo do projeto');
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await act(async () => {
      window.location.hash = `/projects/${project.id}`;
    });
    await waitFor(() => expect(window.confirm).toHaveBeenCalled());
    expect(window.location.hash).toBe(`#/projects/${project.id}/editor`);
    expect(screen.getByLabelText('Vídeo do projeto')).toBe(video);
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(2_000);
    await act(async () => {
      window.location.hash = `/projects/${project.id}`;
    });
    await waitFor(() =>
      expect(screen.queryByRole('heading', { name: 'Editor do projeto.' })).not.toBeInTheDocument(),
    );
  });

  it('registers beforeunload only when needed and removes protection on unmount', async () => {
    server(saved);
    const view = render(<EditorWorkspace projectId={project.id} />);
    await screen.findByRole('heading', { name: 'Editor do projeto.' });
    expect(window.dispatchEvent(new Event('beforeunload', { cancelable: true }))).toBe(true);
    setNumber('Entrada (ms)', 2_000);
    expect(window.dispatchEvent(new Event('beforeunload', { cancelable: true }))).toBe(false);
    view.unmount();
    expect(window.dispatchEvent(new Event('beforeunload', { cancelable: true }))).toBe(true);
  });
});

describe('Playback stays within the selected region', () => {
  it('clamps a seek before entry while already playing without playing excluded content', async () => {
    render(
      <VideoPlayer
        projectId={project.id}
        asset={source}
        trim={{ start_ms: 1_000, end_ms: 3_000 }}
      />,
    );
    const video = screen.getByLabelText('Vídeo do projeto') as HTMLVideoElement;
    metadata(video);
    await userEvent.click(screen.getByRole('button', { name: 'Reproduzir vídeo' }));
    fireEvent.change(screen.getByLabelText('Buscar no vídeo'), { target: { value: '500' } });
    expect(video.currentTime).toBe(1);
    expect(video.paused).toBe(false);
  });

  it('pauses at the new exit after trimming a playing source', async () => {
    const view = render(
      <VideoPlayer projectId={project.id} asset={source} trim={{ start_ms: 0, end_ms: 10_000 }} />,
    );
    const video = screen.getByLabelText('Vídeo do projeto') as HTMLVideoElement;
    metadata(video);
    await userEvent.click(screen.getByRole('button', { name: 'Reproduzir vídeo' }));
    video.currentTime = 5;
    view.rerender(
      <VideoPlayer
        projectId={project.id}
        asset={source}
        trim={{ start_ms: 1_000, end_ms: 3_000 }}
      />,
    );
    fireEvent.timeUpdate(video);
    expect(video.currentTime).toBe(3);
    expect(video.paused).toBe(true);
  });
});
