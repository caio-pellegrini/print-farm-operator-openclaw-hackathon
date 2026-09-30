# Current product boundaries

- The default runtime is Plow's OpenClaw base. WhatsApp is an experimental
  source path and is not included in this image or the tester install flow.
- Normal chat uses Plow Chat; local WebChat supports owner onboarding and a
  trusted single-STL upload. No raw iMessage or macOS Messages.app integration.
- Farm onboarding provisions the Plow owner as the initial owner. The domain
  contains verified user/role workflows, but additional transport-to-role
  provisioning is not part of this variant's first-run setup.
- WebChat and Plow Chat accept one staged `.stl` up to 25 MiB and persist its
  analysis, request, and job as a draft without a price. Phone-line media must
  be staged by the channel runtime. Other analysis inputs must be staged under
  the private farm jobs directory.
- Automated Cura slicing requires the isolated runtime image and Docker
  execution path. These are not bundled in the Plow variant. Quote creation
  must follow the existing profile, material, business configuration, and
  approval gates.
- The production adapter is manual. It records operator-confirmed assignment,
  start, completion, and failure. It does not control printers or read
  telemetry. Bambu, OctoPrint, Moonraker, and other printer control are not
  included.
- An Agent Index demo video still needs to be recorded from a real Plow
  installation.
