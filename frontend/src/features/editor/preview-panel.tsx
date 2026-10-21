import { useEffect, useRef, useState } from 'react';
import { Check, Clapperboard, LoaderCircle, RefreshCw, VideoOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { EditSpecRevision } from '@/lib/api/edit-spec';
import {
  isPreviewId,
  previewError,
  previewFailure,
  previewIsActive,
  previewsApi,
  type PreviewJob,
} from '@/lib/api/previews';
import { ApiError } from '@/lib/api/projects';
import { formatBytes, formatDuration } from '@/lib/format';
import { pauseOtherVideos } from './playback';

interface PreviewPanelProps {
  projectId: string;
  revision: EditSpecRevision | null;
  dirty: boolean;
  busy: boolean;
}

function rememberedJob(key: string): string | null {
  try {
    const id = localStorage.getItem(key);
    return id && isPreviewId(id) ? id : null;
  } catch {
    return null;
  }
}

const labels = {
  queued: 'Na fila',
  preparing: 'Preparando composição',
  encoding: 'Codificando prévia',
  validating: 'Validando arquivo',
  publishing: 'Publicando prévia',
  completed: 'Prévia concluída',
  failed: 'Falha no processamento',
};

function PreviewVideo({ job }: { job: PreviewJob }) {
  const video = useRef<HTMLVideoElement>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    const element = video.current;
    return () => element?.pause();
  }, []);
  return (
    <div className="preview-result-player">
      <video
        ref={video}
        src={job.file_url ?? undefined}
        aria-label="Prévia gerada"
        controls
        controlsList="nodownload"
        preload="metadata"
        playsInline
        onPlay={(event) => pauseOtherVideos(event.currentTarget)}
        onLoadedMetadata={() => setLoading(false)}
        onCanPlay={() => setLoading(false)}
        onWaiting={() => setLoading(true)}
        onPlaying={() => setLoading(false)}
        onError={(event) => {
          setLoading(false);
          const code = event.currentTarget.error?.code;
          setError(
            code === 3 || code === 4
              ? 'Este navegador não conseguiu decodificar a prévia. Verifique a compatibilidade H.264/AAC.'
              : 'A reprodução da prévia foi interrompida. Verifique a conexão e consulte o arquivo novamente.',
          );
        }}
      />
      {loading && !error && <p role="status">Carregando prévia…</p>}
      {error && (
        <div className="feedback error">
          <p role="alert">
            <VideoOff size={15} aria-hidden="true" /> {error}
          </p>
          <Button
            variant="outline"
            onClick={() => {
              setError('');
              setLoading(true);
              video.current?.load();
            }}
          >
            Recarregar vídeo de prévia
          </Button>
        </div>
      )}
    </div>
  );
}

