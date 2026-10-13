import { describe, expect, test } from 'vitest';
import { canTake } from '../src/data/computer/takeAvailability';

describe('TAKEOVER_ON_DEMAND button gate', () => {
  test('holder=bot, requested=false, streamReady=true => ENABLED', () => {
    expect(canTake({ holder: 'bot', requested: false }, true)).toBe(true);
  });
  test('holder=bot, requested=true, streamReady=true => ENABLED (existing request)', () => {
    expect(canTake({ holder: 'bot', requested: true }, true)).toBe(true);
  });
  test('streamReady=false => DISABLED', () => {
    expect(canTake({ holder: 'bot', requested: false }, false)).toBe(false);
  });
  test('transitioning=true => DISABLED', () => {
    expect(canTake({ holder: 'bot', requested: false, transitioning: true }, true)).toBe(false);
  });
  test('holder=human or no control state => DISABLED', () => {
    expect(canTake({ holder: 'human', requested: false }, true)).toBe(false);
    expect(canTake(null, true)).toBe(false);
  });
});
