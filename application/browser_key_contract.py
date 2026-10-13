"""The pinned Playwright keyboard.press contract used by OpenBot /key."""
from __future__ import annotations

import json
import re
import string


# OpenBot passes /key directly to Playwright. These are the code names in the
# pinned Playwright USKeyboardLayout, including its aliases and printable key
# values. No IAOS-specific normalization is performed.
_CODE_NAMES = frozenset(
    {
        "Escape", "Backquote", "Minus", "Equal", "Backslash", "Backspace",
        "Tab", "BracketLeft", "BracketRight", "CapsLock", "Semicolon", "Quote",
        "Enter", "ShiftLeft", "ShiftRight", "Comma", "Period", "Slash",
        "ControlLeft", "ControlRight", "MetaLeft", "MetaRight", "AltLeft",
        "AltRight", "Space", "AltGraph", "ContextMenu", "PrintScreen",
        "ScrollLock", "Pause", "PageUp", "PageDown", "Insert", "Delete",
        "Home", "End", "ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown",
        "AudioVolumeMute", "AudioVolumeDown", "AudioVolumeUp", "MediaTrackNext",
        "MediaTrackPrevious", "MediaPlayPause", "NumLock", "NumpadDivide",
        "NumpadMultiply", "NumpadSubtract", "NumpadAdd", "NumpadDecimal",
        "NumpadEnter", "Clear",
        *(f"F{i}" for i in range(1, 13)),
        *(f"Digit{i}" for i in range(10)),
        *(f"Key{letter}" for letter in string.ascii_uppercase),
        *(f"Numpad{i}" for i in range(10)),
    }
)
_ALIASES = frozenset({"Shift", "Control", "ControlOrMeta", "Alt", "Meta", "\n", "\r"})
_PRINTABLE_KEYS = frozenset(
    string.ascii_letters + string.digits + "`~!@#$%^&*()-_=+\\|[]{};:'\",.<>/? "
)


def _valid_token(token: str) -> bool:
    return token in _CODE_NAMES or token in _ALIASES or token in _PRINTABLE_KEYS


def canonical_browser_key_tokens() -> tuple[str, ...]:
    """Return the exact token universe shared by prompt and runtime validation."""
    return tuple(sorted(_CODE_NAMES | _ALIASES | _PRINTABLE_KEYS))


def browser_key_action_schema() -> dict[str, object]:
    """Return the machine-readable action schema for the canonical key contract."""
    tokens = canonical_browser_key_tokens()
    token_pattern = "(?:" + "|".join(re.escape(token) for token in tokens) + ")"
    return {
        "type": "object",
        "required": ["key"],
        "additionalProperties": False,
        "properties": {
            "key": {
                "type": "string",
                "maxLength": 64,
                "pattern": f"^{token_pattern}(?:\\+{token_pattern})*$",
            },
        },
    }


def is_valid_browser_key(value: object) -> bool:
    """Match the pinned Playwright ``keyboard.press`` tokenization and lookup."""
    if not isinstance(value, str) or not value or len(value) > 64:
        return False
    tokens: list[str] = []
    building = ""
    for char in value:
        if char == "+" and building:
            tokens.append(building)
            building = ""
        else:
            building += char
    tokens.append(building)
    return all(_valid_token(token) for token in tokens)


BROWSER_KEY_CONTRACT_DESCRIPTION = (
    "a pinned Playwright key name/code, one supported character, or a + combination "
    "of the exact canonical tokens "
    f"{json.dumps(canonical_browser_key_tokens(), ensure_ascii=False)}; "
    "never an element ref and never a mouse action"
)
