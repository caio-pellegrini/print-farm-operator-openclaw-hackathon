# Farm Configuration Store

The local SQLite application database is migrated by `farm_domain.py` and shared with STL analysis and estimate persistence. It does not use OpenClaw conversation history.

Initialize or inspect the configured database:

```sh
python3 experiments/farm_domain.py --database .stage1/state/print-farm.sqlite init
python3 experiments/farm_domain.py --database .stage1/state/print-farm.sqlite show
```

Register the Stage 3 execution evidence for review. This operation is idempotent for unchanged proof files and does not approve quoting:

```sh
python3 experiments/farm_domain.py --database .stage1/state/print-farm.sqlite register-stage3
```

Add a farm material with density source and cost, then record business costs. Material updates create a new version; business updates create a new active version while keeping the old snapshot:

```sh
python3 experiments/farm_domain.py --database .stage1/state/print-farm.sqlite add-material \
  --material-id pla-black --name "PLA Black" --density-g-cm3 1.24 \
  --density-source "Supplier technical datasheet" --density-source-ref "supplier-document.pdf" \
  --cost-per-kg 82 --selling-price-per-kg 160 --currency BRL

python3 experiments/farm_domain.py --database .stage1/state/print-farm.sqlite set-business \
  --machine-hour-cost 2.50 --energy-kwh-per-hour 0.12 --energy-cost-per-kwh 0.95 \
  --minimum-margin 0.35 --minimum-job-fee 10 --setup-fee 0 --currency BRL
```

Import a profile bundle by naming its source files and all three preset identifiers. Its SHA-256 digest covers sorted absolute source-file names and bytes. A new import starts execution `unverified` and quote status `needs_review`:

```sh
python3 experiments/farm_domain.py --database .stage1/state/print-farm.sqlite import-profile \
  --profile-id farm-orca-a1-04 --display-name "Farm A1 0.4 / PLA / Standard" \
  --slicer-id orcaslicer --slicer-version 2.4.2 --source "Farm preset export 2026-09-27" \
  --machine-preset-id "A1 0.4" --process-preset-id "0.20mm Standard" \
  --material-preset-id "PLA Black" --profile-file printer.json --profile-file process.json \
  --profile-file filament.json --nozzle-mm 0.4 --material-id pla-black --material-version 1
```

Use `record-execution --status available|blocked --evidence-ref ...` to record slice execution evidence. Use `set-profile-material` when changing a profile's material association; it creates another profile version and resets quote trust to review. Use `validate-profile --status needs_review|approved|blocked --evidence-ref ...` to append a quote-validation decision and findings. Approval requires an executable profile and an active material. Evidence references are operator supplied; the software does not independently certify them.

The Stage 3 proof records intentionally retain their findings: Cura material-volume parity is unresolved; Orca needs review for its fixed extrusion-mode compatibility and external density; Bambu's anomalous material amount blocks its current proof profile; Creality needs farm profile/metric validation. Bambu's current proof profile cannot be approved. No profile change widens the OpenClaw tool allowlist, which remains fixed to Cura.

The Bambu record keeps the original Stage 3 A1 profile as version 1 (`available` execution, `blocked` quote). The release-pinned inheritance-free A1 bundle is version 2; Bambu Studio 02.08.02.61 rejects it with `process not compatible with printer`, so both execution and quote status are `blocked`. Its source digest matches the three files used by the adapter and its failure evidence is `experiments/slicing/evidence/bambu-resolved-cli-failure.log`.

## Stage 5 identity and customer intake

`identity_workflow.py` is the application authorization and intake adapter. A trusted deployment bridge must authenticate the upstream person and issue a short-lived signed assertion before calling `resolve_identity`; do not create assertions from OpenClaw session keys, labels, or conversation IDs. `bootstrap_first_owner` can be used once on an empty farm. After bootstrap, only a resolved Owner can bind a verified identity with `bind_verified_user` or change roles. Roles are read from `farm_users` / `user_roles` and capabilities are unioned.

The bridge resolves an OpenClaw managed attachment reference through its trusted attachment API, then calls `register_openclaw_upload` with the opaque reference and resolver callback. Customer intake calls `submit_customer_request` with the resolved principal and upload reference. This creates an analysis, a draft quote, an order, and a job. Use `inspect_job`, `update_job_status`, `owner_add_material`, `owner_set_business_configuration`, and `set_user_roles` for role-checked operations. Run `cleanup_expired_uploads` and `cleanup_expired_job_files` on a deployment-owned schedule. Default retention is 30 minutes for unconsumed uploads and 30 days for job files.

The repo tests this assertion and attachment bridge contract locally. It does not include a configured OpenClaw authenticated sender provider or live managed-attachment connector. Keep the legacy shared-directory plugin tools away from customer-facing roles until such a bridge mediates requests. Intake deliberately stores a draft quote only; the Stage 4 digest-matched profile/material/business gate remains mandatory for any real quote.
