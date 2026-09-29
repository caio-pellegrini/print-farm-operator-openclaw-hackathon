import { constants } from "node:fs";
import { open, realpath } from "node:fs/promises";
import { basename, extname, isAbsolute, resolve, sep } from "node:path";

export const MAX_INBOUND_STL_BYTES = 25 * 1024 * 1024;

export type OpenClawMediaFact = {
  path?: string;
  url?: string;
  contentType?: string;
  kind?: string;
  messageId?: string;
  workspaceDir?: string;
};

export type OpenClawInboundEvent = {
  content?: string;
  messageId?: string;
  senderId?: string;
  timestamp?: number;
  media?: OpenClawMediaFact[];
  originalMedia?: OpenClawMediaFact[];
  mediaStagingPending?: boolean;
};

export type OpenClawInboundContext = {
  channelId: string;
  accountId?: string;
  conversationId?: string;
  senderId?: string;
  messageId?: string;
};

export type NormalizedInboundEvent = {
  channel: string;
  account_id: string;
  sender_id: string;
  conversation_id: string;
  message_id: string;
  timestamp?: string;
  text?: string;
  media: OpenClawMediaFact[];
};

export class TrustedMediaReadError extends Error {
  constructor(readonly category: string) {
    super(category);
    this.name = "TrustedMediaReadError";
  }
}

/** Map OpenClaw's event/context contract before passing anything to the domain bridge. */
export function normalizeOpenClawInboundEvent(
  event: OpenClawInboundEvent,
  context: OpenClawInboundContext,
): NormalizedInboundEvent | undefined {
  const sender = event.senderId || context.senderId;
  const conversation = context.conversationId;
  const message = event.messageId || context.messageId;
  if (!sender || !conversation || !message) return undefined;

  const media = event.media?.length
    ? event.media
    : event.mediaStagingPending ? event.originalMedia ?? [] : event.media ?? [];
  // The OpenClaw message_received hook exposes agent-facing `content`, not a
  // separate raw body. For media-only events, content may be a generated
  // placeholder, so only text-only events contribute customer request text.
  const text = media.length > 0 ? undefined : event.content?.trim() || undefined;
  const timestamp = typeof event.timestamp === "number" && Number.isFinite(event.timestamp)
    ? new Date(event.timestamp).toISOString()
    : undefined;

  return {
    channel: context.channelId.trim().toLowerCase(),
    account_id: context.accountId || "default",
    sender_id: sender,
    conversation_id: conversation,
    message_id: message,
    timestamp,
    text,
    media,
  };
}

/** Read only media staged beneath the explicitly configured OpenClaw media root. */
export async function readTrustedOpenClawMedia(
  fact: OpenClawMediaFact,
  trustedMediaRoot: string,
): Promise<Buffer> {
  if (!isAbsolute(trustedMediaRoot)) throw new TrustedMediaReadError("invalid_trusted_root");
  if (!fact.path || !isAbsolute(fact.path)) throw new TrustedMediaReadError("missing_local_media_path");
  if (extname(fact.path).toLowerCase() !== ".stl") throw new TrustedMediaReadError("unsupported_file_extension");

  let root: string;
  let actual: string;
  try {
    root = await realpath(resolve(trustedMediaRoot));
    actual = await realpath(resolve(fact.path));
  } catch {
    throw new TrustedMediaReadError("staged_media_unavailable");
  }
  if (root !== resolve(trustedMediaRoot) || actual !== resolve(fact.path)) {
    throw new TrustedMediaReadError("symlinked_media_path");
  }
  if (!actual.startsWith(`${root}${sep}`)) throw new TrustedMediaReadError("outside_trusted_media_root");

  let handle;
  try {
    handle = await open(actual, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
    const metadata = await handle.stat();
    if (!metadata.isFile() || metadata.size <= 0 || metadata.size > MAX_INBOUND_STL_BYTES) {
      throw new TrustedMediaReadError("invalid_file_size_or_type");
    }
    const bounded = Buffer.alloc(MAX_INBOUND_STL_BYTES + 1);
    let offset = 0;
    while (offset < bounded.length) {
      const { bytesRead } = await handle.read(bounded, offset, bounded.length - offset, offset);
      if (bytesRead === 0) break;
      offset += bytesRead;
    }
    if (offset === 0 || offset > MAX_INBOUND_STL_BYTES) {
      throw new TrustedMediaReadError("invalid_file_size_or_type");
    }
    return bounded.subarray(0, offset);
  } catch (error) {
    if (error instanceof TrustedMediaReadError) throw error;
    const code = (error as NodeJS.ErrnoException).code;
    if (code === "ELOOP") throw new TrustedMediaReadError("symlinked_media_path");
    throw new TrustedMediaReadError("staged_media_unavailable");
  } finally {
    await handle?.close();
  }
}

export function trustedMediaFilename(fact: OpenClawMediaFact): string {
  return basename(fact.path || "attachment.stl");
}
