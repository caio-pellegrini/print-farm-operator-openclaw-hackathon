#!/usr/bin/env python3
"""Create a clean deployment config without importing workstation state."""

import json
import os
from pathlib import Path


app = Path(os.environ.get("APP_ROOT", "/opt/print-farm-operator"))
data = Path(os.environ.get("PRINT_FARM_DATA_DIR", "/data"))
state = Path(os.environ["OPENCLAW_STATE_DIR"])
config_file = Path(os.environ["OPENCLAW_CONFIG_PATH"])
workspace = app / "openclaw" / "workspace"
plugin = app / "openclaw" / "plugins" / "print-farm-stl"
config_file.parent.mkdir(parents=True, exist_ok=True)

farm_config = {
    "jobsDirectory": str(data / "jobs"),
    "analyzerScript": str(app / "experiments" / "stl-analysis" / "analyze_stl.py"),
    "persistenceScript": str(app / "experiments" / "stl-analysis" / "persistence.py"),
    "productionEstimateScript": str(app / "experiments" / "slicing" / "openclaw_production_estimate.py"),
    "quoteEngine": str(app / "experiments" / "quote-engine" / "quote.py"),
    "databasePath": str(data / "farm" / "farm.sqlite"),
    "bridgeScript": str(app / "experiments" / "openclaw_channel_bridge.py"),
    "localToolsScript": str(app / "experiments" / "openclaw_local_tools.py"),
    "identityKeyFile": str(data / "secrets" / "identity.key"),
    "uploadSpool": str(data / "upload-spool"),
    "pendingIntakeRoot": str(data / "pending-intake"),
    "openclawMediaRoot": str(state / "media" / "inbound"),
    "openclawCli": "/usr/local/bin/openclaw",
    "privateJobsRoot": str(data / "private-jobs"),
    "identityAudience": os.environ["PRINT_FARM_IDENTITY_AUDIENCE"],
    "pythonExecutable": "/usr/bin/python3",
    "dockerExecutable": "docker",
}

config = {
    "agents": {"defaults": {"workspace": str(workspace)}},
    "gateway": {"mode": "local", "bind": "lan", "port": 18789,
                "auth": {"mode": "token"},
                "controlUi": {"allowedOrigins": [
                    "http://127.0.0.1:18789", "http://localhost:18789"]}},
    "plugins": {
        "allow": ["print-farm-stl"],
        "load": {"paths": [str(plugin)]},
        "entries": {"print-farm-stl": {"enabled": True, "config": farm_config}},
    },
}

# Keep an operator-edited config on container restarts. A clean volume always
# receives the project defaults above and never receives host OpenClaw state.
if not config_file.exists():
    config_file.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    config_file.chmod(0o600)
