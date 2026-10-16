/** Guard app-owned copy only. A person's own labels and journal are never censored. */
export function wordingViolations(copy: string): string[] {
  const reviewed = copy.replaceAll("This does not prove cause.", "");
  return (
    reviewed.match(
      /\b(?:causes?|caused|causing|improves?|improved|cures?|treats?|prevents?|leads? to|results? in|because of|will (?:boost|raise|reduce|help))\b/gi,
    ) ?? []
  );
}
