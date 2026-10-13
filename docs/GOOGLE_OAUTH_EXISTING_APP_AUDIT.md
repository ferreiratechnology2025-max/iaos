# Existing Google OAuth app audit — read-only

Audit decision: **do not create or reuse an OAuth client from this evidence**.

## Local evidence for Melo Video

The real private YouTube upload is documented in
`D:\melo_video\publishing\RESULTADO.md` and identifies the verified account
and private video receipt. The local OAuth client metadata was inspected
without exposing client secrets or token contents:

```ini
MELO_YOUTUBE_UPLOAD=CONFIRMED_LOCAL_EVIDENCE
GOOGLE_CLIENT_TYPE=installed
GOOGLE_CLOUD_PROJECT=melovideo-508110
AUTHORIZED_REDIRECT_URI=http://localhost
YOUTUBE_SCOPES=openid,email,youtube.upload,youtube.readonly
```

The implementation in `D:\melo_video\packages\publishing\src\youtube-oauth.ts`
also confirms that this client was a YouTube-specific desktop OAuth flow with
PKCE/state and channel verification. This is strong evidence for the Melo
Video flow, not evidence that its consent-screen display name was `d:reframe`.

## `d:reframe`

```ini
APP_DISPLAY_NAME=NOT_VERIFIED
GOOGLE_CLOUD_PROJECT=NOT_VERIFIED
OAUTH_CLIENT_TYPES=NOT_VERIFIED
OAUTH_CLIENT_COUNT=NOT_VERIFIED
AUTHORIZED_REDIRECT_URIS=NOT_VERIFIED
CURRENT_SCOPES=NOT_VERIFIED
CONSENT_CONFIG=NOT_VERIFIED
TEST_USERS=NOT_VERIFIED
PUBLISHING_STATUS=NOT_VERIFIED
YOUTUBE_UPLOAD_USED_BY_THIS_APP=UNKNOWN
CONTROLLED_BY_US=UNKNOWN
SUITABLE_FOR_IAOS=NOT_EVALUATED
```

No local IAOS/Melo source, receipt, or configuration identified `d:reframe`.
The Google Cloud console read-only check could not be completed because the
available browser session was not authenticated. No login, client creation,
scope change, redirect change, revocation, or other external mutation was
performed.

## Product decision

Keep `melovideo-508110` as Melo Video/publishing infrastructure. Do not reuse
it for IAOS: its observed scopes mix YouTube publishing authority with the
future Calendar/Gmail product surface, and its consent branding is not proven
to be IAOS.

Create a separately branded IAOS OAuth app only after the internal Google
Cloud operator configures it. IAOS should request Calendar read-only first;
Gmail remains incremental and unavailable in the current slice.

```ini
GOOGLE_OAUTH_EXISTING_APP_AUDIT=PARTIAL
MELO_VIDEO_OAUTH_PROJECT_IDENTIFIED=PASS
DREFRAME_IDENTITY_CONFIRMED=NO
IAOS_INTERNAL_OAUTH_CLIENT_CONFIGURED=NOT_PROVEN
IAOS_GOOGLE_CONNECTION_UX_V1=PROPOSED_NOT_YET_PROVEN
```
