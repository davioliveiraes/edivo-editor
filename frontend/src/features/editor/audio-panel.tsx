import { useEffect, useRef, useState } from 'react';
import { Check, FileAudio, LoaderCircle, Music2, Upload } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Pagination } from '@/features/projects/pagination';
import { audioError, uploadAudio, type AudioAsset } from '@/lib/api/audio';
import type { AudioSpec } from '@/lib/api/edit-spec';
import type { UploadProgress } from '@/lib/api/media';
import { projectRequest, type Page } from '@/lib/api/projects';
import { useResource } from '@/lib/api/use-resource';
import { formatBytes, formatDuration } from '@/lib/format';

function positiveSetting(value: unknown, fallback: number): number {
  const parsed = Number(value);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : fallback;
}
const maxBytes = positiveSetting(import.meta.env.VITE_AUDIO_UPLOAD_MAX_BYTES, 31_457_280);
const maxSeconds = positiveSetting(import.meta.env.VITE_AUDIO_MAX_DURATION_SECONDS, 600);

interface AudioPanelProps {
  projectId: string;
  audio: AudioSpec;
  hasSourceAudio: boolean;
  disabled: boolean;
  onChange: (audio: AudioSpec) => void;
  onBusyChange: (busy: boolean) => void;
}

export function AudioPanel({
  projectId,
  audio,
  hasSourceAudio,
  disabled,
  onChange,
  onBusyChange,
}: AudioPanelProps) {
  const [offset, setOffset] = useState(0);
  const [file, setFile] = useState<File | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [selected, setSelected] = useState<AudioAsset | null>(null);
  const [selectedError, setSelectedError] = useState('');
  const [selectionAttempt, setSelectionAttempt] = useState(0);
  const controllerRef = useRef<AbortController | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const busy = progress !== null;
  const locked = disabled || busy;
  const {
    data,
    loading,
    error: libraryError,
    reload,
  } = useResource<Page<AudioAsset>>(`/${projectId}/audio?limit=20&offset=${offset}`);
  const listedSelection = data?.items.find((item) => item.id === audio.background_audio_id);
  const selectedAsset =
    listedSelection ?? (selected?.id === audio.background_audio_id ? selected : null);
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
  useEffect(() => {
    if (!audio.background_audio_id || listedSelection) return;
    const controller = new AbortController();
    void projectRequest<AudioAsset>(`/${projectId}/audio/${audio.background_audio_id}`, {
      signal: controller.signal,
    })
      .then((asset) => {
        if (!controller.signal.aborted) {
          setSelected(asset);
          setSelectedError('');
        }
      })
      .catch((failure: unknown) => {
        if (!controller.signal.aborted) setSelectedError(audioError(failure));
      });
    return () => controller.abort();
  }, [audio.background_audio_id, listedSelection, projectId, selectionAttempt]);

  function choose(files: FileList | null) {
    if (locked) return;
    setFile(null);
    setError('');
    setSuccess('');
    if (!files?.length) return;
    if (files.length !== 1) {
      setError('Selecione um único MP3 por importação.');
      return;
    }
    const next = files[0]!;
    if (!/\.mp3$/i.test(next.name)) {
      setError('Selecione um arquivo com extensão .mp3.');
      return;
    }
    if (next.size === 0) {
      setError('O arquivo está vazio. Selecione um MP3 válido.');
      return;
    }
    if (next.size > maxBytes) {
      setError(`O arquivo ultrapassa ${formatBytes(maxBytes)}. Selecione um MP3 menor.`);
      return;
    }
    setFile(next);
  }

  async function importMusic() {
    if (!file || locked) return;
    const controller = new AbortController();
    controllerRef.current = controller;
    setError('');
    setSuccess('');
    setProgress({ stage: 'transferring', percent: null });
    try {
      const imported = await uploadAudio(projectId, file, controller.signal, setProgress);
      if (controller.signal.aborted) return;
      setSelected(imported);
      setFile(null);
      if (inputRef.current) inputRef.current.value = '';
      onChange({ ...audio, background_audio_id: imported.id });
      setSuccess(
        `${imported.original_filename} importado e selecionado. Salve a edição para utilizar a música.`,
      );
      if (offset === 0) reload();
      else setOffset(0);
    } catch (failure) {
      if (!controller.signal.aborted) setError(audioError(failure));
    } finally {
      if (!controller.signal.aborted) setProgress(null);
      if (controllerRef.current === controller) controllerRef.current = null;
    }
  }

  return (
    <section className="composition-panel audio-panel" aria-labelledby="audio-title">
      <div className="composition-panel-heading">
        <div>
          <p className="section-kicker">05 / ÁUDIO</p>
          <h2 id="audio-title">A voz e o ritmo.</h2>
        </div>
        <Music2 size={22} strokeWidth={1.4} aria-hidden="true" />
      </div>
      <p className="panel-description">
        Preserve a clareza da voz e comece com a música baixa. O mix não possui normalização
        automática.
      </p>
      <fieldset className="audio-controls" disabled={locked}>
        <legend className="sr-only">Mix de áudio</legend>
        <label className="composition-slider">
          <span>
            Volume original <span aria-hidden="true">{Math.round(audio.source_volume * 100)}%</span>
          </span>
          <input
            type="range"
            aria-label="Volume original"
            aria-valuetext={`${Math.round(audio.source_volume * 100)}%`}
            min={0}
            max={200}
            step={1}
            value={Math.round(audio.source_volume * 100)}
            disabled={!hasSourceAudio}
            onChange={(event) =>
              onChange({ ...audio, source_volume: Number(event.target.value) / 100 })
            }
          />
        </label>
        {!hasSourceAudio && (
          <p className="field-help">A fonte original é silenciosa. Uma música é opcional.</p>
        )}
        <label htmlFor="background-audio">Música de fundo</label>
        <select
          id="background-audio"
          value={audio.background_audio_id ?? ''}
          onChange={(event) => {
            setSelectedError('');
            onChange({ ...audio, background_audio_id: event.target.value || null });
          }}
        >
          <option value="">Sem música de fundo</option>
          {audio.background_audio_id && !listedSelection && (
            <option value={audio.background_audio_id}>
              {selectedAsset?.original_filename ?? 'Música salva neste projeto'}
            </option>
          )}
          {data?.items.map((asset) => (
            <option key={asset.id} value={asset.id}>
              {asset.original_filename} · {formatDuration(asset.duration_ms)}
            </option>
          ))}
        </select>
        {loading && (
          <p className="field-help" role="status">
            Carregando músicas…
          </p>
        )}
        {!!libraryError && (
          <div className="feedback error">
            <p role="alert">{audioError(libraryError)}</p>
            <Button variant="outline" onClick={reload}>
              Recarregar músicas
            </Button>
          </div>
        )}
        {selectedError && audio.background_audio_id && !selectedAsset && (
          <div className="feedback error">
            <p role="alert">{selectedError}</p>
            <Button variant="outline" onClick={() => setSelectionAttempt((value) => value + 1)}>
              Consultar música salva
            </Button>
          </div>
        )}
        {data && data.total > data.limit && (
          <Pagination
            total={data.total}
            offset={offset}
            count={data.items.length}
            label="músicas"
            onChange={setOffset}
          />
        )}
        {selectedAsset && (
          <details className="integrity-details audio-details">
            <summary>
              <FileAudio size={14} aria-hidden="true" /> Informações da música
            </summary>
            <p>
              {selectedAsset.original_filename} · {formatDuration(selectedAsset.duration_ms)} ·{' '}
              {formatBytes(selectedAsset.byte_size)}
            </p>
            <p>
              MP3 · {selectedAsset.sample_rate} Hz · {selectedAsset.channels} canais
            </p>
            <code>{selectedAsset.checksum_sha256}</code>
          </details>
        )}
        <label className="composition-slider">
          <span>
            Volume da música{' '}
            <span aria-hidden="true">{Math.round(audio.background_volume * 100)}%</span>
          </span>
          <input
            type="range"
            aria-label="Volume da música"
            aria-valuetext={`${Math.round(audio.background_volume * 100)}%`}
            min={0}
            max={100}
            step={1}
            value={Math.round(audio.background_volume * 100)}
            disabled={!audio.background_audio_id}
            onChange={(event) =>
              onChange({ ...audio, background_volume: Number(event.target.value) / 100 })
            }
          />
        </label>
        <label className="composition-checkbox">
          <input
            type="checkbox"
            checked={audio.background_loop}
            disabled={!audio.background_audio_id}
            onChange={(event) => onChange({ ...audio, background_loop: event.target.checked })}
          />
          Repetir música
        </label>
        <div className="composition-form-grid">
          {(['fade_in_ms', 'fade_out_ms'] as const).map((field) => (
            <label key={field}>
              {field === 'fade_in_ms' ? 'Fade-in (ms)' : 'Fade-out (ms)'}
              <input
                type="number"
                min={0}
                max={3000}
                step={1}
                value={Number.isFinite(audio[field]) ? audio[field] : ''}
                onChange={(event) => onChange({ ...audio, [field]: event.target.valueAsNumber })}
              />
            </label>
          ))}
        </div>
      </fieldset>
      <p className="field-help audio-monitor-note">
        O player original permite ouvir volume de até 100%. Música, fades do mix e ganho acima de
        100% serão ouvidos na prévia codificada. Os fades pertencem ao trecho completo.
      </p>
      <div className="audio-upload">
        <label htmlFor="audio-file">Selecionar música MP3</label>
        <input
          id="audio-file"
          ref={inputRef}
          type="file"
          accept=".mp3,audio/mpeg"
          disabled={locked}
          aria-describedby="audio-upload-help"
          onChange={(event) => choose(event.target.files)}
        />
        <p className="field-help" id="audio-upload-help">
          Um MP3 por vez · Até {formatBytes(maxBytes)} · Até {maxSeconds / 60} minutos. Use música
          própria ou com licença válida. Você é responsável pelos direitos de uso.
        </p>
        {file && (
          <p className="audio-selected-file">
            {file.name} · {formatBytes(file.size)}
          </p>
        )}
        {progress && (
          <div className="upload-status" role="status" aria-live="polite">
            <div className="upload-status-label">
              <LoaderCircle size={16} className="animate-spin" aria-hidden="true" />
              <span>
                {progress.stage === 'analyzing' ? 'Analisando áudio…' : 'Transferindo MP3…'}
              </span>
              {progress.stage === 'transferring' && progress.percent !== null && (
                <strong>{progress.percent}%</strong>
              )}
            </div>
            {progress.stage === 'transferring' && (
              <progress
                aria-label="Progresso da transferência MP3"
                max={100}
                value={progress.percent ?? undefined}
              />
            )}
            <p>
              {progress.stage === 'analyzing'
                ? 'Arquivo recebido. Aguardando validação e metadados, sem previsão percentual.'
                : 'Mantenha esta página aberta até a confirmação.'}
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
            <Check size={16} aria-hidden="true" />
            {success}
          </p>
        )}
        <Button variant="outline" disabled={!file || locked} onClick={() => void importMusic()}>
          {busy ? (
            <LoaderCircle size={15} className="animate-spin" aria-hidden="true" />
          ) : (
            <Upload size={15} aria-hidden="true" />
          )}
          {busy ? 'Importando MP3…' : 'Importar MP3'}
        </Button>
      </div>
    </section>
  );
}
