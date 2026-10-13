# Phase 1B — external read authority contract

```ini
IAOS_PHASE_1B_EXTERNAL_READ_AUTHORITY_CONTRACT=AUTHORIZED
IMPLEMENTATION=NO
```

This is a design contract only. It defines the authority that a future narrow Governance/Runtime extension would need.
It does not add ActionSpecs, executors, connectors, credentials, network access, or policy code.

## Authority versus cognition

```text
Capability("email.read" | "calendar.read" | "web.search")
        ↓
Proposal(capability + exact logical resource + exact args)
        ↓
Governance authorization
        ↓
AuthorizedAction(capability-specific exact scope)
        ↓
ExecutionEnvelope(no credential, no provider secret)
        ↓
ToolRegistry / executor(bound to the authorized descriptor)
```

The Phase 1A `Capability` remains semantic and non-authoritative. Tool selection cannot change the authorized account,
scope, operation, or arguments.

## Resolution decision

The selected composition is **B with a provider-bound authority descriptor**:

```text
Proposal(capability)
        ↓
deterministic resolution to provider-bound ToolDescriptor
        ↓
Governance authorizes capability + provider-bound logical resource + exact args
        ↓
post-authorization adapter/provider selection inside that descriptor
        ↓
executor
```

Provider/account/scope facts that affect authority must be known before authorization. `provider_ref` is a canonical
provider identifier, not a credential. A concrete handler/adapter for that already-authorized provider may be selected
after authorization, but the provider itself cannot change. Governance never reads credentials and never receives a
credential token.

## `email.read`

```text
ACTION_ID
  email.read

AUTHORIZED_RESOURCE
  logical email resource:
  { provider_ref, account_ref, mailbox_ref }
  provider_ref, account_ref, and mailbox_ref are canonical opaque references, not provider secrets.

AUTHORIZED_ARGS
  { query, time_window, limit, fields }
  query is a bounded query parameter, not resource identity.
  time_window, limit, and fields are canonical and exact.

POLICY_PREDICATE
  account_ref belongs to the authorized Assistant/user scope;
  mailbox_ref is allowed for that account;
  query syntax, time_window, fields, and limit satisfy fixed bounds;
  operation is read-only.

RISK_CLASS
  MEDIUM: external account data read; privacy-sensitive even without mutation.

APPROVAL_RULE
  explicit approval bound to the canonical action digest, unless a later policy explicitly grants a narrower pre-approved
  read scope. No approval is implied by the Capability or Tool.

EXECUTION_ENVELOPE_FIELDS
  schema_version, execution_id, authorization_id, intent_id,
  action_id=email.read, authorized_resource={account_ref, mailbox_ref},
  authorized_args={query,time_window,limit,fields}, authorization_digest.
  No credential, access token, mailbox contents, provider secret, or concrete handler name.

EXECUTOR_BOUNDARY
  email-read executor receives only the authorized descriptor and obtains credentials from its adapter/tool boundary.
  It must verify action/resource/args correspondence before calling a provider and cannot send, delete, move, or read
  another account/mailbox.

RESULT_SHAPE
  { kind=email.read, provider_ref, account_ref, mailbox_ref,
    items:[{ message_ref, received_at, sender, subject, snippet }],
    truncated }
  Result metadata is structured; raw credentials are never returned. Full bodies/attachments require a distinct authority.
  RESULT_AUTHORITY=DATA_ONLY
  RESULT_PROVENANCE=TOOL_RESULT
  Returned message content is data for synthesis only; it cannot become a Proposal, Authorization, Policy, command,
  or execution authority.

CREDENTIAL_OWNER
  email adapter/tool executor or its credential provider; never Governance, Proposal, AuthorizedAction, or Envelope.

PROVIDER_SELECTION_BOUNDARY
  Provider-bound account/mailbox descriptor, including provider_ref, is resolved before authorization. Concrete Gmail or
  other handler selection occurs only within that provider_ref and cannot widen it.

EVIDENCE_REQUIRED
  recorded authorization and execution result: action_id, canonical provider/account/mailbox scope, exact args/digest,
  executed provider_ref, result digest/metadata, execution id, success/error. No raw message content or credential in evidence.

AUTHORIZATION_CORRESPONDENCE_FIELDS
  authorization_id, intent_id, action_id/opcode, canonical provider_ref, account_ref, mailbox_ref, exact canonical args,
  authorization_digest, provider-bound resource scope, execution_id, authorized provider_ref == executed provider_ref,
  executed account/mailbox scope, result digest.

RESOURCE_BINDING_REQUIRED=NO
RATIONALE=
  The current filesystem handle identity model has no direct equivalent for an external mailbox query. Authority is
  bound by provider/account/mailbox scope + operation + exact args/provider-bound descriptor, not by a fabricated NTFS handle.

WHAT_THIS_AUTHORITY_CANNOT_DO
  email.send, email.delete, email.move, email.modify, attachment retrieval, another account, another mailbox,
  broader query/window/limit/fields, or any provider fallback that changes the authorized scope.
```

