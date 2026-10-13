# IAOS Gmail Bounded Read Plan Contract

```ini
IAOS_GMAIL_BOUNDED_READ_PLAN_CONTRACT=READY
ACTION_ID=email.read
PLAN_KIND=google.gmail.read.v1
LOGICAL_EFFECT_COUNT=1
GOVERNANCE_CHANGE_REQUIRED=NO
AUTHORITY_CONTRACT_CHANGE_REQUIRED=NO
```

## Logical effect

One `AuthorizedAction(email.read)` and one `ExecutionEnvelope` authorize one
bounded logical Gmail read. The physical provider plan is fixed:

```text
exactly one users.messages.list
    -> zero through K users.messages.get
       where K <= authorized_args.limit
    -> one EmailReadResult
```

This is not permission for arbitrary network access. The executor may call
only `google.gmail.v1` for the authorized account and mailbox.

```ini
PHYSICAL_GMAIL_REQUEST_COUNT=1+K
MAX_GMAIL_API_REQUESTS=1+authorized_limit
MAX_CONCURRENCY=1
AUTOMATIC_RETRY=NO
BATCH_API=NO
HIDDEN_REQUESTS=NO
PAGINATION=NO
```

Credential refresh belongs to `GoogleCredentialBroker` and is not a Gmail
read request.

## Provider and request binding

```ini
PROVIDER_REF=google.gmail.v1
PROVIDER_FALLBACK=NO
ACCOUNT_FALLBACK=NO
MAILBOX_FALLBACK=NO
DERIVED_MESSAGE_REF_SOURCE=AUTHORIZED_LIST_RESULT_ONLY
```

The list request is derived deterministically from the authorized
`provider_ref`, `account_ref`, `mailbox_ref`, `query`, `time_window`, and
`limit`. It performs exactly one `users.messages.list` with `maxResults <=
authorized_args.limit` and no `pageToken`.

If the response includes `nextPageToken`, the executor sets `truncated=true`
and does not request another page.

Each detail request must use a `message_ref` returned by that same list call.
The reference cannot come from the user, an LLM, a workflow, a previous
result, another executor, or another execution. It remains bound to the same
provider, account, mailbox, and authorized limit.

```text
authorized query
    -> messages.list
    -> [M1, M2, M3]
    -> get(M1), get(M2), get(M3)
```

## Detail projection and data boundary

`users.messages.get` may return only the fields required for the authorized
DTO: `id`, `internalDate`, `snippet`, and strictly authorized headers such as
`From` and `Subject`. The executor must not return full body content, MIME,
parts, or attachments.

```ini
EMAIL_BODY_READ=NO
ATTACHMENT_READ=NO
FULL_BODY_RETURNED=NO
```

The logical result is:

```text
message_ref
received_at
sender
subject
snippet
```

## Execution and failure semantics

The order is deterministic and sequential:

```text
list
-> get(ref1)
-> get(ref2)
-> ...
-> get(refK)
```

If `list` fails, `email.read` fails. If any detail request fails, the whole
logical read fails. Partial product results are never returned as success,
and V1 performs no implicit retry.

One `authorization_id` and one `execution_id` cover the complete plan.
Evidence may record the plan kind, authorized limit, list count, detail count,
derived-reference count, completed-step count, success/error, and result
digest. It must not record message body, snippet, subject, sender, OAuth
tokens, or refresh tokens.

Gmail content is always data only. It cannot create an `AuthorizedAction`,
alter policy, start a tool, or add requests to the plan.

```ini
RESULT_AUTHORITY=DATA_ONLY
RESULT_PROVENANCE=TOOL_RESULT
PARTIAL_SUCCESS=NO
ARBITRARY_MESSAGE_REF=DENIED
```

## Incremental consent invariants

The existing Google identity, connection, and `account_ref` are reused. Gmail
consent adds only `gmail.readonly`; it creates no new account binding. If the
user denies Gmail, Calendar remains connected. If identity validation, token
update, or persistence fails, the previous valid Google connection remains
untouched.

```ini
GOOGLE_CONNECTION_REUSED=YES
ACCOUNT_REF_REUSED=YES
NEW_ACCOUNT_BINDING_REQUIRED=NO
CALENDAR_MUST_REMAIN_CONNECTED=YES
GMAIL_CONSENT_DENIED_PRESERVES_CALENDAR=YES
GMAIL_TOKEN_UPDATE_FAILURE_PRESERVES_CONNECTION=YES
GMAIL_EXECUTOR_FAILURE_DELETES_CREDENTIAL=NO
```

## Scope and out of scope

The minimum scope for the existing contract is
`https://www.googleapis.com/auth/gmail.readonly`. `gmail.metadata` cannot
support the authorized Gmail query because Gmail disallows `q` with that
scope. `gmail.send`, `gmail.modify`, deletion, pagination, batching, retries,
attachments, full-body reads, arbitrary `messages.get`, and new Governance
authority are out of scope.

## Gate

```ini
IAOS_GMAIL_BOUNDED_READ_PLAN_CONTRACT=READY

ONE_AUTHORIZATION_ONE_LOGICAL_EFFECT=PASS
MULTI_REQUEST_BOUND_EXPLICIT=PASS
LOGICAL_EFFECT_COUNT=1
PHYSICAL_REQUEST_COUNT=1+K

LIST_REQUEST_EXACTLY_ONE=PASS
DETAIL_REQUESTS_LE_AUTHORIZED_LIMIT=PASS
NO_PAGINATION=PASS
NO_AUTOMATIC_RETRY=PASS
NO_BATCH=PASS

DERIVED_RESOURCE_LINEAGE=PASS
ARBITRARY_MESSAGE_REF=DENIED

PROVIDER_SCOPE_PRESERVED=PASS
ACCOUNT_SCOPE_PRESERVED=PASS
MAILBOX_SCOPE_PRESERVED=PASS

EMAIL_BODY_READ=NO
ATTACHMENT_READ=NO
PARTIAL_SUCCESS=NO

RESULT_AUTHORITY=DATA_ONLY
RESULT_PROVENANCE=TOOL_RESULT

GOVERNANCE_CHANGE_REQUIRED=NO
AUTHORITY_CONTRACT_CHANGE_REQUIRED=NO
GMAIL_EXECUTOR_IMPLEMENTATION_READY=YES
```
