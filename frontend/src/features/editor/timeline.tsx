import { useRef, type KeyboardEvent, type PointerEvent } from 'react';
import { Scissors } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { EditSpec } from '@/lib/api/edit-spec';
import { formatDuration } from '@/lib/format';

interface TimelineProps {
  duration: number;
  trim: EditSpec['trim'];
  playhead: number;
  filename: string;
  disabled: boolean;
  onTrim: (trim: EditSpec['trim']) => void;
  onSeek: (milliseconds: number) => void;
}

export function Timeline({
  duration,
  trim,
  playhead,
  filename,
  disabled,
  onTrim,
  onSeek,
}: TimelineProps) {
  const track = useRef<HTMLDivElement>(null);
  const dragging = useRef<{ kind: 'start_ms' | 'end_ms'; pointer: number } | null>(null);
  const minimum = Math.min(100, duration);
  function mark(kind: 'start_ms' | 'end_ms', value: number) {
    if (disabled || !Number.isFinite(value)) return;
    const bounded =
      kind === 'start_ms'
        ? Math.max(0, Math.min(Math.round(value), trim.end_ms - minimum))
        : Math.min(duration, Math.max(Math.round(value), trim.start_ms + minimum));
    onTrim({ ...trim, [kind]: bounded });
  }
  function position(event: PointerEvent) {
    const box = track.current?.getBoundingClientRect();
    return box && box.width > 0
      ? Math.round(Math.max(0, Math.min(1, (event.clientX - box.left) / box.width)) * duration)
      : null;
  }
  function keyboard(event: KeyboardEvent, kind: 'start_ms' | 'end_ms') {
    const step = event.shiftKey ? 1_000 : 100;
    const keys: Record<string, number> = {
      ArrowLeft: trim[kind] - step,
      ArrowDown: trim[kind] - step,
      ArrowRight: trim[kind] + step,
      ArrowUp: trim[kind] + step,
      Home: kind === 'start_ms' ? 0 : trim.start_ms + minimum,
      End: kind === 'end_ms' ? duration : trim.end_ms - minimum,
    };
    const value = keys[event.key];
    if (value !== undefined) {
      event.preventDefault();
      mark(kind, value);
    }
  }
  const percent = (value: number) => `${(value / duration) * 100}%`;
  return (
    <section className="editor-timeline" aria-label="Timeline de uma faixa">
      <div className="timeline-heading">
        <div>
          <p className="section-kicker">02 / TRECHO</p>
          <h2>Escolha o que fica.</h2>
        </div>
        <output aria-label="Duração selecionada">
          {formatDuration(trim.end_ms - trim.start_ms)} <span>selecionados</span>
        </output>
      </div>
      <div className="timeline-ruler" aria-hidden="true">
        {[0, 0.25, 0.5, 0.75, 1].map((fraction) => (
          <span key={fraction}>{formatDuration(Math.round(duration * fraction))}</span>
        ))}
      </div>
      <div
        className="timeline-track"
        ref={track}
        onPointerDown={(event) => {
          if (disabled || event.button !== 0) return;
          const value = position(event);
          if (value !== null) onSeek(value);
        }}
      >
        <div
          className="timeline-selection"
          style={{ left: percent(trim.start_ms), width: percent(trim.end_ms - trim.start_ms) }}
        >
          <span className="timeline-filename">{filename}</span>
        </div>
        <div className="timeline-playhead" style={{ left: percent(playhead) }} aria-hidden="true" />
        {(['start_ms', 'end_ms'] as const).map((kind) => (
          <button
            key={kind}
            type="button"
            role="slider"
            className={`timeline-marker ${kind === 'start_ms' ? 'marker-start' : 'marker-end'}`}
            aria-label={kind === 'start_ms' ? 'Marcador de entrada' : 'Marcador de saída'}
            aria-valuemin={kind === 'start_ms' ? 0 : trim.start_ms + minimum}
            aria-valuemax={kind === 'start_ms' ? trim.end_ms - minimum : duration}
            aria-valuenow={trim[kind]}
            aria-valuetext={formatDuration(trim[kind])}
            disabled={disabled}
            style={{ left: percent(trim[kind]) }}
            onKeyDown={(event) => keyboard(event, kind)}
            onPointerDown={(event) => {
              event.stopPropagation();
              if (event.button !== 0 || disabled) return;
              dragging.current = { kind, pointer: event.pointerId };
              event.currentTarget.setPointerCapture(event.pointerId);
              event.currentTarget.focus();
            }}
            onPointerMove={(event) => {
              if (dragging.current?.pointer !== event.pointerId || dragging.current.kind !== kind)
                return;
              const value = position(event);
              if (value !== null) mark(kind, value);
            }}
            onPointerUp={(event) => {
              if (dragging.current?.pointer === event.pointerId) dragging.current = null;
            }}
            onPointerCancel={() => {
              dragging.current = null;
            }}
            onLostPointerCapture={() => {
              dragging.current = null;
            }}
          >
            <span aria-hidden="true">Ⅱ</span>
          </button>
        ))}
      </div>
      <label className="timeline-seek">
        <span className="sr-only">Posição na timeline</span>
        <input
          type="range"
          min={0}
          max={duration}
          step={1}
          value={playhead}
          disabled={disabled}
          onChange={(event) => onSeek(Number(event.target.value))}
          aria-valuetext={formatDuration(playhead)}
        />
      </label>
      <fieldset className="trim-controls" disabled={disabled}>
        <legend className="sr-only">Limites do trecho em milissegundos</legend>
        <label>
          Entrada (ms)
          <input
            type="number"
            min={0}
            max={trim.end_ms - minimum}
            step={1}
            value={trim.start_ms}
            onChange={(event) => mark('start_ms', event.target.valueAsNumber)}
          />
        </label>
        <Button variant="outline" onClick={() => mark('start_ms', playhead)}>
          <Scissors size={14} aria-hidden="true" />
          Marcar entrada
        </Button>
        <label>
          Saída (ms)
          <input
            type="number"
            min={trim.start_ms + minimum}
            max={duration}
            step={1}
            value={trim.end_ms}
            onChange={(event) => mark('end_ms', event.target.valueAsNumber)}
          />
        </label>
        <Button variant="outline" onClick={() => mark('end_ms', playhead)}>
          <Scissors size={14} aria-hidden="true" />
          Marcar saída
        </Button>
      </fieldset>
      <p className="field-help">
        Arraste as marcas ou use as setas (100 ms; Shift: 1 s). Home/End vão aos limites. Corte não
        destrutivo; a precisão do seek varia com codec, keyframes e vídeos VFR.
      </p>
    </section>
  );
}
