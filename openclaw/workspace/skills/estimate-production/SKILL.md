---
name: estimate-production
description: Estimate production time and material for a staged STL using the fixed Cura profile; return a business quote only when persisted farm configuration and profile validation allow it.
---

# Estimate STL production

For a public WhatsApp customer request, follow `customer-request-intake` instead. That path only creates a request/job and draft quote; do not promise a price or call the legacy shared-directory tools.

When a user asks for a Cura estimate or quote for a staged STL:

1. Call `analyze_stl` with the filename only. Never pass an arbitrary path.
2. If analysis succeeds, call `slice_stl` with the same filename, the returned
   `analysis_id`, the approved `cura-ultimaker2plus-generic-pla-normal` profile, and the
   requested quantity (default to one when unspecified).
3. Report mesh dimensions separately from Cura time and filament volume. Grams
   are derived only from the persisted material density when a quote-ready
   configuration exists.
4. Check `quote_readiness_status`. If it is not `approved`, report that slicing
   completed but a business quote was withheld. Include the returned validation
   findings and do not invent a price or material mass.
5. When a quote is returned, label time, material, cost, price, and gross
   contribution as estimates. State the farm configuration used.
6. Mention Cura warnings or errors returned by the tool. Do not present the
   estimate as validated printer-ready G-code or a hardware-tested profile.
7. If the slice or quote fails, explain the returned error and do not invent
   missing metrics or prices.
