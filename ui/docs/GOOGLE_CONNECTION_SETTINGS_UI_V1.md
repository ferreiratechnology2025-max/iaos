# IAOS_GOOGLE_CONNECTION_SETTINGS_UI

The product shell exposes `#/settings/integrations/google` under
`Configurações → Integrações → Google`.

The browser receives only this sanitized shape:

```json
{
  "connected": true,
  "email": "user@example.com",
  "calendar": true,
  "gmail": false
}
```

The UI calls same-origin backend operations:

- `GET /integrations/google`
- `POST /integrations/google/connect`
- `POST /integrations/google/capabilities/gmail`
- `POST /integrations/google/disconnect`

The backend owns OAuth initiation/callback, credential storage, refresh and
revocation. The browser does not access tokens, client secrets, credential
paths, raw profiles or internal account references.

## Gate status

```ini
SETTINGS_INTEGRATIONS_GOOGLE=PASS
CONNECT_BUTTON=PASS
CONNECTED_IDENTITY_VISIBLE=PASS
CALENDAR_GRANT_VISIBLE=PASS
GMAIL_GRANT_VISIBLE=PASS
DISCONNECT_BUTTON=PASS
BROWSER_ACCESS_TOKEN=NO
BROWSER_REFRESH_TOKEN=NO
BROWSER_CLIENT_SECRET=NO
BROWSER_CREDENTIAL_PROFILE=NO
LIVE_BACKEND_OAUTH_CALLBACK=NOT_YET_PROVEN
END_TO_END_CALENDAR_SMOKE=NOT_YET_PROVEN
```
