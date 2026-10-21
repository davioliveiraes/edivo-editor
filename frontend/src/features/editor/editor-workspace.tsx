import { useEffect, useRef, useState } from 'react';
import { ArrowLeft, FileVideo, LoaderCircle, Save, ShieldCheck } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  editSpecApi,
  editSpecError,
  initialSpec,
  revisionSpec,
  type EditSpecV2,
  type EditSpecRevision,
} from '@/lib/api/edit-spec';
import type { MediaAsset } from '@/lib/api/media';
import { ApiError, projectRequest, publicError, type Page, type Project } from '@/lib/api/projects';
import { formatDuration, formatFps } from '@/lib/format';
import { VideoPlayer, type PlayerHandle } from './video-player';
import { Timeline } from './timeline';
import { TextPanel } from './text-panel';
import { AudioPanel } from './audio-panel';
import { PreviewPanel } from './preview-panel';

interface EditorData {
  project: Project;
  media: Page<MediaAsset>;
  revision: EditSpecRevision | null;
}

function EditorSession({ data }: { data: EditorData }) {
  const { project } = data;
  const [revision, setRevision] = useState(data.revision);
  const [assets, setAssets] = useState(data.media.items);
  const [selectedId, setSelectedId] = useState(revision?.source_media_id ?? assets[0]?.id ?? '');
  const [draft, setDraft] = useState<EditSpecV2 | null>(() =>
    revision ? revisionSpec(revision) : assets[0] ? initialSpec(project, assets[0]) : null,
  );
  const [baseline, setBaseline] = useState(draft);
  const [busy, setBusy] = useState(false);
  const [audioBusy, setAudioBusy] = useState(false);
  const [trimError, setTrimError] = useState('');
  const [reloading, setReloading] = useState(false);
  const [saveError, setSaveError] = useState('');
  const [conflict, setConflict] = useState(false);
  const [notice, setNotice] = useState('');
  const [playhead, setPlayhead] = useState(revision?.trim.start_ms ?? 0);
  const player = useRef<PlayerHandle>(null);
  const operation = useRef<AbortController | null>(null);
  const dirty = JSON.stringify(draft) !== JSON.stringify(baseline);
  const [offset, setOffset] = useState(Math.min(data.media.items.length, data.media.limit));
  const [moreBusy, setMoreBusy] = useState(false);
  const [moreError, setMoreError] = useState('');
  const asset = assets.find((item) => item.id === selectedId);
  const locked = busy || audioBusy;
  const validationError = draft ? editSpecError(draft) : '';

  useEffect(() => {
    const protect = (event: Event) => {
      if (
        locked ||
        (dirty && !window.confirm('Há alterações não salvas. Deseja sair e descartá-las?'))
      )
        event.preventDefault();
    };
    const unload = (event: BeforeUnloadEvent) => {
      if (dirty || locked) {
        event.preventDefault();
        event.returnValue = '';
      }
    };
    window.addEventListener('edivo:before-navigate', protect);
    window.addEventListener('beforeunload', unload);
    return () => {
      window.removeEventListener('edivo:before-navigate', protect);
      window.removeEventListener('beforeunload', unload);
    };
  }, [dirty, locked]);
  useEffect(() => () => operation.current?.abort(), []);

  function updateDraft(next: EditSpecV2) {
    setDraft(next);
    setTrimError('');
    setNotice('');
    if (!conflict) setSaveError('');
  }
  function selectSource(id: string) {
    if (locked || id === selectedId) return;
    const next = assets.find((item) => item.id === id);
    if (!next) return;
    const removeTexts = !!draft?.text_overlays.length;
    const resetFades =
      !!draft && draft.audio.fade_in_ms + draft.audio.fade_out_ms > next.duration_ms;
    if (
      (dirty || removeTexts || resetFades) &&
      !window.confirm(
        'Trocar o vídeo reiniciará o trecho e manterá o enquadramento e a música.' +
          (removeTexts ? ' Os textos serão descartados, inclusive os da edição salva.' : '') +
          (resetFades ? ' Os fades incompatíveis serão zerados.' : '') +
          (dirty ? ' Há alterações não salvas.' : '') +
          ' Deseja continuar?',
      )
    )
      return;
    const current = draft ?? initialSpec(project, next);
    setSelectedId(id);
    updateDraft({
      ...current,
      source_media_id: next.id,
      trim: { start_ms: 0, end_ms: next.duration_ms },
      text_overlays: [],
      audio: resetFades ? { ...current.audio, fade_in_ms: 0, fade_out_ms: 0 } : current.audio,
    });
    setPlayhead(0);
  }
  function updateTrim(trim: EditSpecV2['trim']) {
    if (!draft || locked) return;
    if (
      draft.text_overlays.some((text) => text.start_ms < trim.start_ms || text.end_ms > trim.end_ms)
    ) {
      setTrimError(
        'O novo trecho deixaria textos fora do intervalo. Ajuste os tempos dos textos ou remova-os antes de alterar o corte.',
      );
      return;
    }
    if (draft.audio.fade_in_ms + draft.audio.fade_out_ms > trim.end_ms - trim.start_ms) {
      setTrimError(
        'O novo trecho seria menor que a soma dos fades. Reduza os fades antes de alterar o corte.',
      );
      return;
    }
    updateDraft({ ...draft, trim });
  }
  async function save() {
    if (!draft || locked || conflict || validationError) return;
    const controller = new AbortController();
    operation.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 15_000);
    setBusy(true);
    setSaveError('');
    setNotice('');
    try {
      const saved = await editSpecApi.save(
        project.id,
        draft,
        revision?.revision_number ?? 0,
        controller.signal,
      );
      if (controller.signal.aborted) return;
      setRevision(saved);
      setBaseline(revisionSpec(saved));
      setDraft(revisionSpec(saved));
      setNotice('Edição salva com sucesso. O original permanece intacto.');
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        setConflict(true);
        setSaveError(
          'Outra revisão foi salva neste projeto. Suas alterações foram mantidas aqui. Recarregue a revisão atual antes de continuar.',
        );
      } else setSaveError(publicError(error));
    } finally {
      window.clearTimeout(timeout);
      if (operation.current === controller) setBusy(false);
    }
  }
  async function reloadRevision() {
    if (locked) return;
    if (
      dirty &&
      !window.confirm(
        'Recarregar a revisão atual descartará suas alterações locais. Deseja continuar?',
      )
    )
      return;
    const controller = new AbortController();
    operation.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 15_000);
    setBusy(true);
    setReloading(true);
    setSaveError('');
    try {
      const current = await editSpecApi.get(project.id, controller.signal);
      if (!assets.some((item) => item.id === current.source_media_id)) {
        const source = await projectRequest<MediaAsset>(
          `/${project.id}/media/${current.source_media_id}`,
          { signal: controller.signal },
        );
        if (!controller.signal.aborted) setAssets((items) => [...items, source]);
      }
      if (controller.signal.aborted) return;
      const spec = revisionSpec(current);
      setRevision(current);
      setSelectedId(current.source_media_id);
      setDraft(spec);
      setBaseline(spec);
      setPlayhead(spec.trim.start_ms);
      player.current?.seek(spec.trim.start_ms);
      setConflict(false);
      setNotice('Revisão atual recarregada.');
    } catch (error) {
      setSaveError(publicError(error));
    } finally {
      window.clearTimeout(timeout);
      if (operation.current === controller) {
        setBusy(false);
        setReloading(false);
      }
    }
  }

  async function moreSources() {
    setMoreBusy(true);
    setMoreError('');
    try {
      const result = await projectRequest<Page<MediaAsset>>(
        `/${project.id}/media?limit=100&offset=${offset}`,
      );
      setAssets((items) => [
        ...items,
        ...result.items.filter((item) => !items.some((existing) => existing.id === item.id)),
      ]);
      setOffset((value) => value + result.items.length);
    } catch (error) {
      setMoreError(publicError(error));
    } finally {
      setMoreBusy(false);
    }
  }

  return (
    <div className="editor-workspace">
      <a className="back-link" href={`#/projects/${project.id}`}>
        <ArrowLeft size={16} aria-hidden="true" />
        Voltar à biblioteca
      </a>
      <header className="editor-heading">
        <div>
          <p className="section-kicker">{project.name}</p>
          <h1>
            Editor do projeto<span className="accent-word">.</span>
          </h1>
          <p>Escolha seu original e encontre o próximo começo.</p>
        </div>
        <div className="editor-save-actions">
          <span className="editor-save-state" role="status">
            {busy
              ? reloading
                ? 'Carregando revisão…'
                : 'Salvando edição…'
              : dirty
                ? 'Alterações não salvas'
                : revision
                  ? `Edição salva · Revisão ${revision.revision_number}`
                  : 'Edição ainda não salva'}
          </span>
          {draft && (
            <Button
              disabled={locked || conflict || !!validationError || (!!revision && !dirty)}
              onClick={() => void save()}
            >
              <Save size={15} aria-hidden="true" />
              Salvar edição
            </Button>
          )}
          <span className="editor-original">
            <ShieldCheck size={15} aria-hidden="true" />
            Original preservado
          </span>
        </div>
      </header>
      {saveError && (
        <div className="editor-save-feedback feedback error" role="alert">
          <p>{saveError}</p>
          {conflict && (
            <Button variant="outline" disabled={busy} onClick={() => void reloadRevision()}>
              Recarregar revisão atual
            </Button>
          )}
        </div>
      )}
      {notice && (
        <p className="feedback success" role="status">
          {notice}
        </p>
      )}
      {validationError && (
        <p className="feedback error" role="alert">
          {validationError}
        </p>
      )}
      {trimError && (
        <p className="feedback error" role="alert">
          {trimError}
        </p>
      )}
      {!asset ? (
        <div className="workspace-state empty-state">
          <FileVideo size={34} aria-hidden="true" />
          <h2>Este projeto ainda não tem vídeos.</h2>
          <p>Importe um MP4 na biblioteca para abrir uma fonte real.</p>
          <a className="text-link" href={`#/projects/${project.id}`}>
            Importar primeiro vídeo
          </a>
        </div>
      ) : (
        <>
          <div className="editor-main">
            <div className="editor-preview-column">
              <VideoPlayer
                key={asset.id}
                ref={player}
                projectId={project.id}
                asset={asset}
                trim={draft?.trim ?? { start_ms: 0, end_ms: asset.duration_ms }}
                canvas={draft?.canvas}
                texts={draft?.text_overlays}
                sourceVolume={draft?.audio.source_volume}
                onTimeChange={setPlayhead}
              />
              {draft && (
                <Timeline
                  duration={asset.duration_ms}
                  trim={draft.trim}
                  playhead={playhead}
                  filename={asset.original_filename}
                  disabled={locked}
                  onTrim={updateTrim}
                  onSeek={(milliseconds) => player.current?.seek(milliseconds)}
                />
              )}
            </div>
            <aside className="editor-inspector" aria-label="Configuração da fonte">
              <p className="section-kicker">01 / FONTE</p>
              <h2>O ponto de partida.</h2>
              <label htmlFor="editor-source">Vídeo de origem</label>
              <select
                id="editor-source"
                value={selectedId}
                disabled={locked}
                onChange={(event) => selectSource(event.target.value)}
              >
                {assets.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.original_filename}
                  </option>
                ))}
              </select>
              {offset < data.media.total && (
                <Button variant="outline" disabled={moreBusy} onClick={() => void moreSources()}>
                  {moreBusy ? 'Carregando fontes…' : 'Carregar mais fontes'}
                </Button>
              )}
              {moreError && (
                <p role="alert" className="feedback error">
                  {moreError}
                </p>
              )}
              <dl className="source-details">
                <div>
                  <dt>Duração</dt>
                  <dd>{formatDuration(asset.duration_ms)}</dd>
                </div>
                <div>
                  <dt>Resolução codificada</dt>
                  <dd>
                    {asset.width} × {asset.height}
                  </dd>
                </div>
                <div>
                  <dt>Quadros</dt>
                  <dd>{formatFps(asset.fps_num, asset.fps_den)}</dd>
                </div>
                <div>
                  <dt>Codecs</dt>
                  <dd>
                    {asset.video_codec?.toUpperCase() ?? 'Não informado'} ·{' '}
                    {asset.has_audio
                      ? (asset.audio_codec?.toUpperCase() ?? 'Áudio presente')
                      : 'Sem áudio'}
                  </dd>
                </div>
              </dl>
              <p className="field-help">
                A orientação de exibição é interpretada pelo navegador. Nem todo codec MP4 é
                compatível com reprodução HTML5.
              </p>
              {draft && (
                <fieldset className="canvas-controls" disabled={locked}>
                  <legend>03 / COMPOSIÇÃO</legend>
                  <p className="inspector-subtitle">Um formato para sua ideia.</p>
                  <div className="preset-options" aria-label="Proporção da composição">
                    {(['9:16', '1:1', '4:5', '16:9'] as const).map((preset) => (
                      <button
                        type="button"
                        key={preset}
                        aria-pressed={draft.canvas.preset === preset}
                        onClick={() =>
                          updateDraft({ ...draft, canvas: { ...draft.canvas, preset } })
                        }
                      >
                        <span
                          className="preset-shape"
                          style={{ aspectRatio: preset.replace(':', '/') }}
                          aria-hidden="true"
                        />
                        {preset}
                      </button>
                    ))}
                  </div>
                  <p className="field-help">
                    Referências de formato: 1080 × 1920, 1080 × 1080, 1080 × 1350 e 1920 × 1080. A
                    visualização mantém o original intacto.
                  </p>
                  <fieldset className="fit-options">
                    <legend>Enquadramento</legend>
                    {(['cover', 'contain'] as const).map((fit) => (
                      <label key={fit}>
                        <input
                          type="radio"
                          name="canvas-fit"
                          checked={draft.canvas.fit === fit}
                          onChange={() =>
                            updateDraft({ ...draft, canvas: { ...draft.canvas, fit } })
                          }
                        />
                        {fit === 'cover' ? 'Preencher (cover)' : 'Ajustar (contain)'}
                      </label>
                    ))}
                  </fieldset>
                  <div className="focal-controls">
                    {(['focal_x', 'focal_y'] as const).map((axis) => (
                      <div key={axis} className="focal-field">
                        <label htmlFor={`editor-${axis}`}>
                          {axis === 'focal_x' ? 'Ponto focal horizontal' : 'Ponto focal vertical'}
                        </label>
                        <output>{Math.round(draft.canvas[axis] * 100)}%</output>
                        <input
                          id={`editor-${axis}`}
                          type="range"
                          aria-label={
                            axis === 'focal_x' ? 'Ponto focal horizontal' : 'Ponto focal vertical'
                          }
                          min={0}
                          max={1}
                          step={0.01}
                          value={draft.canvas[axis]}
                          disabled={locked || draft.canvas.fit === 'contain'}
                          onChange={(event) =>
                            updateDraft({
                              ...draft,
                              canvas: { ...draft.canvas, [axis]: Number(event.target.value) },
                            })
                          }
                        />
                      </div>
                    ))}
                  </div>
                  <p className="field-help">
                    Cover recorta bordas na visualização. Contain mostra o vídeo inteiro e mantém o
                    ponto focal salvo, sem aplicá-lo. A composição é apenas visual no navegador.
                  </p>
                </fieldset>
              )}
            </aside>
          </div>
          {draft && (
            <div className="editor-composition-panels">
              <TextPanel
                spec={draft}
                disabled={locked}
                onChange={(texts) => updateDraft({ ...draft, text_overlays: texts })}
              />
              <div className="editor-audio-preview-column">
                <AudioPanel
                  projectId={project.id}
                  audio={draft.audio}
                  hasSourceAudio={asset.has_audio}
                  disabled={busy}
                  onChange={(audio) => updateDraft({ ...draft, audio })}
                  onBusyChange={setAudioBusy}
                />
                <PreviewPanel
                  projectId={project.id}
                  revision={revision}
                  dirty={dirty}
                  busy={locked || conflict}
                />
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export function EditorWorkspace({ projectId }: { projectId: string }) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ attempt: number; data?: EditorData; error?: unknown }>();
  useEffect(() => {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 15_000);
    let active = true;
    async function load() {
      const [project, media, revision] = await Promise.all([
        projectRequest<Project>(`/${projectId}`, { signal: controller.signal }),
        projectRequest<Page<MediaAsset>>(`/${projectId}/media?limit=100&offset=0`, {
          signal: controller.signal,
        }),
        editSpecApi.get(projectId, controller.signal).catch((error: unknown) => {
          if (
            error instanceof ApiError &&
            error.status === 404 &&
            error.code === 'edit_spec_not_found'
          )
            return null;
          throw error;
        }),
      ]);
      if (revision && !media.items.some((item) => item.id === revision.source_media_id)) {
        const source = await projectRequest<MediaAsset>(
          `/${projectId}/media/${revision.source_media_id}`,
          { signal: controller.signal },
        );
        media.items.push(source);
      }
      return { project, media, revision };
    }
    void load()
      .then((data) => {
        if (active) setResult({ attempt, data });
      })
      .catch((error: unknown) => {
        controller.abort();
        if (active) setResult({ attempt, error });
      })
      .finally(() => window.clearTimeout(timeout));
    return () => {
      active = false;
      controller.abort();
      window.clearTimeout(timeout);
    };
  }, [attempt, projectId]);
  const current = result?.attempt === attempt ? result : undefined;
  if (!current)
    return (
      <div className="workspace-state" role="status">
        <LoaderCircle className="animate-spin" aria-hidden="true" />
        Carregando editor…
      </div>
    );
  if (!current.data)
    return (
      <div className="workspace-state">
        <p role="alert">{publicError(current.error)}</p>
        <Button variant="outline" onClick={() => setAttempt((value) => value + 1)}>
          Recarregar editor
        </Button>
        <a className="back-link" href={`#/projects/${projectId}`}>
          Voltar à biblioteca
        </a>
      </div>
    );
  return <EditorSession key={`${projectId}:${attempt}`} data={current.data} />;
}
