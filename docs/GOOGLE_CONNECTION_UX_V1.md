# IAOS_GOOGLE_CONNECTION_UX_V1=PROPOSED_NOT_YET_PROVEN

This is the intended product contract, not proof that the Settings UI and the
complete OAuth flow are already deployed end-to-end. The backend composition
selects `WindowsDPAPIGoogleCredentialStore` for production; tests continue to
inject `InMemoryGoogleCredentialStore`.

The intended product entry point is `Settings > Integrations > Google`. The user starts
OAuth with one click, chooses a Google account, grants the requested service
permission, and returns to a sanitized connected state. No terminal, JSON
file, environment variable, `account_ref`, refresh token, or Google Cloud
Console setup is part of the user experience.

Calendar and Gmail share one Google connection. `GoogleCredentialBroker` owns
identity binding, credential persistence, token lifecycle integration, and
disconnect. Gmail may be added later through incremental consent without a
second Google connection.

The UI contract is:

```json
{
  "connected": true,
  "email": "user@example.com",
  "calendar": true,
  "gmail": false
}
```

Provider/account identifiers and credential material are internal broker data.
`calendar.read` continues to consume a provider-bound `CredentialProfile`; the
product layer supplies that profile through the broker rather than exposing
its lookup key to the user.

The backend callback contract redirects back to this Settings route. End-to-end
UI wiring is not yet proven. Success has
no query parameters; failure carries only an allowlisted `error` code. Raw
OAuth callback data is never redirected to or rendered by the UI.
