import { describe, expect, test } from "bun:test";
import { createControl } from "../src/control";
import type { Screencast } from "../src/screencast";
import { createViewerSlot } from "../src/viewer";
import {
  attachWithin,
  createFollowTick,
  singleFlight,
  VIEWER_ATTACH_TIMED_OUT,
} from "../src/viewer-attach";

/**
 * VIEWER_ATTACH_TIMEOUT_GATE.
 *
 * An attach that never finishes used to leave the viewer `starting` for the life of the socket.
 * These tests use attaches that really never finish (or finish late), a real viewer slot and a real
 * control, and a short deadline so the deadline itself is what ends the wait. No browser.
 */

const T = 30;
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

function fakeCast(): Screencast & { stops: number } {
  const cast = {
    stops: 0,
    async stop() {
      cast.stops += 1;
    },
    async send() {},
  };
  return cast as Screencast & { stops: number };
}

/** A promise settled from outside, standing in for a CDP call that answers whenever it likes. */
function deferred<V>() {
  let resolve!: (value: V) => void;
  const promise = new Promise<V>((r) => (resolve = r));
  return { promise, resolve };
}

/** Mirrors the open handler in index.ts: claim, bounded first attach, give up with an error. */
async function openStream(
  slot: ReturnType<typeof createViewerSlot>,
  socket: object,
  begin: () => Promise<{ cast: Screencast; target: string } | undefined>,
) {
  const told: string[] = [];
  let closed = false;
  const claim = slot.claim(socket, (reason) => told.push(reason));
  const outcome = await attachWithin({
    timeoutMs: T,
    begin,
    install: (started) => claim.install(started.cast),
    stop: (cast) => cast.stop(),
  });
  if (outcome === "timeout") {
    await slot.release(socket);
    told.push(VIEWER_ATTACH_TIMED_OUT);
    closed = true;
  }
  return { outcome, told, closed: () => closed };
}

describe("CASE 1: a normal attach", () => {
  test("installs the first cast and the viewer is casting", async () => {
    const slot = createViewerSlot();
    const socket = {};
    const cast = fakeCast();
    const { outcome } = await openStream(slot, socket, async () => ({ cast, target: "page-1" }));
    expect(outcome).toBe("installed");
    expect(slot.standingOf(socket).state).toBe("casting");
    expect(cast.stops).toBe(0);
  });
});

describe("CASE 2: the initial attach never resolves", () => {
  test("the deadline fires, the viewer leaves starting, the socket is told and closed", async () => {
    const slot = createViewerSlot();
    const socket = {};
    const never = new Promise<never>(() => {});
    const { outcome, told, closed } = await openStream(slot, socket, () => never);
    expect(outcome).toBe("timeout");
    expect(told).toEqual([VIEWER_ATTACH_TIMED_OUT]);
    expect(closed()).toBe(true);
    expect(slot.standingOf(socket).state).toBe("gone"); // no longer `starting`
    expect(slot.occupied()).toBe(false); // the claim was released, nothing keeps the session occupied
  });
});

describe("CASE 3: the underlying operation finishes after the deadline", () => {
  test("the late cast is stopped, never installed, and the viewer is not resurrected", async () => {
    const slot = createViewerSlot();
    const socket = {};
    const late = deferred<{ cast: Screencast; target: string }>();
    const cast = fakeCast();
    const { outcome } = await openStream(slot, socket, () => late.promise);
    expect(outcome).toBe("timeout");

    late.resolve({ cast, target: "page-1" });
    await sleep(10);

    expect(cast.stops).toBe(1);
    expect(slot.standingOf(socket).state).toBe("gone");
    expect(slot.occupied()).toBe(false);
  });

  test("on its own, the attach never calls install for a cast that arrives after the deadline", async () => {
    const late = deferred<{ cast: Screencast; target: string }>();
    const cast = fakeCast();
    let installs = 0;
    const outcome = await attachWithin({
      timeoutMs: T,
      begin: () => late.promise,
      install: async () => {
        installs += 1;
        return true;
      },
      stop: (c) => c.stop(),
    });
    expect(outcome).toBe("timeout");
    late.resolve({ cast, target: "page-1" });
    await sleep(10);
    expect(installs).toBe(0);
    expect(cast.stops).toBe(1);
  });
  test("a rejection after the deadline is swallowed", async () => {
    const outcome = await attachWithin({
      timeoutMs: T,
      begin: () => new Promise((_, reject) => setTimeout(() => reject(new Error("late")), T * 2)),
      install: async () => true,
      stop: async () => {},
    });
    expect(outcome).toBe("timeout");
    await sleep(T * 3); // would surface as an unhandled rejection and fail the run
  });
});