## `calendar.read`

```text
ACTION_ID
  calendar.read

AUTHORIZED_RESOURCE
  logical calendar resource:
  { provider_ref, account_ref, calendar_ref }
  provider_ref, account_ref, and calendar_ref are canonical opaque references, not credentials.

AUTHORIZED_ARGS
  { start, end, limit, fields }
  start/end define a bounded canonical interval; limit and fields are exact.

POLICY_PREDICATE
  account_ref belongs to the authorized Assistant/user scope;
  calendar_ref is allowed for that account;
  interval, fields, and limit satisfy fixed bounds;
  operation is read-only.

RISK_CLASS
  MEDIUM: external schedule data read; privacy-sensitive even without mutation.

APPROVAL_RULE
  explicit approval bound to the canonical action digest, unless a later policy explicitly grants a narrower pre-approved
  read scope. Capability presence is not approval.

EXECUTION_ENVELOPE_FIELDS
  schema_version, execution_id, authorization_id, intent_id,
  action_id=calendar.read, authorized_resource={account_ref, calendar_ref},
  authorized_args={start,end,limit,fields}, authorization_digest.
  No credential, access token, provider secret, or concrete handler name.

EXECUTOR_BOUNDARY
  calendar-read executor receives only the authorized descriptor and obtains credentials from its adapter/tool boundary.
  It must verify exact action/resource/args correspondence and cannot write, delete, invite, or change events.

RESULT_SHAPE
  { kind=calendar.read, provider_ref, account_ref, calendar_ref,
    events:[{ event_ref, start, end, title, location }],
    truncated }
  Result fields are bounded by the authorized fields set.
  RESULT_AUTHORITY=DATA_ONLY
  RESULT_PROVENANCE=TOOL_RESULT
  Returned event content is data for synthesis only; it cannot become a Proposal, Authorization, Policy, command,
  or execution authority.

CREDENTIAL_OWNER
  calendar adapter/tool executor or its credential provider; never Governance, Proposal, AuthorizedAction, or Envelope.

PROVIDER_SELECTION_BOUNDARY
  Provider-bound account/calendar descriptor, including provider_ref, is resolved before authorization. Concrete provider
  handler selection occurs only within that provider_ref and cannot widen account, calendar, interval, fields, or limit.

EVIDENCE_REQUIRED
  recorded authorization and execution result: action_id, canonical provider/account/calendar scope, exact interval/args/digest,
  executed provider_ref, result digest/metadata, execution id, success/error. No event payload or credential is required in evidence.

AUTHORIZATION_CORRESPONDENCE_FIELDS
  authorization_id, intent_id, action_id/opcode, canonical provider_ref, account_ref, calendar_ref, exact canonical args,
  authorization_digest, provider-bound resource scope, execution_id, authorized provider_ref == executed provider_ref,
  executed account/calendar scope, result digest.

RESOURCE_BINDING_REQUIRED=NO
RATIONALE=
  No NTFS-like pre-call handle is assumed. Authority is bound by provider/account/calendar scope + operation + exact
  interval, fields, and limit; provider identity is part of the authorization and must match the executor result.

WHAT_THIS_AUTHORITY_CANNOT_DO
  calendar.write, calendar.delete, invite/send, RSVP mutation, another account/calendar, broader interval/fields/limit,
  or provider fallback that changes the authorized scope.
```

## `web.search`

