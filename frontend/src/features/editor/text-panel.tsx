import { Plus, Trash2, Type } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { textError, type EditSpecV2, type TextOverlay } from '@/lib/api/edit-spec';

interface TextPanelProps {
  spec: EditSpecV2;
  disabled: boolean;
  onChange: (texts: TextOverlay[]) => void;
}

export function TextPanel({ spec, disabled, onChange }: TextPanelProps) {
  function change(id: string, patch: Partial<TextOverlay>) {
    onChange(spec.text_overlays.map((text) => (text.id === id ? { ...text, ...patch } : text)));
  }
  return (
    <section className="composition-panel text-panel" aria-labelledby="texts-title">
      <div className="composition-panel-heading">
        <div>
          <p className="section-kicker">04 / TEXTOS</p>
          <h2 id="texts-title">Uma ideia em poucas palavras.</h2>
        </div>
        <Type size={22} strokeWidth={1.4} aria-hidden="true" />
      </div>
      <p className="panel-description">
        Até três textos simples, com tempo absoluto da fonte. A visualização acompanha o vídeo.
      </p>
      {spec.text_overlays.length === 0 && (
        <p className="composition-empty">Um título, uma mensagem ou um convite. Comece por aqui.</p>
      )}
      <div className="text-cards">
        {spec.text_overlays.map((text, index) => {
          const label = `texto ${index + 1}`;
          const error = textError(text, spec.trim);
          return (
            <fieldset className="text-card" key={text.id} disabled={disabled}>
              <legend>Texto {index + 1}</legend>
              <Button
                variant="ghost"
                size="icon"
                className="text-remove"
                aria-label={`Remover ${label}`}
                onClick={() => onChange(spec.text_overlays.filter((item) => item.id !== text.id))}
              >
                <Trash2 size={15} aria-hidden="true" />
              </Button>
              <label htmlFor={`text-${text.id}`}>Conteúdo do {label}</label>
              <textarea
                id={`text-${text.id}`}
                rows={2}
                value={text.text}
                aria-invalid={!!error}
                aria-describedby={`text-help-${text.id}`}
                onChange={(event) => change(text.id, { text: event.target.value })}
              />
              <p className="field-help text-character-count" id={`text-help-${text.id}`}>
                {Array.from(text.text.trim()).length}/120 caracteres · Até duas linhas
              </p>
              <div className="composition-form-grid">
                <label>
                  Início do {label} (ms)
                  <input
                    type="number"
                    min={spec.trim.start_ms}
                    max={text.end_ms - 1}
                    step={1}
                    value={Number.isFinite(text.start_ms) ? text.start_ms : ''}
                    onChange={(event) => change(text.id, { start_ms: event.target.valueAsNumber })}
                  />
                </label>
                <label>
                  Fim do {label} (ms)
                  <input
                    type="number"
                    min={text.start_ms + 1}
                    max={spec.trim.end_ms}
                    step={1}
                    value={Number.isFinite(text.end_ms) ? text.end_ms : ''}
                    onChange={(event) => change(text.id, { end_ms: event.target.valueAsNumber })}
                  />
                </label>
                <label>
                  Posição do {label}
                  <select
                    value={text.position}
                    onChange={(event) =>
                      change(text.id, { position: event.target.value as TextOverlay['position'] })
                    }
                  >
                    <option value="top">Superior</option>
                    <option value="center">Centro</option>
                    <option value="bottom">Inferior</option>
                  </select>
                </label>
                <label>
                  Tamanho do {label}
                  <select
                    value={text.size}
                    onChange={(event) =>
                      change(text.id, { size: event.target.value as TextOverlay['size'] })
                    }
                  >
                    <option value="small">Pequeno</option>
                    <option value="medium">Médio</option>
                    <option value="large">Grande</option>
                  </select>
                </label>
                <label className="composition-full-width">
                  Estilo do {label}
                  <select
                    value={text.style}
                    onChange={(event) =>
                      change(text.id, { style: event.target.value as TextOverlay['style'] })
                    }
                  >
                    <option value="white_outline">Branco com contorno</option>
                    <option value="blue_outline">Azul com contorno</option>
                    <option value="white_plain">Branco simples</option>
                  </select>
                </label>
              </div>
              {error && (
                <p className="feedback error">
                  Texto {index + 1}: {error}
                </p>
              )}
            </fieldset>
          );
        })}
      </div>
      <Button
        variant="outline"
        disabled={disabled || spec.text_overlays.length >= 3}
        onClick={() =>
          onChange([
            ...spec.text_overlays,
            {
              id: crypto.randomUUID(),
              text: 'Seu texto aqui',
              start_ms: spec.trim.start_ms,
              end_ms: spec.trim.end_ms,
              position: 'bottom',
              style: 'white_outline',
              size: 'medium',
            },
          ])
        }
      >
        <Plus size={15} aria-hidden="true" /> Adicionar texto
      </Button>
      <p className="field-help">
        Texto centralizado, com margem de 7%. A fonte DejaVu Sans é local; símbolos raros podem não
        ter glifos. CSS é referência de edição: confira legibilidade na prévia codificada.
      </p>
    </section>
  );
}
