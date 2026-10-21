/** Coordinate actual native play events; no independent playback clock. */
export function pauseOtherVideos(current: HTMLVideoElement): void {
  const scope = current.closest('.editor-workspace') ?? current.parentElement;
  scope?.querySelectorAll('video').forEach((video) => {
    if (video !== current && !video.paused) video.pause();
  });
}
