# Universal Tool Access Hypothesis

**Status:** strategic architecture hypothesis; no runtime implementation authorized by this document.

## Thesis

An external digital service does not require an IAOS-native integration to be usable. If a service is operable by a person through an available interface, IAOS may be able to operate it through a governed computer runtime.

The scope is deliberately bounded: this applies only to digital services operable within the available capabilities, policies, network boundaries, and human-assistance paths. CAPTCHA, WebAuthn, anti-automation controls, desktop-only applications, service terms, unstable interfaces, and actions requiring human judgment remain explicit limits.

## Preferred execution order

```text
1. deterministic native capability
2. API
3. MCP
4. CLI
5. governed computer/browser
6. human takeover when required
```

The computer is a strategic execution fallback, not a replacement for deterministic integrations. Repeated or high-volume browser procedures can later justify a Skill, API, MCP, or CLI capability.

## Authority invariant

```text
REACHABILITY != AUTHORITY
```

The ability to open a page, see a control, or reach a terminal does not authorize an action. Computer actions must preserve the IAOS sequence:

```text
Proposal
  -> Governance
  -> AuthorizedAction
  -> same computer/session/snapshot identity
  -> execution
  -> Evidence
```

State-changing controls such as Publish, Delete, Buy, Send, Grant Access, and plan changes require policy appropriate to their effect. Login, 2FA, CAPTCHA, WebAuthn, secrets, and judgment boundaries may require human takeover.

## Skill contract implication

A Skill stores procedural knowledge and decision boundaries, not brittle coordinates:

```text
GOAL
PRECONDITIONS
PROCEDURE
DECISION_BOUNDARIES
SUCCESS_EVIDENCE
```

The LLM interprets the current situation, the Computer operates the current interface, Governance decides whether an effect may cross the boundary, and Evidence records what occurred.

## Product implication

The user should not need to ask whether IAOS has a dedicated connector before asking for a task. IAOS should select the strongest available execution path and fall back to a Governed Computer when no deterministic integration exists.

This is a horizontal capability hypothesis, not a claim that IAOS works on every site. A safe refusal or an explicit request for human takeover is a valid product outcome.

## Roadmap decision

`OPENBOT_COMPUTER_SPIKE` is the next experiment. `SCHEDULED_DELIVERY_V1` is deferred until the governed computer hypothesis is evaluated further. A passing spike does not authorize importing OpenBot wholesale or modifying IAOS runtime code; it authorizes a later extraction audit covering the computer, supervisor, browser profile, workspace, shell, snapshot identity, takeover, gateway contract, and evidence requirements.

