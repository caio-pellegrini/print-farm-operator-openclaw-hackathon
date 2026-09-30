# Print Farm STL Tools

OpenClaw plugin for analyzing staged STL files, slicing with the existing Cura
POC, and calculating an example quote. The model receives narrow project tools;
it does not receive a general shell tool.

## Build

```bash
npm ci
npm run plugin:build
npm run plugin:validate
```

Configure `jobsDirectory`, script paths, `quoteEngine`, and `databasePath` as
absolute paths in `plugins.entries.print-farm-stl.config` in `openclaw.json`.
`analyze_stl` accepts a basename only, reads regular `.stl` files up to 25 MiB,
makes a private temporary copy, and limits analysis to 10 seconds.

In WebChat, one attached STL is imported by the `message_received` hook from
OpenClaw's managed media facts. The hook accepts only a single `.stl` under
OpenClaw's managed media root or the exact session workspace supplied by the
runtime, then sends the bytes through the existing channel-neutral upload
validation and private job persistence flow. The original path never comes
from model arguments. Message IDs provide idempotency for hook retries. The
result is available through `get_latest_stl_analysis`, including the persisted
request/job and quote status.

For an estimate, call `analyze_stl`, then call `slice_stl` with that exact
`analysis_id`, the same filename, and the approved
`cura-ultimaker2plus-generic-pla-normal` profile. The Stage 2 runner uses the
official Cura 5.13.0 CuraEngine runtime and bundled Ultimaker 2+ definitions,
Generic PLA material, and Normal process. It starts the fixed local Cura Docker
image as the Gateway caller's numeric UID/GID, without a network,
with a read-only root, 1 GiB memory, two CPUs, 64 MiB temporary filesystem,
30-second process timeout, and 1 MiB output limit. It discards generated G-code and persists the normalized result and
example quote in SQLite. `get_latest_production_estimate` retrieves the latest
completed record across OpenClaw sessions.

This is a controlled proof profile, not a profile validated against the farm's
physical printer. Do not use the example quote parameters as a farm's real
pricing configuration.
