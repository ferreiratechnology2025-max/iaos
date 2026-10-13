"""Generic, read-only scope facts derived from the aria text that is already in a trusted Observation.

No DOM, selectors or coordinates: only role / sanitized name / ref / containment as printed by the snapshot.
Iframe content is flattened under its ``iframe`` node, so containment in the text equals containment on the page.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

_LINE = re.compile(r'^(\s*)- ([^\s:"\[]+)(?: "((?:[^"\\]|\\.)*)")?(.*)$')
_REF = re.compile(r"\[ref=([A-Za-z0-9]+)\]")
_FRAME = re.compile(r"^(f\d+)e\d+$")
_DIALOG_ROLES = ("dialog", "alertdialog")
_CONTROL_ROLES = ("button", "link", "textbox", "searchbox", "checkbox", "radio", "combobox", "menuitem", "tab",
                  "switch", "slider", "spinbutton", "option", "menuitemcheckbox", "menuitemradio")
NAME_LIMIT = 80


def clean_name(value: Optional[str]) -> str:
    text = re.sub(r"\s+", " ", (value or "").replace('\\"', '"')).strip()
    return text[:NAME_LIMIT]


def frame_of(ref: str) -> str:
    match = _FRAME.match(ref or "")
    return match.group(1) if match else "main"


@dataclass(frozen=True)
class RefScope:
    ref: str
    role: str
    name: str
    frame: str
    inside_dialog: bool
    nearest_dialog: Optional[str]  # sanitized dialog name ("" when it has none); None when outside any dialog
    ancestry: Tuple[str, ...]      # roles from the root, capped


@dataclass
class DialogInfo:
    ref: Optional[str]
    role: str
    name: str
    attributes: str
    controls: List[RefScope] = field(default_factory=list)


@dataclass
class ScopeReport:
    scopes: Dict[str, RefScope]
    dialogs: List[DialogInfo]
    controls_outside: int


def analyze(aria: str) -> ScopeReport:
    scopes: Dict[str, RefScope] = {}
    dialogs: List[DialogInfo] = []
    stack: List[Tuple[int, str, str, Optional[str], Optional[DialogInfo]]] = []  # indent, role, name, ref, dialog
    outside = 0
    for raw in (aria or "").splitlines():
        m = _LINE.match(raw)
        if not m:
            continue
        indent, role, name, rest = len(m.group(1)), m.group(2), clean_name(m.group(3)), m.group(4)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        ref_match = _REF.search(rest)
        ref = ref_match.group(1) if ref_match else None
        dialog: Optional[DialogInfo] = None
        if role in _DIALOG_ROLES:
            dialog = DialogInfo(ref, role, name, re.sub(r"\[ref=[^\]]+\]", "", rest).strip(": ").strip())
            dialogs.append(dialog)
        stack.append((indent, role, name, ref, dialog))
        if ref is None:
            continue
        enclosing = next((s[4] for s in reversed(stack[:-1]) if s[4] is not None), None)
        if dialog is not None:
            enclosing = dialog if enclosing is None else enclosing
        scope = RefScope(ref, role, name, frame_of(ref), enclosing is not None,
                         enclosing.name if enclosing is not None else None,
                         tuple(s[1] for s in stack[-6:]))
        scopes[ref] = scope
        if role in _CONTROL_ROLES:
            if enclosing is not None:
                enclosing.controls.append(scope)
            else:
                outside += 1
    return ScopeReport(scopes, dialogs, outside)


def describe_target(aria: str, ref: str) -> str:
    """One log fragment for a chosen ref. Never raises: logging must not change behavior."""
    try:
        scope = analyze(aria).scopes.get(ref)
    except Exception:
        scope = None
    if scope is None:
        return f"TARGET ref={ref} frame={frame_of(ref)} scope=unknown"
    dialog = "NO" if not scope.inside_dialog else "YES"
    nearest = "" if scope.nearest_dialog is None else f' nearest_dialog="{scope.nearest_dialog}"'
    return (f'TARGET ref={ref} role={scope.role} name="{scope.name}" frame={scope.frame} '
            f"inside_dialog={dialog}{nearest}")


# ---------------------------------------------------------------- interaction context (prompt projection)

MAX_DIALOGS = 3
MAX_OWN_CONTROLS = 15


@dataclass(frozen=True)
class DialogContext:
    ref: Optional[str]
    name: str
    frame: str
    own_frame_controls: Tuple[RefScope, ...]
    embedded_frames: Tuple[Tuple[str, int], ...]  # (frame id, actionable control count)


def interaction_context(aria: str) -> Tuple[DialogContext, ...]:
    """Per dialog: controls in the dialog's own frame, and embedded frames summarized by count.

    Derived only from the already-captured aria text. Nothing is hidden from the full observation and no
    control is marked forbidden: a control in an embedded frame may be exactly the right target.
    """
    report = analyze(aria)
    result: List[DialogContext] = []
    for dialog in report.dialogs:
        frame = frame_of(dialog.ref) if dialog.ref else "main"
        own = tuple(c for c in dialog.controls if c.frame == frame)
        counts: Dict[str, int] = {}
        for c in dialog.controls:
            if c.frame != frame:
                counts[c.frame] = counts.get(c.frame, 0) + 1
        result.append(DialogContext(dialog.ref, dialog.name, frame, own, tuple(sorted(counts.items()))))
    return tuple(result)


def render_interaction_context(aria: str) -> List[str]:
    """Lines for the observation block. Page-derived names are quoted with repr, like the element list."""
    contexts = interaction_context(aria)
    if not contexts:
        return ["interaction_context:", "  no dialog is present."]
    lines = ["interaction_context:",
             "  a dialog is present (role=dialog does not by itself prove it blocks the page)."]
    for ctx in contexts[:MAX_DIALOGS]:
        name = repr(ctx.name) if ctx.name else "<unnamed>"
        lines.append(f"  dialog ref={ctx.ref or '<none>'} name={name} frame={ctx.frame}")
        lines.append("    controls in the dialog's own frame:")
        for c in ctx.own_frame_controls[:MAX_OWN_CONTROLS]:
            lines.append(f"    - ref={c.ref} role={c.role} name={c.name!r}")
        if len(ctx.own_frame_controls) > MAX_OWN_CONTROLS:
            lines.append(f"    - (+{len(ctx.own_frame_controls) - MAX_OWN_CONTROLS} more in the elements list)")
        if not ctx.own_frame_controls:
            lines.append("    - (none)")
        if ctx.embedded_frames:
            total = sum(n for _, n in ctx.embedded_frames)
            lines.append(f"    embedded content inside the dialog: {len(ctx.embedded_frames)} frames, {total} actionable controls")
            lines += [f"    - frame {fid}: {n} actionable controls" for fid, n in ctx.embedded_frames]
            lines.append("    controls in embedded frames belong to embedded content and may still be valid targets; "
                         "their detail stays in the elements list and aria below.")
    return lines


def context_summary(aria: str) -> str:
    """One log line fragment (counts only)."""
    try:
        contexts = interaction_context(aria)
    except Exception:
        return "INTERACTION_CONTEXT unavailable"
    if not contexts:
        return "INTERACTION_CONTEXT dialogs=0"
    d = contexts[0]
    return (f"INTERACTION_CONTEXT dialogs={len(contexts)} dialog_frame={d.frame} own_frame_controls={len(d.own_frame_controls)} "
            f"embedded_frames={len(d.embedded_frames)} embedded_controls={sum(n for _, n in d.embedded_frames)}")
