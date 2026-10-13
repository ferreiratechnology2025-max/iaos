/**
 * Choose one option of a select control by its exact visible label.
 *
 * Kept out of `index.ts`, beside the other small decisions, so the rule can be tested without a
 * browser: the label is the only way to choose, and a label that is not in the list is an error.
 * There is no index, no value and no "first option" fallback. A wrong label that quietly selected
 * something else would turn a typo into an effect nobody asked for.
 */
export const MAX_OPTION_CHARS = 256;

export type SelectableControl = {
  selectOption(
    option: { label: string },
    options?: Record<string, unknown>,
  ): Promise<string[]>;
};

export function validOption(option: unknown): option is string {
  return (
    typeof option === "string" &&
    option.trim().length > 0 &&
    option.length <= MAX_OPTION_CHARS
  );
}

export async function selectByExactLabel(
  control: SelectableControl,
  option: string,
  acting: Record<string, unknown>,
): Promise<number> {
  // Exactly one call, with exactly the label. Playwright rejects ("did not find some options") when the
  // label is absent; that rejection is the answer and is propagated unchanged.
  const chosen = await control.selectOption({ label: option }, acting);
  return chosen.length;
}
