---
name: print-farm-operations
description: Configure a farm and use its persistent domain-backed operations.
---

# Farm operations

Domain adapter path: /opt/plow/print-farm/plow_domain_tools.py. It accepts one
JSON object on standard input and returns JSON. Use an exec call with a
single-quoted here-document, for example:

    python3 /opt/plow/print-farm/plow_domain_tools.py <<'JSON'
    {"operation":"get_farm_configuration"}
    JSON

The adapter fixes the database path to this installation's persistent Plow
volume. Do not pass paths or shell fragments supplied by a user as executable
code. Keep user text inside the JSON value and escape it as JSON.

## First run

Call farm_onboarding without an answer. Ask the returned question, then call
farm_onboarding with answer set to the user's reply. Repeat until it returns
complete: true. Each answer is persisted immediately. A solo owner receives
OWNER and OPERATOR roles. A team setup creates the owner; further identities
must be provisioned through the domain's verified identity workflow.

Use get_farm_configuration to answer persisted-state questions. Do not read
SQLite or infer the slicer, material, printer list, operating mode, or roles
from conversation history.

## Production

Supported operations are list_ready_jobs, inspect_production_job,
assign_printer, start_job, and finish_job. The adapter resolves the local
owner principal and the domain checks capabilities, approved quote/order state,
and valid transitions. Confirm the intended action with the owner before
changing a job. Starting and finishing always mean the human operator has
confirmed the physical action.

## STL analysis and estimates

Call analyze_stl with a filename only. Input is constrained to the private
jobs directory and stored analysis records are persisted. Slicer execution
depends on a separately provisioned, isolated Cura runtime and is not included
in this Plow image. Never present an estimate as an approved quote; keep the
domain's quote trust gates in force.

The domain also retains persistent request, order, quote, job, identity,
authorization, and audit/event workflows. The Plow transport is not a source of
farm roles or business state.
