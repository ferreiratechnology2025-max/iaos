import { describe, expect, test } from "bun:test";
import { parse as parseYaml } from "yaml";
import {
  MAX_OBSERVATION_BYTES,
  ObservationError,
  OBSERVATION_TOO_LARGE,
  OBSERVATION_UNREADABLE,
  observableTree,
  valueFieldRefs,
} from "../src/aria-observation";
import { parseAriaSnapshot } from "../src/aria-snapshot";
import { MASKED_VALUE } from "../src/secret-masking";

/**
 * ARIA_OBSERVATION_GATE.
 *
 * The control list `/snapshot` returns cannot tell a caller what a todo says, which checkbox belongs
 * to it, what a field holds or how many items are left. The tree can, and now rides on the same
 * capture. These tests use the real shape Playwright 1.62 produced for TodoMVC (captured in a
 * scratch browser), and check three things: what a verifier needs survives, a secret does not leave,
 * and a tree that is too big is refused rather than cut. No browser.
 */

const NONE: ReadonlySet<string> = new Set();

const TODOMVC = `- generic [ref=e35]:
  - generic [ref=e36]:
    - heading "todos" [level=1] [ref=e37]
    - textbox "New Todo Input" [active] [ref=e38]:
      - /placeholder: What needs to be done?
      - text: rascunho nao enviado
  - main [ref=e39]:
    - list [ref=e41]:
      - listitem [ref=e42]:
        - generic [ref=e43]:
          - checkbox [checked] [ref=e44]
          - generic [ref=e45]: comprar café
          - button "Delete todo" [ref=e46]: ×
      - listitem [ref=e47]:
        - generic [ref=e48]:
          - checkbox [ref=e49]
          - generic [ref=e50]: ligar para Maria
          - text: ×
  - generic [ref=e51]:
    - generic [ref=e52]: 1 item left!
    - list [ref=e53]:
      - listitem [ref=e54]:
        - link "All" [ref=e55] [cursor=pointer]:
          - /url: "#/"
`;

const LOGIN_INLINE = `- generic [ref=e1]:
  - textbox "Email" [ref=e8]: ana@example.com
  - textbox "Password" [ref=e9]: hunter2
  - button "Sign in" [ref=e10]
`;

const LOGIN_PLACEHOLDER = `- generic [ref=e1]:
  - textbox "Email" [ref=e8]:
    - /placeholder: you@example.com
    - text: ana@example.com
  - textbox "Password" [ref=e9]:
    - /placeholder: Your password
    - text: hunter2
  - button "Sign in" [ref=e10]
`;

describe("what a verifier needs survives", () => {
  const out = observableTree(TODOMVC, NONE);
  const tree = parseYaml(out, { schema: "failsafe" });

  test("todo text, checked state, field value and items-left text are all present", () => {
    expect(out).toContain("comprar café");
    expect(out).toContain("ligar para Maria");
    expect(out).toContain("[checked]");
    expect(out).toContain("rascunho nao enviado");
    expect(out).toContain("1 item left!");
  });

  test("a checkbox is still in the same row as its label", () => {
    const rows: unknown[][] = [];
    const walk = (node: unknown): void => {
      if (Array.isArray(node)) return void node.forEach(walk);
      if (node && typeof node === "object") {
        for (const [key, value] of Object.entries(node)) {
          if (key.startsWith("listitem") && /e42|e47/.test(key))
            rows.push([key, JSON.stringify(value)]);
          walk(value);
        }
      }
    };
    walk(tree);
    expect(rows.length).toBe(2);
    const [cafe, maria] = rows.map((row) => String(row[1]));
    expect(cafe).toContain("checkbox [checked]");
    expect(cafe).toContain("comprar café");
    expect(cafe).not.toContain("ligar para Maria");
    expect(maria).toContain("ligar para Maria");
    expect(maria).not.toContain("[checked]");
  });

  test("the tree is unchanged by serialising it when nothing is sensitive", () => {
    expect(tree).toEqual(parseYaml(TODOMVC, { schema: "failsafe" }));
  });

  test("every control the element list reports is in the tree: one source", () => {
    const refs = parseAriaSnapshot(TODOMVC).elements.map((e) => e.ref);
    expect(refs.length).toBeGreaterThan(0);
    for (const ref of refs) expect(out).toContain(`[ref=${ref}]`);
  });
});

describe("a secret does not leave", () => {
  test("an inline password value is masked", () => {
    const out = observableTree(LOGIN_INLINE, new Set(["e9"]));
    expect(out).not.toContain("hunter2");
    expect(out).toContain(MASKED_VALUE);
  });

  test("a password reported as a text child, as it is when the field has a placeholder, is masked", () => {
    const out = observableTree(LOGIN_PLACEHOLDER, new Set(["e9"]));
    expect(out).not.toContain("hunter2");
    expect(out).toContain(MASKED_VALUE);
  });

  test("the placeholder of a sensitive field is kept: it is the page's text, not the secret", () => {
    const out = observableTree(LOGIN_PLACEHOLDER, new Set(["e9"]));
    expect(out).toContain("Your password");
  });

  test("a field that is not sensitive is unchanged", () => {
    for (const source of [LOGIN_INLINE, LOGIN_PLACEHOLDER]) {
      const out = observableTree(source, new Set(["e9"]));
      expect(out).toContain("ana@example.com");
      expect(out).toContain('"Sign in"');
    }
  });

  test("the fields to ask the page about include one whose value only the tree shows", () => {
    expect(valueFieldRefs(LOGIN_PLACEHOLDER).sort()).toEqual(["e8", "e9"]);
    expect(valueFieldRefs(LOGIN_INLINE).sort()).toEqual(["e8", "e9"]);
    // A textbox with no value at all is still asked about: the tree can change between captures.
    expect(valueFieldRefs("- textbox \"Empty\" [ref=e3]\n")).toEqual(["e3"]);
  });

  test("a tree that cannot be read is refused, not returned unmasked", () => {
    let caught: unknown;
    try {
      observableTree("- textbox: [unclosed\n  - : :", NONE);
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(ObservationError);
    expect((caught as ObservationError).code).toBe(OBSERVATION_UNREADABLE);
  });
});

describe("a tree that is too big is refused, not cut", () => {
  const big = `- generic [ref=e1]:\n${Array.from(
    { length: 400 },
    (_, i) => `  - generic [ref=e${i + 2}]: item number ${i}`,
  ).join("\n")}\n`;

  test("over the limit is an explicit failure", () => {
    let caught: unknown;
    try {
      observableTree(big, NONE, 2_000);
    } catch (error) {
      caught = error;
    }
    expect(caught).toBeInstanceOf(ObservationError);
    expect((caught as ObservationError).code).toBe(OBSERVATION_TOO_LARGE);
  });

  test("at the limit it is returned whole, every line", () => {
    const whole = observableTree(big, NONE, 1_000_000);
    const exact = Buffer.byteLength(whole, "utf8");
    expect(observableTree(big, NONE, exact)).toBe(whole);
    expect(whole).toContain("item number 399");
    expect(() => observableTree(big, NONE, exact - 1)).toThrow(ObservationError);
  });

  test("the default limit is large enough for an ordinary page", () => {
    expect(MAX_OBSERVATION_BYTES).toBeGreaterThanOrEqual(64 * 1024);
    expect(Buffer.byteLength(observableTree(TODOMVC, NONE), "utf8")).toBeLessThan(
      MAX_OBSERVATION_BYTES,
    );
  });
});
