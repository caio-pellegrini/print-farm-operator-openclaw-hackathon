import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  normalizeOpenClawInboundEvent,
  readTrustedOpenClawMedia,
  readTrustedChannelStlUpload,
  TrustedMediaReadError,
} from "../dist/inbound-media.js";

async function fixture() {
  const root = await mkdtemp(join(tmpdir(), "farm-openclaw-media-test-"));
  const mediaRoot = join(root, "state", "media");
  const mediaDirectory = join(mediaRoot, "inbound");
  const workspaceDir = join(root, "agent-workspace");
  await mkdir(mediaDirectory, { recursive: true, mode: 0o700 });
  await mkdir(workspaceDir, { recursive: true, mode: 0o700 });
  const path = join(mediaDirectory, "opaque-model.stl");
  await writeFile(path, Buffer.from("validated by the application bridge"), { mode: 0o600 });
  return { root, mediaRoot, mediaDirectory, workspaceDir, path };
}

test("replays OpenClaw text and media event shapes without using agent-facing attachment text", () => {
  const media = {
    path: "/private/openclaw/state/media/inbound/file.stl",
    url: "/private/openclaw/state/media/inbound/file.stl",
    contentType: "application/vnd.ms-pki.stl",
    kind: "document",
  };
  const context = {
    channelId: "WhatsApp",
    accountId: "default",
    senderId: "sender-fixture",
    conversationId: "sender-fixture",
    messageId: "media-message-fixture",
  };
  const textEvent = normalizeOpenClawInboundEvent({
    content: "Please quote three units",
    messageId: "text-message-fixture",
    timestamp: 1790563787209,
  }, context);
  const mediaEvent = normalizeOpenClawInboundEvent({
    // The live hook's agent-facing content is a generated attachment envelope.
    content: "<redacted OpenClaw attachment envelope>",
    messageId: "media-message-fixture",
    timestamp: 1790563787663,
    media: [media],
  }, context);

  assert.equal(textEvent?.text, "Please quote three units");
  assert.equal(textEvent?.media.length, 0);
  assert.equal(mediaEvent?.text, undefined);
  assert.equal(mediaEvent?.media.length, 1);
  assert.equal(mediaEvent?.channel, "whatsapp");
  assert.equal(mediaEvent?.account_id, "default");
  assert.equal(mediaEvent?.sender_id, "sender-fixture");
  assert.equal(mediaEvent?.conversation_id, "sender-fixture");
  assert.equal(mediaEvent?.message_id, "media-message-fixture");
  assert.equal(mediaEvent?.media[0]?.contentType, "application/vnd.ms-pki.stl");
});

test("accepts a trusted OpenClaw media-root path outside the agent workspace", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.root, { recursive: true, force: true }));
  const bytes = await readTrustedOpenClawMedia({
    path: f.path,
    contentType: "application/vnd.ms-pki.stl",
    kind: "document",
  }, f.mediaRoot);
  assert.equal(bytes.toString(), "validated by the application bridge");
});

test("rejects media outside the configured OpenClaw media root and symlink substitution", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.root, { recursive: true, force: true }));
  const outside = join(f.root, "outside.stl");
  await writeFile(outside, "outside bytes", { mode: 0o600 });
  await assert.rejects(
    readTrustedOpenClawMedia({ path: outside }, f.mediaRoot),
    (error) => error instanceof TrustedMediaReadError && error.category === "outside_trusted_media_root",
  );
  const link = join(f.mediaDirectory, "linked.stl");
  await symlink(outside, link);
  await assert.rejects(
    readTrustedOpenClawMedia({ path: link }, f.mediaRoot),
    (error) => error instanceof TrustedMediaReadError && error.category === "symlinked_media_path",
  );
});

test("accepts one browser STL from trusted WebChat workspace media and rejects other channels", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.root, { recursive: true, force: true }));
  const workspaceUpload = join(f.workspaceDir, "browser-part.stl");
  await writeFile(workspaceUpload, Buffer.from("browser upload bytes"), { mode: 0o600 });
  const event = {
    messageId: "webchat-message-1",
    media: [{ path: workspaceUpload, workspaceDir: f.workspaceDir, messageId: "webchat-message-1" }],
  };
  const context = {
    channelId: "WebChat", accountId: "local-webchat", senderId: "browser-owner",
    conversationId: "webchat-session-1", messageId: "webchat-message-1",
  };
  const upload = await readTrustedChannelStlUpload(event, context, f.mediaRoot);
  assert.equal(upload.channel, "webchat");
  assert.equal(upload.account_id, "local-webchat");
  assert.equal(upload.sender_id, "browser-owner");
  assert.equal(upload.conversation_id, "webchat-session-1");
  assert.equal(upload.message_id, "webchat-message-1");
  assert.equal(upload.filename, "browser-part.stl");
  assert.equal(upload.bytes.toString(), "browser upload bytes");
  await assert.rejects(
    readTrustedChannelStlUpload(event, { ...context, channelId: "whatsapp" }, f.mediaRoot),
    (error) => error instanceof TrustedMediaReadError && error.category === "unsupported_channel",
  );
});

test("accepts staged Plow phone-line STL media from the trusted OpenClaw media root", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.root, { recursive: true, force: true }));
  const event = {
    from: "plow-owner",
    senderId: "plow-owner",
    messageId: "plow-message-1",
    media: [{ path: f.path, contentType: "model/stl", kind: "document", messageId: "plow-message-1" }],
  };
  const context = {
    channelId: "plow", accountId: "chat", senderId: "plow-owner",
    conversationId: "plow-conversation-1", messageId: "plow-message-1",
  };
  const upload = await readTrustedChannelStlUpload(event, context, f.mediaRoot);
  assert.equal(upload.channel, "plow");
  assert.equal(upload.account_id, "chat");
  assert.equal(upload.sender_id, "plow-owner");
  assert.equal(upload.conversation_id, "plow-conversation-1");
  assert.equal(upload.message_id, "plow-message-1");
  assert.equal(upload.filename, "opaque-model.stl");
  assert.equal(upload.bytes.toString(), "validated by the application bridge");

  await assert.rejects(
    readTrustedChannelStlUpload({ ...event, mediaStagingPending: true }, context, f.mediaRoot),
    (error) => error instanceof TrustedMediaReadError && error.category === "media_staging_pending",
  );
  await assert.rejects(
    readTrustedChannelStlUpload(event, { ...context, isGroup: true }, f.mediaRoot),
    (error) => error instanceof TrustedMediaReadError && error.category === "group_conversation_not_supported",
  );
});
