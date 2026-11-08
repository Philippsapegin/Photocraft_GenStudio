# GenGate project rules

## User-facing release notes

- Write for the person installing and using GenGate: visible improvements,
  implemented fixes and necessary installation/update actions.
- State the supported PhotoCraft version once as compatibility information.
  Omit the engineering work needed to adapt to that version: source hooks,
  buffers, channels, crate changes, build details and internal updater wiring.
- Describe fixes by the user's symptom and resulting behavior.
- Omit unchanged features, absence of a fix, unsuccessful investigations and
  repeated statements that an issue was not reproduced or its cause was not found.
- Keep technical explanations, unresolved investigations and validation evidence
  in development/audit documents. Release notes are not a development log.
- Keep historical release tags and shipped assets immutable.

## Validation scope

Completed real-model coverage and user acceptance are recorded in
[model validation](docs/MODEL_VALIDATION.md). Consult that matrix before claiming
models are untested or planning a repeat inference run.

Automated checks use isolated profiles, synthetic backends and tiny CPU fixtures.
Real model inference requires the user's explicit authorization. Do not load
checkpoints, initialize CUDA for generation or modify the user's GPU Python
dependencies during an audit. Preserve user documents, preferences and models.
