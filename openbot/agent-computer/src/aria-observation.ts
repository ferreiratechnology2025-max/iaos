/**
 * The page as the accessibility tree says it is, for a caller that has to check what the page shows.
 *
 * `/snapshot` has always returned the actionable controls and nothing around them. That is enough to
 * act and not enough to verify: the text of a todo, which checkbox belongs to which row, what a field
 * holds and "1 item left" are all in the tree and none are in the control list. This module turns the
 * same `ariaSnapshot()` text the control list is built from into a string a caller can read, so both
 * come from one capture and share one snapshot id.
 *
 * Two rules, both fail-closed:
 *
 *  - A sensitive field's value never leaves this process. The tree reports a textbox's value as a
 *    `text:` child (or as the entry's own value when it has no other children), and a password is
 *    there in plain text. The mask works on the parsed tree, by ref, before anything is serialised.
 *    A field that cannot be classified is treated as sensitive, as `secret-masking.ts` does.
 *  - Nothing is cut. A tree over the limit is refused rather than shortened, because a shortened tree
 *    that lost "1 item left" or a row reads as a complete observation of a page that is not.
 *
 * No Playwright import, so the rules have tests that need no browser.
 */

import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import { parseDescriptor } from "./aria-snapshot";
import { numberFromEnv } from "./env";
import { MASKED_VALUE } from "./secret-masking";

/** The largest tree handed back whole. Over it, the caller is told so; nothing is shortened. */
export const MAX_OBSERVATION_BYTES = numberFromEnv(
  "COMPUTER_OBSERVATION_MAX_BYTES",
  256 * 1024,
  { min: 1_024, max: 8 * 1024 * 1024 },
);

/** What the caller is told when the tree is over the limit. */
export const OBSERVATION_TOO_LARGE = "observation_too_large";
/** What the caller is told when the tree cannot be read, so cannot be masked. */
export const OBSERVATION_UNREADABLE = "observation_unreadable";

export class ObservationError extends Error {
  constructor(
    readonly code: typeof OBSERVATION_TOO_LARGE | typeof OBSERVATION_UNREADABLE,
    message: string,
  ) {
    super(message);
    this.name = "ObservationError";
  }
}

/** The roles whose contents are text somebody typed. Same set `secret-masking.ts` asks the page about. */
const VALUE_ROLES = new Set(["textbox", "searchbox", "combobox", "spinbutton"]);

function parseTree(yaml: string): unknown {
  try {
    return parseYaml(yaml, { schema: "failsafe" });
  } catch {
    throw new ObservationError(
      OBSERVATION_UNREADABLE,
      "The page could not be read, so it was not returned.",
    );
  }
}

function refOf(key: string): string | undefined {
  return parseDescriptor(key)?.flags.get("ref");
}

function roleOf(key: string): string | undefined {
  return parseDescriptor(key)?.role;
}

/**
 * Every ref in the tree that may hold typed text, filled or not.
 *
 * Not only the fields the element list shows a value for: a textbox with a placeholder reports its
 * value as a `text:` child, so the element list sees no value and would never ask the page about it,
 * while the tree carries it in plain text.
 */
export function valueFieldRefs(yaml: string): string[] {
  const refs: string[] = [];
  const walk = (node: unknown): void => {
    if (Array.isArray(node)) {
      for (const child of node) walk(child);
      return;
    }
    if (typeof node === "string") {
      const ref = refOf(node);
      if (ref && VALUE_ROLES.has(roleOf(node) ?? "")) refs.push(ref);
      return;
    }
    if (node && typeof node === "object") {
      for (const [key, value] of Object.entries(node)) {
        const ref = refOf(key);
        if (ref && VALUE_ROLES.has(roleOf(key) ?? "")) refs.push(ref);
        walk(value);
      }
    }
  };
  walk(parseTree(yaml));
  return refs;
}

/** A sensitive entry's contents with every typed value replaced and everything else left alone. */
function maskContents(value: unknown): unknown {
  if (typeof value === "string") return MASKED_VALUE;
  if (!Array.isArray(value)) return value;
  return value.map((child) => {
    if (child && typeof child === "object" && !Array.isArray(child)) {
      const entries = Object.entries(child);
      return Object.fromEntries(
        entries.map(([key, inner]) => [
          key,
          key === "text" ? MASKED_VALUE : inner,
        ]),
      );
    }
    return child;
  });
}

function maskTree(node: unknown, sensitive: ReadonlySet<string>): unknown {
  if (Array.isArray(node)) return node.map((child) => maskTree(child, sensitive));
  if (node && typeof node === "object") {
    return Object.fromEntries(
      Object.entries(node).map(([key, value]) => {
        const ref = refOf(key);
        if (ref && sensitive.has(ref)) return [key, maskContents(value)];
        return [key, maskTree(value, sensitive)];
      }),
    );
  }
  return node;
}

/**
 * The tree as text, with every sensitive field's value replaced, or an error.
 *
 * `sensitive` is the set of refs the page said are sensitive; the caller asks the page about every ref
 * `valueFieldRefs` returned. A tree that will not parse is refused: it cannot be masked, so it is not
 * shown. A tree over the limit is refused: it would have to be cut.
 */
export function observableTree(
  yaml: string,
  sensitive: ReadonlySet<string>,
  maxBytes: number = MAX_OBSERVATION_BYTES,
): string {
  const tree = parseTree(yaml);
  if (tree === null || tree === undefined) return "";
  const text = stringifyYaml(maskTree(tree, sensitive), {
    indent: 2,
    lineWidth: 0,
    schema: "failsafe",
  });
  if (Buffer.byteLength(text, "utf8") > maxBytes) {
    throw new ObservationError(
      OBSERVATION_TOO_LARGE,
      "The page is too large to return whole, so none of it was returned.",
    );
  }
  return text;
}
