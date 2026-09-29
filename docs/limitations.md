# Current product boundaries

- The default runtime is Plow's OpenClaw base. WhatsApp is an experimental
  source path and is not included in this image or the tester install flow.
- The standard interaction path is the tester's Plow phone line. There is no
  raw iMessage or macOS Messages.app integration in this project.
- Farm onboarding provisions the Plow owner as the initial owner. The domain
  contains verified user/role workflows, but additional transport-to-role
  provisioning is not part of this variant's first-run setup.
- STL analysis is available for files staged under the installation's private
  farm jobs directory. The Plow base does not provide file transfer for
  arbitrary STL uploads as part of this repository.
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
