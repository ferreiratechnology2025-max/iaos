import { expect, test } from "bun:test";
import { detectChallenge } from "../src/challenge";

function fakeControl() {
  const calls: unknown[][] = [];
  return {
    calls,
    control: {
      requestHelp: (...args: unknown[]) => {
        calls.push(args);
        return { request: { id: "req-1" } };
      },
    } as never,
  };
}
const page = {} as never; // the cfMitigated path never touches the page

test("default: a detected challenge opens a handoff request (legacy behaviour unchanged)", async () => {
  const { calls, control } = fakeControl();
  const result = await detectChallenge(page, control, { cfMitigated: "challenge" });
  expect(result).toMatchObject({ kind: "cloudflare", requestId: "req-1" });
  expect(calls.length).toBe(1);
});

test("requestHelp:false classifies the challenge without creating a request", async () => {
  const { calls, control } = fakeControl();
  const result = await detectChallenge(page, control, { cfMitigated: "challenge", requestHelp: false });
  expect(result).toMatchObject({ kind: "cloudflare" });
  expect((result as Record<string, unknown>).requestId).toBeUndefined();
  expect(calls.length).toBe(0);
});

test("requestHelp:false and no challenge returns undefined and creates nothing", async () => {
  const { calls, control } = fakeControl();
  const evaluating = { evaluate: async () => ({ text: "hello", visibleChallengeControl: false }) } as never;
  expect(await detectChallenge(evaluating, control, { requestHelp: false })).toBeUndefined();
  expect(calls.length).toBe(0);
});
