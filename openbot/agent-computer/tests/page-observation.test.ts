import { describe, expect, test } from "bun:test";
import { createControl } from "../src/control";
import {
  guardControl,
  observeWithin,
  PAGE_OBSERVATION_TIMED_OUT,
  PageObservationTimeout,
  snapshotWithin,
} from "../src/page-observation";

/**
 * PAGE_OBSERVATION_DEADLINE_GATE.
 *
 * `/read` and `/snapshot` used to wait for a page that never answered, forever: the HTTP request
 * hung, nothing was logged, and `resumeSnapshotRequired` stayed true with no way out. These tests use
 * reads that really never finish (or finish late), a real control, and a short deadline so the
 * deadline itself is what ends the wait. No browser.
 */

const T = 30;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));
const never = <V>() => new Promise<V>(() => undefined);

/** Never lets a missing deadline hang the suite: the answer is "still pending" instead. */
async function settledWithin<V>(promise: Promise<V>, ms: number) {
  return Promise.race([
    promise.then(
      (value) => ({ kind: "resolved" as const, value }),
      (error: unknown) => ({ kind: "rejected" as const, error }),
    ),
    sleep(ms).then(() => ({ kind: "pending" as const })),
  ]);
}

/** A control the way it is after a person handed the browser back: a fresh snapshot is owed. */
async function controlOwingASnapshot() {
  const control = createControl();
  const request = control.requestHelp("Sign in", "call-1").request!;
  await control.take(request.id);
  control.release(request.id);
  expect(control.get().resumeSnapshotRequired).toBe(true);
  return control;
}

describe("CASE 1: /read normal", () => {
  test("resolves with what the read produced, once", async () => {
    let calls = 0;
    const value = await observeWithin(async () => {
      calls += 1;
      return { text: "hello" };
    }, 500);
    expect(value).toEqual({ text: "hello" });
    expect(calls).toBe(1);
  });

  test("a read that fails in time fails with its own error, not a timeout", async () => {
    const outcome = await settledWithin(
      observeWithin(async () => {
        throw new Error("Target closed");
      }, 500),
      300,
    );
    expect(outcome.kind).toBe("rejected");
    if (outcome.kind === "rejected") {
      expect((outcome.error as Error).message).toBe("Target closed");
      expect(outcome.error).not.toBeInstanceOf(PageObservationTimeout);
    }
  });
});

describe("CASE 2: /snapshot normal", () => {
  test("commits exactly once and clears the owed snapshot", async () => {
    const control = await controlOwingASnapshot();
    let commits = 0;
    const result = await snapshotWithin({
      timeoutMs: 500,
      read: async () => ({ elements: 3 }),
      commit: () => {
        commits += 1;
        control.snapshotTaken();
      },
    });
    expect(result).toEqual({ elements: 3 });
    expect(commits).toBe(1);
    expect(control.get().resumeSnapshotRequired).toBe(false);
  });
});

describe("CASE 3: /read hung", () => {
  test("ends at the deadline with a recoverable error", async () => {
    const started = Date.now();
    const outcome = await settledWithin(
      observeWithin(() => never(), T),
      T * 20,
    );
    expect(outcome.kind).toBe("rejected");
    if (outcome.kind === "rejected") {
      expect(outcome.error).toBeInstanceOf(PageObservationTimeout);
      expect((outcome.error as Error).message).toBe(PAGE_OBSERVATION_TIMED_OUT);
    }
    expect(Date.now() - started).toBeLessThan(T * 20);
  });

  test("is not retried", async () => {
    let calls = 0;
    await settledWithin(
      observeWithin(() => {
        calls += 1;
        return never();
      }, T),
      T * 10,
    );
    await sleep(T * 3);
    expect(calls).toBe(1);
  });
});

