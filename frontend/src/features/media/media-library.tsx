import { useEffect, useRef, useState, type DragEvent } from 'react';
import {
  Check,
  FileVideo,
  HardDrive,
  LoaderCircle,
  RefreshCw,
  ShieldCheck,
  Upload,
  Volume2,
  VolumeX,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import { uploadMedia, type MediaAsset, type UploadProgress } from '@/lib/api/media';
import { publicError, type Page } from '@/lib/api/projects';
import { useResource } from '@/lib/api/use-resource';
import { formatBytes, formatDate, formatDuration, formatFps } from '@/lib/format';
import { Pagination } from '@/features/projects/pagination';

function positiveSetting(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}

const MAX_UPLOAD_BYTES = positiveSetting(import.meta.env.VITE_UPLOAD_MAX_BYTES, 524_288_000);
const MAX_DURATION_SECONDS = positiveSetting(import.meta.env.VITE_MEDIA_MAX_DURATION_SECONDS, 600);

export function MediaLibrary({
  projectId,
  onBusyChange,
}: {
  projectId: string;
  onBusyChange: (busy: boolean) => void;
}) {
  const [offset, setOffset] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const controllerRef = useRef<AbortController | null>(null);
  const busy = progress !== null;
  const {
    data,
    error: libraryError,
    loading,
    reload,
  } = useResource<Page<MediaAsset>>(`/${projectId}/media?limit=20&offset=${offset}`);

  useEffect(() => {
    onBusyChange(busy);
  }, [busy, onBusyChange]);
  useEffect(
    () => () => {
      controllerRef.current?.abort();
      onBusyChange(false);
    },
    [onBusyChange],
  );

  function choose(next: File | undefined) {
    if (busy) return;
    setError('');
    setSuccess('');
    setFile(null);
    if (!next) return;
    if (!/\.mp4$/i.test(next.name)) {
      setError('Selecione um arquivo com extensão .mp4.');
      return;
    }
    if (next.size === 0) {
      setError('O arquivo está vazio. Selecione um vídeo MP4 válido.');
      return;
    }
    if (next.size > MAX_UPLOAD_BYTES) {
      setError(`O arquivo ultrapassa ${formatBytes(MAX_UPLOAD_BYTES)}. Selecione um vídeo menor.`);
      return;
    }
    setFile(next);
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    if (busy) return;
    if (event.dataTransfer.files.length !== 1) {
      setFile(null);
      setSuccess('');
      setError('Selecione um único vídeo MP4 por importação.');
      return;
    }
    choose(event.dataTransfer.files[0]);
  }

  async function importVideo() {
    if (!file || busy) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setProgress({ stage: 'transferring', percent: null });
    setError('');
    setSuccess('');
    try {
      const asset = await uploadMedia(projectId, file, controller.signal, setProgress);
      if (controller.signal.aborted) return;
      setSuccess(
        `${asset.original_filename} importado. O original foi preservado e os metadados estão disponíveis na biblioteca.`,
      );
      setFile(null);
      if (inputRef.current) inputRef.current.value = '';
      if (offset === 0) reload();
      else setOffset(0);
    } catch (failure) {
      if (!controller.signal.aborted) setError(publicError(failure));
    } finally {
      if (!controller.signal.aborted) setProgress(null);
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }

  return (
    <div className="media-workspace">
      <section className="upload-panel" aria-labelledby="upload-title">
        <div className="panel-heading">
          <Upload size={19} aria-hidden="true" />
          <h3 id="upload-title">Importar vídeo</h3>
        </div>
        <p className="panel-description">
          Traga um original para este projeto. Seus arquivos permanecem no ambiente local.
        </p>
        <div
          className={`upload-dropzone${dragging ? ' dragging' : ''}${busy ? ' is-busy' : ''}`}
          onDragOver={(event) => {
            event.preventDefault();
            if (!busy) setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={drop}
        >
          <FileVideo size={29} strokeWidth={1.4} aria-hidden="true" />
          <label htmlFor="media-file" className="file-label">
            Selecionar vídeo MP4
          </label>
          <span className="drop-hint">ou arraste um arquivo para cá</span>
          <input
            ref={inputRef}
            id="media-file"
            className="file-input"
            type="file"
            accept=".mp4,video/mp4"
            disabled={busy}
            aria-describedby="upload-limits"
            onChange={(event) => choose(event.target.files?.[0])}
          />
        </div>
        <p className="field-help" id="upload-limits">
          Um MP4 por vez · Até {formatBytes(MAX_UPLOAD_BYTES)} · Até {MAX_DURATION_SECONDS / 60}{' '}
          minutos. O servidor valida formato, conteúdo e duração.
        </p>
        {file && (
          <div className="selected-file">
            <FileVideo size={17} aria-hidden="true" />
            <span>{file.name}</span>
            <small>{formatBytes(file.size)}</small>
          </div>
        )}
        {progress && (
          <div className="upload-status" role="status" aria-live="polite">
            <div className="upload-status-label">
              <LoaderCircle size={16} className="animate-spin" aria-hidden="true" />
              <span>
                {progress.stage === 'analyzing' ? 'Analisando vídeo…' : 'Transferindo arquivo…'}
              </span>
              {progress.stage === 'transferring' && progress.percent !== null && (
                <strong>{progress.percent}%</strong>
              )}
            </div>
            {progress.stage === 'transferring' && (
              <progress
                aria-label="Progresso da transferência"
                max={100}
                value={progress.percent ?? undefined}
              />
            )}
            <p>
              {progress.stage === 'analyzing'
                ? 'Transferência recebida. Aguardando validação e metadados, sem previsão percentual.'
                : 'Mantenha esta página aberta até a confirmação da importação.'}
            </p>
          </div>
        )}
        {error && (
          <p className="feedback error" role="alert">
            {error}
          </p>
        )}
        {success && (
          <p className="feedback success" role="status">
            <Check size={17} aria-hidden="true" />
            {success}
          </p>
        )}
        <Button
          className="import-button"
          disabled={!file || busy}
          onClick={() => void importVideo()}
        >
          {busy ? (
            <LoaderCircle className="animate-spin" aria-hidden="true" />
          ) : (
            <Upload aria-hidden="true" />
          )}
          {busy ? 'Importando…' : 'Importar MP4'}
        </Button>
        <p className="original-note">
          <ShieldCheck size={14} aria-hidden="true" />
          Original preservado. Nenhuma edição é aplicada.
        </p>
      </section>
      <section className="library-panel" aria-labelledby="library-title">
        <div className="library-heading">
          <div>
            <p className="section-kicker">ARQUIVOS DO PROJETO</p>
            <h3 id="library-title">Biblioteca de mídia</h3>
          </div>
          {data && (
            <span className="asset-count">
              {data.total} {data.total === 1 ? 'vídeo' : 'vídeos'}
            </span>
          )}
        </div>
        {loading ? (
          <div className="workspace-state compact-state" role="status">
            <LoaderCircle className="animate-spin" aria-hidden="true" />
            <p>Carregando vídeos…</p>
          </div>
        ) : libraryError ? (
          <div className="workspace-state compact-state">
            <p role="alert">{publicError(libraryError)}</p>
            <Button variant="outline" onClick={reload}>
              <RefreshCw aria-hidden="true" />
              Recarregar biblioteca
            </Button>
          </div>
        ) : data?.items.length === 0 ? (
          <div className="workspace-state empty-library">
            <HardDrive size={31} strokeWidth={1.3} aria-hidden="true" />
            <h4>Os primeiros vídeos chegam por aqui.</h4>
            <p>Importe um MP4 para consultar duração, formato e informações técnicas.</p>
          </div>
        ) : (
          <>
            <div className="media-list">
              {data?.items.map((asset) => (
                <article className="media-card" key={asset.id}>
                  <div className="media-title">
                    <span className="media-symbol">
                      <FileVideo size={24} strokeWidth={1.4} aria-hidden="true" />
                    </span>
                    <div>
                      <h4>{asset.original_filename}</h4>
                      <p>
                        Importado em{' '}
                        <time dateTime={asset.created_at}>{formatDate(asset.created_at)}</time>
                      </p>
                    </div>
                    <span className="media-format">MP4</span>
                  </div>
                  <dl className="media-metadata">
                    <div>
                      <dt>Duração</dt>
                      <dd>{formatDuration(asset.duration_ms)}</dd>
                    </div>
                    <div>
                      <dt>Resolução</dt>
                      <dd>
                        {asset.width} × {asset.height}
                      </dd>
                    </div>
                    <div>
                      <dt>Quadros</dt>
                      <dd>{formatFps(asset.fps_num, asset.fps_den)}</dd>
                    </div>
                    <div>
                      <dt>Tamanho</dt>
                      <dd title={`${asset.byte_size.toLocaleString('pt-BR')} bytes`}>
                        {formatBytes(asset.byte_size)}
                      </dd>
                    </div>
                    <div>
                      <dt>Vídeo</dt>
                      <dd>{asset.video_codec?.toUpperCase() || 'Não informado'}</dd>
                    </div>
                    <div>
                      <dt>Áudio</dt>
                      <dd className="audio-value">
                        {asset.has_audio ? (
                          <Volume2 size={14} aria-hidden="true" />
                        ) : (
                          <VolumeX size={14} aria-hidden="true" />
                        )}
                        {asset.has_audio
                          ? asset.audio_codec?.toUpperCase() || 'Presente'
                          : 'Sem áudio'}
                      </dd>
                    </div>
                  </dl>
                  <details className="integrity-details">
                    <summary>
                      <ShieldCheck size={13} aria-hidden="true" />
                      Integridade do original
                    </summary>
                    <dl>
                      <div>
                        <dt>SHA-256</dt>
                        <dd>
                          <code>{asset.checksum_sha256}</code>
                        </dd>
                      </div>
                      <div>
                        <dt>Tamanho verificado</dt>
                        <dd>
                          {asset.byte_size.toLocaleString('pt-BR')} bytes · {asset.mime_detected}
                        </dd>
                      </div>
                    </dl>
                  </details>
                </article>
              ))}
            </div>
            {data && (
              <Pagination
                total={data.total}
                offset={offset}
                count={data.items.length}
                label="vídeos"
                onChange={setOffset}
              />
            )}
          </>
        )}
      </section>
    </div>
  );
}
