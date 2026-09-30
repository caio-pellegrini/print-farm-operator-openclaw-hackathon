import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  MAX_INBOUND_STL_BYTES,
  normalizeOpenClawInboundEvent,
  readWebChatStlUpload,
  readTrustedOpenClawMedia,
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

test("prepares one WebChat STL from a managed media fact and trusted message context", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.root, { recursive: true, force: true }));
  const upload = await readWebChatStlUpload({
    messageId: "webchat-message-1",
    media: [{ path: f.path, contentType: "application/vnd.ms-pki.stl", messageId: "webchat-message-1" }],
  }, {
    channelId: "WebChat", accountId: "default", senderId: "owner-client",
    conversationId: "session-1", messageId: "webchat-message-1",
  }, f.mediaRoot);
  assert.equal(upload.channel, "webchat");
  assert.equal(upload.sender_id, "owner-client");
  assert.equal(upload.filename, "opaque-model.stl");
  assert.equal(upload.bytes.toString(), "validated by the application bridge");

  const sessionPath = join(f.workspaceDir, "session-upload.stl");
  await writeFile(sessionPath, "staged by OpenClaw in the session workspace", { mode: 0o600 });
  const sessionUpload = await readWebChatStlUpload({
    messageId: "webchat-message-2",
    media: [{ path: sessionPath, workspaceDir: f.workspaceDir, messageId: "webchat-message-2" }],
  }, {
    channelId: "webchat", accountId: "default", senderId: "owner-client",
    conversationId: "session-1", messageId: "webchat-message-2",
  }, f.mediaRoot);
  assert.equal(sessionUpload.filename, "session-upload.stl");
  assert.equal(sessionUpload.bytes.toString(), "staged by OpenClaw in the session workspace");
});

test("WebChat upload rejects other channels, multiple files, non-STL, pending, oversized, and unsafe paths", async (t) => {
  const f = await fixture();
  t.after(() => rm(f.root, { recursive: true, force: true }));
  const context = {
    channelId: "webchat", accountId: "default", senderId: "owner-client",
    conversationId: "session-1", messageId: "webchat-message-1",
  };
  const assertCategory = async (event, ctx, category) => assert.rejects(
    readWebChatStlUpload(event, ctx, f.mediaRoot),
    (error) => error instanceof TrustedMediaReadError && error.category === category,
  );
  await assertCategory({ messageId: "webchat-message-1", media: [{ path: f.path }, { path: f.path }] }, context, "expected_one_attachment");
  await assertCategory({ messageId: "webchat-message-1", media: [{ path: join(f.mediaDirectory, "part.pdf") }] }, context, "unsupported_file_extension");
  await assertCategory({ messageId: "webchat-message-1", mediaStagingPending: true, originalMedia: [{ path: f.path }] }, context, "media_staging_pending");
  await assertCategory({ messageId: "webchat-message-1", media: [{ path: f.path }] }, { ...context, channelId: "whatsapp" }, "unsupported_channel");

  const outside = join(f.root, "outside.stl");
  await writeFile(outside, "outside bytes", { mode: 0o600 });
  await assertCategory({ messageId: "webchat-message-1", media: [{ path: outside }] }, context, "outside_trusted_media_root");
  const link = join(f.mediaDirectory, "linked.stl");
  await symlink(outside, link);
  await assertCategory({ messageId: "webchat-message-1", media: [{ path: link }] }, context, "symlinked_media_path");
  const large = join(f.mediaDirectory, "large.stl");
  await writeFile(large, Buffer.alloc(MAX_INBOUND_STL_BYTES + 1), { mode: 0o600 });
  await assertCategory({ messageId: "webchat-message-1", media: [{ path: large }] }, context, "invalid_file_size_or_type");
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
