# Image node implementation matrix

This matrix records implementation identities and shared policies for the production image-node
catalogue. It is derived from sections 7, 8, 9, 10, 11.3–11.4, 16, 18.7, 21, and 24 of the master
architecture. Display labels may evolve; the type, port, parameter, and enum IDs in this document
are persistent identities.

## Shared implementation plan

- `media/image_common.py` owns common border/interpolation conversion, kernel validation,
  finite-value reporting/sanitation, descriptor-backed channel selection, alpha splitting and
  recombination, colour-value conversion, and immutable frame construction.
- `media/dimensions.py`, `media/adjustments.py`, `media/filters.py`, and `media/compositing.py` own
  Qt-free NumPy/OpenCV algorithms. There are no Python pixel loops.
- `nodes/image/dimensions.py`, `adjustments.py`, `filters.py`, `channels.py`, `utilities.py`, and
  `temporal.py` own definitions and thin runtimes. `nodes/image/catalogue.py` composes them in a
  stable display order. The `core.py` facade remains import-compatible while its contents are split.
- `nodes/utility/scalar_bridges.py` owns scalar bridge definitions. `nodes/visualization` owns
  Channel Display metadata while `runtime/previews.py` performs bounded engine-local conversion.
- `tests/nodes/image/conftest.py` and `tests/support/image_conformance.py` provide synthetic frames, immutable
  input guards, `NoData` propagation checks, and common metadata/dtype/shape/clock assertions.

## Common policies

- Image outputs are read-only C-contiguous `float32`, preserve the input context/provenance and
  colour descriptor unless a contract explicitly changes them, and never alias mutable storage.
- `COLOUR` channel selection means every descriptor channel except alpha; `ALL` includes alpha;
  `CHANNEL_1` through `CHANNEL_4` select descriptor positions and reject unavailable positions.
- The spatial border enum is `REFLECT_101`, `REFLECT`, `REPLICATE`, `CONSTANT`, `WRAP`; default is
  `REFLECT_101`. The interpolation enum remains `AUTO`, `NEAREST`, `LINEAR`, `AREA`, `CUBIC`,
  `LANCZOS`.
- Stateless nodes rely on scheduler-owned `NoData` propagation. Finite values are propagated unless
  a row below declares sanitation, rejection, or finite-only reduction. Display boundaries sanitize
  NaN/Inf. Parameter combinations are checked by definition validators and again at runtime where
  malformed persisted/internal values could otherwise reach native code.
- Multi-input nodes require equal shape and source clock and descriptor compatibility. They do not
  resize, synchronize, or convert implicitly.
- All parameters are `LIVE` unless a row explicitly says `RECOMPILE`.

## Catalogue

