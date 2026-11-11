/** Ignore responses belonging to an assistant that is no longer selected. */
export function isCurrentBotResponse(requestedBotId: string, currentBotId: string | null): boolean {
  return requestedBotId === currentBotId;
}
