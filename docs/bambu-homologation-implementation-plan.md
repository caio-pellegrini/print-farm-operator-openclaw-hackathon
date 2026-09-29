# Bambu Studio A1 Homologation

## Priority and scope

Prioritize Bambu Studio as a **distribution-driven product priority**. One committed tester confirmed a Bambu Lab A1 with a 0.4 mm nozzle and uses Bambu Studio because it already connects directly to the printer. This makes slicing estimates compatible with the tester's current workflow important for onboarding, and signals an eventual expectation of printer-side integration.

The checked-in A1 0.4 mm machine target now directly matches that tester's confirmed hardware. The single-filament Bambu PLA Basic profile and 0.20 mm Standard process are the current reproducible homologation configuration; the tester has not yet confirmed their material or process preset. Keep printer APIs, job control, telemetry, and Stage 6 adapters out of this stage. Do not add Bambu to the model-facing OpenClaw allowlist.

## Evidence and decisions

- **TESTER-CONFIRMED:** Bambu Lab A1, 0.4 mm nozzle, Bambu Studio. The stated reason is that Studio already connects directly to the printer.
- **TESTED:** The historical Stage 3 profile sliced the checked-in 20 × 20 × 12 mm fixture in Bambu Studio 02.08.02.61 and returned about 43,487 mm³ / 1,271 s. Its G-code reported 18,079.96 mm of 1.75 mm filament, 43,487.39 mm³, 54.79 g, and 1.26 g/cm³. The values reconcile mathematically, but the toolpath is anomalous and remains untrusted.
- **INVESTIGATED:** Bambu's [official CLI guide](https://github.com/bambulab/BambuStudio/wiki/Command-Line-Usage) says `--load-settings` and `--load-filaments` need full configs, not raw files from `resources/profiles/BBL`. The release's bundled system presets carry `from: system`, `setting_id`, `name`, and `inherits` metadata. The flattened bundle failed compatibility when these fields were removed.
- **TESTED:** A new bundle fully merges parent-to-child settings and preserves the bundled system identities and ancestry. The CLI accepts the A1 / 0.20 Standard / Bambu PLA Basic combination (`compatible 1`) and slices the same hashed fixture successfully. The result is 481.49 s, 825.51 mm filament, 1,985.58 mm³, 2.50 g at 1.26 g/cm³, one object with 12 triangles, one filament, no filament changes, and 60 layers. Filament length and volume agree within 0.001%; volume, mass, and density also reconcile.
- **INVESTIGATED:** Bambu CLI `--export-settings` did not emit a settings file in the tested headless STL invocation. `--export-3mf` did expose its effective configuration as `Metadata/project_settings.config`; that exact config and the associated G-code/result files are preserved under `experiments/slicing/evidence/bambu-a1-system-chain/`.
- **HYPOTHESIS:** Preserving Bambu's system preset identity while supplying complete resolved values appears to explain both the earlier compatibility rejection and the much smaller new material result. The root cause of the historical ~43,487 mm³ toolpath is still unconfirmed; no GUI parity comparison has been made.
- **Version:** Pin Bambu Studio 02.08.02.61 and the Ubuntu 24.04 AppImage SHA-256 in the profile manifest and Dockerfile. Newer listed versions were beta at the time of the original investigation.

## Profile and CLI findings

`experiments/slicing/resolve_bambu_profiles.py` now merges inheritance values from parent to child, while preserving Bambu's system preset name, `from`, `setting_id`, `inherits`, compatibility metadata, and ancestry. The manifest pins the exact source preset files, app version, AppImage hash, emitted profile hashes, and the combined bundle digest.

The validated system identifiers and source parents are:

| Role | Preset | System ID | Source parent |
|---|---|---|---|
| Machine | `Bambu Lab A1 0.4 nozzle` | `GM030` | `fdm_bbl_3dp_001_common` |
| Process | `0.20mm Standard @BBL A1` | `GP079` | `fdm_process_single_0.20` |
| Filament | `Bambu PLA Basic @BBL A1` | `GFSA00_04` | `Bambu PLA Basic @base` |

The bundle currently hashes to `ed6ace8e94e869076168f560f4cbaa971a09eeefb40d2b40adca1753a6bff242`. The CLI-exported effective project reports a 0.20 mm layer height, two wall loops, 15% sparse infill, 5 mm brim, zero skirt loops, five top layers, three bottom layers, no supports, 1.75 mm filament, and 1.26 g/cm³ density. Its selected IDs match the pinned system presets. These are the effective CLI settings; equality with the tester's GUI project remains to be checked.

Loading the bundled BBL machine/process/filament files directly did satisfy compatibility, but the generated G-code had zero density and weight, so the resource fragments alone are not a usable effective config. Flattening values while preserving the original system identity/ancestry supplies both the complete CLI values and the compatibility metadata. The inheritance-free `from: User` version failed; do not use `--no-check` to bypass it.