export function PreviewPanel({ projectId, revision, dirty, busy }: PreviewPanelProps) {
  const storageKey = `edivo:preview:${projectId}`;
  const [jobId, setJobId] = useState(() => rememberedJob(storageKey));
  const [job, setJob] = useState<PreviewJob | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [selection, setSelection] = useState({ revisionId: '', start: 0, duration: 8_000 });
  const operation = useRef<AbortController | null>(null);
  const currentSelection =
    revision && selection.revisionId !== revision.id
      ? {
          revisionId: revision.id,
          start: revision.trim.start_ms,
          duration: Math.min(8_000, revision.trim.end_ms - revision.trim.start_ms),
        }
      : selection;
  const start = currentSelection.start;
  const duration = currentSelection.duration;
  const effectiveEnd = revision ? Math.min(start + duration, revision.trim.end_ms) : 0;
  const validWindow =
    !!revision &&
    Number.isSafeInteger(start) &&
    start >= revision.trim.start_ms &&
    start < revision.trim.end_ms &&
    Number.isSafeInteger(duration) &&
    duration >= 1 &&
    duration <= 8_000;
  const active = job ? previewIsActive(job) : false;
  const restoring = !!jobId && !job;
  const disabled = !revision || dirty || busy || submitting || active || restoring || !validWindow;

  useEffect(() => {
    if (!jobId) return;
    const controller = new AbortController();
    let timer = 0;
    let mounted = true;
    async function poll() {
      try {
        const current = await previewsApi.get(
          projectId,
          jobId!,
          AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
        );
        if (!mounted) return;
        setJob(current);
        setError('');
        if (previewIsActive(current)) timer = window.setTimeout(() => void poll(), 2_000);
      } catch (failure) {
        if (!mounted) return;
        setError(previewError(failure));
        if (failure instanceof ApiError && failure.status === 404) {
          setJob(null);
          setJobId(null);
          try {
            localStorage.removeItem(storageKey);
          } catch {
            // A blocked browser store does not change server-side ownership.
          }
        }
      }
    }
    void poll();
    return () => {
      mounted = false;
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [jobId, projectId, storageKey, refresh]);

  useEffect(() => () => operation.current?.abort(), []);

  async function generate() {
    if (disabled || !revision) return;
    const controller = new AbortController();
    operation.current = controller;
    setSubmitting(true);
    setError('');
    try {
      const result = await previewsApi.create(
        projectId,
        { edit_spec_revision_id: revision.id, preview_start_ms: start, duration_ms: duration },
        AbortSignal.any([controller.signal, AbortSignal.timeout(15_000)]),
      );
      if (controller.signal.aborted) return;
      if (
        result.revision_id !== revision.id ||
        result.preview_start_ms !== start ||
        result.duration_ms !== effectiveEnd - start
      )
        throw new Error('INVALID_PREVIEW_REVISION_OR_WINDOW');
      setJob(result);
      setJobId(result.id);
      try {
        localStorage.setItem(storageKey, result.id);
      } catch {
        // The result remains usable if persistent browser storage is unavailable.
      }
    } catch (failure) {
      if (!controller.signal.aborted) setError(previewError(failure));
    } finally {
      if (!controller.signal.aborted) setSubmitting(false);
    }
  }

  return (
    <section className="composition-panel preview-panel" aria-labelledby="preview-title">
      <div className="composition-panel-heading">
        <div>
          <p className="section-kicker">06 / PRÉVIA</p>
          <h2 id="preview-title">Confira antes de seguir.</h2>
        </div>
        <Clapperboard size={22} strokeWidth={1.4} aria-hidden="true" />
      </div>
      <p className="panel-description">
        Um MP4 curto, de até 8 segundos, com textos, enquadramento e mix reais. Qualidade reduzida
        para revisão local; não é uma exportação final.
      </p>
      {revision ? (
        <>
          <p className="preview-revision">
            Gerar a partir da revisão salva {revision.revision_number}
          </p>
          <fieldset
            className="preview-window composition-form-grid"
            disabled={busy || submitting || active}
          >
            <legend className="sr-only">Janela da prévia</legend>
            <label>
              Início da prévia (ms)
              <input
                type="number"
                min={revision.trim.start_ms}
                max={revision.trim.end_ms - 1}
                step={1}
                value={Number.isFinite(start) ? start : ''}
                onChange={(event) =>
                  setSelection({ ...currentSelection, start: event.target.valueAsNumber })
                }
              />
            </label>
            <label>
              Duração da prévia (ms)
              <input
                type="number"
                min={1}
                max={8_000}
                step={1}
                value={Number.isFinite(duration) ? duration : ''}
                onChange={(event) =>
                  setSelection({ ...currentSelection, duration: event.target.valueAsNumber })
                }
              />
            </label>
          </fieldset>
          <p className="field-help">
            {validWindow
              ? `Janela na fonte: ${start}–${effectiveEnd} ms (${formatDuration(effectiveEnd - start)}). O fim respeita o trecho salvo.`
              : 'Informe início inteiro dentro do trecho salvo e duração inteira entre 1 e 8000 ms.'}
          </p>
        </>
      ) : (
        <p className="field-help">Salve uma revisão da edição para gerar a primeira prévia.</p>
      )}
      {dirty && <p className="preview-save-hint">Salve a edição antes de gerar uma nova prévia.</p>}
      <Button disabled={disabled} onClick={() => void generate()}>
        {submitting ? (
          <LoaderCircle className="animate-spin" size={16} aria-hidden="true" />
        ) : (
          <Clapperboard size={16} aria-hidden="true" />
        )}
        {submitting
          ? 'Solicitando prévia…'
          : job?.status === 'failed'
            ? 'Tentar nova prévia'
            : 'Gerar prévia'}
      </Button>
      {restoring && !error && (
        <p className="field-help" role="status">
          Recuperando prévia do projeto…
        </p>
      )}
      {error && (
        <div className="feedback error">
          <p role="alert">{error}</p>
          {jobId && (
            <Button
              variant="outline"
              disabled={submitting}
              onClick={() => setRefresh((value) => value + 1)}
            >
              <RefreshCw size={14} aria-hidden="true" /> Verificar prévia novamente
            </Button>
          )}
        </div>
      )}
      {job && (
        <div className="preview-result" aria-label="Resultado da prévia">
          <div className="preview-status" role="status">
            {active ? (
              <LoaderCircle className="animate-spin" size={16} aria-hidden="true" />
            ) : job.status === 'completed' ? (
              <Check size={16} aria-hidden="true" />
            ) : (
              <VideoOff size={16} aria-hidden="true" />
            )}
            <span>{labels[job.stage]}</span>
          </div>
          <p className="preview-revision">Prévia da revisão {job.revision_number}</p>
          <p className="field-help">
            Fonte: {job.preview_start_ms}–{job.preview_start_ms + job.duration_ms} ms ·{' '}
            {formatDuration(job.duration_ms)}
          </p>
          {revision && job.revision_id !== revision.id && (
            <p className="preview-save-hint">
              A edição está na revisão {revision.revision_number}; esta prévia mantém a revisão{' '}
              {job.revision_number}.
            </p>
          )}
          {dirty && (
            <p className="preview-save-hint">As alterações locais ainda não estão nesta prévia.</p>
          )}
          {job.status === 'failed' && (
            <p className="feedback error" role="alert">
              {previewFailure(job.error_code)}
            </p>
          )}
          {job.artifact_available && job.file_url && (
            <>
              <PreviewVideo key={`${job.id}:${refresh}`} job={job} />
              <p className="field-help">
                MP4 validado · {formatBytes(job.byte_size ?? 0)} · Arquivo privado
              </p>
            </>
          )}
        </div>
      )}
    </section>
  );
}
