#!/usr/bin/env python3
"""Add the app plugin to Plow's generated OpenClaw config without replacing Plow boot."""

from pathlib import Path


CONFIG = Path("/opt/plow/boot/config.ts")

LOAD_AND_ENTRIES = '''plugins: { load: { paths: ["/opt/plow/plugin", "/opt/plow/print-farm-stl-plugin"] }, entries: {
      plow: { enabled: true },
      "print-farm-stl": { enabled: true, config: {
        jobsDirectory: "/var/lib/plow/print-farm/jobs",
        analyzerScript: "/opt/plow/print-farm/experiments/stl-analysis/analyze_stl.py",
        persistenceScript: "/opt/plow/print-farm/experiments/stl-analysis/persistence.py",
        productionEstimateScript: "/opt/plow/print-farm/experiments/slicing/openclaw_production_estimate.py",
        quoteEngine: "/opt/plow/print-farm/experiments/quote-engine/quote.py",
        databasePath: "/var/lib/plow/print-farm/farm.sqlite",
        bridgeScript: "/opt/plow/print-farm/experiments/openclaw_channel_bridge.py",
        localToolsScript: "/opt/plow/print-farm/experiments/openclaw_local_tools.py",
        identityKeyFile: "/var/lib/plow/print-farm/secrets/identity.key",
        uploadSpool: "/var/lib/plow/print-farm/uploads",
        pendingIntakeRoot: "/var/lib/plow/print-farm/pending-intakes",
        openclawMediaRoot: "/var/lib/plow/media",
        privateJobsRoot: "/var/lib/plow/print-farm/jobs",
        identityAudience: "print-farm-operator"
      } }
    } },'''

TOOL_ALLOWLIST = '''tools: { profile: "messaging", toolSearch: false, sessions: { visibility: "tree" }, alsoAllow: ["read", "write", "edit", "exec", "plow_start_thread", "plow_set_thread_trust", "plow_ask_owner", "plow_reply_to", "analyze_stl", "get_latest_stl_analysis", "slice_stl", "get_latest_production_estimate", "farm_onboarding", "get_farm_configuration", "list_ready_jobs", "inspect_production_job", "assign_printer", "start_job", "finish_job"], deny: ["ask_user"] },'''


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"Expected one Plow {label} anchor, found {count}.")
    return source.replace(old, new, 1)


def main() -> None:
    source = CONFIG.read_text(encoding="utf-8")
    source = replace_once(
        source,
        'plugins: { load: { paths: ["/opt/plow/plugin"] }, entries: { plow: { enabled: true } } },',
        LOAD_AND_ENTRIES,
        "plugin config",
    )
    source = replace_once(
        source,
        'tools: { profile: "messaging", toolSearch: false, sessions: { visibility: "tree" }, alsoAllow: ["read", "write", "edit", "exec", "plow_start_thread", "plow_set_thread_trust", "plow_ask_owner", "plow_reply_to"], deny: ["ask_user"] },',
        TOOL_ALLOWLIST,
        "tool allowlist",
    )
    CONFIG.write_text(source, encoding="utf-8")


if __name__ == "__main__":
    main()
