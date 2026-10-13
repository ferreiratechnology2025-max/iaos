import { describe, expect, test } from 'vitest';
import { createHumanInputSender, createKeyForwarder, createPasteForwarder, mouseMessage, modifierBits } from '../src/data/computer/humanInput';

const ev = (key: string, extra: object = {}) => ({
  key, code: key.length === 1 ? `Key${key.toUpperCase()}` : key, keyCode: key.length === 1 ? key.toUpperCase().charCodeAt(0) : 0,
  altKey: false, ctrlKey: false, metaKey: false, shiftKey: false, target: {},
  preventDefault() { this.prevented = true; }, stopPropagation() { this.stopped = true; },
  prevented: false, stopped: false, ...extra,
});

function rig(humanReady: boolean) {
  const sent: string[] = [];
  const socket = { readyState: 1, send: (data: string) => { sent.push(data); } };
  const send = createHumanInputSender(() => socket, () => humanReady);
  return { sent, send, forwarder: createKeyForwarder(send, () => false) };
}
const parsed = (sent: string[]) => sent.map((s) => JSON.parse(s));

describe('HUMAN_INPUT_TRANSPORT_GATE', () => {
  test('holder=human: click sends 1 pressed + 1 released, key A sends 1 down + 1 up, no duplicates', () => {
    const { sent, send, forwarder } = rig(true);
    const rect = { left: 10, top: 20, width: 400, height: 300 };
    const frame = { width: 800, height: 600 };
    const click = { clientX: 110, clientY: 120, detail: 1, button: 0 };
    send(mouseMessage('pressed', click, rect, frame));
    send(mouseMessage('released', click, rect, frame));
    const down = ev('a'); const up = ev('a');
    forwarder.onKeyDown(down); forwarder.onKeyUp(up);
    const out = parsed(sent);
    expect(out.filter((m) => m.type === 'mouse' && m.event === 'pressed')).toHaveLength(1);
    expect(out.filter((m) => m.type === 'mouse' && m.event === 'released')).toHaveLength(1);
    expect(out.filter((m) => m.type === 'key' && m.event === 'down')).toHaveLength(1);
    expect(out.filter((m) => m.type === 'key' && m.event === 'up')).toHaveLength(1);
    expect(out).toHaveLength(4);
    expect(out[0]).toMatchObject({ x: 200, y: 200, button: 'left', clickCount: 1 }); // scaled by real frame size
    expect(out[2]).toMatchObject({ key: 'a', code: 'KeyA', text: 'a', modifiers: 0 });
    expect(down.prevented && down.stopped && up.prevented).toBe(true);
  });

  test('holder=bot or stream not ready: outbound human input = 0', () => {
    const { sent, send, forwarder } = rig(false);
    send(mouseMessage('pressed', { clientX: 0, clientY: 0, detail: 1, button: 0 }, { left: 0, top: 0, width: 1, height: 1 }, { width: 1, height: 1 }));
    forwarder.onKeyDown(ev('a')); forwarder.onKeyUp(ev('a'));
    expect(sent).toHaveLength(0);
  });

  test('Enter and Shift+letter carry key text/modifiers correctly; Backspace carries no text', () => {
    const { sent, forwarder } = rig(true);
    forwarder.onKeyDown(ev('Enter')); forwarder.onKeyDown(ev('Backspace')); forwarder.onKeyDown(ev('N', { shiftKey: true }));
    const [enter, back, cap] = parsed(sent);
    expect(enter.text).toBeUndefined(); expect(back.text).toBeUndefined();
    expect(cap).toMatchObject({ text: 'N', modifiers: 8 });
    expect(modifierBits({ altKey: false, ctrlKey: false, metaKey: true, shiftKey: false })).toBe(2);
  });

  test('Escape stays local; keys typed into local IAOS fields stay local (and their keyup too)', () => {
    const sent: string[] = [];
    const send = createHumanInputSender(() => ({ readyState: 1, send: (d: string) => { sent.push(d); } }), () => true);
    const forwarder = createKeyForwarder(send, (t) => t === 'local-field');
    const esc = ev('Escape'); forwarder.onKeyDown(esc); forwarder.onKeyUp(ev('Escape'));
    forwarder.onKeyDown(ev('x', { target: 'local-field' })); forwarder.onKeyUp(ev('x'));
    expect(sent).toHaveLength(0);
    expect(esc.prevented).toBe(false);
  });
});

describe('HUMAN_PASTE_GATE', () => {
  const clip = (text: string, target: unknown = {}) => ({ target, clipboardData: { getData: () => text }, prevented: false, preventDefault() { this.prevented = true; } });
  const ctrlV = (extra = {}) => ev('v', { ctrlKey: true, ...extra });

  test('holder=human: Ctrl+V sends no remote key, paste sends exactly one text message', () => {
    const { sent, send, forwarder } = rig(true);
    const down = ctrlV(); const up = ctrlV();
    forwarder.onKeyDown(ev('Control', { ctrlKey: true }));
    forwarder.onKeyDown(down);
    const event = clip('IAOS_PASTE_V1_OK');
    createPasteForwarder(send, () => false)(event);
    forwarder.onKeyUp(up);
    const out = parsed(sent);
    expect(down.prevented).toBe(false);
    expect(up.prevented).toBe(false);
    expect(out.filter((m) => m.type === 'key' && (m.key === 'v' || m.key === 'V'))).toHaveLength(0);
    expect(out.filter((m) => m.type === 'text')).toEqual([{ type: 'text', text: 'IAOS_PASTE_V1_OK' }]);
    expect(event.prevented).toBe(true);
  });

  test('paste into a local field or with empty clipboard sends nothing', () => {
    const { sent, send } = rig(true);
    createPasteForwarder(send, () => true)(clip('x'));
    createPasteForwarder(send, () => false)(clip(''));
    expect(sent).toHaveLength(0);
  });

  test('holder=bot: outbound paste = 0', () => {
    const { sent, send } = rig(false);
    createPasteForwarder(send, () => false)(clip('IAOS_PASTE_V1_OK'));
    expect(sent).toHaveLength(0);
  });
});