| Batch | Stable type ID | Fixed ports | Stable parameters and defaults | Alpha / finite policy |
|---|---|---|---|---|
| 1 | `synmachine.image.resize` | `image: IMAGE -> image: IMAGE` | `width=500`, `height=500` (connectable INT, 1–8192), `preserve_aspect=true`, `fit_mode=CONTAIN`, `interpolation=AUTO` | Processes alpha; Contain pads transparent black for alpha images and black otherwise; propagates non-finite values. |
| 1 | `synmachine.image.crop` | `image: IMAGE -> image: IMAGE` | `coordinate_mode=NORMALIZED`, `left=0`, `top=0`, `right=1`, `bottom=1`, `out_of_bounds=CLAMP`, `pad_colour=(0,0,0,0)` | Crops alpha with colour channels; constant padding uses descriptor-converted colour/alpha; propagates non-finite values. |
| 1 | `synmachine.image.flip` | `image: IMAGE -> image: IMAGE` | `mode=HORIZONTAL` (`HORIZONTAL`, `VERTICAL`, `BOTH`) | Reorders all channels including alpha; propagates non-finite values. |
| 1 | `synmachine.image.rotate` | `image: IMAGE -> image: IMAGE` | `angle_degrees=0` (connectable FLOAT), `centre_x=0.5`, `centre_y=0.5` (connectable FLOAT, 0–1), `expand_canvas=false`, `interpolation=AUTO`, `border_mode=REFLECT_101`, `border_colour=(0,0,0,0)` | Processes alpha with colour channels; constant border uses descriptor-converted colour/alpha; propagates non-finite values. |
| 2 | `synmachine.image.brightness` | `image: IMAGE -> image: IMAGE` | `offset=0` (connectable FLOAT), `channels=COLOUR` | Preserves alpha by default; IEEE propagation. |
| 2 | `synmachine.image.contrast` | `image: IMAGE -> image: IMAGE` | `factor=1`, `pivot=0.5` (connectable FLOAT), `channels=COLOUR` | Preserves alpha by default; IEEE propagation. |
| 2 | `synmachine.image.clamp` | `image: IMAGE -> image: IMAGE` | `minimum=0`, `maximum=1` (connectable FLOAT), `channels=COLOUR`, `include_alpha=false` | Alpha optional; NumPy clamp leaves NaN as NaN and maps infinities to selected bounds. |
| 2 | `synmachine.image.colour_levels` | `image: IMAGE -> image: IMAGE` | `input_black=0`, `input_white=1`, `gamma=1`, `output_black=0`, `output_white=1` (connectable FLOAT), `channels=COLOUR` | Preserves alpha by default; rejects invalid ranges/gamma; IEEE propagation. |
| 2 | `synmachine.image.hue` | `image: IMAGE -> image: IMAGE` | `turns=0` (connectable FLOAT, 0–1 slider) | Preserves alpha; wraps hue modulo one; finite colour input required for OpenCV conversion. |
| 2 | `synmachine.image.saturation` | `image: IMAGE -> image: IMAGE` | `factor=1` (connectable non-negative FLOAT) | Preserves alpha; finite colour input required for conversion. |
| 2 | `synmachine.image.invert_colour` | `image: IMAGE -> image: IMAGE` | `invert_alpha=false` | Preserves alpha unless enabled; IEEE propagation. |
| 2 | `synmachine.image.opacity` | `image: IMAGE -> image: IMAGE` | `factor=1` (connectable non-negative FLOAT) | Creates straight alpha when absent and multiplies existing alpha; IEEE propagation. |
| 2 | `synmachine.image.stretch_contrast` | `image: IMAGE -> image: IMAGE` | `mode=PER_CHANNEL`, `lower_percentile=0`, `upper_percentile=100`, `ignore_non_finite=true`, `constant_policy=PRESERVE`, `channels=COLOUR` | Preserves alpha by default; optionally ignores non-finite samples and preserves their positions. |
| 2 | `synmachine.image.gamma` | `image: IMAGE -> image: IMAGE` | `gamma=1` (connectable positive FLOAT), `channels=COLOUR` | Preserves alpha by default; selected negative values become zero; NaN propagates and infinities follow power semantics. |
| 2 | `synmachine.image.add_scalar` | `image: IMAGE -> image: IMAGE` | `value=0` (connectable FLOAT), `channels=COLOUR` | Preserves alpha by default; IEEE propagation. |
| 2 | `synmachine.image.multiply_scalar` | `image: IMAGE -> image: IMAGE` | `value=1` (connectable FLOAT), `channels=COLOUR` | Preserves alpha by default; IEEE propagation. |
| 2 | `synmachine.image.divide_scalar` | `image: IMAGE -> image: IMAGE` | `value=1` (connectable FLOAT), `near_zero_policy=REPLACE_WITH_ZERO`, `epsilon=1e-6`, `channels=COLOUR` | Preserves alpha by default; explicit near-zero policy; other IEEE values propagate. |
| 3 | `synmachine.image.gaussian_blur` | `image: IMAGE -> image: IMAGE` | `kernel_width=3`, `kernel_height=3`, `sigma_x=0`, `sigma_y=0`, `border_mode=REFLECT_101` | Blurs non-alpha channels and preserves alpha; OpenCV propagates/combines non-finite values. |
| 3 | `synmachine.image.sharpen` | `image: IMAGE -> image: IMAGE` | `amount=1`, `sigma=1`, `threshold=0`, `border_mode=REFLECT_101` | Sharpens non-alpha channels and preserves alpha; no clipping; IEEE/OpenCV propagation. |
| 3 | `synmachine.image.add_noise` | `image: IMAGE -> image: IMAGE` | `noise_type=GAUSSIAN`, `amount=0.05`, `seed=0`, `monochrome=false`, `animate_seed=false`, `channels=COLOUR` | Preserves alpha by default; deterministic seed policy; existing non-finite values propagate. |
| 3 | `synmachine.image.posterize` | `image: IMAGE -> image: IMAGE` | `levels=4`, `clamp_input=true`, `channels=COLOUR` | Preserves alpha by default; NaN propagates and infinities are clipped only when `clamp_input` is enabled. |
| 4 | `synmachine.image.threshold` | `channel: CHANNEL -> channel: CHANNEL` | `mode=BINARY`, `threshold=0.5`, `maximum=1` (connectable FLOAT) | Preserves channel metadata; OpenCV-compatible threshold semantics including non-finite behavior. |
| 4 | `synmachine.image.canny` | `image: IMAGE -> channel: CHANNEL` | `low_threshold=0.1`, `high_threshold=0.3`, `aperture_size=3`, `l2_gradient=false`, `pre_blur_sigma=0` | Converts to luminance, rejects non-finite input, emits immutable float32 0/1 luminance-semantic mask; alpha is ignored. |
| 4 | `synmachine.image.convolve` | `image: IMAGE -> image: IMAGE` | `kernel=[[1]]` (nested finite odd matrix, max 15×15), `normalization=NONE`, `scale=1`, `delta=0`, `border_mode=REFLECT_101` | Filters non-alpha channels and preserves alpha; rejects non-finite kernel; image non-finite values follow OpenCV. |
| 4 | `synmachine.image.dilate` | `image: IMAGE -> image: IMAGE` | `kernel_shape=RECTANGLE`, `kernel_width=3`, `kernel_height=3`, `iterations=1`, `anchor_x=-1`, `anchor_y=-1`, `border_mode=REFLECT_101`, `process_alpha=false` | Operates on non-alpha channels unless enabled; native max semantics for non-finite values. |
| 4 | `synmachine.image.erode` | `image: IMAGE -> image: IMAGE` | Same IDs/defaults as Dilate | Operates on non-alpha channels unless enabled; native min semantics for non-finite values. |
| 4 | `synmachine.image.high_pass` | `image: IMAGE -> image: IMAGE` | `sigma=1`, `display_offset=0`, `gain=1`, `border_mode=REFLECT_101` | Processes non-alpha channels and preserves alpha; no clipping; IEEE/OpenCV propagation. |
| 4 | `synmachine.image.low_pass` | `image: IMAGE -> image: IMAGE` | `sigma=1`, `border_mode=REFLECT_101` | Blurs non-alpha channels and preserves alpha; IEEE/OpenCV propagation. |
| 5 | `synmachine.image.blend_images` | `a: IMAGE`, `b: IMAGE`, optional `mask: CHANNEL -> image: IMAGE` | `blend_mode=NORMAL`, `opacity=1` (connectable FLOAT, 0–1), `alpha_policy=COMPOSITE` | Requires equal dimensions/clock/colour descriptor; Normal uses straight-alpha source-over interpolation; mask must match shape/clock and non-finite mask values are rejected. |
| 5 | `synmachine.image.separate_channels` | `image: IMAGE -> channel_1..channel_4: CHANNEL` | none | Emits descriptor-backed read-only 2D views; missing outputs are `NoData`; non-finite values are unchanged. |
| 5 | `synmachine.image.combine_channels` | optional `channel_1..channel_4: CHANNEL -> image: IMAGE` | `target_colour_space=SRGB` (`RECOMPILE`) | Required count/semantics come from descriptor; requires equal dimensions/clock; never infers unlabeled semantics; non-finite values are unchanged. |
| 5 | `synmachine.image.to_luminance` | `image: IMAGE -> channel: CHANNEL` | none | Ignores alpha; preserves clock; finite RGB-family conversion input required. |
| 6 | `synmachine.utility.channel_statistics` | `channel: CHANNEL -> value: FLOAT` | `statistic=MEAN`, `percentile=50`, `ignore_non_finite=true` | Finite-only reduction by default; empty finite selection is recoverable; does not mutate input. |
| 6 | `synmachine.utility.remap_number` | `value: FLOAT -> value: FLOAT` | `input_minimum=0`, `input_maximum=1`, `output_minimum=0`, `output_maximum=1` (connectable FLOAT), `clamp=false` | Rejects equal input endpoints; IEEE scalar non-finite values propagate unless clamping maps infinities. |
| 6 | `synmachine.utility.float_to_integer` | `value: FLOAT -> value: INT` | `mode=ROUND` (`ROUND`, `FLOOR`, `CEIL`, `TRUNCATE`) | Rejects NaN/Inf as a recoverable finite-required error. |
| 6 | `synmachine.visualization.channel_display` | `channel: CHANNEL` | `preview_fps=30`, `max_dimension=800`, `fit_mode=CONTAIN`, `value_display_mode=NOMINAL_RANGE`, `show_histogram=false` | Demand-root visualizer; sanitizes and maps nominal range to bounded uint8 preview; no float32 array crosses IPC. |
| 7 | `synmachine.image.hold_image` | `image: IMAGE -> image: IMAGE` | `delay_frames=1` (1–600, `RECOMPILE`), `memory_limit_mb=256` (1–4096, `RECOMPILE`) | Retains immutable frame references in a bounded deque; outputs `NoData` until full; validates estimated retained bytes and publishes compact memory diagnostics. |

## Hold Image reset and memory contract

Hold Image resets on every scheduler source-component reset (stop/reload, seek, source loop, clock or
engine change), plan replacement when a state-significant parameter changes, and runtime-detected
shape or colour-descriptor changes. `delay_frames=1` appends the current processed frame after
selecting the output, so tick two outputs tick one. Estimated retained bytes are
`input.data.nbytes * delay_frames`; processing intermediates are not retained.

## Catalogue coverage clarification

The catalogue includes the three explicit scalar arithmetic definitions described in section 16.4.
Threshold remains grouped with the analysis filters in stable registry order.
