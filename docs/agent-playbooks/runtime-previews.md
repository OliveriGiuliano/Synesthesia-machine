# Runtime preview and visualization playbook

Read this playbook for preview contracts, preview brokers, shared-memory previews, scalar preview IPC,
connection pills, visualization nodes or docks, preview demand roots, and preview metrics.

## Ownership and routing invariants

- Image, channel, integer, and float connection previews are owned by the producing
  `(node_id, output_port_id)`, not by the destination connection or visualizer.
- Fan-out connections from one producer port share one converted/coalesced preview. Outputs from
  different ports must never bleed into one another.
- Cable pills and preview docks are different consumers. Every matching visible pill may render a
  producer preview, but only producer ports connected to Display Image Data or Channel Display may
  update the image dock. Note Visualizer remains the owner of note-dock summaries.
- Per-connection visibility is persisted and undoable. It must survive graph save/load, clipboard
  serialization, copy/paste, duplicate, undo, and redo.

## Lifecycle and demand invariants

- A visible eligible pill may demand an otherwise unused producer chain. A hidden pill must not add
  demand, although another sink or visualizer may still demand the same chain.
- Successful graph activation, plan replacement, and engine restart clear sequence cursors plus every
  retained pill/dock payload from the previous generation.
- Rejected activation preserves the last valid runtime and its current previews.
- Engine/UI cleanup remains idempotent after normal shutdown, startup failure, or forced termination.

## Bounds and metrics

- Full-resolution arrays remain in the engine. Image/channel previews use bounded shared memory;
  scalar previews use compact bounded messages. UI consumption must never block graph execution.
- When target cardinality changes, recalculate aggregate upper bounds and audit `deque(maxlen=...)`,
  slot counts, polling thresholds, caches, counters, and throughput metrics. A bound sized for display
  nodes is not automatically valid for every connected producer port.
- Rolling metrics must report the full defined time window, including values above historical target
  counts. Test one target, multiple targets, fan-out, multi-output producers, and a rate above the old
  fixed capacity.

## Required adversarial tests

- Publish several image/channel previews in an order where an unrelated producer arrives last; the
  image dock must still show the source connected to its visualizer while every cable gets its own
  preview.
- Exercise a producer with multiple output ports and fan-out from one port; assert exact routing and
  no sibling-port contamination.
- Populate image, scalar, and note UI payloads, then activate a new valid graph and assert every old
  cursor and rendered payload is cleared. Separately assert rejected activation retains valid state.
- Hide a pill, round-trip and remap it through every persistence/clipboard path, and assert it remains
  hidden.
- For sequence-threshold polling, first assert the current sequence is suppressed, then publish a new
  sequence and assert exactly that sequence is returned. Do not accept an assertion that passes when
  the threshold key is ignored.

Changing preview ownership, routing, cadence, dimensions, transport, demand semantics, or lifecycle
policy is architectural. Add or supersede an ADR before implementation unless current accepted
documents already describe the exact policy.