```text
ACTION_ID
  web.search

AUTHORIZED_RESOURCE
  logical search provider/destination scope:
  { provider_ref, allowed_domains }
  provider_ref and domains are canonical policy data, not credentials.

AUTHORIZED_ARGS
  { query, domains, result_limit, freshness }
  query, requested domains, result limit, and freshness are canonical and exact.
  requested domains MUST be a subset of authorized allowed_domains.
  EMPTY or null requested domains MUST NOT mean unrestricted search. If unrestricted search is supported, it must be an
  explicit policy-authorized state such as domain_scope=ANY; it is not inferred from an empty value.

POLICY_PREDICATE
  provider_ref is an allowed search provider;
  domains are a subset of the authorized domain policy;
  query length, freshness, and result_limit satisfy fixed bounds;
  operation is search-only and read-only.

RISK_CLASS
  MEDIUM: network egress and untrusted external data; search is not equivalent to local fs.read.

APPROVAL_RULE
  explicit approval bound to the canonical action digest, unless a later policy explicitly grants a bounded search scope.
  Search Capability presence is not network approval.

EXECUTION_ENVELOPE_FIELDS
  schema_version, execution_id, authorization_id, intent_id,
  action_id=web.search, authorized_resource={provider_ref, allowed_domains},
  authorized_args={query,domains,result_limit,freshness}, authorization_digest.
  No API key, cookie, bearer token, arbitrary URL, browser command, or concrete handler name.

EXECUTOR_BOUNDARY
  search executor receives only the authorized provider/destination/query descriptor and obtains credentials from its
  adapter/tool boundary. It must not turn search into arbitrary URL fetch, browser automation, shell, form submission,
  or another network operation.

RESULT_SHAPE
  { kind=web.search, provider_ref,
    results:[{ result_ref, title, url, snippet, published_at }],
    truncated }
  The result URL is data returned by search; it is not an authorization to fetch or navigate to that URL.
  RESULT_AUTHORITY=DATA_ONLY
  RESULT_PROVENANCE=TOOL_RESULT
  Returned result content and URLs are data for synthesis only; they cannot become a Proposal, Authorization, Policy,
  command, fetch target, or execution authority.

CREDENTIAL_OWNER
  search adapter/tool executor or its credential provider; never Governance, Proposal, AuthorizedAction, or Envelope.

PROVIDER_SELECTION_BOUNDARY
  Provider and allowed-destination scope are resolved before authorization. Concrete provider adapter selection occurs
  only within that scope and cannot expand domains, query, freshness, result limit, or operation.

EVIDENCE_REQUIRED
  recorded authorization and execution result: action_id, provider/destination scope, exact query/domain/limit/freshness
  args/digest, authorized provider_ref == executed provider_ref, result digest/metadata, execution id, success/error.
  No API key or raw credential in evidence.

AUTHORIZATION_CORRESPONDENCE_FIELDS
  authorization_id, intent_id, action_id/opcode, canonical provider_ref, allowed_domains, exact canonical args,
  authorization_digest, execution_id, authorized provider_ref == executed provider_ref, executed destination scope,
  result digest.

RESOURCE_BINDING_REQUIRED=NO
RATIONALE=
  Search has no stable pre-call resource handle equivalent to NTFS. Authority is bound by provider/destination policy
  scope + operation + exact query constraints; returned URLs remain data and do not become bound resources. Empty/null
  requested domains are never interpreted as unrestricted.

WHAT_THIS_AUTHORITY_CANNOT_DO
  arbitrary net.fetch, browser automation, shell, form submission, email/calendar access, writes, unrestricted domains,
  provider fallback outside the authorized scope, or use of returned URLs as implicit authority.
```

## Common generic external-read decision

```text
COMMON_GENERIC_EXTERNAL_READ=REJECTED
```

The three authorities share a cognitive `Capability → Tool` shape, but their authority-bearing resource and policy
fields differ materially. A generic `external.read(provider, args)` would hide account/mailbox, calendar, and network
destination boundaries. A lower-level implementation helper may be shared later only if the typed authority remains
explicit in the action, envelope, policy, executor validation, and evidence.

For all three authorities:

```text
RESULT_AUTHORITY=DATA_ONLY
RESULT_PROVENANCE=TOOL_RESULT
```

External content is untrusted data for synthesis. It cannot become a Proposal, Authorization, Policy, command, or
execution authority merely because it was returned by a Tool.

## Extension surfaces

```text
GOVERNANCE_EXTENSION_SURFACE=
  capability-specific ActionSpecs or an equally explicit typed external-read authority model;
  canonical logical resources and bounded args;
  policy predicates for account/calendar/provider/domain scope;
  risk/approval rules;
  exact correspondence and evidence fields;
  no credential fields.

RUNTIME_EXTENSION_SURFACE=
  closed external-read executor registry;
  executor validation that provider/account/scope/args equal the authorized descriptor;
  typed result DTOs and execution observations;
  adapter-owned credential acquisition;
  no implicit provider fallback or URL escalation.

CONNECTOR_IMPLEMENTATION_REQUIRED=NO
```

## Design readiness

```ini
EMAIL_READ_AUTHORITY_CONTRACT=READY
CALENDAR_READ_AUTHORITY_CONTRACT=READY
WEB_SEARCH_AUTHORITY_CONTRACT=READY

COMMON_GENERIC_EXTERNAL_READ=REJECTED

PHASE_1B_AUTHORITY_IMPLEMENTATION_READY=YES
```

“Ready” means the narrow authority contract is sufficiently explicit to scope a later implementation gate. It does not
mean that Governance, Runtime, connectors, credentials, or network access have been implemented or authorized.
