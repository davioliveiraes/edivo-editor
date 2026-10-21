import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import type { TextOverlay } from '@/lib/api/edit-spec';

const sizeRatios = { small: 0.042, medium: 0.056, large: 0.072 };

export function TextOverlays({ texts, currentMs }: { texts: TextOverlay[]; currentMs: number }) {
  const layer = useRef<HTMLDivElement>(null);
  const [measurement, setMeasurement] = useState({ width: 0, height: 0, fontReady: false });
  useEffect(() => {
    const element = layer.current;
    if (!element) return;
    let active = true;
    let fontReady = false;
    const measure = () => {
      if (!active) return;
      const { width, height } = element.getBoundingClientRect();
      setMeasurement((current) =>
        current.width === width && current.height === height && current.fontReady === fontReady
          ? current
          : { width, height, fontReady },
      );
    };
    const observer = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(measure) : null;
    observer?.observe(element);
    measure();
    void document.fonts
      ?.load('16px "Edivo Overlay"')
      .then(() => {
        fontReady = true;
        measure();
      })
      .catch(() => {
        /* The local font error leaves CSS fallback visible for inspection. */
      });
    window.addEventListener('resize', measure);
    return () => {
      active = false;
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, []);

  const styles = useMemo(() => {
    const context =
      measurement.fontReady && typeof CanvasRenderingContext2D !== 'undefined'
        ? document.createElement('canvas').getContext('2d')
        : null;
    return new Map(
      texts.map((text) => {
        const base = measurement.height * sizeRatios[text.size];
        let fontSize = base;
        if (base > 0 && context) {
          context.font = `${base}px "Edivo Overlay"`;
          const width = Math.max(
            ...text.text.split('\n').map((line) => context.measureText(line).width),
          );
          const available = Math.max(1, measurement.width * 0.86 - Math.max(2, base * 0.08));
          if (width > available && width > 0) fontSize = (base * available) / width;
        }
        return [
          text.id,
          {
            '--text-size': `${fontSize || 16}px`,
            '--text-outline': `${Math.max(0.5, fontSize * 0.04)}px`,
          } as CSSProperties,
        ] as const;
      }),
    );
  }, [texts, measurement]);

  return (
    <div className="composition-text-layer" ref={layer} aria-hidden="true">
      {texts
        .filter((text) => currentMs >= text.start_ms && currentMs < text.end_ms)
        .map((text) => (
          <p
            key={text.id}
            data-text-id={text.id}
            data-position={text.position}
            className={`composition-text text-position-${text.position} text-style-${text.style}`}
            style={styles.get(text.id)}
          >
            {text.text}
          </p>
        ))}
    </div>
  );
}
