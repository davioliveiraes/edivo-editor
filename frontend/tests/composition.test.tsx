import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EditorWorkspace } from '@/features/editor/editor-workspace';
import { AudioPanel } from '@/features/editor/audio-panel';
import { VideoPlayer } from '@/features/editor/video-player';
import {
  defaultAudio,
  revisionSpec,
  type EditSpecRevision,
  type TextOverlay,
} from '@/lib/api/edit-spec';
import type { AudioAsset } from '@/lib/api/audio';
import { asset, FakeXhr, json, page, project } from './fixtures';

const source = { ...asset, duration_ms: 12_000 };
const second = {
  ...source,
  id: '87988183-2041-429b-85ea-1d919e9dcbad',
  original_filename: 'second.mp4',
};
const music: AudioAsset = {
  id: 'f04c4e22-5121-491a-9d06-53c26172a824',
  project_id: project.id,
  original_filename: 'musica.mp3',
  checksum_sha256: 'b'.repeat(64),
  byte_size: 3_000,
  mime_detected: 'audio/mpeg',
  duration_ms: 2_000,
  codec: 'mp3',
  sample_rate: 48_000,
  channels: 1,
  created_at: project.created_at,
};
const text: TextOverlay = {
  id: 'c842041d-d5e9-41c8-ac7f-b9db62bce8df',
  text: 'Edição: ação! Ω',
  start_ms: 2_000,
  end_ms: 10_000,
  position: 'bottom',
  style: 'white_outline',
  size: 'medium',
};
const legacy: EditSpecRevision = {
  id: '8e7c2ae6-9e4f-4c51-9d92-c73ab053a40c',
  project_id: project.id,
  revision_number: 1,
  created_at: project.created_at,
  schema_version: 1,
  source_media_id: source.id,
  trim: { start_ms: 1_000, end_ms: 11_000 },
  canvas: { preset: '9:16', fit: 'cover', focal_x: 0.5, focal_y: 0.5 },
};
const v2: EditSpecRevision = {
  ...legacy,
  schema_version: 2,
  text_overlays: [text],
  audio: { ...defaultAudio(), background_audio_id: music.id, fade_in_ms: 300, fade_out_ms: 700 },
};

beforeEach(() => {
  FakeXhr.instances = [];
  vi.stubGlobal('XMLHttpRequest', FakeXhr);
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  vi.stubGlobal(
    'requestAnimationFrame',
    vi.fn(() => 1),
  );
  vi.stubGlobal('cancelAnimationFrame', vi.fn());
});

function server(initial: EditSpecRevision | null = null, audios: AudioAsset[] = [music]) {
  let revision = initial;
  let fail = 0;
  let audioFail = 0;
  const puts: Record<string, unknown>[] = [];
  const fetchMock = vi.fn(async (url: string, options?: RequestInit) => {
    if (url.includes('/renders?')) return json(page([]));
    if (url.endsWith('/edit-spec')) {
      if (options?.method === 'PUT') {
        const payload = JSON.parse(options.body as string) as Record<string, unknown>;
        puts.push(payload);
        if (fail)
          return json(
            {
              code: fail === 409 ? 'edit_spec_conflict' : 'invalid_request',
              message: '/private/unsafe',
            },
            fail,
          );
        const plan = { ...payload };
        delete plan.expected_revision;
        revision = {
          ...legacy,
          ...plan,
          revision_number: (revision?.revision_number ?? 0) + 1,
        } as EditSpecRevision;
      }
      return revision ? json(revision) : json({ code: 'edit_spec_not_found' }, 404);
    }
    if (url.includes('/audio?'))
      return audioFail
        ? json({ code: 'unavailable', message: '/private/audio' }, audioFail)
        : json(page(audios));
    if (url.endsWith(`/audio/${music.id}`)) return json(music);
    if (url.includes('/media?')) return json(page([source, second]));
    if (url.endsWith(project.id)) return json(project);
    return json(page([]));
  });
  vi.stubGlobal('fetch', fetchMock);
  return {
    puts,
    fetchMock,
    fail: (status: number) => {
      fail = status;
    },
    failAudio: (status: number) => {
      audioFail = status;
    },
    external: (value: EditSpecRevision) => {
      revision = value;
    },
  };
}
async function open() {
  const view = render(<EditorWorkspace projectId={project.id} />);
  await screen.findByRole('heading', { name: 'Editor do projeto.' });
  await waitFor(() => expect(screen.queryByText('Carregando músicas…')).not.toBeInTheDocument());
  return view;
}
function number(label: string, value: number | string) {
  fireEvent.change(screen.getByLabelText(label), { target: { value: String(value) } });
}
function metadata(video: HTMLVideoElement) {
  Object.defineProperty(video, 'duration', { configurable: true, value: 12 });
  fireEvent.loadedMetadata(video);
}
async function addText() {
  await userEvent.click(screen.getByRole('button', { name: 'Adicionar texto' }));
}
async function startUpload() {
  await userEvent.upload(
    screen.getByLabelText('Selecionar música MP3'),
    new File(['synthetic'], 'musica.MP3', { type: 'audio/mpeg' }),
  );
  await userEvent.click(screen.getByRole('button', { name: 'Importar MP3' }));
  return FakeXhr.instances[0]!;
}

