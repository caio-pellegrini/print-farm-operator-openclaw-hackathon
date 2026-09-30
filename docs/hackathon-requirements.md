# OpenClaw / Plow hackathon alignment

This repository is the independent hackathon build. The historical source
repository is not changed or used as an installation source.

## Runtime

- The image starts from the official
  [Plow OpenClaw base](https://github.com/plow-pbc/plow-openclaw-agent), pinned
  by immutable tag and digest in the Dockerfile.
- Plow owns the OpenClaw startup contract, Plow Chat channel, credential
  injection, model route, optional Latch relay, and its supported Agent Index
  reporter.
- The project adds its prompt, skills, domain code, and STL plugin to Plow's
  generated plugin config. The pinned base, Plow boot, and reporter stay intact.
- WhatsApp is absent from the default image. No Telegram or custom
  macOS/iMessage integration is included.

## Install and real use

The documented source install uses the official plow-agents CLI to authenticate
and mint credentials for a tester's Plow line, then builds the variant locally
with Docker Compose. The tester uses Plow Chat or the local owner WebChat,
completes onboarding, and performs a real interaction.

An installation has a fresh state volume and its own Index installation id and
key. The base reads OpenClaw session usage and reports every five minutes after
the tester uses the agent. Nothing in this repository creates synthetic usage.

## Public listing

The variant sets Agent Index id, name, description, and runtime as image
metadata. The fuller listing metadata is documented in
agent-index-metadata.json. The new public GitHub repository and MIT license are
included. A recorded demo video is still needed.

Plow cloud admission is separate from publishing this source repository. The
public agent image must be admitted for the listing by a Plow admin before the
cloud deploy command is available. Until that happens, use the source install.

## Product boundaries

The preserved farm code includes STL analysis, onboarding, farm configuration,
role and capability checks, persistent customer requests, orders, quotes and
jobs, quote trust gates, audit/job events, and the ManualPrinterAdapter.
Automated slicing needs the isolated Cura runtime, which is not bundled in this
Plow variant. Printer control is not implemented.