The historical run's G-code and profile differ materially from this current bundle. The earlier huge toolpath is not overwritten or reinterpreted. Do not use the new sane-looking result to assert a confirmed root cause or quote safety.

## Current implementation and trust state

- The Bambu adapter remains fixed-profile, digest-checked, no-network, read-only-root, and resource-bounded. It normalizes time and filament volume into the existing `SlicerResult` and independently checks volume against filament length and mass against density.
- Generic quoting must use application-configured material density, not silently trust Bambu's density. A successful slice never approves a business quote.
- Keep the historical Stage 3 proof and previously rejected flattened bundle blocked. Stage 4 stores the identity-preserving bundle as profile version 4; its registered digest now exactly matches the adapter bundle digest, execution is `available`, and quote status remains `blocked` pending GUI parity. A prior import attempt with path-based digest keys remains historical; Bambu imports now digest the three fixed basenames exactly as the adapter does.
- Bambu approval now requires structured evidence whose bundle digest matches the registered profile and records `gui_parity_status: passed`, `physical_print_validation.status: passed`, and `quote_safe: true`. A CLI slice or a manually submitted `approved` status cannot bypass these checks.
- Stage 4 already stores profile/version/digest, run and quote states, evidence references, and versioned material density/source in existing snapshots. No schema migration is currently needed.
- Keep Bambu outside OpenClaw's model-facing allowlist. Later exposure requires quote-safe validation, active/executed digest equality, and a separate OpenClaw enforcement check.

## Exact next steps

1. **Obtain the GUI baseline.** On Bambu Studio 02.08.02.61, select A1 0.4 mm and the matching single-filament PLA/process configuration. Save the unsliced project plus a GUI-sliced `.gcode.3mf` or G-code and screenshots/exports that identify all three selected presets. Confirm which plate, support, brim, and skirt choices the tester uses. A system preset bundle export may remain a delta; do not feed it to the CLI as if it were a full effective config.
2. **Compare GUI with the current CLI bundle on the same hashed fixture.** Compare selected preset IDs and inheritance ancestry, effective settings, total estimated time, filament length, volume, mass/density metadata, object/triangle/filament/layer counts, and generated toolpath. Require volume within 2%, time within 5%, matching material semantics, and independent length/volume agreement within 1% and mass/density within `max(0.2 g, 3%)`. Investigate any disagreement instead of changing trust gates.
3. **Resolve the historical anomaly.** Inspect effective settings and G-code extrusion paths from both GUI and CLI. Reconcile the old ~43,487 mm³ result against brim, skirt, supports, purge, and actual extrusion. Any result above 2× modeled solid volume remains blocked until its geometry/toolpath explains the amount.
4. **Repeat CLI slices** once GUI-equivalent settings are established: volume range ≤0.5%, time range ≤1%. Preserve commands, logs, effective project config, G-code, result JSON, fixture hash, and profile digest.
5. **Only after GUI/CLI parity passes**, record the candidate as `UNDER_VALIDATION` (Stage 4's `needs_review`) with the matching GUI evidence. Pass its normalized `SlicerResult` through the existing generic quote function using application-configured density. Persist the exact profile and density provenance; keep the quote blocked if active and executed digests differ or evidence is incomplete. Current registry verification confirms the bundle digest matches, while readiness remains blocked because parity is pending.
6. **Then begin physical validation**, which is explicitly deferred for now: manually print three representative single-filament PLA jobs and record elapsed time and spool mass before/after. Require median time error ≤15%, no individual >25%, median material error ≤10%, and no individual >20%. This remains manual validation only; no printer API or control automation is introduced.
7. Advance only this A1 0.4 mm single-filament PLA profile from `UNDER_VALIDATION` to `QUOTE_SAFE` after every gate passes. Do not generalize approval to other models, nozzles, materials, or AMS/multi-color workflows.

## Current evidence and limitations

The current Bambu run is a successful, internally consistent CLI slice based on the pinned bundled preset chain. It is **not yet a GUI parity result**: this environment has no Bambu Studio GUI session or tester project to compare against. The tester's printer and nozzle are confirmed; their material/process choices remain unconfirmed. Physical print validation has not started, per instruction. See [`slice-comparison.json`](../experiments/slicing/evidence/bambu-a1-system-chain/slice-comparison.json), [`effective-settings.json`](../experiments/slicing/evidence/bambu-a1-system-chain/effective-settings.json), [`normalized-result.json`](../experiments/slicing/evidence/bambu-a1-system-chain/normalized-result.json), and the preserved [`CLI log and artifacts`](../experiments/slicing/evidence/bambu-a1-system-chain/).

Bambu Studio is AGPL-3.0. Review notices and corresponding-source obligations before distributing its container or offering it as a network service. Keep the optional proprietary networking plugin out of this slicer-only image.
