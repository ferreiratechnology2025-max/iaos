# IAOS Phase 2 — Real Connector Mapping

```ini
IAOS_PHASE_2_REAL_CONNECTOR_MAPPING=REVIEWED
IMPLEMENTATION=NO
GOVERNANCE_CHANGE=NO
NETWORK_CALLS=NO
REAL_CONNECTORS=NO
COMMIT_AUTHORIZED=NO
```

This document maps the first real-read replacement while preserving the
frozen Phase 1B authorities. It does not authorize implementation.

## Frozen authority alignment

The provider request may contain only a deterministic translation of fields
already present in the authorized descriptor. Provider-specific parameters are
not added to the authority contract.

```text
web.search:
  query        -> q
  result_limit -> count
  freshness    -> freshness
  domains      -> deterministic domain restriction

calendar.read:
  start  -> timeMin
  end    -> timeMax
  limit  -> maxResults
  fields -> fields
  singleEvents=false or true -> fixed implementation constant only

email.read:
  query + time_window -> deterministic q
  limit               -> maxResults
  fields              -> authorized projection
  includeSpamTrash=false -> fixed implementation constant only
```

The following are excluded from the first slice:

```ini
WEB_COUNTRY=NOT_AUTHORIZED
WEB_SEARCH_LANG=NOT_AUTHORIZED
WEB_OFFSET=NOT_AUTHORIZED
CALENDAR_PAGE_TOKEN=NOT_AUTHORIZED
EMAIL_PAGE_TOKEN=NOT_AUTHORIZED
```

## Common connector invariants

```ini
PAGINATION_FIRST_SLICE=DISABLED
ONE_AUTHORIZATION=ONE_PROVIDER_REQUEST
```

If the provider returns continuation metadata, it may be returned only as
`DATA_ONLY` metadata:

```ini
TRUNCATED=TRUE
CONTINUATION_METADATA=DATA_ONLY
FOLLOWUP_REQUEST=NO
```

A later page requires a new explicit contract decision. No connector may
silently turn one authorization into multiple provider requests.

Credentials remain outside Governance:

```ini
CREDENTIALS_IN_PROPOSAL=NO
CREDENTIALS_IN_AUTHORIZED_ACTION=NO
CREDENTIALS_IN_EXECUTION_ENVELOPE=NO
CREDENTIALS_IN_EVIDENCE=NO
```

## `web.search`

```ini
SELECTED_PROVIDER=Brave Search API
PROVIDER_REF=brave.search.v1
AUTH_METHOD=X-Subscription-Token API key
CREDENTIAL_LOCATION=process environment or external secret store
CREDENTIAL_LIFECYCLE=provision, rotate, revoke
TOKEN_REFRESH_REQUIRED=NO
CURRENT_LIBRARY_OR_SDK=none declared; HTTP client must be explicit
NEW_DEPENDENCY_REQUIRED=YES unless stdlib transport is selected
```

```ini
BRAVE_NETWORK_HOST=api.search.brave.com
BRAVE_NETWORK_ENDPOINT=/res/v1/web/search
FOLLOW_REDIRECTS=NO
PROVIDER_FALLBACK=NO
NETWORK_SCOPE_FIXED=YES
```

Request mapping:

```text
authorized.args.query        -> q
authorized.args.result_limit -> count
authorized.args.freshness    -> freshness
authorized.resource.domains  -> deterministic site/domain restriction
```

The executor must validate every returned URL against the authorized domain
set. A provider query hint is not sufficient authority by itself. A redirect
to a host other than `api.search.brave.com` is rejected; no result is followed
and no alternate provider is selected.

```ini
PAGINATION_MODEL=DISABLED_FIRST_SLICE
RATE_LIMIT_MODEL=HTTP_429_PROVIDER_QUOTA
TIMEOUT_MODEL=bounded_connect_and_read_timeout
ERROR_MAPPING=401/403 credential_or_scope; 429 rate_limit; 5xx provider_unavailable; timeout network_timeout
```

Result DTO:

```text
provider web result -> {title, url, description, age}
continuation token -> data-only continuation metadata, never followed
```

```ini
RESULT_AUTHORITY=DATA_ONLY
RESULT_PROVENANCE=TOOL_RESULT
CAN_EXECUTOR_PRESERVE_EXACT_AUTHORITY=YES
CAN_PROVIDER_FALLBACK_BE_DISABLED=YES
```

Blocker for live smoke:

```ini
BRAVE_SEARCH_API_KEY=PENDING
```

## `calendar.read`

```ini
SELECTED_PROVIDER=Google Calendar API v3
PROVIDER_REF=google.calendar.v3
AUTH_METHOD=OAuth 2.0 authorization code
CREDENTIAL_LOCATION=encrypted local store or OS secret store
CREDENTIAL_LIFECYCLE=consent, encrypted refresh-token persistence, revocation, re-consent
TOKEN_REFRESH_REQUIRED=YES
CURRENT_LIBRARY_OR_SDK=google-api-python-client + google-auth-oauthlib locally present
NEW_DEPENDENCY_REQUIRED=YES for clean installation
```

Identity remains three separate values:

```text
account_ref       = stable IAOS credential-profile alias
OAuth credential  = secret material associated with that alias
provider identity = Google account identity bound to that credential
```

