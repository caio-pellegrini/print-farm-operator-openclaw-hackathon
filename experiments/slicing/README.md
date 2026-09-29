# Slicing experiments

The Stage 2 OpenClaw baseline uses CuraEngine 5.13.0 and its locally built image `print-farm-cura-5.13.0:baseline`. The checked-in `Dockerfile.cura` and `run_cura.py` preserve an earlier Cura 5.0.0 / legacy Ender-3 experiment only; do not treat those historical G-code files as current estimates or print them on hardware.

## Stage 3 proof

The Stage 3 adapters use fixed profile IDs and a common result shape. The OpenClaw plugin remains Cura-only. Build the three checked-in AppImage runtime images and run the four executed adapters:

```sh
docker build -f experiments/slicing/Dockerfile.orca -t print-farm-orca-2.4.2:baseline experiments/slicing
docker build -f experiments/slicing/Dockerfile.bambu -t print-farm-bambu-studio-2.8.2.61:baseline experiments/slicing
docker build -f experiments/slicing/Dockerfile.creality -t print-farm-creality-print-7.2.1:baseline experiments/slicing
python3 experiments/slicing/run_stage3_proof.py
```

The proof runner also requires the Stage 2 Cura image `print-farm-cura-5.13.0:baseline` to already exist. It uses the checked-in `small-box-20mm.stl` fixture, stages one private copy for all four adapters, and writes normalized results and generic quote outputs to `stage3-proof-results.json`.

The AppImage Dockerfiles pin the Ubuntu base digest and official AppImage SHA-256, but Ubuntu package versions are not locked. Bambu's A1 profile bundle is resolved parent-to-child by `resolve_bambu_profiles.py`. It retains bundled system `from`, `setting_id`, preset name, `inherits`, compatibility metadata, ancestry, and source/file/bundle hashes. Bambu Studio 02.08.02.61 accepts the full resolved bundle with those identities retained (`compatible 1`) and slices the hashed fixture. The resulting effective settings are exported inside `Metadata/project_settings.config` in the CLI-created 3MF; the tested headless `--export-settings` invocation did not emit a separate settings file. CLI success is not GUI parity or quote approval. See `evidence/bambu-a1-system-chain/`. Creality uses bundled Ender-3 V3 SE leaf presets. The Cura 5.13.0 image build recipe also remains uncommitted, so the runner requires that existing local Stage 2 image.

Run `python3 experiments/slicing/run_bambu_homologation.py` to check profile integrity and repeat the fixed Stage 3 fixture. The runner does not silently fall back to the historical profile and leaves quote readiness blocked until GUI parity is established.

Bambu's historical result was anomalously high; the current system-identity-preserving resolved profile returns a materially smaller result that reconciles internally. The root cause of the difference and GUI parity are still unconfirmed, so its pipeline quote is not a business estimate. Creality's result JSON time fields are zero, so its adapter reads time and volume from G-code comments. All slicer profiles are fixed allowlisted IDs; no arbitrary model-provided paths, slicer flags, or settings are accepted.

See the [Stage 3 report](../../docs/stage-3-multi-slicer-proof-report.md) and [slicer comparison](../../docs/slicer-comparison.md) for measurements, assumptions, and limitations.
