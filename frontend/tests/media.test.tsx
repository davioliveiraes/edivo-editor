import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MediaLibrary } from '@/features/media/media-library';
import { asset, FakeXhr, json, page, project } from './fixtures';

const validFile = () =>
  new File(['synthetic fixture'], 'colecao.MP4', { type: 'application/octet-stream' });

beforeEach(() => {
  FakeXhr.instances = [];
  vi.stubGlobal('XMLHttpRequest', FakeXhr);
});

async function startUpload() {
  await userEvent
    .setup({ applyAccept: false })
    .upload(screen.getByLabelText('Selecionar vídeo MP4'), validFile());
  await userEvent.click(screen.getByRole('button', { name: 'Importar MP4' }));
  const xhr = FakeXhr.instances[0];
  if (!xhr) throw new Error('Expected upload transport');
  return xhr;
}

describe('Media library and import', () => {
  it('renders verified metadata, rational FPS, size, audio and integrity details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(page([asset]))));
    render(<MediaLibrary projectId={project.id} onBusyChange={vi.fn()} />);
    expect(await screen.findByRole('heading', { name: asset.original_filename })).toBeVisible();
    expect(screen.getByText('00:01,501')).toBeVisible();
    expect(screen.getByText('1920 × 1080')).toBeVisible();
    expect(screen.getByText('29,97 FPS · 30000/1001')).toBeVisible();
    expect(screen.getByText('1 MiB')).toBeVisible();
    expect(screen.getByText('H264')).toBeVisible();
    expect(screen.getByText('AAC')).toBeVisible();
    await userEvent.click(screen.getByText('Integridade do original'));
    expect(screen.getByText(asset.checksum_sha256)).toBeVisible();
    expect(screen.getByText('1.048.576 bytes · video/mp4')).toBeVisible();
    expect(document.querySelector('video')).toBeNull();
    expect(document.querySelector('audio')).toBeNull();
  });

  it('accepts and describes a valid source without audio', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockResolvedValue(
          json(page([{ ...asset, has_audio: false, audio_codec: null, fps_num: 24, fps_den: 1 }])),
        ),
    );
    render(<MediaLibrary projectId={project.id} onBusyChange={vi.fn()} />);
    expect(await screen.findByText('Sem áudio')).toBeVisible();
    expect(screen.getByText('24 FPS')).toBeVisible();
  });

  it('distinguishes loading, library failure and a successful empty retry', async () => {
    vi.stubGlobal(
      'fetch',
      vi
        .fn()
        .mockRejectedValueOnce(new Error('private-path'))
        .mockResolvedValueOnce(json(page([]))),
    );
    render(<MediaLibrary projectId={project.id} onBusyChange={vi.fn()} />);
    expect(screen.getByText('Carregando vídeos…')).toBeVisible();
    expect(await screen.findByRole('alert')).toHaveTextContent('Verifique a conexão');
    expect(screen.queryByText('private-path')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Recarregar biblioteca' }));
    expect(await screen.findByText('Os primeiros vídeos chegam por aqui.')).toBeVisible();
  });

  it('rejects wrong extensions, empty files and over-limit files before transfer', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(page([]))));
    render(<MediaLibrary projectId={project.id} onBusyChange={vi.fn()} />);
    const input = screen.getByLabelText('Selecionar vídeo MP4');
    const user = userEvent.setup({ applyAccept: false });
    await user.upload(input, new File(['fake'], 'fake.exe', { type: 'video/mp4' }));
    expect(screen.getByRole('alert')).toHaveTextContent('extensão .mp4');
    await user.upload(input, new File([], 'vazio.mp4'));
    expect(screen.getByRole('alert')).toHaveTextContent('arquivo está vazio');
    const tooLarge = validFile();
    Object.defineProperty(tooLarge, 'size', { value: 524_288_001 });
    await user.upload(input, tooLarge);
    expect(screen.getByRole('alert')).toHaveTextContent('ultrapassa 500 MiB');
    expect(screen.getByRole('button', { name: 'Importar MP4' })).toBeDisabled();
    expect(FakeXhr.instances).toHaveLength(0);
  });

  it('rejects a multi-file drop instead of silently choosing a file', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(page([]))));
    render(<MediaLibrary projectId={project.id} onBusyChange={vi.fn()} />);
    fireEvent.drop(screen.getByLabelText('Selecionar vídeo MP4').parentElement!, {
      dataTransfer: { files: [validFile(), validFile()] },
    });
    expect(screen.getByRole('alert')).toHaveTextContent('um único vídeo');
    expect(FakeXhr.instances).toHaveLength(0);
  });

  it('uses real transport progress, a separate inspection phase and only confirms a 201 import', async () => {
    let imported = false;
    const fetchMock = vi.fn(async () => json(page(imported ? [asset] : [])));
    const busy = vi.fn();
    vi.stubGlobal('fetch', fetchMock);
    render(<MediaLibrary projectId={project.id} onBusyChange={busy} />);
    await screen.findByText('Os primeiros vídeos chegam por aqui.');
    const xhr = await startUpload();
    expect(xhr.open).toHaveBeenCalledWith('POST', `/api/v1/projects/${project.id}/media`);
    const form = xhr.send.mock.calls[0]?.[0] as FormData | undefined;
    expect(form?.get('file')).toBeInstanceOf(File);
    expect([...form!.keys()]).toEqual(['file']);
    expect(screen.getByText('Transferindo arquivo…')).toBeVisible();
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value');
    expect(busy).toHaveBeenLastCalledWith(true);
    act(() =>
      xhr.upload.onprogress?.(
        new ProgressEvent('progress', { lengthComputable: true, loaded: 30, total: 100 }),
      ),
    );
    expect(screen.getByRole('progressbar')).toHaveAttribute('value', '30');
    expect(screen.getByText('30%')).toBeVisible();
    act(() =>
      xhr.upload.onprogress?.(
        new ProgressEvent('progress', { lengthComputable: true, loaded: 100, total: 100 }),
      ),
    );
    expect(screen.getByText('Transferindo arquivo…')).toBeVisible();
    expect(screen.queryByText('Analisando vídeo…')).not.toBeInTheDocument();
    act(() => xhr.upload.onload?.());
    expect(screen.getByText('Analisando vídeo…')).toBeVisible();
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByText('100%')).not.toBeInTheDocument();
    expect(screen.queryByText(/importado. O original/)).not.toBeInTheDocument();
    imported = true;
    await act(async () => xhr.respond(asset));
    expect(await screen.findByRole('heading', { name: asset.original_filename })).toBeVisible();
    expect(screen.getByText(/importado. O original foi preservado/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Importar MP4' })).toBeDisabled();
    expect(busy).toHaveBeenLastCalledWith(false);
  });

  it('keeps progress indeterminate when the browser has no length information', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(page([]))));
    render(<MediaLibrary projectId={project.id} onBusyChange={vi.fn()} />);
    const xhr = await startUpload();
    act(() =>
      xhr.upload.onprogress?.(
        new ProgressEvent('progress', { lengthComputable: false, loaded: 20 }),
      ),
    );
    expect(screen.getByRole('progressbar')).not.toHaveAttribute('value');
    expect(screen.queryByText(/\d+%/)).not.toBeInTheDocument();
  });

  it.each([
    [413, 'upload_too_large', 'ultrapassa o limite'],
    [415, 'unsupported_media', 'precisa ser um vídeo MP4 válido'],
    [422, 'invalid_media', 'Não foi possível validar'],
    [422, 'media_duration_exceeded', 'ultrapassa a duração permitida'],
    [503, 'probe_timeout', 'análise do vídeo demorou'],
    [503, 'storage_unavailable', 'temporariamente indisponível'],
  ])('handles HTTP %s (%s) without claiming success', async (status, code, message) => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(page([]))));
    render(<MediaLibrary projectId={project.id} onBusyChange={vi.fn()} />);
    const xhr = await startUpload();
    await act(async () => xhr.respond({ code, message: 'private-path-never-display' }, status));
    expect(await screen.findByRole('alert')).toHaveTextContent(message);
    expect(screen.queryByText(/private-path/)).not.toBeInTheDocument();
    expect(screen.queryByText(/importado. O original/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Importar MP4' })).toBeEnabled();
  });

  it.each(['onerror', 'ontimeout'] as const)(
    'recovers from %s with a retryable selection',
    async (event) => {
      vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(page([]))));
      render(<MediaLibrary projectId={project.id} onBusyChange={vi.fn()} />);
      const xhr = await startUpload();
      await act(async () => xhr[event]?.());
      expect(await screen.findByRole('alert')).toHaveTextContent('Verifique a conexão');
      expect(screen.getByRole('button', { name: 'Importar MP4' })).toBeEnabled();
    },
  );

  it('aborts its transport when leaving the project', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json(page([]))));
    const busy = vi.fn();
    const { unmount } = render(<MediaLibrary projectId={project.id} onBusyChange={busy} />);
    const xhr = await startUpload();
    unmount();
    await waitFor(() => expect(xhr.abort).toHaveBeenCalledOnce());
    expect(busy).toHaveBeenLastCalledWith(false);
  });

  it('paginates the project library through relative requests', async () => {
    const first = Array.from({ length: 20 }, (_, index) => ({
      ...asset,
      id: `fixture-${index}`,
      original_filename: `video-${index}.mp4`,
    }));
    const fetchMock = vi.fn(async (url: string) =>
      json(url.includes('offset=20') ? page([asset], 21, 20) : page(first, 21)),
    );
    vi.stubGlobal('fetch', fetchMock);
    render(<MediaLibrary projectId={project.id} onBusyChange={vi.fn()} />);
    await screen.findByRole('heading', { name: 'video-0.mp4' });
    await userEvent.click(screen.getByRole('button', { name: 'Próxima página de vídeos' }));
    expect(await screen.findByRole('heading', { name: asset.original_filename })).toBeVisible();
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/v1/projects/${project.id}/media?limit=20&offset=20`,
      expect.any(Object),
    );
    expect(screen.getByRole('button', { name: 'Próxima página de vídeos' })).toBeDisabled();
  });
});
