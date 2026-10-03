import { useState } from "react"
import type { FormEvent } from "react"
import { Trans, useTranslation } from "react-i18next"
import { useNavigate } from "react-router-dom"

import { useImmichLinks } from "../api/config"
import { apiErrorMessage } from "../api/errors"
import { Button } from "../games/shared/Button"
import { AuthCard } from "./AuthCard"
import { AuthField } from "./AuthField"
import { useAuth } from "./useAuth"

// The permissions a player's key needs - mirrors backend/src/services/immich/account.py's
// REQUIRED_PERMISSIONS (and docs/IMMICH_API_KEY.md). Shown so players can create a minimal key.
const REQUIRED_PERMISSIONS = ["user.read", "asset.view", "person.read"]

// Links the player's own Immich account via an API key. Reached from ProfilePage, and where
// RequireImmich.tsx sends anyone without a linked account - games only ever show photos that
// player can see in Immich, so nothing is playable until this is done.
export function ImmichConnectionPage() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { user, linkImmich, unlinkImmich } = useAuth()
  const links = useImmichLinks()
  const [apiKey, setApiKey] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saved, setSaved] = useState(false)

  // RequireAuth (App.tsx) guarantees a session - TypeScript narrowing only.
  if (!user) return null
  const account = user.immich_account

  async function run(action: () => Promise<unknown>) {
    setBusy(true)
    setError(null)
    setSaved(false)
    try {
      await action()
      setApiKey("")
      setSaved(true)
    } catch (err) {
      setError(apiErrorMessage(err) ?? t("auth.error.generic"))
    } finally {
      setBusy(false)
    }
  }

  function handleSave(e: FormEvent) {
    e.preventDefault()
    void run(() => linkImmich(apiKey.trim()))
  }

  return (
    <AuthCard
      title={t("auth.profile.immich.title")}
      backLabel={t("common.back")}
      onBack={() => navigate(account ? "/" : "/profile")}
    >
      <p className="mb-4 text-sm text-body">
        {account
          ? t("auth.profile.immich.connectedAs", { name: account.name, email: account.email })
          : t("auth.profile.immich.intro")}
      </p>

      <form onSubmit={handleSave} className="flex flex-col gap-4">
        <AuthField
          id="immichApiKey"
          type="password"
          label={t("auth.profile.immich.apiKey")}
          autoComplete="off"
          required
          value={apiKey}
          onChange={(e) => {
            setApiKey(e.target.value)
            setSaved(false)
          }}
        />
        <div className="text-sm text-muted">
          <p>
            {links ? (
              <Trans
                i18nKey="auth.profile.immich.howToLinked"
                components={{
                  link: (
                    <a
                      href={links.apiKeysUrl}
                      target="_blank"
                      rel="noreferrer"
                      className="font-semibold text-primary underline"
                    />
                  ),
                }}
              />
            ) : (
              t("auth.profile.immich.howTo")
            )}
          </p>
          <ul className="mt-2 list-inside list-disc">
            {REQUIRED_PERMISSIONS.map((permission) => (
              <li key={permission}>
                <code>{permission}</code>
              </li>
            ))}
          </ul>
        </div>
        {error && <p className="text-sm font-semibold text-rose-600">{error}</p>}
        {saved && !error && (
          <p className="text-sm font-semibold text-emerald-600">
            {user.immich_account
              ? t("auth.profile.immich.saved")
              : t("auth.profile.immich.removed")}
          </p>
        )}
        <Button type="submit" variant="primary" className="w-full py-2.5" disabled={busy}>
          {account ? t("auth.profile.immich.replace") : t("auth.profile.immich.save")}
        </Button>
      </form>

      {account && (
        <Button
          variant="secondary"
          className="mt-3 w-full py-2.5"
          disabled={busy}
          onClick={() => void run(unlinkImmich)}
        >
          {t("auth.profile.immich.disconnect")}
        </Button>
      )}
    </AuthCard>
  )
}
