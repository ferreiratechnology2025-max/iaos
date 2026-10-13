# IAOS

IAOS is a generic governed-execution kernel for AI agents.

The language model makes cognitive decisions and host runtimes provide
capabilities. IAOS governs the execution boundary: authorization, exact
invocation binding, durable consumption, observation and effect evidence,
receipts, and deterministic verification. It does not replace model reasoning
or reimplement every tool and runtime.

## Architecture

```text
Agnes      -> cognition and decision
OpenDots   -> AgentLoop and tool catalog
OpenBot    -> browser and computer runtime
MCP/tools  -> external capabilities
IAOS       -> authorization, binding, consumption, evidence, receipts, verification
```

The generic path is:

```text
model decision
  -> tool invocation
  -> IAOS authorization and exact binding
  -> governed runtime execution
  -> durable receipt/evidence
  -> deterministic readback and verification
```

New capabilities belong at the host/tool layer. They should not require a new
IAOS handler when they use the generic invocation contract.

## What can an IAOS-governed agent do?

The available tools determine the agent's capabilities. Depending on the host,
an agent may be able to:

- perform web research and read public sources;
- interact with a browser or computer;
- read and modify files, write programs, and run tests or builds;
- create documents, pages, or sites;
- use MCP and other external integrations;
- perform authorized external effects.

IAOS governs these operations; it is not itself a research agent, browser
agent, coding agent, or product-specific executor.

## V1.6 Architecture 2 — proven state

The following properties were demonstrated end-to-end with Agnes hosted by
OpenDots and IAOS used as the generic governance kernel:

```text
IAOS_V1_6_ARCHITECTURE_2=PROVEN
GENERIC_TOOL_GOVERNANCE=PROVEN
DURABLE_AUTHORIZATION=PROVEN
DURABLE_CONSUMPTION=PROVEN
DURABLE_OBSERVATION_EVIDENCE=PROVEN
DURABLE_EFFECT_RECEIPT=PROVEN
SOURCE_BINDING=PROVEN
EXACT_EFFECT_BINDING=PROVEN
RECEIPT_DERIVED_READBACK=PROVEN
DETERMINISTIC_FINAL_VERIFICATION=PROVEN
```

The proof covered real source research, durable `OBSERVE` evidence, an
authorized `EFFECT`, pre-execution binding, one original executor call, a
durable receipt, receipt-derived page readback, and deterministic verification.

These results are evidence for this architecture and scope. They do not claim
distributed exactly-once execution, immutable historical revisions, universal
tool safety, or completed production hardening.

## Operational configuration

Runtime state must live outside the repository. At minimum, the local gateway
uses:

```text
IAOS_TOOL_STATE_DB=<path to a durable SQLite database>
IAOS_B2_ENABLE_EFFECTS=<explicit truthy value when governed effects are enabled>
```

The default is fail-closed for effects:

```text
IAOS_B2_ENABLE_EFFECTS absent
  -> EFFECT authorization is denied
```

Observation governance can still operate without enabling effects. Never place
API keys, tokens, `.env` files, runtime databases, receipts, or live evidence
in this repository.

The safe variable template is [`.env.example`](.env.example). The product UI
lives in [`ui/`](ui/), OpenDots in [`opendots/`](opendots/), and the imported
OpenBot runtime in [`openbot/`](openbot/). The UI reads only the IAOS gateway's
read-only `/ui/*` surface for governance state. OpenDots remains the
Architecture 2 host and owns model configuration: configure its
OpenAI-compatible provider with
`OPENAI_API_KEY`, `OPENAI_BASE_URL`, and `OPENAI_MODEL` in the OpenDots `.env`
file. IAOS does not own the model or AgentLoop.

For a local gateway-only check, install the project dependencies, set
`IAOS_TOOL_STATE_DB` to a path outside this repository, and run:

```powershell
python scripts/iaos_tool_gateway.py
```

The gateway listens on `http://127.0.0.1:8791`. A clean checkout can start the
gateway, OpenDots host, and UI together with `scripts\dev.ps1`; the
Docker-managed OpenBot computer runtime remains a separate prerequisite and is
deliberately not started implicitly. OpenDots agent computer actions are
mediated through `IAOS_TOOL_GATEWAY_URL` before they reach OpenBot; owner
takeover remains a direct control path. Copy `ui/.env.example` to a local
`.env` and use `VITE_IAOS_DEMO_MODE=true` only for explicit demo/test runs.

The public workspace is intentionally one repository with explicit internal
boundaries:

```text
iaos/
├── governance/  IAOS kernel
├── opendots/    AgentLoop, tools, and AG-UI host
├── openbot/     browser/computer runtime source
├── ui/          IAOS console
├── scripts/     development and test entry points
├── tests/       IAOS tests
├── docs/
├── .env.example
└── README.md
```

Runtime flow:

```text
IAOS UI → OpenDots / DotAgent → tool invocation → IAOS governance → OpenBot
```

Autonomous computer actions are authorized and bound by IAOS before reaching
OpenBot; receipts are recorded after execution. Human takeover uses the
separate explicit owner-control channel. `scripts\dev.ps1` reports
`OPENBOT_RUNTIME=READY` or `OPENBOT_RUNTIME=NOT_RUNNING` and never starts
Docker implicitly.

## Development

Create an isolated virtual environment, install the project dependencies, and
run:

```powershell
python -m pytest -q
python -m compileall application assurance audit claims cognitive evidence governance scripts skilleval skills
git diff --check
```

The repository contains contracts, kernel code, tests, and synthetic fixtures.
Live credentials and runtime state are intentionally external and are not
required for the ordinary test suite.

Relevant contracts include:

- [`docs/EXECUTION_ENVELOPE_CONTRACT.md`](docs/EXECUTION_ENVELOPE_CONTRACT.md)
- [`docs/AUTHORIZATION_CORRESPONDENCE.md`](docs/AUTHORIZATION_CORRESPONDENCE.md)
- [`docs/CLAIMED_RESULT.md`](docs/CLAIMED_RESULT.md)
- [`docs/INDEPENDENT_AUDIT.md`](docs/INDEPENDENT_AUDIT.md)

## Scope and limits

IAOS currently proves a governed local execution architecture. It does not
claim that every possible tool is automatically safe, that all persistence is
distributed, or that a deployment is production-hardened merely because the
kernel contract is generic. Capability-specific correctness remains the
responsibility of the host runtime and its tests.
