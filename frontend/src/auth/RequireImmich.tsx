import { Navigate, Outlet } from "react-router-dom"

import { useAuth } from "./useAuth"

// Everything a game shows comes from the player's own Immich library (see
// backend/src/services/immich/_scope.py), so every game route sits behind a linked Immich account.
// Nested inside RequireAuth (App.tsx), so `user` is always set by the time this renders. Also the
// landing spot for a key Immich starts rejecting mid-use: AuthProvider.tsx's interceptor clears
// immich_account on the backend's 409, and this redirects on the next render.
export function RequireImmich() {
  const { user } = useAuth()

  if (user && !user.immich_account) return <Navigate to="/profile/immich" replace />
  return <Outlet />
}
