---
name: analyze-stl
description: Analyze an STL staged in this Plow installation's private farm jobs directory.
---

# Analyze an STL

When the owner asks to inspect an STL:

1. A single STL attached through the WebChat file selector is validated and
   analyzed automatically by the trusted intake hook. Retrieve it with
   get_latest_stl_analysis; do not ask the owner to upload or stage the same
   file again. For an already staged file without a browser intake, ask for its
   filename if unclear and call analyze_stl with the filename only.
2. Report measured XYZ dimensions in millimetres, triangle count, and the
   watertight heuristic. Explain that STL does not encode units and this
   analyzer assumes millimetres.
3. Say volume is unavailable when the returned value is null. The mesh checks
   are heuristics and do not prove that a model is printable.
4. When the saved result contains request/job state, report those identifiers
   and statuses and explain that the request is not a priced or approved quote.
   Never invent a price, print time, or missing measurement.
5. Never guess missing measurements from the filename or the owner's message.
