# Synesthesia Machine implementation packets

These files are intended to be supplied to a coding agent one at a time. The master architecture is `../00_master_architecture.md`.

## Use order

1. `phase_0_foundation.md`
2. `phase_1_graph_core.md`
3. `phase_2_node_editor.md`
4. `phase_3_vertical_slice.md`
5. `phase_4_engine_camera_midi.md`
6. `phase_5_image_nodes.md`
7. `phase_6_synesthesia_nodes.md`
8. `phase_7_productivity_robustness.md`
9. `phase_8_performance_diagnostics.md`
10. `phase_9_packaging_release.md`

## Global rules for every agent

- Read the packet and the public README files of packages it names. Do not load the entire repository unless necessary.
- Preserve stable node type IDs, parameter IDs, port IDs, and persisted JSON fields.
- Make no architectural deviation without adding an ADR under `docs/adr/`.
- Add tests with every behaviour change.
- Run the packet's required checks before reporting completion.
- Do not implement image processing with Python pixel loops.
- Do not import Qt from engine/node algorithm modules.
- Do not send full image arrays through normal IPC queues.
- Do not add dependencies without a written justification and lockfile update.
- Finish with the completion-report template included in the packet.