describe("CASE 4: /snapshot hung before the commit", () => {
  test("times out with nothing committed and the snapshot still owed", async () => {
    const control = await controlOwingASnapshot();
    let commits = 0;
    const outcome = await settledWithin(
      snapshotWithin({
        timeoutMs: T,
        read: () => never(),
        commit: () => {
          commits += 1;
          control.snapshotTaken();
        },
      }),
      T * 20,
    );
    expect(outcome.kind).toBe("rejected");
    if (outcome.kind === "rejected")
      expect(outcome.error).toBeInstanceOf(PageObservationTimeout);
    expect(commits).toBe(0);
    expect(control.get().resumeSnapshotRequired).toBe(true);
  });

  test("a read that fails after part of the work leaves the snapshot owed too", async () => {
    const control = await controlOwingASnapshot();
    let commits = 0;
    const outcome = await settledWithin(
      snapshotWithin({
        timeoutMs: 500,
        read: async () => {
          await sleep(5);
          throw new Error("challenge check failed");
        },
        commit: () => {
          commits += 1;
          control.snapshotTaken();
        },
      }),
      300,
    );
    expect(outcome.kind).toBe("rejected");
    expect(commits).toBe(0);
    expect(control.get().resumeSnapshotRequired).toBe(true);
  });

  test("the commit does not run while a read is still pending", async () => {
    const control = await controlOwingASnapshot();
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const pending = snapshotWithin({
      timeoutMs: 500,
      read: async () => {
        await gate;
        return "late enough";
      },
      commit: () => control.snapshotTaken(),
    });
    await sleep(10);
    expect(control.get().resumeSnapshotRequired).toBe(true);
    release();
    await pending;
    expect(control.get().resumeSnapshotRequired).toBe(false);
  });
});

describe("CASE 5: the read finishes after the deadline", () => {
  test("a late snapshot is ignored: nothing is committed", async () => {
    const control = await controlOwingASnapshot();
    let commits = 0;
    const outcome = await settledWithin(
      snapshotWithin({
        timeoutMs: T,
        read: async () => {
          await sleep(T * 3);
          return "too late";
        },
        commit: () => {
          commits += 1;
          control.snapshotTaken();
        },
      }),
      T * 20,
    );
    expect(outcome.kind).toBe("rejected");
    await sleep(T * 5);
    expect(commits).toBe(0);
    expect(control.get().resumeSnapshotRequired).toBe(true);
  });

  test("a late rejection is swallowed", async () => {
    const outcome = await settledWithin(
      observeWithin(async () => {
        await sleep(T * 3);
        throw new Error("Target closed, much later");
      }, T),
      T * 20,
    );
    expect(outcome.kind).toBe("rejected");
    if (outcome.kind === "rejected")
      expect(outcome.error).toBeInstanceOf(PageObservationTimeout);
    await sleep(T * 5);
  });

  test("a challenge found late cannot open a help request", async () => {
    const control = createControl();
    let attempted = false;
    const outcome = await settledWithin(
      observeWithin(async (deadline) => {
        await sleep(T * 3);
        attempted = true;
        // What detectChallenge does when its evaluate comes back with a challenge on the page.
        guardControl(control, deadline).requestHelp("Challenge", "late-1");
        return "unreachable";
      }, T),
      T * 20,
    );
    expect(outcome.kind).toBe("rejected");
    await sleep(T * 5);
    expect(attempted).toBe(true);
    expect(control.get().requested).toBe(false);
    expect(control.get().request).toBeUndefined();
  });

  test("a challenge found in time still opens a help request", async () => {
    const control = createControl();
    await observeWithin(async (deadline) => {
      guardControl(control, deadline).requestHelp("Challenge", "in-time-1");
      return "ok";
    }, 500);
    expect(control.get().requested).toBe(true);
  });

  test("the guarded control still answers every other call", async () => {
    const control = createControl();
    const holder = await observeWithin(
      async (deadline) => guardControl(control, deadline).get().holder,
      500,
    );
    expect(holder).toBe("bot");
  });
});
