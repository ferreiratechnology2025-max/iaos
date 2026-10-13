import { describe, expect, test } from "bun:test";
import {
  MAX_OPTION_CHARS,
  selectByExactLabel,
  validOption,
} from "../src/select-option";

/** A control whose options are fixed; it mimics Playwright: an unknown label rejects, nothing else is chosen. */
function control(options: string[]) {
  const calls: unknown[] = [];
  let selected: string | null = null;
  return {
    calls,
    get selected() {
      return selected;
    },
    async selectOption(arg: { label: string }) {
      calls.push(arg);
      if (!options.includes(arg.label)) {
        throw new Error("Timeout 5000ms exceeded. did not find some options");
      }
      selected = arg.label;
      return [arg.label];
    },
  };
}

describe("select by exact visible label", () => {
  test("an existing label is chosen with exactly one call that carries only the label", async () => {
    const c = control(["Text", "Heading", "Button"]);
    expect(await selectByExactLabel(c, "Button", {})).toBe(1);
    expect(c.calls).toEqual([{ label: "Button" }]);
    expect(c.selected).toBe("Button");
  });

  test("a missing label fails and never falls back to the first option or to any index/value", async () => {
    const c = control(["Text", "Heading", "Button"]);
    await expect(selectByExactLabel(c, "Buton", {})).rejects.toThrow(
      "did not find some options",
    );
    expect(c.calls).toEqual([{ label: "Buton" }]);
    expect(c.selected).toBeNull();
  });

  test("empty, blank and oversized options are not valid", () => {
    expect(validOption("Button")).toBeTrue();
    for (const bad of ["", "   ", "x".repeat(MAX_OPTION_CHARS + 1), 3, null]) {
      expect(validOption(bad)).toBeFalse();
    }
  });
});
