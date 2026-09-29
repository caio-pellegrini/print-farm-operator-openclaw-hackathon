import test from "node:test";
import assert from "node:assert/strict";
import { parseBridgeResponse } from "../dist/bridge-response.js";

test("accepts a successful bridge response", () => {
  assert.deepEqual(parseBridgeResponse('{"status":"created","job_id":"job-1"}'), {
    status: "created",
    job_id: "job-1",
  });
});

test("raises bridge errors returned in a zero-exit JSON envelope", () => {
  assert.throws(
    () => parseBridgeResponse('{"error":"The request could not be completed."}'),
    /The request could not be completed\./,
  );
});

test("rejects malformed or non-object bridge responses", () => {
  assert.throws(() => parseBridgeResponse("not-json"), /invalid structured output/);
  assert.throws(() => parseBridgeResponse("[]"), /invalid structured output/);
});
