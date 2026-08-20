# ADR-0008: Anchor live previews on producer ports and connection pills

## Status

Accepted.

## Context

The original preview design treated Display Image Data and Channel Display nodes as the owners of
image previews. That made the image dock useful, but it did not let an author inspect data at an
ordinary graph connection. It also coupled preview cadence and dimensions to a downstream display
node even when one producer output fanned out to several consumers.

Connection previews must preserve the process boundary: full-resolution float arrays stay in the
engine, UI rendering remains in the UI process, and a slow UI must not block graph execution. Preview
visibility also affects demand because an otherwise unused branch should execute only while its pill
is visible.

## Decision

Anchor image, channel, integer, and float previews on the producing `(node_id, output_port_id)`.

- Create one coalesced image/channel target and shared-memory slot per connected producer port, then
  route that preview to every fan-out connection pill for the port.
- Carry integer and float pill text as compact, bounded preview messages. Sanitize non-finite values
  before display.
- Persist per-connection pill visibility in `ConnectionModel.ui_state` and keep it undoable. A visible
  pill adds its producer as a demand root when no sink or visualizer already demands the branch.
- Keep Display Image Data and Channel Display as the selectors for the image preview dock. Only the
  producer ports connected to those visualizers may update the dock; other previews update pills
  only. Note Visualizer remains the owner of compact note summaries for its dock.
- Use an application-wide 30 Hz and 800-pixel cap for image/channel previews and 60 Hz for scalar
  previews. These replace the removed per-display `preview_fps` and `max_dimension` parameters.
- Clear UI sequence cursors and retained pill/dock payloads after every successful graph activation
  or engine restart so data from an earlier runtime cannot be presented as current.

This decision supersedes the display-node-owned, independently configurable preview policy in the
original master architecture.

## Consequences

- Any eligible connection can show live data without adding a display node solely for inspection.
- Fan-out does not duplicate image conversion or shared-memory storage for the same producer port.
- Large graphs can publish more than 240 image previews per second in aggregate. Metrics therefore
  retain all publication timestamps in the rolling one-second window rather than imposing a fixed
  history count.
- Visible preview pills can add runtime work. Authors can hide individual pills, and transport remains
  bounded by per-port cadence, latest-value coalescing, and capped image dimensions.
- Clipboard fragment version 2 carries connection UI state. Version 1 fragments remain readable and
  default missing connection UI state to an empty mapping.

## References

- `synesthesia_machine_design/00_master_architecture.md`, sections 10.6, 13.3, and 16.16
- `src/synesthesia_machine/runtime/previews.py`
- `src/synesthesia_machine/ui/main_window.py`
- `src/synesthesia_machine/persistence/clipboard.py`
