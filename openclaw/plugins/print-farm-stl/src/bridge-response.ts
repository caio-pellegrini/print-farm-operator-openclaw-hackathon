/** Parse the private Python bridge's JSON protocol, including its error envelope. */
export function parseBridgeResponse(stdout: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(stdout);
  } catch {
    throw new Error("The farm bridge returned invalid structured output.");
  }
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("The farm bridge returned invalid structured output.");
  }
  const result = parsed as Record<string, unknown>;
  if (typeof result.error === "string" && result.error.length > 0) {
    throw new Error(result.error);
  }
  return result;
}
