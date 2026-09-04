# ADR 0015: The editor no longer surfaces alpha channels or the Opacity node

- Status: Accepted
- Date: 2026-09-16

## Context

The app's two image sources (video files and cameras) decode to three-channel frames and never
carry an alpha channel, so no graph in practice ever produces a meaningful fourth channel.
Despite that, several node surfaces offered it: Separate Channels and Combine Channels exposed a
`channel_4` socket, Change Colour Space and Combine Channels offered RGBA as a target, the
adjustment nodes offered a `CHANNEL_4` channel selection (and Clamp offered `include_alpha`,
Invert Colour an `invert_alpha`), and the dedicated Opacity node existed almost solely to
fabricate an alpha channel that nothing downstream could consume. These surfaces cluttered the
editor, expanded the parameter space of node migrations and translations, and made Combine
Channels' required-input rule dependent on a colour space that no saved graph could reach.

The media layer keeps full RGBA support: colour conversion, adjustment, and compositing all
still operate on four-channel frames, and RGBA stays a valid `ColorSpace` for internal
conversions and preview handling. Only the user-facing node definitions are trimmed.

## Decision

Remove the fourth (alpha) channel from every user-facing image node surface, in three parts:

1. **Socket removal.** Separate Channels (v2) exposes `channel_1..channel_3` only; Combine
   Channels (v2) accepts `channel_1..channel_3` only and no longer offers RGBA as a target.
2. **Choice removal.** Change Colour Space (v2) no longer offers RGBA among its targets, and
   every adjustment node (brightness, contrast, colour levels, stretch contrast, gamma, clamp,
   add/multiply/divide scalar, v2) no longer offers the `CHANNEL_4` selection. Clamp (v2) loses
   `include_alpha`; Invert Colour (v2) loses `invert_alpha` and always preserves alpha.
3. **Node removal.** The `synmachine.image.opacity` node is deleted from the catalogue. The
   Blend node's `opacity` parameter and `alpha_policy` are unrelated to the alpha channel and
   are unchanged.

Graph schema version moves 3 -> 4. The graph migration drops Opacity nodes, drops every
connection touching a dropped node, and drops connections into `combine_channels.channel_4` or
out of `separate_channels.channel_4`. Per-node migrations (all v1 -> v2) rewrite the retired
parameter values: an RGBA target becomes the SRGB default, a `CHANNEL_4` selection becomes the
COLOUR default, and the removed boolean parameters are dropped. The catalogue and bundled
example graphs are re-serialized at the new schema.

## Consequences

- Saved graphs that used the Opacity node or a fourth channel still load: the v3 -> v4
  migration deterministically drops the retired nodes, sockets, and parameters, so no remaining
  node references a removed surface. Graphs that wired a required input *through* the Opacity
  node can load invalid (that input is now unconnected) and need rewiring, matching the engine
  auto-stop behaviour for broken graphs.
- The `ColorSpace.RGBA` enum value, its descriptor, and RGBA conversion/compositing code stay
  in the media layer for internal use; no engine protocol, scheduler, or IPC change is needed.
- Combine Channels' required inputs are now constant (`channel_1..channel_3`) because every
  offered target has exactly three channels.
- `tests/fixtures/compatibility/catalogues/catalogue-50-definitions.synmachine.json` is
  intentionally left as a schema-v1 historical fixture: loading it exercises the whole
  1 -> 2 -> 3 -> 4 migration chain (including v3 -> v4) and yields the 49 surviving
  definitions.

## Correction (2026-09-16)

The initial scope covered the adjustment package (`adjustments.py`). A second
`_channel_parameter()` helper in the filter package (`filters.py`) still offered
`CHANNEL_4` to **Add Noise** and **Posterize**. It is now excluded the same way,
and both nodes moved from v1 to v2 with the shared CHANNEL_4 -> COLOUR node
migration, completing the decision above.

## References

- `docs/architecture/master.md` (sections 16.4 and 16.7)
- `docs/reference/image-node-matrix.md`
- `src/synesthesia_machine/nodes/image/channels.py`, `adjustments.py`, `utilities.py`, `filters.py`
- `src/synesthesia_machine/persistence/schemas.py` (v3 -> v4)
- `src/synesthesia_machine/persistence/node_migrations.py` (v1 -> v2 family)
