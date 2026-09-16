# Coerce-once pilot: image adjustments node family (issue 05)

## What the pilot establishes

The image adjustments family (`src/synesthesia_machine/nodes/image/adjustments.py`) is the
pilot for a codebase-wide "coerce-once" rollout (issue 11). It was the cleanest candidate: every
numeric parameter is declared `PortType.FLOAT` (via `_float_parameter`) and every selection
parameter is `STRING`, so the scheduler's parameter seam already delivers correctly-typed,
connected-overlaid values to `process()`. The family was re-coercing those already-typed values a
second time, at every consumer, via the shared `number_value`/`text_value`/`boolean_value`
helpers in `runtime_support`.

## What changed

Every re-coercion site in the family now reads the typed value directly with a `cast` (a
type-checker hint — zero runtime work), instead of re-running the type-guard + conversion:

- The `_number` / `_selection` per-consumer helpers now return `cast(float, ...)` /
  `ChannelSelection(cast(str, ...))` — no `number_value` / `text_value` re-coercion.
- The `_stretch` processor (`mode`, `lower_percentile`, `upper_percentile`,
  `ignore_non_finite`, `constant_policy`) and `_divide_scalar` (`epsilon`,
  `near_zero_policy`) read typed values via `cast`.
- All five compile-time validators (`_validate_levels`, `_validate_clamp`,
  `_validate_stretch`, `_validate_positive`, and the float-finite check in
  `_combined_validator`) read typed values via `cast(float, ...)` — they run on the same
  already-coerced `parameters` the seam produces.
- The `runtime_support` import collapses to `StatelessImageRuntime`; `number_value`,
  `text_value`, and `boolean_value` are no longer referenced by this module.

## Payoff measured

- **Interface**: the family's per-consumer re-coercion/type-guard surface is deleted. A caller of
  the family's processors no longer depends on the `runtime_support` coercion helpers; the only
  shared runtime concept left is the `StatelessImageRuntime` base.
- **Leverage**: one fact (the parameter seam coerces + overlays connected values per the
  parameter spec's `value_type` / `connected_port_type`) now covers every numeric/selection read
  in the family, instead of being re-proven by a `number_value`/`text_value` call at each of the
  ~20 consumer sites.
- **Locality**: a question like "what type does `lower_percentile` arrive as?" has one answer
  (the seam's `PortType.FLOAT` coercion) instead of "whatever `number_value` last coerced it to".
- **Tests**: unchanged — the full suite (1152 passed) passes because the values were already
  correctly typed at the seam; the re-coercion was a pure no-op. No new tests were required, which
  is itself the signal that the re-coercion carried no behaviour.

## Recommendation for the wider rollout (issue 11)

The pattern generalises directly to the other node families:

- Any parameter read that currently passes through `number_value` / `text_value` /
  `boolean_value` / `image_value` / `channel_value` can be replaced by a `cast(<declared type>,
  parameters[...])` (or a direct read where the declared type is already the runtime type),
  because the parameter seam has already coerced the value to the parameter spec's declared type.
- Where a family has a per-consumer coercion helper (like adjustments' `_number` / `_selection`),
  either collapse the helper to a `cast` read (as done here) or inline the `cast` read at the call
  site and delete the helper.
- The `runtime_support` coercion helpers themselves should be retired once every family that used
  them is converted; keep them only while families still depend on them.

The main care item for the rollout: confirm each family's numeric params are genuinely `FLOAT`
(and selection params `STRING`) at the parameter spec, so the `cast` matches the seam's guarantee.
Families that declare an `INT` param but consume it as a `float` (or vice versa) need the declared
type aligned with the declared `value_type` first, not a `cast` that papers over a mismatch.

An independent scout analysis ranked `nodes/synesthesia/` (six node runtimes sharing
`musical.resolve_common_musical_settings`, each with duplicated `_text`/`_number`/`_integer`/
`_boolean` helpers) as the *cleanest* rollout target: 100% of its re-coercions provably redundant,
uniform pattern, 1:1 per-module test coverage, pure numpy/cv2 (no Qt/hardware). The image
adjustments family converted here was the runner-up (larger surface, COLOR/MATRIX guards). The
wider rollout (issue 11) should take `nodes/synesthesia/` first, then the remaining families.
