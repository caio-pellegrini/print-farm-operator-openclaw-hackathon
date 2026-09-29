---
name: customer-request-intake
description: Register a customer print request and STL through the identity-bound WhatsApp intake flow.
---

# Customer request intake

Use this workflow when a customer asks to print or price a model through public WhatsApp.

1. Text and attachments are independent inbound events. The application persists and correlates them for 30 minutes by the trusted channel/account/sender/conversation tuple. Text may arrive before or after the STL; never ask the customer to resend an STL only because it arrived as a separate event.
2. The application hook validates and stages STL attachments, correlates the request, and creates the CUSTOMER-owned job deterministically. Do not call a model-selected submit tool or ask for sender IDs, account IDs, message IDs, attachment references, local paths, or filenames.
3. Call `farm_get_pending_intake_status` when the customer discusses or sends a new request/model. It returns only this verified sender's state for the current conversation. If it says `awaiting_attachment`, ask for the STL only when the current inbound message did not include media. If the customer just sent a file, do not ask them to resend or reattach it; say the message arrived and the application is checking the match. If it says `awaiting_request`, ask what the customer wants made and the quantity.
4. The application hook sends one fixed confirmation after a new intake is created, using an atomic persistent claim keyed by `intake_id`. The pending-reply drain also retries completed intakes that have no claim after Gateway startup. If the status result includes `confirmation_results`, return `NO_REPLY`; do not call the generic `message` tool or write another confirmation. If status is `completed` and no confirmation result is present, `farm_send_intake_confirmation` is an idempotent fallback. A duplicate result, uncertain delivery result, or failed claim also requires `NO_REPLY` and no fallback success/failure message.
5. If the status tool reports an ambiguous match, ask which displayed request the file belongs to. Only after the customer chooses, call `farm_resolve_pending_intake` with the opaque request and attachment IDs returned by the status tool. Do not claim that a file has been matched or consumed before that tool succeeds.
6. Do not promise, estimate, or invent a price, quote, print time, or material amount. The request creates a draft quote only; Stage 4 trust gating must pass before any customer quote is issued. Do not call legacy shared-directory tools such as `analyze_stl`, `slice_stl`, `get_latest_stl_analysis`, or `get_latest_production_estimate` for public customer intake.
7. If status is `none`/`unavailable`, a recent event has not been correlated, or ambiguity resolution fails, do not ask for the STL to be resent or reattached. Acknowledge that the message/file arrived, say its request match is being checked, and avoid claiming that an application request/job was created until confirmed. Never treat OpenClaw's generated media-envelope text as customer request text; the raw message body is authoritative.
