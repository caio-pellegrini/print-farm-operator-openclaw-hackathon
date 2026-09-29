import assert from "node:assert/strict";
import { mkdtemp, mkdir, rm, symlink, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import {
  normalizeOpenClawInboundEvent,
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