The executor must resolve exactly one credential profile and verify its Google
account identity before calling Calendar. It cannot substitute another
account, credential, or provider.

Request mapping:

```text
resource.calendar_ref -> calendarId
args.start             -> timeMin
args.end               -> timeMax
args.limit             -> maxResults
args.fields            -> fields projection
singleEvents           -> fixed constant only
```

```ini
CALENDAR_PAGE_TOKEN=NOT_AUTHORIZED
PAGINATION_MODEL=DISABLED_FIRST_SLICE
NETWORK_DESTINATIONS=www.googleapis.com and oauth2.googleapis.com only
NETWORK_SCOPE_FIXED=YES
RATE_LIMIT_MODEL=Google quota / HTTP 429
TIMEOUT_MODEL=bounded_connect_and_read_timeout
ERROR_MAPPING=401 invalid_grant; 403 scope_or_acl; 404 calendar_not_found; 429 quota; 5xx provider_unavailable
```

Result DTO contains only the authorized event projection. Continuation data,
if present, is returned as data only and is not followed.

```ini
RESULT_AUTHORITY=DATA_ONLY
RESULT_PROVENANCE=TOOL_RESULT
CAN_EXECUTOR_PRESERVE_EXACT_AUTHORITY=YES
CAN_PROVIDER_FALLBACK_BE_DISABLED=YES
```

Blockers:

```ini
GOOGLE_OAUTH_CLIENT=ABSENT
GOOGLE_REFRESH_TOKEN=ABSENT
ACCOUNT_BINDING=IMPLEMENTATION_REQUIRED
```

## `email.read`

```ini
SELECTED_PROVIDER=Gmail API v1
PROVIDER_REF=google.gmail.v1
AUTH_METHOD=OAuth 2.0 authorization code
CREDENTIAL_LOCATION=encrypted local store or OS secret store
CREDENTIAL_LIFECYCLE=consent, encrypted refresh-token persistence, revocation, re-consent
TOKEN_REFRESH_REQUIRED=YES
CURRENT_LIBRARY_OR_SDK=google-api-python-client + google-auth-oauthlib locally present
NEW_DEPENDENCY_REQUIRED=YES for clean installation
```

Identity has the same strict separation:

```text
account_ref       = stable IAOS credential-profile alias
OAuth credential  = secret material associated with that alias
provider identity = Gmail account identity bound to that credential
```

Request mapping:

```text
args.query + args.time_window -> deterministic Gmail q
args.limit                   -> maxResults
args.fields                  -> authorized projection
includeSpamTrash=false       -> fixed constant only
```

```ini
EMAIL_PAGE_TOKEN=NOT_AUTHORIZED
PAGINATION_MODEL=DISABLED_FIRST_SLICE
EMAIL_BODY_READ=NO
ATTACHMENT_READ=NO
NETWORK_DESTINATIONS=gmail.googleapis.com and oauth2.googleapis.com only
NETWORK_SCOPE_FIXED=YES
RATE_LIMIT_MODEL=Google quota / HTTP 429
TIMEOUT_MODEL=bounded_connect_and_read_timeout
ERROR_MAPPING=401 invalid_grant; 403 scope_or_account; 404 mailbox_not_found; 429 quota; 5xx provider_unavailable
```

The result is limited to:

```text
message_ref
received_at
sender
subject
snippet
```

`messages.get` may be used only if required to obtain those authorized fields.
It must not return a full body, attachment, raw MIME payload, or a new read
authority.

```ini
RESULT_AUTHORITY=DATA_ONLY
RESULT_PROVENANCE=TOOL_RESULT
CAN_EXECUTOR_PRESERVE_EXACT_AUTHORITY=YES
CAN_PROVIDER_FALLBACK_BE_DISABLED=YES
```

Blockers:

```ini
GOOGLE_OAUTH_CLIENT=ABSENT
GOOGLE_REFRESH_TOKEN=ABSENT
ACCOUNT_BINDING=IMPLEMENTATION_REQUIRED
MAILBOX_MAPPING=IMPLEMENTATION_REQUIRED
```

## Local environment inspection

Only presence/absence was inspected; no secret value was read or printed.

```ini
AGNES_API_KEY=ABSENT
BRAVE_SEARCH_API_KEY=ABSENT
GOOGLE_CLIENT_SECRET_FILE=ABSENT
GOOGLE_APPLICATION_CREDENTIALS=ABSENT
GOOGLE_TOKEN_FILE=ABSENT
.env=ABSENT
.env.local=ABSENT
credentials.json=ABSENT
token.json=ABSENT
client_secret.json=ABSENT
```

## Recommended implementation order

```text
freeze this corrected mapping
    -> BraveSearchExecutor only
    -> injected HTTP fake tests
    -> one live Brave smoke when key is provisioned
    -> calendar OAuth/account binding
    -> Gmail OAuth/mailbox binding
```

```ini
PHASE_2_REAL_CONNECTOR_MAPPING_REVIEW=CHANGES_REQUESTED
REAL_CONNECTOR_IMPLEMENTATION=NOT_AUTHORIZED_YET
FIRST_CONNECTOR=web.search
```
