// Human input transport for the takeover surface (pure, unit-testable).
// Mirrors OpenBot's live-screen: the canvas cannot hold focus, so keystrokes are captured on
// window while the human holds the wheel and forwarded to the remote browser.

export type KeyLike = {
  key: string; code: string; keyCode: number;
  altKey: boolean; ctrlKey: boolean; metaKey: boolean; shiftKey: boolean;
  target?: unknown;
};

export type OutboundMessage = Record<string, unknown>;

/** CDP modifier bitmask: Alt 1, Control 2 (Command is sent as Control), Shift 8. */
export function modifierBits(event: Pick<KeyLike, 'altKey' | 'ctrlKey' | 'metaKey' | 'shiftKey'>): number {
  return (event.altKey ? 1 : 0) | (event.ctrlKey || event.metaKey ? 2 : 0) | (event.shiftKey ? 8 : 0);
}

function remoteKey(event: KeyLike) {
  return event.key === 'Meta'
    ? { key: 'Control', code: event.code === 'MetaRight' ? 'ControlRight' : 'ControlLeft', windowsVirtualKeyCode: 17 }
    : { key: event.key, code: event.code, windowsVirtualKeyCode: event.keyCode };
}

/** A key aimed at one of IAOS's own editable fields must stay local. */
export function isLocalField(target: unknown): boolean {
  if (typeof HTMLElement === 'undefined' || !(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName);
}

export function keyDownMessage(event: KeyLike): OutboundMessage {
  return {
    type: 'key', event: 'down', ...remoteKey(event),
    // Only a printable character carries text; Backspace with text would insert a character.
    ...(event.key.length === 1 ? { text: event.key } : {}),
    modifiers: modifierBits(event),
  };
}

export function keyUpMessage(event: KeyLike): OutboundMessage {
  return { type: 'key', event: 'up', ...remoteKey(event), modifiers: modifierBits(event) };
}

export type MouseLike = { clientX: number; clientY: number; detail: number; button: number };
export type Rect = { left: number; top: number; width: number; height: number };

export function mouseMessage(kind: 'pressed' | 'released' | 'moved', event: MouseLike, rect: Rect, frame: { width: number; height: number }): OutboundMessage {
  const x = (event.clientX - rect.left) * frame.width / rect.width;
  const y = (event.clientY - rect.top) * frame.height / rect.height;
  if (kind === 'moved') return { type: 'mouse', event: 'moved', x, y };
  return { type: 'mouse', event: kind, button: 'left', clickCount: Math.max(1, event.detail || 1), x, y };
}

/** Ctrl/Cmd+V: the local browser must create the paste event; the shortcut itself is never forwarded. */
export function isPasteShortcut(event: Pick<KeyLike, 'key' | 'code' | 'ctrlKey' | 'metaKey'>): boolean {
  if (!(event.ctrlKey || event.metaKey)) return false;
  const written = event.key.toLowerCase();
  if (written.length !== 1 || written.charCodeAt(0) < 0x80) return written === 'v';
  return event.code === 'KeyV';
}

/** Window-level key forwarding. Escape stays local; keys typed into local fields stay local. */
export function createKeyForwarder(send: (message: OutboundMessage) => void, isLocal: (target: unknown) => boolean = isLocalField) {
  const localKeyUps = new Set<string>();
  return {
    onKeyDown(event: KeyLike & { preventDefault(): void; stopPropagation(): void }) {
      if (event.key === 'Escape') return;
      if (isPasteShortcut(event) || isLocal(event.target)) { localKeyUps.add(event.code); return; }
      event.preventDefault();
      event.stopPropagation();
      send(keyDownMessage(event));
    },
    onKeyUp(event: KeyLike & { preventDefault(): void }) {
      if (event.key === 'Escape') return;
      if (localKeyUps.delete(event.code) || isPasteShortcut(event)) return;
      event.preventDefault();
      send(keyUpMessage(event));
    },
  };
}

type SocketLike = { readyState: number; send(data: string): void };

/** The only path to the socket for human input: nothing leaves unless the human holds the wheel. */
export function createHumanInputSender(getSocket: () => SocketLike | null, isHumanReady: () => boolean, openState = 1) {
  return (message: OutboundMessage) => {
    const socket = getSocket();
    if (isHumanReady() && socket && socket.readyState === openState) socket.send(JSON.stringify(message));
  };
}

/** Paste arrives as one block of text (the remote inserts it as text, not key events). */
export function createPasteForwarder(send: (message: OutboundMessage) => void, isLocal: (target: unknown) => boolean = isLocalField) {
  return (event: { target?: unknown; clipboardData?: { getData(type: string): string } | null; preventDefault(): void }) => {
    if (isLocal(event.target)) return;
    const text = event.clipboardData?.getData('text');
    if (!text) return;
    event.preventDefault();
    send({ type: 'text', text });
  };
}
