import { numberFromEnv } from "./env";

/**
 * A bounded life for reading the Bot's page.
 *
 * `/read` and `/snapshot` end in CDP calls (`evaluate`, `ariaSnapshot`, `title`) that have no
 * deadline of their own. A renderer that is not answering leaves them pending for good: the HTTP
 * request hangs, nothing is logged, no error reaches the caller, and because `snapshotTaken()` is
 * what clears `resumeSnapshotRequired`, the Bot stays correctly refused with no way out.
 *
 * Nothing here can cancel the underlying calls. A deadline can only stop WAITING for them, so what
 * matters is what happens if they finish late: their result is dropped, and they must not be able to
 * change anything the caller already decided was a failure. Two things in the page-reading path can
 * change state: `snapshotTaken()`, which is the commit point for a snapshot and so runs last, and
 * `detectChallenge()`, which may open a help request through `control.requestHelp`.
 *
 * There is no retry. A page that did not answer once is not asked again behind the caller's back.
 *
 * Kept free of `playwright`, like `viewer-attach.ts`, so the decisions can be tested without a browser.
 */

/** What the caller is told when the page did not answer in time. Recoverable: ask again later. */
export const PAGE_OBSERVATION_TIMED_OUT =
  "The page did not answer in time. Try again in a moment.";

/** One central deadline for reading a page. CDP calls that are working answer in milliseconds. */
export const PAGE_OBSERVATION_TIMEOUT_MS = numberFromEnv(
  "COMPUTER_PAGE_OBSERVATION_TIMEOUT_MS",
  8_000,
  { min: 100, max: 120_000 },
);

export class PageObservationTimeout extends Error {
  constructor() {
    super(PAGE_OBSERVATION_TIMED_OUT);
    this.name = "PageObservationTimeout";
  }
}

/** Whether the observation is still the one the caller is waiting for. */
export type Deadline = { live(): boolean };

/**
 * Run one observation with a deadline.
 *
 * Resolves or rejects with what `run` did if that happens in time. Otherwise rejects with
 * `PageObservationTimeout`, and whatever `run` does afterwards, including a late rejection, is
 * ignored. `run` is given `live()` so it can refuse to change state once it is too late.
 */
export function observeWithin<T>(
  run: (deadline: Deadline) => Promise<T>,
  timeoutMs: number = PAGE_OBSERVATION_TIMEOUT_MS,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let expired = false;
    let settled = false;
    const deadline: Deadline = { live: () => !expired && !settled };
    const timer = setTimeout(() => {
      if (settled) return;
      expired = true;
      reject(new PageObservationTimeout());
    }, timeoutMs);
    Promise.resolve()
      .then(() => run(deadline))
      .then(
        (value) => {
          if (expired || settled) return;
          settled = true;
          clearTimeout(timer);
          resolve(value);
        },
        (error: unknown) => {
          if (expired || settled) return;
          settled = true;
          clearTimeout(timer);
          reject(error);
        },
      );
  });
}

/**
 * Read a snapshot, then commit it.
 *
 * `read` does every page read the answer needs, challenge detection included. `commit` is
 * `snapshotTaken()`: it runs only after all of them have succeeded and only while the deadline is
 * still live, in the same turn as the answer that is returned. So a caller never sees
 * `resumeSnapshotRequired=false` together with a failed or timed-out snapshot, and a read that
 * finishes late cannot clear it behind a response that already said it failed.
 */
export function snapshotWithin<R>(options: {
  read: (deadline: Deadline) => Promise<R>;
  commit: () => void;
  timeoutMs?: number;
}): Promise<R> {
  return observeWithin(async (deadline) => {
    const result = await options.read(deadline);
    if (!deadline.live()) throw new PageObservationTimeout();
    options.commit();
    return result;
  }, options.timeoutMs);
}

/**
 * A view of the control that cannot open a help request once the observation has expired.
 *
 * `detectChallenge` calls `control.requestHelp` when it finds a challenge. If its `evaluate` returns
 * after the deadline, that call would open a takeover request for a read the caller was told had
 * failed. It throws instead, and the late rejection is swallowed by `observeWithin`.
 */
export function guardControl<C extends object>(
  control: C,
  deadline: Deadline,
): C {
  return new Proxy(control, {
    get(target, property, receiver) {
      const value = Reflect.get(target, property, receiver);
      if (typeof value !== "function") return value;
      if (property === "requestHelp") {
        return (...args: unknown[]) => {
          if (!deadline.live()) throw new PageObservationTimeout();
          return value.apply(target, args);
        };
      }
      return value.bind(target);
    },
  });
}
