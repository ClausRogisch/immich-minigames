import axios from "axios"

// The backend's own domain exceptions (main.py's _error_handler) always come back as
// {"detail": "..."} - surface that message when present (e.g. "email already registered")
// instead of a generic fallback, since it's meaningful UX on auth forms.
export function apiErrorMessage(err: unknown): string | undefined {
  if (axios.isAxiosError(err)) {
    const detail = err.response?.data?.detail
    if (typeof detail === "string") return detail
  }
  return undefined
}

// Every route that reads the player's Immich library answers this (409) while they have no linked
// Immich account, or Immich rejected the one they linked - see backend/src/services/errors.py's
// ImmichNotLinkedError. AuthProvider.tsx's interceptor turns it into a redirect to link one.
export const IMMICH_NOT_LINKED = "immich_not_linked"

export function isImmichNotLinked(err: unknown): boolean {
  return apiErrorStatus(err) === 409 && apiErrorMessage(err) === IMMICH_NOT_LINKED
}

// The HTTP status of a failed request, when the failure was an HTTP response at all - for callers
// that branch on a specific status (e.g. the daily flow treating 409 "already played" as a state,
// not an error) rather than on the human-readable detail above.
export function apiErrorStatus(err: unknown): number | undefined {
  return axios.isAxiosError(err) ? err.response?.status : undefined
}
