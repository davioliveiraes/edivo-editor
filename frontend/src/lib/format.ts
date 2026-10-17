export const formatDate = (value: string) =>
  new Date(value).toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' });

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2)
    return `${(bytes / 1024).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} KiB`;
  return `${(bytes / 1024 ** 2).toLocaleString('pt-BR', { maximumFractionDigits: 1 })} MiB`;
}

export function formatDuration(milliseconds: number): string {
  const minutes = Math.floor(milliseconds / 60_000);
  const seconds = Math.floor((milliseconds % 60_000) / 1_000);
  const fraction = milliseconds % 1_000;
  return `${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')}${fraction ? `,${String(fraction).padStart(3, '0')}` : ''}`;
}

export function formatFps(numerator: number, denominator: number): string {
  if (denominator <= 0 || numerator <= 0) return 'Não informado';
  const fps = (numerator / denominator).toLocaleString('pt-BR', { maximumFractionDigits: 3 });
  return denominator === 1 ? `${fps} FPS` : `${fps} FPS · ${numerator}/${denominator}`;
}
