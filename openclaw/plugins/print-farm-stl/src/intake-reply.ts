export type IntakeReplyClaim = {
  send: boolean;
  job_id?: string;
  safe_filename?: string;
};

export type IntakeReplyResult = {
  status: "sent" | "duplicate" | "delivery_uncertain" | "not_sent" | "not_completed";
  final_reply: "NO_REPLY";
};

export type IntakeOutboundResult = {
  outcome?: string;
  messageId?: string;
  receipt?: { platformMessageIds?: string[] };
};

/** Only completed application results may enter the customer reply path. */
export async function sendReplyForCompletedIntake(
  result: Record<string, unknown>,
  sendForIntake: (intakeId: string, jobId: string) => Promise<IntakeReplyResult>,
): Promise<IntakeReplyResult> {
  if ((result.status !== "created" && result.status !== "completed")
    || typeof result.intake_id !== "string" || !result.intake_id
    || typeof result.job_id !== "string" || !result.job_id) {
    return { status: "not_completed", final_reply: "NO_REPLY" };
  }
  return sendForIntake(result.intake_id, result.job_id);
}

export function markDuplicateInboundSession(
  sessionKey: string | undefined,
  sessions: Map<string, number>,
  now = Date.now(),
): void {
  if (!sessionKey) return;
  for (const [key, expiresAt] of sessions) if (expiresAt <= now) sessions.delete(key);
  if (sessions.size >= 256 && !sessions.has(sessionKey)) sessions.delete(sessions.keys().next().value as string);
  sessions.set(sessionKey, now + 30_000);
}

export function shouldSuppressDuplicateSessionReply(
  sessionKey: string | undefined,
  sessions: Map<string, number>,
  now = Date.now(),
): boolean {
  if (!sessionKey) return false;
  const expiresAt = sessions.get(sessionKey);
  if (expiresAt === undefined) return false;
  if (expiresAt <= now) {
    sessions.delete(sessionKey);
    return false;
  }
  return true;
}

/** Send only after the persistent application claim succeeds. */
export async function sendClaimedIntakeReply(
  claim: () => Promise<IntakeReplyClaim>,
  send: (text: string) => Promise<IntakeOutboundResult>,
  markSent: (messageId: string) => Promise<void>,
  prepareSend?: () => Promise<void>,
  markNotSent?: () => Promise<void>,
  markUncertain?: () => Promise<void>,
): Promise<IntakeReplyResult> {
  // Resolve local transport prerequisites before writing the persistent claim.
  // If the adapter is unavailable, a later turn can safely try again because
  // no outbound call has happened and no intake reply state was consumed.
  if (prepareSend) {
    try {
      await prepareSend();
    } catch {
      return { status: "delivery_uncertain", final_reply: "NO_REPLY" };
    }
  }
  const claimed = await claim();
  if (!claimed.send || !claimed.job_id || !claimed.safe_filename) {
    return { status: "duplicate", final_reply: "NO_REPLY" };
  }
  const text = `Recebi seu pedido ${claimed.job_id} com o arquivo ${claimed.safe_filename}. A equipe verificará os detalhes antes de confirmar o orçamento.`;
  try {
    const result = await send(text);
    if (result.outcome === "not_sent" && !result.messageId
      && !(result.receipt?.platformMessageIds?.length)) {
      await markNotSent?.();
      return { status: "not_sent", final_reply: "NO_REPLY" };
    }
    const messageId = result.messageId || result.receipt?.platformMessageIds?.[0];
    if (!messageId) {
      await markUncertain?.();
      return { status: "delivery_uncertain", final_reply: "NO_REPLY" };
    }
    await markSent(messageId);
    return { status: "sent", final_reply: "NO_REPLY" };
  } catch {
    // The transport may have accepted the send before reporting an error. Never
    // retry a one-shot claim or let the model issue a duplicate fallback reply.
    try { await markUncertain?.(); } catch { /* keep the original outcome uncertain */ }
    return { status: "delivery_uncertain", final_reply: "NO_REPLY" };
  }
}
