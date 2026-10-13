# Phase 1B — external read execution mapping

```ini
IAOS_PHASE_1B_EXTERNAL_READ_MAPPING=AUTHORIZED
IMPLEMENTATION=NO
GOVERNANCE_CHANGE=NO
REAL_CONNECTORS=NO
NETWORK_CALLS=NO
```

## Boundary distinction

```text
Capability  = semantic operation requested by cognition
Tool        = possible implementation of that Capability
Governed Action = authority-bearing operation accepted by Governance
Executor    = runtime implementation selected by the closed effect registry
```

The Phase 1A `Capability` and `Tool` objects are cognitive/product contracts only. The fake tools in
`cognitive/registry.py` return in-memory data and do not prove any governed external effect.

Current governed boundary:

```text
ACTION_REGISTRY = fs.read | fs.read_bytes | fs.write
default AEPExecutorRegistry = fs.read | fs.read_bytes
Policy resource rule = canonical resource must be inside workspace root
Budget forbidden class = shell | net
```

Evidence: `governance/contract.py`, `governance/policy.py`, `governance/budget.py`,
`governance/aep_executors.py`, `governance/canonicalize.py`.

## Capability mapping

### `email.read`

```ini
CAPABILITY=email.read
CURRENT_COGNITIVE_CONTRACT=cognitive.contracts.Capability("email.read")
CURRENT_TOOL_CONTRACT=cognitive.registry.Tool; current implementation is FakeTool("fake-email-read")

EXISTING_GOVERNED_ACTION=NO
EXISTING_ACTION_SPEC=NO; ACTION_REGISTRY contains only fs.read, fs.read_bytes, fs.write
EXISTING_POLICY_PATH=NO; current Policy only evaluates canonical filesystem resources under workspace root
EXISTING_RISK_PATH=NO; risk.assess can classify only a registered ActionSpec; unknown action is HIGH/fail-closed
EXISTING_AUTHORIZATION_PATH=NO; Canonicalizer rejects unknown action before Authorizer can produce AuthorizedAction
EXISTING_EXECUTION_ENVELOPE_PATH=NO; AEPAdapter translates only an existing AuthorizedAction
EXISTING_EXECUTOR=NO; default AEPExecutorRegistry has no email executor
EXISTING_RESULT_SHAPE=NO governed shape; Phase 1A fake result is Mapping(items), not AdapterResult/evidence

REQUIRES_NEW_AUTHORITY_CLASS=YES
REQUIRES_GOVERNANCE_CHANGE=YES
REQUIRES_RUNTIME_EXECUTOR=YES
REQUIRES_NETWORK_AUTHORITY=YES
REQUIRES_PROVIDER_SECRET=YES for a real account/provider connector; no secret is used by the fake tool

CLEAN_COMPOSITION_PATH=NO
BLOCKING_GAPS=No account/mailbox scope, provider/session identity, query/read bounds, network policy, ActionSpec, authorization path, envelope semantics, executor, or governed result evidence.
MINIMAL_EXTENSION_IF_REQUIRED=Define an email-specific read-only governed action with explicit account/mailbox scope, bounded query/fields, provider identity, risk/policy rules, executor result DTO, and evidence fields. Do not model it as fs.read or generic external.read.
```

### `calendar.read`

```ini
CAPABILITY=calendar.read
CURRENT_COGNITIVE_CONTRACT=cognitive.contracts.Capability("calendar.read")
CURRENT_TOOL_CONTRACT=cognitive.registry.Tool; current implementation is FakeTool("fake-calendar-read")

EXISTING_GOVERNED_ACTION=NO
EXISTING_ACTION_SPEC=NO; no calendar ActionSpec exists
EXISTING_POLICY_PATH=NO; current Policy is workspace-path based and has no calendar/account scope semantics
EXISTING_RISK_PATH=NO; only registered filesystem ActionSpecs reach risk.assess
EXISTING_AUTHORIZATION_PATH=NO; unknown calendar action is rejected by Canonicalizer
EXISTING_EXECUTION_ENVELOPE_PATH=NO; no AuthorizedAction exists to translate
EXISTING_EXECUTOR=NO; default AEPExecutorRegistry has no calendar executor
EXISTING_RESULT_SHAPE=NO governed shape; Phase 1A fake result is Mapping(items), not an observed external result

REQUIRES_NEW_AUTHORITY_CLASS=YES
REQUIRES_GOVERNANCE_CHANGE=YES
REQUIRES_RUNTIME_EXECUTOR=YES
REQUIRES_NETWORK_AUTHORITY=YES
REQUIRES_PROVIDER_SECRET=YES for a real calendar account/provider connector; no secret is used by the fake tool

CLEAN_COMPOSITION_PATH=NO
BLOCKING_GAPS=No account/calendar scope, time-window bounds, provider/session identity, network policy, ActionSpec, authorization path, envelope semantics, executor, or governed result evidence.
MINIMAL_EXTENSION_IF_REQUIRED=Define a calendar-specific read-only governed action with explicit account/calendar scope, bounded time range and fields, provider identity, risk/policy rules, executor result DTO, and evidence fields. Do not reuse filesystem identity semantics.
```

