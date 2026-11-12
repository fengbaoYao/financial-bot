export type TranscriptScrollMetrics = {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
};

export function isTranscriptNearBottom(
  metrics: TranscriptScrollMetrics,
  threshold = 72,
): boolean {
  const distance = metrics.scrollHeight - metrics.clientHeight - metrics.scrollTop;
  return Number.isFinite(distance) && distance <= threshold;
}

export function scrollTranscriptToBottom(
  element: { scrollTop: number; scrollHeight: number } | null,
  shouldFollow: boolean,
): boolean {
  if (!element || !shouldFollow) return false;
  element.scrollTop = element.scrollHeight;
  return true;
}
