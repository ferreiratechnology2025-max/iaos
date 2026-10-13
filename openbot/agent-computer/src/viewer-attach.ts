import { numberFromEnv } from "./env";

/**
 * A bounded life for the work that puts a cast behind the live screen.
 *
 * Starting a cast asks for the Bot's page and then for a CDP session on it, and neither call has a
 * deadline of its own: a page that is mid-navigation, a renderer that is not answering or a target
 * that went away leaves the call pending for good. Pending was not a state anything could leave. The
 * viewer stayed `starting`, the follow loop, which is only installed after the first attach, never
 * ran, and every message the person sent got "still starting" back for as long as the socket lived.
 *
 * Nothing here can cancel the underlying calls. A deadline can only stop WAITING for them, so what
 * matters is what happens if they finish late: a cast that arrives after its deadline is stopped and
 * never installed, or a slow page would turn "starting forever" into a screen that comes back later
 * to a socket that has already been told it failed.
 *
 * Kept free of `playwright`, like `viewer.ts`, so the decisions can be tested without a browser.
 */

/** What the person is told when the screen could not be started in time. Recoverable: reconnect. */
export const VIEWER_ATTACH_TIMED_OUT =
  "This screen could not be started in time. Reconnect to try again.";

/** One central deadline for an attach. CDP calls that are working answer in milliseconds. */
export const VIEWER_ATTACH_TIMEOUT_MS = numberFromEnv(
  "COMPUTER_VIEWER_ATTACH_TIMEOUT_MS",
  5_000,
  { min: 100, max: 120_000 },
);

/** How many follow attaches in a row may time out before the screen is given up and told so. */
export const VIEWER_FOLLOW_MAX_TIMEOUTS = 3;

export type Started<Cast, Target> = { cast: Cast; target: Target };

export type AttachOutcome = "installed" | "unchanged" | "refused" | "timeout";

/**
 * Run one attach with a deadline.
 *
 * `begin` finds the page and starts the cast, or answers `undefined` when the cast is already on the
 * right page. `install` puts the cast behind the viewer's claim, which may refuse it. `stop` ends a
 * cast nobody will use.
 *
 * A rejection from `begin` or `install` before the deadline is the caller's to handle, exactly as
 * before. After the deadline nothing can be reported any more, so it is swallowed here.
 */
export async function attachWithin<Cast, Target>(options: {
  timeoutMs: number;
  begin: () => Promise<Started<Cast, Target> | undefined>;
  install: (started: Started<Cast, Target>) => Promise<boolean>;
  stop: (cast: Cast) => Promise<void>;
}): Promise<AttachOutcome> {
  let expired = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const work: Promise<AttachOutcome> = (async () => {
    const started = await options.begin();
    if (!started) return "unchanged";
    if (expired) {
      // Late: the deadline already answered for this attach, so it must not come back to life.
      await options.stop(started.cast).catch(() => undefined);
      return "timeout";
    }
    return (await options.install(started)) ? "installed" : "refused";
  })();

  const deadline = new Promise<AttachOutcome>((resolve) => {
    timer = setTimeout(() => {
      expired = true;
      resolve("timeout");
    }, options.timeoutMs);
  });

  try {
    return await Promise.race([work, deadline]);
  } finally {
    if (timer) clearTimeout(timer);
    // If the deadline won, the work may still reject later; there is nobody left to tell.
    void work.catch(() => undefined);
  }
}

/**
 * One tick at a time. A tick that starts while the previous one is still pending is skipped, not
 * queued, so a hung attach cannot turn a once-a-second loop into a pile of pending promises.
 */
export function singleFlight<T>(run: () => Promise<T>): () => Promise<T | "skipped"> {
  let inFlight = false;
  return async () => {
    if (inFlight) return "skipped";
    inFlight = true;
    try {
      return await run();
    } finally {
      inFlight = false;
    }
  };
}

/**
 * The follow loop's tick: single-flight, bounded, and giving up loudly.
 *
 * A timed-out tick leaves the cast the person already has in place and the next tick may try again,
 * but only a few times in a row. Each abandoned attach is still pending underneath, so retrying
 * forever would leak one per interval; after the limit the screen is handed back as failed, which
 * is the same recoverable answer the first attach gives.
 */
export function createFollowTick(options: {
  attach: () => Promise<AttachOutcome>;
  maxConsecutiveTimeouts?: number;
  onGiveUp: () => void | Promise<void>;
}): () => Promise<AttachOutcome | "skipped"> {
  const limit = options.maxConsecutiveTimeouts ?? VIEWER_FOLLOW_MAX_TIMEOUTS;
  let timeouts = 0;
  return singleFlight(async () => {
    const outcome = await options.attach();
    if (outcome !== "timeout") {
      timeouts = 0;
      return outcome;
    }
    timeouts += 1;
    if (timeouts >= limit) await options.onGiveUp();
    return outcome;
  });
}
