import assert from "node:assert/strict";
import test from "node:test";
import {
  markDuplicateInboundSession,
  sendClaimedIntakeReply,
  sendReplyForCompletedIntake,
  shouldSuppressDuplicateSessionReply,
} from "../dist/intake-reply.js";

test("an exact duplicate inbound event cancels its customer-facing reply without affecting other sessions", () => {
  const sessions = new Map();
  markDuplicateInboundSession("session-fixture", sessions, 1000);
  assert.equal(shouldSuppressDuplicateSessionReply("session-fixture", sessions, 1001), true);
  assert.equal(shouldSuppressDuplicateSessionReply("other-session", sessions, 1001), false);
  assert.equal(shouldSuppressDuplicateSessionReply("session-fixture", sessions, 31000), false);
  assert.equal(markDuplicateInboundSession(undefined, sessions, 1000), undefined);
});

test("one intake claim sends one fixed success reply and later turn retries are silent", async () => {
  let claimed = false;
  let sendCount = 0;
  let markedSent = 0;
  const claim = async () => {
    if (claimed) return { send: false };
    claimed = true;
    return { send: true, job_id: "job-safe-id", safe_filename: "model.stl" };
  };
  const send = async (text) => {
    sendCount += 1;
    assert.match(text, /job-safe-id/);
    assert.match(text, /model\.stl/);
    return { messageId: "platform-message" };
  };
  const markSent = async (messageId) => { assert.equal(messageId, "platform-message"); markedSent += 1; };

  const first = await sendClaimedIntakeReply(claim, send, markSent);
  const second = await sendClaimedIntakeReply(claim, send, markSent);

  assert.deepEqual(first, { status: "sent", final_reply: "NO_REPLY" });
  assert.deepEqual(second, { status: "duplicate", final_reply: "NO_REPLY" });
  assert.equal(sendCount, 1);
  assert.equal(markedSent, 1);
});

test("transport setup failure happens before and does not consume an intake claim", async () => {
  let claimed = false;
  let attempts = 0;
  let sendCount = 0;
  let setupAttempt = 0;
  const claim = async () => {
    attempts += 1;
    if (claimed) return { send: false };
    claimed = true;
    return { send: true, job_id: "job-safe-id", safe_filename: "model.stl" };
  };
  const prepareSend = async () => {
    setupAttempt += 1;
    if (setupAttempt === 1) throw new Error("adapter unavailable");
  };
  const send = async () => { sendCount += 1; return { messageId: "platform-message" }; };
  const markSent = async () => {};

  const unavailable = await sendClaimedIntakeReply(claim, send, markSent, prepareSend);
  assert.deepEqual(unavailable, { status: "delivery_uncertain", final_reply: "NO_REPLY" });
  assert.equal(attempts, 0);

  const available = await sendClaimedIntakeReply(claim, send, markSent, prepareSend);
  assert.deepEqual(available, { status: "sent", final_reply: "NO_REPLY" });
  assert.equal(attempts, 1);
  assert.equal(sendCount, 1);
});

test("only completed application results reach the confirmation sender", async () => {
  let callCount = 0;
  const pending = await sendReplyForCompletedIntake({ status: "pending" }, async () => {
    callCount += 1;
    return { status: "sent", final_reply: "NO_REPLY" };
  });
  const duplicate = await sendReplyForCompletedIntake({ status: "duplicate" }, async () => {
    callCount += 1;
    return { status: "sent", final_reply: "NO_REPLY" };
  });
  assert.deepEqual(pending, { status: "not_completed", final_reply: "NO_REPLY" });
  assert.deepEqual(duplicate, { status: "not_completed", final_reply: "NO_REPLY" });
  assert.equal(callCount, 0);
});

test("consecutive completed intakes use independent one-shot confirmation claims", async () => {
  const claimed = new Set();
  const sent = [];
  const confirm = (result) => sendReplyForCompletedIntake(result, async (intakeId, jobId) => {
    const claim = async () => {
      if (claimed.has(intakeId)) return { send: false };
      claimed.add(intakeId);
      return { send: true, job_id: jobId, safe_filename: "model.stl" };
    };
    return sendClaimedIntakeReply(claim,
      async () => { sent.push(intakeId); return { messageId: `platform-${intakeId}` }; },
      async () => {});
  });
  const first = { status: "created", intake_id: "intake-one", job_id: "job-one" };
  const second = { status: "created", intake_id: "intake-two", job_id: "job-two" };

  assert.equal((await confirm(first)).status, "sent");
  assert.equal((await confirm(first)).status, "duplicate");
  assert.equal((await confirm({ status: "duplicate", intake_id: "intake-one", job_id: "job-one" })).status, "not_completed");
  assert.equal((await confirm(second)).status, "sent");
  assert.deepEqual(sent, ["intake-one", "intake-two"]);
});

test("an uncertain channel send is not retried and does not create a model fallback reply", async () => {
  let claimed = false;
  let sendCount = 0;
  const claim = async () => {
    if (claimed) return { send: false };
    claimed = true;
    return { send: true, job_id: "job-safe-id", safe_filename: "model.stl" };
  };
  const send = async () => { sendCount += 1; throw new Error("transport outcome uncertain"); };
  const markSent = async () => assert.fail("a failed send must not be marked sent");

  const first = await sendClaimedIntakeReply(claim, send, markSent);
  const retry = await sendClaimedIntakeReply(claim, send, markSent);

  assert.deepEqual(first, { status: "delivery_uncertain", final_reply: "NO_REPLY" });
  assert.deepEqual(retry, { status: "duplicate", final_reply: "NO_REPLY" });
  assert.equal(sendCount, 1);
});

test("explicit adapter not_sent outcome releases the claim for a safe retry", async () => {
  let claimAvailable = true;
  let sendCount = 0;
  let released = 0;
  const claim = async () => {
    if (!claimAvailable) return { send: false };
    claimAvailable = false;
    return { send: true, job_id: "job-safe-id", safe_filename: "model.stl" };
  };
  const markNotSent = async () => { released += 1; claimAvailable = true; };
  const first = await sendClaimedIntakeReply(claim, async () => {
    sendCount += 1;
    return { outcome: "not_sent" };
  }, async () => {}, undefined, markNotSent);
  const retry = await sendClaimedIntakeReply(claim, async () => {
    sendCount += 1;
    return { messageId: "platform-message" };
  }, async () => {});

  assert.deepEqual(first, { status: "not_sent", final_reply: "NO_REPLY" });
  assert.deepEqual(retry, { status: "sent", final_reply: "NO_REPLY" });
  assert.equal(released, 1);
  assert.equal(sendCount, 2);
});

test("adapter response without provider evidence remains uncertain", async () => {
  let sendCount = 0;
  let uncertain = 0;
  const result = await sendClaimedIntakeReply(
    async () => ({ send: true, job_id: "job-safe-id", safe_filename: "model.stl" }),
    async () => { sendCount += 1; return {}; },
    async () => assert.fail("missing provider message ID must not mark sent"),
    undefined,
    undefined,
    async () => { uncertain += 1; },
  );
  assert.deepEqual(result, { status: "delivery_uncertain", final_reply: "NO_REPLY" });
  assert.equal(sendCount, 1);
  assert.equal(uncertain, 1);
});