describe('Versioned composition and explicit saves', () => {
  it('normalizes v1 only in memory without mutating its snapshot, marking dirty or saving on seek', async () => {
    const snapshot = JSON.stringify(legacy);
    const normalized = revisionSpec(legacy);
    expect(normalized.schema_version).toBe(2);
    expect(normalized.audio).toEqual(defaultAudio());
    expect(normalized.text_overlays).toEqual([]);
    const api = server(legacy);
    await open();
    const video = screen.getByLabelText('Vídeo do projeto') as HTMLVideoElement;
    metadata(video);
    video.currentTime = 3;
    fireEvent.timeUpdate(video);
    expect(screen.getByText('Edição salva · Revisão 1')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Salvar edição' })).toBeDisabled();
    expect(JSON.stringify(legacy)).toBe(snapshot);
    expect(api.puts).toHaveLength(0);
  });

  it('persists text and audio as v2 only after the explicit save action', async () => {
    const api = server(legacy);
    await open();
    await addText();
    fireEvent.change(screen.getByLabelText('Conteúdo do texto 1'), {
      target: { value: 'Título: "ação" \\ Ω\nSegunda linha' },
    });
    await userEvent.selectOptions(screen.getByLabelText('Música de fundo'), music.id);
    number('Volume original', 125);
    number('Volume da música', 12);
    number('Fade-in (ms)', 500);
    number('Fade-out (ms)', 1000);
    await userEvent.click(screen.getByLabelText('Repetir música'));
    expect(api.puts).toHaveLength(0);
    expect(screen.getByText('Alterações não salvas')).toBeVisible();
    await userEvent.click(screen.getByRole('button', { name: 'Salvar edição' }));
    expect(await screen.findByText('Edição salva · Revisão 2')).toBeVisible();
    expect(api.puts[0]).toMatchObject({
      schema_version: 2,
      expected_revision: 1,
      source_media_id: source.id,
      text_overlays: [
        { text: 'Título: "ação" \\ Ω\nSegunda linha', start_ms: 1_000, end_ms: 11_000 },
      ],
      audio: {
        source_volume: 1.25,
        background_audio_id: music.id,
        background_volume: 0.12,
        background_loop: true,
        fade_in_ms: 500,
        fade_out_ms: 1000,
      },
    });
    expect(api.puts[0]).not.toHaveProperty('playhead');
  });

  it('recovers a saved v2 composition including text, music and fades', async () => {
    server(v2);
    await open();
    expect(screen.getByLabelText('Conteúdo do texto 1')).toHaveValue(text.text);
    expect(screen.getByLabelText('Música de fundo')).toHaveValue(music.id);
    expect(screen.getByLabelText('Fade-in (ms)')).toHaveValue(300);
    expect(screen.getByLabelText('Fade-out (ms)')).toHaveValue(700);
    expect(screen.getByRole('button', { name: 'Salvar edição' })).toBeDisabled();
  });

  it('preserves a v2 draft on409 and reloads the current revision only after confirmation', async () => {
    const api = server(v2);
    api.fail(409);
    await open();
    fireEvent.change(screen.getByLabelText('Conteúdo do texto 1'), {
      target: { value: 'Meu rascunho' },
    });
    await userEvent.click(screen.getByRole('button', { name: 'Salvar edição' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Outra revisão');
    expect(screen.getByLabelText('Conteúdo do texto 1')).toHaveValue('Meu rascunho');
    api.external({
      ...v2,
      revision_number: 2,
      schema_version: 2,
      text_overlays: [{ ...text, text: 'Revisão externa' }],
    });
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await userEvent.click(screen.getByRole('button', { name: 'Recarregar revisão atual' }));
    expect(screen.getByLabelText('Conteúdo do texto 1')).toHaveValue('Meu rascunho');
    await userEvent.click(screen.getByRole('button', { name: 'Recarregar revisão atual' }));
    expect(await screen.findByDisplayValue('Revisão externa')).toBeVisible();
    expect(screen.getByText('Edição salva · Revisão 2')).toBeVisible();
    expect(api.puts).toHaveLength(1);
  });
});

describe('Text controls and native overlay timing', () => {
  it('adds up to three texts and removes a text without an implicit save', async () => {
    const api = server();
    await open();
    await addText();
    await addText();
    await addText();
    expect(screen.getByRole('button', { name: 'Adicionar texto' })).toBeDisabled();
    expect(screen.getAllByRole('textbox')).toHaveLength(3);
    await userEvent.click(screen.getByRole('button', { name: 'Remover texto 2' }));
    expect(screen.getAllByRole('textbox')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Adicionar texto' })).toBeEnabled();
    expect(api.puts).toHaveLength(0);
  });

  it.each([
    ['Posição do texto 1', 'top'],
    ['Posição do texto 1', 'center'],
    ['Posição do texto 1', 'bottom'],
    ['Estilo do texto 1', 'white_outline'],
    ['Estilo do texto 1', 'blue_outline'],
    ['Estilo do texto 1', 'white_plain'],
    ['Tamanho do texto 1', 'small'],
    ['Tamanho do texto 1', 'medium'],
    ['Tamanho do texto 1', 'large'],
  ])('supports the controlled option %s=%s', async (label, value) => {
    server();
    await open();
    await addText();
    await userEvent.selectOptions(screen.getByLabelText(label), value);
    expect(screen.getByLabelText(label)).toHaveValue(value);
    expect(screen.getByRole('button', { name: 'Salvar edição' })).toBeEnabled();
  });

  it.each([
    ['', '1 a 120 caracteres'],
    ['x'.repeat(121), '1 a 120 caracteres'],
    ['a\nb\nc', 'duas linhas'],
    ['a\u200bb', 'caracteres invisíveis'],
    ['<script>alert(1)</script>', 'sem marcação'],
    ['%{n}', 'sem marcação'],
  ])('keeps an invalid text editable and blocks save: %j', async (value, message) => {
    const api = server();
    await open();
    await addText();
    fireEvent.change(screen.getByLabelText('Conteúdo do texto 1'), { target: { value } });
    expect(screen.getByRole('alert')).toHaveTextContent(message);
    expect(screen.getByRole('button', { name: 'Salvar edição' })).toBeDisabled();
    expect(document.querySelector('script')).toBeNull();
    expect(api.puts).toHaveLength(0);
  });

  it('counts Unicode code points, accepting 120 supported characters and literal punctuation', async () => {
    server();
    await open();
    await addText();
    const valid = 'Á: "ação" \\ Ω\n' + 'é'.repeat(104);
    fireEvent.change(screen.getByLabelText('Conteúdo do texto 1'), { target: { value: valid } });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Salvar edição' })).toBeEnabled();
    fireEvent.change(screen.getByLabelText('Conteúdo do texto 1'), {
      target: { value: '\u{10400}'.repeat(120) },
    });
    expect(screen.getByText('120/120 caracteres · Até duas linhas')).toBeVisible();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('requires absolute integer text intervals inside the trim', async () => {
    server(legacy);
    await open();
    await addText();
    number('Início do texto 1 (ms)', 999);
    expect(screen.getByRole('alert')).toHaveTextContent('dentro do trecho');
    number('Início do texto 1 (ms)', 1_000);
    number('Fim do texto 1 (ms)', 11_001);
    expect(screen.getByRole('button', { name: 'Salvar edição' })).toBeDisabled();
    number('Fim do texto 1 (ms)', 2_000.5);
    expect(screen.getByRole('alert')).toHaveTextContent('inteiros');
    number('Fim do texto 1 (ms)', 2_000);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows text only in its interval according to native currentTime, including seek', async () => {
    server(v2);
    await open();
    const video = screen.getByLabelText('Vídeo do projeto') as HTMLVideoElement;
    metadata(video);
    const visibleText = () => document.querySelector('.composition-text');
    expect(visibleText()).toBeNull();
    video.currentTime = 2;
    fireEvent.timeUpdate(video);
    expect(visibleText()).toHaveTextContent(text.text);
    expect(visibleText()).toHaveAttribute('data-position', 'bottom');
    fireEvent.change(screen.getByLabelText('Buscar no vídeo'), { target: { value: '10000' } });
    expect(visibleText()).toBeNull();
    video.currentTime = 3;
    fireEvent.seeked(video);
    expect(visibleText()).toHaveTextContent(text.text);
    expect(document.querySelector('.composition-text-layer')).toHaveAttribute(
      'aria-hidden',
      'true',
    );
  });

  it('blocks incompatible trim changes and preserves texts until the user adjusts them', async () => {
    server(v2);
    await open();
    number('Entrada (ms)', 3_000);
    expect(screen.getByRole('alert')).toHaveTextContent('Ajuste os tempos dos textos');
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(1_000);
    expect(screen.getByLabelText('Conteúdo do texto 1')).toHaveValue(text.text);
    number('Início do texto 1 (ms)', 3_000);
    number('Entrada (ms)', 3_000);
    expect(screen.getByLabelText('Entrada (ms)')).toHaveValue(3_000);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('confirms discarding overlays on a source change even when the saved revision is clean', async () => {
    server(v2);
    await open();
    vi.mocked(window.confirm).mockReturnValueOnce(false);
    await userEvent.selectOptions(screen.getByLabelText('Vídeo de origem'), second.id);
    expect(window.confirm).toHaveBeenCalledWith(
      expect.stringContaining('inclusive os da edição salva'),
    );
    expect(screen.getByLabelText('Vídeo de origem')).toHaveValue(source.id);
    expect(screen.getByLabelText('Conteúdo do texto 1')).toHaveValue(text.text);
    await userEvent.selectOptions(screen.getByLabelText('Vídeo de origem'), second.id);
    expect(screen.queryByLabelText('Conteúdo do texto 1')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Música de fundo')).toHaveValue(music.id);
    expect(screen.getByText('Alterações não salvas')).toBeVisible();
  });
});

describe('Audio mix and safe MP3 import', () => {
  it('offers optional music and applies native volume only up to100 percent', async () => {
    server(legacy);
    await open();
    const video = screen.getByLabelText('Vídeo do projeto') as HTMLVideoElement;
    expect(screen.getByLabelText('Música de fundo')).toHaveValue('');
    expect(screen.getByLabelText('Volume da música')).toBeDisabled();
    number('Volume original', 50);
    expect(video.volume).toBe(0.5);
    number('Volume original', 200);
    expect(video.volume).toBe(1);
    expect(screen.getByText(/Música, fades do mix e ganho acima/)).toBeVisible();
    await userEvent.selectOptions(screen.getByLabelText('Música de fundo'), music.id);
    expect(screen.getByLabelText('Volume da música')).toBeEnabled();
    expect(document.querySelector('audio')).toBeNull();
  });

  it('describes silent source video without requiring music', () => {
    render(
      <AudioPanel
        projectId={project.id}
        audio={defaultAudio()}
        hasSourceAudio={false}
        disabled={false}
        onChange={vi.fn()}
        onBusyChange={vi.fn()}
      />,
    );
    expect(screen.getByLabelText('Volume original')).toBeDisabled();
    expect(screen.getByText('A fonte original é silenciosa. Uma música é opcional.')).toBeVisible();
  });

  it.each([
    [-1, 0],
    [3001, 0],
    [100.5, 0],
    [3000, 3000],
  ])('validates fade ranges and combined trim length (%s,%s)', async (start, end) => {
    server({ ...legacy, trim: { start_ms: 1_000, end_ms: 5_000 } });
    await open();
    number('Fade-in (ms)', start);
    number('Fade-out (ms)', end);
    expect(screen.getByRole('alert')).toHaveTextContent('Use fades de 0 a 3000 ms');
    expect(screen.getByRole('button', { name: 'Salvar edição' })).toBeDisabled();
  });

  it('prevents a trim change from silently shortening configured fades', async () => {
    server(legacy);
    await open();
    number('Fade-in (ms)', 3_000);
    number('Fade-out (ms)', 3_000);
    number('Saída (ms)', 4_000);
    expect(screen.getByRole('alert')).toHaveTextContent('Reduza os fades');
    expect(screen.getByLabelText('Saída (ms)')).toHaveValue(11_000);
  });

  it('retrieves selected music outside the library page and displays its checksum', async () => {
    const api = server(v2, []);
    await open();
    await waitFor(() =>
      expect(screen.getByLabelText('Música de fundo')).toHaveTextContent(music.original_filename),
    );
    expect(api.fetchMock).toHaveBeenCalledWith(
      `/api/v1/projects/${project.id}/audio/${music.id}`,
      expect.any(Object),
    );
    await userEvent.click(screen.getByText('Informações da música'));
    expect(screen.getByText(music.checksum_sha256)).toBeVisible();
  });

  it('does not block video editing when the music library fails, and retries safely', async () => {
    const api = server(legacy);
    api.failAudio(503);
    await open();
    expect(screen.getByRole('alert')).toHaveTextContent('temporariamente indisponível');
    expect(screen.getByRole('button', { name: 'Adicionar texto' })).toBeEnabled();
    expect(screen.queryByText('/private/audio')).not.toBeInTheDocument();
    api.failAudio(0);
    await userEvent.click(screen.getByRole('button', { name: 'Recarregar músicas' }));
    expect(await screen.findByRole('option', { name: /musica.mp3/ })).toBeInTheDocument();
  });

  it('validates extension, zero bytes, count and the independent30MiB limit before transfer', async () => {
    server();
    await open();
    const input = screen.getByLabelText('Selecionar música MP3');
    const user = userEvent.setup({ applyAccept: false });
    await user.upload(input, new File(['x'], 'fake.mp4', { type: 'audio/mpeg' }));
    expect(screen.getByRole('alert')).toHaveTextContent('extensão .mp3');
    await user.upload(input, new File([], 'empty.mp3'));
    expect(screen.getByRole('alert')).toHaveTextContent('arquivo está vazio');
    const large = new File(['x'], 'large.mp3');
    Object.defineProperty(large, 'size', { value: 31_457_281 });
    await user.upload(input, large);
    expect(screen.getByRole('alert')).toHaveTextContent('ultrapassa 30 MiB');
    fireEvent.change(input, {
      target: { files: [new File(['x'], 'a.mp3'), new File(['x'], 'b.mp3')] },
    });
    expect(screen.getByRole('alert')).toHaveTextContent('um único MP3');
    expect(FakeXhr.instances).toHaveLength(0);
    expect(screen.getByText(/Você é responsável pelos direitos de uso/)).toBeVisible();
  });

  it('reports actual MP3 transfer separately from analysis and selects music only after201', async () => {
    const api = server(legacy);
    await open();
    const xhr = await startUpload();
    expect(xhr.open).toHaveBeenCalledWith('POST', `/api/v1/projects/${project.id}/audio`);
    const form = xhr.send.mock.calls[0]?.[0] as FormData;
    expect([...form.keys()]).toEqual(['file']);
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value');
    expect(screen.getByRole('button', { name: 'Salvar edição' })).toBeDisabled();
    expect(window.dispatchEvent(new Event('edivo:before-navigate', { cancelable: true }))).toBe(
      false,
    );
    act(() =>
      xhr.upload.onprogress?.(
        new ProgressEvent('progress', { lengthComputable: true, loaded: 42, total: 100 }),
      ),
    );
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '42');
    act(() => xhr.upload.onload?.());
    expect(screen.getByText('Analisando áudio…')).toBeVisible();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.getByLabelText('Música de fundo')).toHaveValue('');
    await act(async () => xhr.respond(music));
    expect(screen.getByLabelText('Música de fundo')).toHaveValue(music.id);
    expect(screen.getByText(/importado e selecionado/)).toBeVisible();
    expect(screen.getByText('Alterações não salvas')).toBeVisible();
    expect(api.puts).toHaveLength(0);
  });

  it.each([
    [400, 'invalid_multipart', 'um único arquivo MP3'],
    [413, 'upload_too_large', 'limite de upload'],
    [415, 'unsupported_audio', 'MP3 válido'],
    [422, 'invalid_audio', 'validar o MP3'],
    [422, 'audio_duration_exceeded', 'duração permitida'],
    [503, 'probe_timeout', 'análise do áudio'],
    [503, 'storage_unavailable', 'temporariamente indisponível'],
  ])('sanitizes failed MP3 import%s/%s without claiming success', async (status, code, message) => {
    server(legacy);
    await open();
    const xhr = await startUpload();
    await act(async () => xhr.respond({ code, message: '/private/audio-path' }, status));
    expect(screen.getByRole('alert')).toHaveTextContent(message);
    expect(screen.queryByText(/private\/audio-path/)).not.toBeInTheDocument();
    expect(screen.getByLabelText('Música de fundo')).toHaveValue('');
    expect(screen.getByRole('button', { name: 'Importar MP3' })).toBeEnabled();
  });

  it('aborts an in-flight MP3 upload on unmount and releases navigation protection', async () => {
    server(legacy);
    const view = await open();
    const xhr = await startUpload();
    view.unmount();
    expect(xhr.abort).toHaveBeenCalledOnce();
    expect(window.dispatchEvent(new Event('beforeunload', { cancelable: true }))).toBe(true);
  });

  it('reflects native source gain without creating a second media player', () => {
    const view = render(
      <VideoPlayer
        projectId={project.id}
        asset={source}
        trim={{ start_ms: 0, end_ms: 12_000 }}
        sourceVolume={0.25}
      />,
    );
    expect(screen.getByLabelText('Vídeo do projeto')).toHaveProperty('volume', 0.25);
    view.rerender(
      <VideoPlayer
        projectId={project.id}
        asset={source}
        trim={{ start_ms: 0, end_ms: 12_000 }}
        sourceVolume={2}
      />,
    );
    expect(screen.getByLabelText('Vídeo do projeto')).toHaveProperty('volume', 1);
    expect(
      within(screen.getByRole('region', { name: 'Player de vídeo' })).getAllByRole('button'),
    ).toHaveLength(2);
  });
});
