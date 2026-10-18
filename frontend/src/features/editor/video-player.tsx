import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type CSSProperties,
} from 'react';
import { LoaderCircle, Pause, Play, RotateCcw, VideoOff } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { EditSpec } from '@/lib/api/edit-spec';
import type { MediaAsset } from '@/lib/api/media';
import { formatDuration } from '@/lib/format';

export interface PlayerHandle {
  seek: (milliseconds: number) => void;
}

interface PlayerProps {
  projectId: string;
  asset: MediaAsset;
  trim: EditSpec['trim'];
  canvas?: EditSpec['canvas'];
  onTimeChange?: (milliseconds: number) => void;
}

const ratios = { '9:16': 9 / 16, '1:1': 1, '4:5': 4 / 5, '16:9': 16 / 9 };

export const VideoPlayer = forwardRef<PlayerHandle, PlayerProps>(function VideoPlayer(
  { projectId, asset, trim, canvas, onTimeChange },
  ref,
) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const headRef = useRef<AbortController | null>(null);
  const [ready, setReady] = useState(false);
  const [loading, setLoading] = useState(true);
  const [playing, setPlaying] = useState(false);
  const [currentMs, setCurrentMs] = useState(0);
  const [error, setError] = useState('');
  const [displayRatio, setDisplayRatio] = useState(asset.width / asset.height);
  const url = `/api/v1/projects/${projectId}/media/${asset.id}/stream`;

  const reportTime = useCallback(() => {
    const video = videoRef.current;
    if (!video || !Number.isFinite(video.currentTime)) return;
    let milliseconds = Math.min(
      asset.duration_ms,
      Math.max(0, Math.round(video.currentTime * 1_000)),
    );
    if (!video.paused && milliseconds < trim.start_ms) {
      video.currentTime = trim.start_ms / 1_000;
      milliseconds = trim.start_ms;
    } else if (!video.paused && milliseconds >= trim.end_ms) {
      video.pause();
      video.currentTime = trim.end_ms / 1_000;
      milliseconds = trim.end_ms;
    }
    setCurrentMs(milliseconds);
    onTimeChange?.(milliseconds);
  }, [asset.duration_ms, onTimeChange, trim.start_ms, trim.end_ms]);

  const seek = useCallback(
    (milliseconds: number) => {
      const video = videoRef.current;
      if (!video || !ready) return;
      const actualDuration = Number.isFinite(video.duration)
        ? video.duration * 1_000
        : asset.duration_ms;
      video.currentTime =
        Math.max(0, Math.min(milliseconds, asset.duration_ms, actualDuration)) / 1_000;
      reportTime();
    },
    [asset.duration_ms, ready, reportTime],
  );
  useImperativeHandle(ref, () => ({ seek }), [seek]);

  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    const tick = () => {
      reportTime();
      frame = window.requestAnimationFrame(tick);
    };
    frame = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(frame);
  }, [playing, reportTime]);

  useEffect(() => {
    const video = videoRef.current;
    return () => {
      headRef.current?.abort();
      video?.pause();
    };
  }, []);

  async function togglePlayback() {
    const video = videoRef.current;
    if (!video || !ready || error) return;
    if (!video.paused) {
      video.pause();
      return;
    }
    if (video.currentTime * 1_000 < trim.start_ms || video.currentTime * 1_000 >= trim.end_ms)
      seek(trim.start_ms);
    try {
      await video.play();
    } catch {
      setError(
        'O navegador não conseguiu iniciar o vídeo. Verifique a conexão e a compatibilidade do codec.',
      );
    }
  }

  async function mediaFailure() {
    const mediaCode = videoRef.current?.error?.code;
    const message =
      navigator.onLine === false || mediaCode === 2
        ? 'A conexão com o vídeo foi interrompida. Verifique a rede e tente abrir o editor novamente.'
        : mediaCode === 3 || mediaCode === 4
          ? 'Este vídeo não pôde ser decodificado. O codec pode não ser compatível com este navegador, ou o arquivo pode conter dados inválidos. Nenhuma conversão foi realizada.'
          : 'O vídeo está indisponível. Verifique a conexão e tente abrir o editor novamente.';
    setError(message);
    setLoading(false);
    setPlaying(false);
    const controller = new AbortController();
    headRef.current?.abort();
    headRef.current = controller;
    const timeout = window.setTimeout(() => controller.abort(), 5_000);
    try {
      const response = await fetch(url, {
        method: 'HEAD',
        cache: 'no-store',
        signal: controller.signal,
      });
      if (!controller.signal.aborted && (response.status === 404 || response.status === 503))
        setError(
          'O arquivo de vídeo não está disponível no momento. Volte à biblioteca e tente novamente.',
        );
    } catch {
      if (!controller.signal.aborted)
        setError('Não foi possível acessar o vídeo. Verifique a conexão e tente novamente.');
    } finally {
      window.clearTimeout(timeout);
    }
  }

  const ratio = canvas ? ratios[canvas.preset] : displayRatio;
  const frameStyle = { '--canvas-ratio': ratio } as CSSProperties;

  return (
    <section className="video-player" aria-label="Player de vídeo">
      <div className="player-heading">
        <span>VISUALIZAÇÃO NO NAVEGADOR</span>
        <span>{canvas?.preset ?? 'Proporção original'}</span>
      </div>
      <div className="player-stage">
        <div className="composition-frame" style={frameStyle}>
          <video
            ref={videoRef}
            src={url}
            preload="metadata"
            playsInline
            aria-label="Vídeo do projeto"
            tabIndex={0}
            style={{
              objectFit: canvas?.fit ?? 'contain',
              objectPosition:
                canvas?.fit === 'cover'
                  ? `${canvas.focal_x * 100}% ${canvas.focal_y * 100}%`
                  : '50% 50%',
            }}
            onLoadedMetadata={(event) => {
              const video = event.currentTarget;
              setReady(true);
              setLoading(false);
              if (video.videoWidth > 0 && video.videoHeight > 0)
                setDisplayRatio(video.videoWidth / video.videoHeight);
              video.currentTime = trim.start_ms / 1_000;
              reportTime();
            }}
            onCanPlay={() => setLoading(false)}
            onWaiting={() => setLoading(true)}
            onSeeking={() => setLoading(true)}
            onSeeked={() => {
              setLoading(false);
              reportTime();
            }}
            onPlay={() => setPlaying(true)}
            onPlaying={() => {
              setPlaying(true);
              setLoading(false);
            }}
            onPause={() => setPlaying(false)}
            onEnded={() => {
              setPlaying(false);
              reportTime();
            }}
            onTimeUpdate={reportTime}
            onError={() => void mediaFailure()}
            onKeyDown={(event) => {
              if (event.key === ' ') {
                event.preventDefault();
                void togglePlayback();
              }
              if (event.key === 'ArrowRight' || event.key === 'ArrowLeft') {
                event.preventDefault();
                seek(currentMs + (event.key === 'ArrowRight' ? 1_000 : -1_000));
              }
            }}
          />
          {loading && !error && (
            <div className="player-overlay" role="status">
              <LoaderCircle className="animate-spin" aria-hidden="true" />
              Carregando vídeo…
            </div>
          )}
          {error && (
            <div className="player-overlay player-error" role="alert">
              <VideoOff size={30} strokeWidth={1.3} aria-hidden="true" />
              <p>{error}</p>
            </div>
          )}
        </div>
      </div>
      <div className="player-controls">
        <Button
          variant="ghost"
          size="icon"
          disabled={!ready || !!error}
          onClick={() => seek(trim.start_ms)}
          aria-label="Voltar à entrada"
        >
          <RotateCcw aria-hidden="true" />
        </Button>
        <Button
          size="icon"
          disabled={!ready || !!error}
          onClick={() => void togglePlayback()}
          aria-label={playing ? 'Pausar vídeo' : 'Reproduzir vídeo'}
        >
          {playing ? <Pause aria-hidden="true" /> : <Play aria-hidden="true" />}
        </Button>
        <output className="player-time" aria-label="Tempo atual" aria-live="off">
          {formatDuration(currentMs)} <span>/ {formatDuration(asset.duration_ms)}</span>
        </output>
        <span className="player-state" aria-live="polite">
          {error ? 'Indisponível' : loading ? 'Carregando' : playing ? 'Reproduzindo' : 'Pausado'}
        </span>
      </div>
      <label className="player-seek">
        <span className="sr-only">Buscar no vídeo</span>
        <input
          type="range"
          min={0}
          max={asset.duration_ms}
          step={1}
          value={currentMs}
          disabled={!ready || !!error}
          onChange={(event) => seek(Number(event.target.value))}
          aria-valuetext={formatDuration(currentMs)}
        />
      </label>
      <p className="player-note">
        Reprodução do original. A posição temporal é aproximada e depende do navegador, codec e
        keyframes.
      </p>
    </section>
  );
});