describe("CASE 4: reconnecting after a timeout", () => {
  test("a new stream on the same session attaches normally", async () => {
    const slot = createViewerSlot();
    const first = {};
    await openStream(slot, first, () => new Promise<never>(() => {}));

    const second = {};
    const cast = fakeCast();
    const { outcome } = await openStream(slot, second, async () => ({ cast, target: "page-1" }));
    expect(outcome).toBe("installed");
    expect(slot.standingOf(second).state).toBe("casting");
    expect(slot.standingOf(first).state).toBe("gone");
    // The recovery never reaches the browser: it only ever sees `begin`, which the test owns.
  });
});

describe("CASE 5: control is independent of viewer recovery", () => {
  for (const who of ["human", "bot"] as const) {
    test(`holder=${who} before the failure is holder=${who} after it`, async () => {
      const control = createControl();
      if (who === "human") {
        const request = control.requestHelp("Sign in", "call-1").request!;
        await control.take(request.id);
      }
      expect(control.get().holder).toBe(who);

      const slot = createViewerSlot();
      const { outcome } = await openStream(slot, {}, () => new Promise<never>(() => {}));
      expect(outcome).toBe("timeout");

      expect(control.get().holder).toBe(who);
    });
  }
});

describe("CASE 6: the follow loop", () => {
  test("a hung follow attach is never overlapped by another", async () => {
    let started = 0;
    const tick = createFollowTick({
      attach: () => {
        started += 1;
        return attachWithin({
          timeoutMs: T * 4,
          begin: () => new Promise<never>(() => {}),
          install: async () => true,
          stop: async () => {},
        });
      },
      onGiveUp: () => {},
    });
    const first = tick();
    const overlapping = await Promise.all([tick(), tick(), tick()]);
    expect(overlapping).toEqual(["skipped", "skipped", "skipped"]);
    expect(started).toBe(1);
    expect(await first).toBe("timeout");
  });

  test("after the timeout the next tick may retry", async () => {
    let started = 0;
    const tick = createFollowTick({
      attach: async () => {
        started += 1;
        return started === 1 ? "timeout" : "installed";
      },
      onGiveUp: () => {},
    });
    expect(await tick()).toBe("timeout");
    expect(await tick()).toBe("installed");
    expect(started).toBe(2);
  });

  test("consecutive timeouts end the screen once, and a success resets the count", async () => {
    let gaveUp = 0;
    const outcomes = ["timeout", "timeout", "installed", "timeout", "timeout", "timeout"];
    const tick = createFollowTick({
      attach: async () => outcomes.shift() as "timeout" | "installed",
      maxConsecutiveTimeouts: 3,
      onGiveUp: () => {
        gaveUp += 1;
      },
    });
    for (let i = 0; i < 6; i += 1) await tick();
    expect(gaveUp).toBe(1);
  });

  test("singleFlight lets the next call through once the previous settles", async () => {
    const gate = deferred<void>();
    let runs = 0;
    const run = singleFlight(async () => {
      runs += 1;
      await gate.promise;
      return "done";
    });
    const first = run();
    expect(await Promise.race([run(), sleep(20).then(() => "pending")])).toBe("skipped");
    gate.resolve();
    expect(await first).toBe("done");
    expect(await run()).toBe("done");
    expect(runs).toBe(2);
  });
});