### `web.search`

```ini
CAPABILITY=web.search
CURRENT_COGNITIVE_CONTRACT=cognitive.contracts.Capability("web.search")
CURRENT_TOOL_CONTRACT=cognitive.registry.Tool; current implementation is FakeTool("fake-web-search")

EXISTING_GOVERNED_ACTION=NO
EXISTING_ACTION_SPEC=NO; no network/search ActionSpec exists
EXISTING_POLICY_PATH=NO; current Policy cannot authorize a network destination or search scope
EXISTING_RISK_PATH=NO; unknown actions become HIGH but have no permitted execution path
EXISTING_AUTHORIZATION_PATH=NO; Canonicalizer rejects an unregistered web-search action
EXISTING_EXECUTION_ENVELOPE_PATH=NO; AEPAdapter cannot translate a semantic fake/tool result into an envelope
EXISTING_EXECUTOR=NO; default AEPExecutorRegistry explicitly has no network executor
EXISTING_RESULT_SHAPE=NO governed shape; Phase 1A fake result is Mapping(items), not a network/evidence result DTO

REQUIRES_NEW_AUTHORITY_CLASS=YES
REQUIRES_GOVERNANCE_CHANGE=YES
REQUIRES_RUNTIME_EXECUTOR=YES
REQUIRES_NETWORK_AUTHORITY=YES
REQUIRES_PROVIDER_SECRET=YES for a real search provider/API; a public provider could change this credential detail, but not the network authority requirement

CLEAN_COMPOSITION_PATH=NO
BLOCKING_GAPS=The current runtime has no real network authority at all; no destination/egress policy, provider scope, query/result limits, ActionSpec, authorization path, envelope semantics, executor, or governed result evidence exists.
MINIMAL_EXTENSION_IF_REQUIRED=Define a search-specific read-only governed action with explicit provider/destination scope, query and result limits, network policy, provider identity, risk/approval rules, executor result DTO, and evidence fields. Do not hide this under generic external.read.
```

## Joint conclusion

```ini
EXTERNAL_READ_EXECUTION_PATH=BLOCKED_REQUIRES_AUTHORITY_EXTENSION
IAOS_PHASE_1B_MAPPING=PASS
CLEAN_COMPOSITION_PATH=NO
REASON=NEW_GOVERNED_EXTERNAL_READ_AUTHORITY_REQUIRED
```

The existing fake `ToolRegistry` proves only:

```text
Capability → fake Tool → structured in-memory result
```

It does not prove:

```text
Capability → Governed Action → AuthorizedAction → ExecutionEnvelope → Executor → external world
```

There is no clean composition path for any of the three capabilities in the current implementation.

## Generic external-read abstraction decision

Do **not** introduce this as the first authority abstraction:

```text
external.read(provider, args)
```

That shape hides materially different authority facts:

```text
email.read     = account/mailbox/message scope and provider session
calendar.read  = account/calendar/time-window scope and provider session
web.search     = network destination/provider/query/result scope
```

The cognitive layer may continue to use the common `Capability → Tool` contract. The governed layer should initially
use capability-specific ActionSpecs or an equally explicit typed authority model, with separate policy/risk/result
semantics. A lower-level shared executor helper may be considered only after those authority fields remain explicit in
the envelope and evidence; it must not erase their distinction.

## Exit gate

```ini
IAOS_PHASE_1B_MAPPING=PASS
CLEAN_COMPOSITION_PATH=NO
REASON=NEW_GOVERNED_EXTERNAL_READ_AUTHORITY_REQUIRED
```

No connector, network call, Governance change, ActionSpec, executor, or UI change was made by this mapping.
