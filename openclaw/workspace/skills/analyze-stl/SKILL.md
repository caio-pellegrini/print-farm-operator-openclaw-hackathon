---
name: analyze-stl
description: Analyze an STL staged in this Plow installation's private farm jobs directory.
---

# Analyze an STL

When the owner asks to inspect an STL:

1. Ask for the filename if it is unclear. The file must already be staged in
   /var/lib/plow/print-farm/jobs.
2. Call the analyze_stl operation through the documented exec bridge in
   print-farm-operations, passing the filename only.
3. Report measured XYZ dimensions in millimetres, triangle count, and the
   watertight heuristic. Explain that STL does not encode units and this
   analyzer assumes millimetres.
4. Say volume is unavailable when the returned value is null. The mesh checks
   are heuristics and do not prove that a model is printable.
5. Never guess missing measurements from the filename or the owner's message.
