# OpenClaw and Plow architecture

## Runtime ownership

The Dockerfile extends the official Plow OpenClaw runtime at a digest-pinned
base image. Plow owns container startup, OpenClaw configuration includes, Plow
Chat messaging, model credentials and routing, optional Latch access, and the
Agent Index client/reporting schedule.

The variant contributes its prompt, skills, Python domain, and STL tools plugin.
It adds the app plugin path and tools to Plow's generated configuration while
keeping Plow startup, provider/model route, channel, and reporter intact. Normal
chat uses Plow Chat; the STL plugin accepts trusted staged media from Plow Chat
or local WebChat.

## Domain boundary

The farm domain stays under experiments/. It owns farm persistence,
authorization, profiles and quote readiness, requests/orders/jobs, production
state transitions, and audit/job events. docker/plow_domain_tools.py is an
adapter around that code; it selects a fixed per-installation database path and
does not accept a caller-selected SQLite path.

The transport does not assign farm roles. The current first-run flow provisions
the Plow owner as the farm owner. The verified domain identity workflow remains
available for extending role mappings.

## State and reporting

Plow's persistent /var/lib/plow volume holds OpenClaw sessions, farm SQLite
records, and the inherited Agent Index installation key and usage ledger. The
Agent Index collector reads actual OpenClaw usage and reports every five
minutes. It does not read local developer state.

## Printer behavior

ManualPrinterAdapter records a human-confirmed printer assignment and
start/finish events. It sends no hardware commands. A Cura execution container
is intentionally not bundled; quote creation is available only through its
existing approved and validated domain path.
