/**
 * Parse the last JSON line from a skill's stdout. Skills emit
 * {"simmer_managed_output":{...}} (preferred) or {"automaton":{...}}
 * (back-compat, AUTOMATON_MANAGED rename transition).
 */

export interface ParsedSkillOutput {
  result: Record<string, unknown> | null;
  log: string;
}

export function parseSkillOutput(stdout: string): ParsedSkillOutput {
  const lines = stdout.split("\n").map((l) => l.trim()).filter((l) => l.length > 0);

  // Walk from the bottom up — last structured-output line wins.
  // Within a line, prefer simmer_managed_output over automaton.
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = lines[i];
    if (!line.startsWith("{")) continue;
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(line);
    } catch { continue; }
    if (typeof parsed !== "object" || parsed === null) continue;
    if ("simmer_managed_output" in parsed) {
      return { result: parsed.simmer_managed_output as Record<string, unknown>, log: stdout };
    }
    if ("automaton" in parsed) {
      return { result: parsed.automaton as Record<string, unknown>, log: stdout };
    }
  }
  return { result: null, log: stdout };
}
