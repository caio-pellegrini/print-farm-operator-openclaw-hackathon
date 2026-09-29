# Slicer Comparison

**Updated:** 2026-09-27. The detailed evidence table, settings, metrics, licensing notes, container sizing, and exact limitations are in the [Stage 3 Multi-slicer Proof Report](stage-3-multi-slicer-proof-report.md).

## Current findings

| Engine | Evidence | Linux/headless status | Profiles and outputs | Stage decision |
|---|---|---|---|---|
| CuraEngine 5.13.0 | **TESTED** | Real headless Docker execution | Official Ultimaker 2+ resources plus simplified fixed settings; time/volume from verbose output; G-code discarded | Existing OpenClaw baseline; full-profile/material parity still unresolved |
| OrcaSlicer 2.4.2 | **TESTED** | Real Linux AppImage CLI execution inside Docker | Bundled Ender-3 V3 SE machine, Standard 0.20 mm process, Generic PLA; time/volume/G-code and warning metadata extracted | Selected second adapter; fixed absolute-E compatibility value and external density are disclosed |
| Bambu Studio 02.08.02.61 | **TESTED CLI; GUI parity pending** | Official Ubuntu 24.04 AppImage ran headlessly in Docker | Preserving bundled `from`, `setting_id`, and `inherits` while merging full values passes compatibility and gives 481.49 s / 1,985.58 mm³. Historical profile remains 1,271 s / 43,486.04 mm³; cause not confirmed. | CLI profile candidate works; business quote remains blocked until GUI parity and all trust gates. Keep Bambu out of OpenClaw. |
| Creality Print 7.2.1.5476 | **TESTED** | Official Linux AppImage ran headlessly in Docker | Bundled Ender-3 V3 SE profiles loaded directly; G-code supplied 507 s and 1,940 mm³; generic quote pipeline succeeded. `result.json` time fields were zero. | Prototype adapter is tested; optional 3MF export had a path error, while G-code-only slice succeeded |
| PrusaSlicer | **INVESTIGATED**, limited CLI probe | Current official Linux install is Flatpak/Drivers; prior Debian `2.9.2+UNKNOWN` CLI help worked, but no slice completed | CLI preset options exist; isolated configuration was missing | Useful reference, but not selected for this proof |

## Proof and comparability

CuraEngine, OrcaSlicer, Bambu Studio, and Creality Print sliced the same staged 20 mm box fixture and returned the common normalized fields. The quote engine consumed each result through one generic function. This proves adapter independence at prototype scope. Machine-readable results and artifact sizes are in `experiments/slicing/stage3-proof-results.json`.

The estimates are not comparable for accuracy: each run used different printer, process, nozzle, or filament profiles. Bambu's volume/mass is internally consistent with its own G-code density but anomalously large for the fixture. Orca declared density and grams as zero. In every case, the generic quote function received application-supplied PLA density (1.24 g/cm³); quote amounts are pipeline proofs only.

The old Cura 5.0.0 / legacy Ender-3 results in `experiments/slicing/cura-results.json` are historical only. They are superseded by the Stage 2 Cura 5.13.0 baseline and Stage 3 report.

Reproduce the four-engine proof after building `Dockerfile.orca`, `Dockerfile.bambu`, and `Dockerfile.creality` with their respective pinned image tags. The runner also needs the locally built Stage 2 Cura image:

```sh
python3 experiments/slicing/run_stage3_proof.py
```

## Stage 4 farm quote trust

Stage 3 execution evidence is registered separately from farm quote approval in the versioned SQLite profile registry. The current proof profiles are executable in the tested environment, but their quote status is Cura **needs review**, Orca **needs review**, Bambu **blocked**, and Creality Print **needs review**. The profile findings preserve the Cura material-parity issue, Orca's extrusion-mode and external-density requirements, and Bambu's anomalous material metric. None of these records expands the OpenClaw allowlist; the model-facing slice tool remains Cura-only.

An operator must import the farm's preset files with `farm_domain.py import-profile`, configure an explicit material density/source and versioned costs, record execution evidence, and validate quote trust before the quote runner returns a business quote. A successful slice and an approved quote are separate outcomes.

One committed tester has confirmed a Bambu Lab A1, 0.4 mm nozzle, and Bambu Studio workflow. The profile/CLI investigation and new result are recorded in the [Bambu homologation plan](bambu-homologation-implementation-plan.md) and [CLI/3MF evidence](../experiments/slicing/evidence/bambu-a1-system-chain/). GUI parity remains pending; quote trust stays blocked.
