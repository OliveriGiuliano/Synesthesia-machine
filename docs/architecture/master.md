# Synesthesia Machine
## Software Architecture and Modular Implementation Design

**Document version:** 1.0  
**Date:** 29 July 2026  
**Target platform:** Windows 11, 64-bit, and Linux, 64-bit (glibc); ADR-0012
**Audience:** project owner, software architects, and coding agents implementing individual work packages

---

## 1. Purpose of this document

This document defines the product architecture for **Synesthesia Machine**, a Windows and Linux desktop application that transforms video and camera data into live MIDI note states through a visual node graph.

It is deliberately implementation-oriented. It fixes the major technical decisions, defines runtime and data contracts, specifies the behaviour of the initial node catalogue, and establishes repository boundaries. Active repository ownership is domain-based; the delivery sequence retained in section 19 is historical context only.

Current coding agents start with `AGENTS.md` and the routed playbooks under `docs/agent-playbooks`. The original delivery packets are preserved under `docs/history/v0-development/phase-packets` as acceptance history and are not active implementation boundaries.

This is an architectural baseline, not an immutable specification. Any deviation must be recorded as an Architecture Decision Record (ADR) before code is changed.

---

## 2. Product vision

Synesthesia Machine is a technical audiovisual instrument for experimental musicians. Users build graphs that:

1. acquire a video stream from a file or camera;
2. manipulate the image or derive numeric/channel data;
3. transform visual features into musical note states;
4. send those note states to a DAW, hardware device, virtual MIDI port, visualizer, or diagnostic synthesizer.

The graph is the primary interface and the primary persisted artifact. It must remain understandable, editable while running, deterministic when given deterministic inputs, and extensible with new node implementations.

The application is a **soft real-time** system. It should maintain low and bounded latency under normal workloads, but it is not a hard real-time system and cannot guarantee sample-accurate scheduling on Windows or CPython.

---

## 3. Scope and non-goals

### 3.1 Initial product scope

The first stable release shall include:

- a ChaiNNer/Fusion-style node editor;
- typed ports with unlimited output fan-out and one incoming connection per ordinary input;
- parameters that may optionally become graph inputs;
- video-file and live-camera sources;
- the image, utility, synesthesia, MIDI utility, output, visualization, and debug nodes specified in this document;
- graph save/load, autosave recovery, clipboard operations, undo, and redo;
- live performance metrics and per-node profiling;
- safe MIDI note lifecycle management, including automatic note-off and panic behaviour;
- a packaged standalone desktop build (Windows and Linux).

### 3.2 Explicit non-goals for version 1

The following are deferred:

- macOS support;
- a browser or cloud version;
- collaborative editing;
- a general DAW, sequencer, piano roll, or MIDI-effects workstation;
- audio/video recording and export rendering;
- arbitrary graph cycles;
- untrusted third-party plug-ins loaded at runtime;
- distributed processing;
- guaranteed GPU acceleration;
- sample-accurate MIDI timing;
- automatic synchronization of unrelated live sources;
- MIDI 2.0-specific features.

These omissions are intentional. They keep the first architecture testable and prevent the graph runtime from becoming a general multimedia framework before the core instrument is proven.

---

## 4. Principal architecture decisions

### AD-001 — Python is the application language

Use **64-bit CPython 3.12** as the supported interpreter for development and packaging.

Python is appropriate because the UI orchestration, graph model, node definitions, persistence, testing, and experimental node development benefit greatly from fast iteration. The expensive operations must not be implemented as Python pixel loops. They shall execute in NumPy, OpenCV, PyAV/FFmpeg, or other native code.

Python 3.12 is selected instead of the newest interpreter because it remains security-supported through October 2028 and has a mature binary-wheel ecosystem. This also avoids the compatibility risks the project has already encountered with Python 3.14. [R1]

### AD-002 — PySide6 and Qt Widgets are the UI stack

Use **PySide6 / Qt 6** with the Qt Widgets and Graphics View frameworks.

- `QMainWindow` provides the application shell, menus, toolbars, dock widgets, status bar, and native Windows behaviour.
- `QGraphicsScene` and `QGraphicsView` provide the zoomable node canvas.
- Custom `QGraphicsObject` items render nodes, ports, and connections.
- `QUndoStack` and command objects implement undo/redo.

PySide6 is the official Qt Python binding. Qt's Graphics View framework is designed to manage and display many custom 2D items in a scrollable scene, which matches a node editor directly. [R2][R3]

Do not use Electron, a browser canvas, or QML for version 1. Electron would add a second language and process ecosystem; QML would add a Python/QML boundary without providing a decisive benefit for this desktop graph editor.

### AD-003 — The UI and processing engine are separate processes

The final runtime consists of two cooperating processes:

1. **UI process** — owns all Qt objects, graph editing state, menus, previews, and user interaction.
2. **Engine process** — owns active runtime nodes, video decoding/capture, image arrays, graph execution, MIDI state generation, and profiling.

The engine process exists so that heavy native calls, decoder faults, and graph execution cannot freeze the UI event loop. The large image arrays remain entirely inside the engine process. Only control messages, metrics, errors, MIDI visualization summaries, and throttled preview frames cross the process boundary.

The in-process engine may be used behind the same interface for tests and diagnostics, but no UI code may depend on that implementation detail.

### AD-004 — CPU-first native image processing

Use **NumPy** and **OpenCV 4.13.x** for image processing. Use **PyAV 18.x**, which wraps FFmpeg, for file decoding and precise presentation timestamps. PyAV provides Windows wheels linked against FFmpeg. OpenCV provides optimized native image-processing operations and Windows video-I/O backends. [R4][R5][R6]

Version 1 is CPU-first. Do not make CUDA, OpenCL, DirectML, or a vendor-specific GPU API a requirement. The ordinary OpenCV Python wheels are not a dependable CUDA distribution, and moving many 500 × 500 buffers between CPU and GPU can cost more than it saves. A compute-backend abstraction is retained for later profiling-driven acceleration.

### AD-005 — The graph is a directed acyclic graph

Ordinary graph connections may not create cycles. Stateful temporal behaviour is implemented by explicit stateful nodes such as **Hold Image**, not by graph feedback loops.

This makes validation, scheduling, caching, state reset, error reporting, and reproducibility tractable. A future explicit Feedback node may introduce controlled single-tick feedback, but arbitrary cycles are out of scope.

### AD-006 — Source-driven, demand-limited execution

Each active source produces ticks. For each tick, the engine executes only nodes that are reachable from an active sink or pinned preview. Each node executes at most once per tick, regardless of output fan-out. Static subgraphs are cached until a parameter or connection invalidates them.

The initial scheduler is deterministic and sequential. OpenCV and NumPy may use native internal threading. Parallel execution of independent graph branches is deferred until profiling demonstrates that scheduler overhead is justified.

### AD-007 — Low latency is preferred over frame completeness

Live camera mode and real-time video playback use bounded queues and a **latest-frame-wins** policy. If processing falls behind, stale unprocessed frames are dropped rather than queued indefinitely. MIDI and previews must reflect recent visual input rather than old frames with growing latency.

A future offline mode may process every frame, but it is not part of the first release.

### AD-008 — MIDI graph values represent desired note state

Synesthesia nodes output immutable **MIDI note-state frames**, not a stream of repeated raw `note_on` messages. A note-state frame describes which notes should currently be active and their velocities.

Output nodes compare the new desired state with their previously sent state and generate the required `note_on` and `note_off` messages. This prevents note floods and centralizes stuck-note prevention.

### AD-009 — MIDI I/O uses Mido with python-rtmidi initially

Use **Mido** for MIDI message objects and port abstraction, with **python-rtmidi** as the first backend (WinMM on Windows, ALSA on Linux). Mido supports MIDI 1.0 messages and output ports; python-rtmidi wraps RtMidi and uses the Windows Multimedia MIDI API. [R7][R8]

The application connects to enumerated physical or virtual MIDI ports. It does not create its own virtual Windows MIDI driver in version 1. Users who need app-to-app routing may select a separately installed virtual loopback port. A later backend may target Windows MIDI Services when its deployment and Python integration are sufficiently stable.

### AD-010 — Runtime data is immutable by contract

Node inputs must be treated as read-only. A node may return a view when semantically safe, but it may not modify an input array in place. This is required because one output may feed multiple downstream nodes.

The first implementation favours correctness over aggressive buffer reuse. A buffer pool or ownership-aware in-place optimization may be added only after profiling and dedicated aliasing tests.

### AD-011 — Graph files are versioned JSON

Persist graphs as human-readable UTF-8 JSON with the extension `.synmachine.json`.

The file includes a schema version, application version, nodes, node versions, connections, parameter literals, positions, comments/groups, and selected document-level settings. Runtime state, decoded images, open handles, MIDI note state, and performance metrics are never saved.

### AD-012 — Packaging uses Qt's deployment path

Development uses `uv`, `pyproject.toml`, and a committed `uv.lock`. `uv` supports locked, synchronized project environments on Windows and Linux. [R9]

Release packaging uses `pyside6-deploy` in standalone mode first. The tool wraps Nuitka and produces a platform-native executable with its required files (`pysidedeploy.spec` on Windows, `pysidedeploy.linux.spec` on Linux). A one-file executable is not the initial target because startup extraction, antivirus false positives, and debugging are worse. [R10]

---

## 5. Quality attributes and measurable targets

### 5.1 Performance target

The phrase “complex graph at 60 fps” is not measurable without a reference graph. The project therefore defines a standard benchmark graph.

**Reference live graph:**

`Synthetic/Camera 500×500 RGB → Resize 500×500 → Gaussian Blur 5×5 → RGB-to-HSV → Separate Channels → Channel-to-Pitch → MIDI Note Visualizer`

with an additional preview branch:

`HSV image → Canny → Display Image`

On a representative mid-tier Windows 11 desktop, after a five-second warm-up:

- processed frame rate: median at least 60 fps when the source supplies 60 fps;
- processing latency: p95 below 16.7 ms for the graph execution portion;
- UI remains interactive while processing;
- no unbounded growth in queue depth or memory;
- dropped-frame count is visible when the target cannot be met.

This is a target, not a promise that every possible graph runs at 60 fps. A large Fourier transform, many full-resolution branches, or multiple camera streams may require downscaling or processing every Nth frame.

### 5.2 Reliability target

- Stopping, reloading, disconnecting, closing a graph, losing a MIDI port, or crashing the engine must trigger best-effort note-off/panic behaviour.
- A malformed graph file must never crash the application.
- A failing node marks itself and its dependent path as unavailable but does not terminate the UI.
- Autosave recovery must protect unsaved edits after a UI or engine crash.

### 5.3 Determinism target

With identical graph JSON, deterministic source frames, parameter values, and random seeds, node outputs must be numerically reproducible within documented floating-point tolerances.

### 5.4 Maintainability target

- The domain graph model has no dependency on Qt widgets.
- UI classes do not implement image algorithms.
- Node algorithms are independently unit-testable without launching Qt.
- Engine process messages are versioned dataclasses.
- Each package contains a short `README.md` describing its public contracts.

### 5.5 Usability target

The user should be able to create the first working graph without reading documentation:

`Load Video → Resize → Change Color Space → Separate Channels → Channel to Pitch → Generate Audio`

Invalid connections must be impossible to complete or must produce an immediate clear explanation.

---

## 6. High-level system architecture

```text
+--------------------------------------------------------------+
| UI PROCESS                                                   |
|                                                              |
| MainWindow                                                   |
|  + Node palette     + Graph canvas       + Inspector/metrics |
|  + Menus/commands   + GraphDocument      + Preview widgets   |
|                           |                                  |
|                    EngineClient API                           |
+---------------------------|----------------------------------+
                            | control/events
                            | preview shared memory
+---------------------------|----------------------------------+
| ENGINE PROCESS                                               |
|                                                              |
| EngineServer -> GraphCompiler -> ExecutionPlan               |
|       |               |              |                       |
|       |               |              + NodeRuntime instances |
|       |               + validation/type resolution           |
|       + source clocks / lifecycle / metrics                  |
|                                                              |
| PyAV file source  OpenCV camera  Image ops  Synesthesia      |
| MIDI output worker  Debug synth  Preview publisher           |
+--------------------------------------------------------------+
```

### 6.1 Process boundary rule

No Qt widget, `QPixmap`, `QImage`, or scene item crosses into the engine process. No live NumPy frame is sent through a normal multiprocessing queue. Preview frames use a bounded shared-memory transport and are converted to `QImage` only in the UI process.

### 6.2 Dependency direction

Dependencies point inward toward contracts:

```text
ui -> application services -> domain graph/contracts
engine -> domain graph/contracts
nodes -> runtime contracts + media/midi abstractions
persistence -> domain graph schemas
```

The domain layer may depend on Python standard-library types, NumPy typing declarations, and Pydantic persistence schemas, but never on Qt Widgets.

---
## 7. Runtime data model

### 7.1 Port types

The initial concrete port types are:

| Type ID | Python/runtime representation | Meaning |
|---|---|---|
| `IMAGE` | `ImageFrame` | Multi-channel image with explicit colour-space metadata |
| `CHANNEL` | `ChannelFrame` | Single 2D numeric channel normalized to a documented range |
| `FLOAT` | Python `float` | Continuous scalar |
| `INT` | Python `int` | Integral scalar |
| `BOOL` | Python `bool` | Boolean scalar |
| `COLOR` | `ColorValue` | RGBA colour, normalized to 0–1 |
| `MIDI_STATE` | `MidiStateFrame` | Desired active MIDI notes at a tick |
| `STRING` | Python `str` | Restricted use for paths/labels; not a general modulation type |

The graph also supports compile-time type variables used only by generic nodes:

- `T` for Pass Through;
- `T` shared by both data inputs and the output of Conditional;
- `MIDI_STATE...` for variadic MIDI Merge inputs;
- `T` / `T[]` for the Buffer value socket and the variadic Statistics `values` sockets, where
  a socket may carry either one element or a Buffer's `ValueArray`.

Generic ports must resolve to a concrete type during graph validation. `ANY` is not a persisted runtime type.

### 7.2 Compatibility and implicit conversion

Connections are allowed when types are identical, or when the compatibility matrix explicitly permits widening:

- `INT -> FLOAT` is allowed and inserts a compiler-owned scalar conversion.
- No other implicit *value* conversions are allowed.
- An array-typed input socket may also accept a single element of its own family
  (`FLOAT`/`INT` -> `SCALAR_ARRAY`, `IMAGE` -> `IMAGE_ARRAY`, `CHANNEL` -> `CHANNEL_ARRAY`);
  this changes the socket's shape, not the value, so the runtime treats the lone element as a
  one-item batch and no conversion is inserted. The reverse (an array into a single-value
  socket) stays rejected.
- Image colour-space conversion is always explicit through Change Colour Space.
- Image-to-channel conversion is explicit through Separate Channels.
- Channel-to-image conversion is explicit through Combine Channels.
- Float-to-int requires an explicit Math/Round node or an integer parameter input policy.

This avoids invisible transformations and makes experimental graphs reproducible.

### 7.3 `NoData`

A valid connection may temporarily have no value. Examples include:

- Hold Image before its history buffer is full;
- a paused source before the first frame;
- a node whose required upstream input failed;
- a disconnected camera during reconnection.

Represent this with a singleton `NoData`, not `None`. `None` may be a valid optional configuration value and must not be overloaded.

Unless a node explicitly handles `NoData`, the scheduler skips it and publishes `NoData` on its outputs for that tick.

### 7.4 `FrameContext`

Every dynamic execution tick carries a context object:

```python
@dataclass(frozen=True, slots=True)
class FrameContext:
    clock_id: UUID
    tick_index: int                  # starts at 1 for each source run
    source_frame_index: int | None   # decoder/camera sequence when known
    source_time_s: float             # PTS or capture-relative time
    received_monotonic_ns: int
    deadline_monotonic_ns: int | None
    is_realtime: bool
```

The Load Video node's second integer output is `tick_index`, which counts frames emitted into the graph after frame-selection rules. It is not the source file's frame number.

### 7.5 `ImageFrame`

```python
@dataclass(frozen=True, slots=True)
class ImageFrame:
    data: NDArray[np.float32]
    color_space: ColorSpace
    channel_names: tuple[str, ...]
    alpha_mode: AlphaMode
    context: FrameContext
    provenance: FrameProvenance
```

Rules:

- `data` shape is `(height, width, channels)`; grayscale images still use `CHANNEL`, not an H×W×1 image, unless an algorithm explicitly returns a grayscale image.
- Pixel values are `float32`.
- Ordinary RGB-family channels are normalized to `[0.0, 1.0]`.
- Hue is normalized to `[0.0, 1.0)` and wraps.
- Other colour spaces define channel ranges in a `ColorSpaceDescriptor`, but Combine/Separate always preserve the descriptor.
- The array must be C-contiguous unless a node documents that a read-only view is returned.
- Input arrays are read-only by contract.
- NaN and infinity are permitted transiently after Divide/Math-like operations, but nodes that require finite input must declare and enforce that requirement. Display and MIDI conversion nodes sanitize non-finite values.

The decision to use normalized float32 values avoids repeated 8-bit clipping, makes Multiply/Divide/Gamma behaviour consistent, and provides suitable input for optical flow and Fourier operations. Source conversion to float32 occurs once.

### 7.6 `ChannelFrame`

```python
@dataclass(frozen=True, slots=True)
class ChannelFrame:
    data: NDArray[np.float32]        # shape H x W
    semantic: ChannelSemantic
    nominal_min: float
    nominal_max: float
    cyclic: bool
    context: FrameContext
```

Most image-derived channels use `[0, 1]`. The nominal range is metadata used by visualizers, bins, and parameter UIs. Algorithms must not assume all channel values are clipped to the nominal range unless a Clamp node precedes them.

### 7.7 `ColorValue`

`ColorValue` stores normalized linear fields `r`, `g`, `b`, and `a`. UI colour pickers may operate in sRGB, but conversion to/from the runtime representation occurs at the boundary.

### 7.8 `MidiStateFrame`

```python
@dataclass(frozen=True, slots=True)
class MidiNoteKey:
    channel: int   # 0..15 internally, shown as 1..16 in UI
    note: int      # 0..127

@dataclass(frozen=True, slots=True)
class MidiStateFrame:
    notes: Mapping[MidiNoteKey, int]  # velocity 1..127
    context: FrameContext
    source_node_id: UUID
```

A zero velocity is never stored; absence means inactive. Duplicate notes created by a node are merged using maximum velocity before the frame is emitted.

### 7.9 Scale definitions

A scale is represented by:

```python
@dataclass(frozen=True, slots=True)
class ScaleDefinition:
    id: str
    display_name: str
    pitch_class_mask: tuple[bool, ...]  # length 12
```

A musical selector combines:

- root pitch class, C through B;
- scale definition;
- inclusive MIDI minimum and maximum, 0 through 127.

The resulting ordered list of allowed MIDI notes is generated by filtering the inclusive range by pitch class. If no notes remain, graph validation fails.

Built-in scales: Chromatic, Major/Ionian, Natural Minor/Aeolian, Harmonic Minor, Melodic Minor, Dorian, Phrygian, Lydian, Mixolydian, Locrian, Major Pentatonic, Minor Pentatonic, Whole Tone, Diminished, and Custom 12-step mask.

---

## 8. Node definition and runtime contracts

### 8.1 Separation of metadata and execution

Every node type has two cooperating classes:

1. `NodeDefinition`: immutable metadata used by the editor, graph validator, serializer, and compiler.
2. `NodeRuntime`: engine-only object that performs work for one graph node instance.

A node implementation must not import UI classes.

### 8.2 Stable node identity

Every node type has a permanent reverse-domain-like type ID, for example:

- `synmachine.input.load_video`
- `synmachine.image.resize`
- `synmachine.synesthesia.channel_to_pitch`

The display name may change; the type ID may not. Each type also has an integer `implementation_version` used for graph migrations.

Each graph node instance has a UUID that remains stable across saves, reloads, copy/paste remapping, and structural runtime plan swaps.

### 8.3 Definition schema

A definition includes:

- type ID and implementation version;
- display name, category, short description, and icon key;
- ordered input-port specifications;
- ordered output-port specifications;
- ordered parameter specifications;
- execution kind: `SOURCE`, `STATELESS`, `STATEFUL`, `SINK`, or `VISUALIZER`;
- cache policy;
- whether the node is realtime-safe;
- reset and migration hooks;
- optional shape and colour-space constraints for runtime validation.

### 8.4 Parameter specification

A parameter includes:

- stable parameter ID;
- label and help text;
- value type;
- default value;
- validation constraints;
- UI editor hint;
- `connectable` flag;
- optional port type when connectable;
- update mode: `LIVE`, `RECOMPILE`, or `RESTART_SOURCE`.

For a connectable parameter, the node editor shows the normal editor and a socket on the same row. When connected:

- the graph input value overrides the literal value;
- the literal remains saved as a fallback;
- the editor is visually disabled but still shows the fallback;
- the effective live value may be displayed separately;
- disconnecting immediately restores the literal.

File paths, camera device identifiers, kernels, and most enums are not connectable in version 1. Numeric values, booleans, colours, and selected integer parameters may be connectable.

### 8.5 Runtime API

Stateless/stateful nodes expose:

```python
class NodeRuntime(Protocol):
    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, RuntimeValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]: ...

    def reset(self, reason: ResetReason) -> None: ...
    def close(self) -> None: ...
```

Sources expose start, pause, resume, stop, reload, and close commands plus a callback/mailbox into the engine scheduler.

Sinks must return quickly. Blocking I/O belongs in a dedicated worker owned by the sink runtime.

### 8.6 State lifecycle

Stateful nodes own state per graph-node UUID. State is reset when:

- the source is stopped or reloaded;
- playback seeks;
- an incompatible parameter changes;
- the node type/version changes;
- the node is removed;
- the relevant source clock changes;
- an engine restart occurs.

During a hot plan swap, unchanged stateful nodes may retain state only when the compiler confirms identical node type, implementation version, source clock, and state-significant parameters.

### 8.7 Error contract

Node errors are structured:

```python
@dataclass(frozen=True, slots=True)
class NodeExecutionError:
    node_id: UUID
    code: str
    message: str
    details: str | None
    recoverable: bool
    tick_index: int | None
```

Expected errors—missing file, unavailable camera, invalid dimensions, mismatched channel shapes, unavailable MIDI port—must not be raised as unhandled exceptions. Unexpected exceptions are caught at the engine boundary, logged with tracebacks, converted into `NodeExecutionError`, and cause `NoData` downstream for that tick.

Repeated identical errors are rate-limited in the UI.

---

## 9. Graph model and validation

### 9.1 Domain objects

The persisted graph model contains:

- `GraphDocument`;
- `NodeModel` instances;
- `ConnectionModel` instances;
- comments/groups and their geometry;
- document settings;
- viewport state, saved separately as optional UI state.

The runtime never mutates the UI's live graph model. The UI serializes an immutable `GraphSnapshot` and sends it to the engine.

### 9.2 Connection rules

- Ordinary input ports accept zero or one connection.
- Output ports accept any number of connections.
- Variadic inputs are represented as a port group with compiler-generated slots.
- Dragging from input to output or output to input is accepted; the editor normalizes direction.
- Releasing over an incompatible port rejects the connection and shows the reason.
- Connecting a new edge to an occupied ordinary input replaces the old edge as one undoable command.

### 9.3 Validation pipeline

Validation runs in this order:

1. JSON/schema validation and migrations.
2. Node type/version lookup.
3. Unique ID and port existence checks.
4. Parameter validation.
5. Connection cardinality and type compatibility.
6. Generic type unification.
7. Cycle detection.
8. Required-input checks.
9. source-clock analysis.
10. sink reachability and warnings.
11. topological ordering and execution-plan construction.

Errors block engine activation. Warnings do not.

### 9.4 Clock-domain rules

Every dynamic value carries a `clock_id`. Static values have no clock.

A node may combine multiple dynamic inputs only when they share the same clock. This naturally supports branches from one video source, including current and delayed images. Combining unrelated cameras or a camera and independently playing video is rejected unless a future synchronization node explicitly establishes a new clock domain.

Multiple independent source components may run in one graph when they do not converge.

### 9.5 Structural edits while running

Parameter changes marked `LIVE` are sent as small engine commands and applied between ticks.

Structural edits are debounced for approximately 100 ms, then the UI sends a new snapshot. The engine:

1. finishes or abandons the current tick at a safe boundary;
2. validates and compiles the new plan off the active path;
3. preserves compatible runtime instances where allowed;
4. atomically swaps plans;
5. closes removed runtimes;
6. reports success or validation errors.

If compilation of the new snapshot fails, the engine stops (sources stop, MIDI
outputs panic, previews clear, state `STOPPED`) instead of keeping the previous
plan running; it resumes automatically when the graph becomes valid again
(ADR 0013).

### 9.6 Demand roots

A node is executed only when reachable upstream from at least one demand root:

- Send MIDI to MIDI Output;
- Generate Audio;
- Note Visualizer;
- Display Image Data;
- a preview explicitly pinned by the UI;
- a source/node diagnostic capture requested by the profiler.

Disconnected experimental branches consume no processing time.

---

## 10. Execution engine

### 10.1 Compiler output

The graph compiler produces an immutable `ExecutionPlan` containing:

- topologically ordered runtime node IDs;
- resolved concrete port types;
- input bindings;
- parameter bindings and literal fallbacks;
- source components and clock IDs;
- demand-root reachability;
- static versus dynamic node classification;
- state-retention compatibility keys;
- preview publication points;
- graph revision number.

### 10.2 Tick execution

For each source tick:

1. Record source timing and queue-delay metrics.
2. Insert source outputs into the per-tick value cache.
3. Traverse the compiled node order for the affected source component.
4. Skip nodes not reachable from active demand roots.
5. Resolve each input from the cache or static cache.
6. Apply connected parameter values over literals.
7. Propagate `NoData` unless the node handles it.
8. Execute the node and record duration, allocations when available, and errors.
9. Cache each output once for all fan-out consumers.
10. Publish sink inputs, requested previews, and metrics.
11. Release per-tick references promptly.

### 10.3 Static cache

Nodes whose outputs depend only on literal/static inputs—Number, Color, fixed kernels, and static math chains—are evaluated when the plan is compiled or invalidated. Their outputs are reused across ticks.

### 10.4 Backpressure

Each source owns a bounded mailbox of at most two pending frames.

- When idle, the scheduler accepts the next frame.
- When busy and one pending frame exists, a newer live frame replaces it.
- Replacement increments a dropped-before-processing counter.
- File playback remains paced by presentation timestamp; it does not slow down to process every frame in real-time mode.

The source's “Process every Nth frame” rule is applied before entering the engine mailbox. A value of `N=1` processes every frame, `N=2` processes frames 1, 3, 5, and so on, while playback timing remains real-time. The UI should use this wording rather than the ambiguous “Skip nth frame.”

### 10.5 Engine IPC

Use `multiprocessing` with the `spawn` start method on Windows and POSIX alike.

Control path:

- one duplex `multiprocessing.Connection` or pair of pipes for commands and acknowledgements;
- one bounded event queue for asynchronous errors and metrics;
- only trusted internal dataclasses are pickled;
- every message includes a protocol version and graph revision where relevant.

Preview path:

- shared-memory slots created and owned by the UI client;
- image/channel slots are keyed by producing node and output port and shared by all fan-out cables;
- engine writes a resized uint8 preview plus a monotonically increasing sequence number;
- compact scalar value previews use bounded control messages rather than shared memory;
- UI polls at a capped rate and copies only when the sequence changes;
- slots are recreated when dimensions change;
- cleanup is idempotent after either process crashes.

Do not send full-rate float32 image arrays through queues.

### 10.6 Preview policy

- Every connected `IMAGE` or `CHANNEL` producer port may publish one coalesced preview for all of its
  visible connection pills. `INT` and `FLOAT` producer ports may publish compact text previews.
- Image/channel previews use a fixed application-wide cap of 30 publications per second per producer
  port and a maximum dimension of 800 pixels. Scalar previews use a 60 Hz cap.
- Display Image Data and Channel Display nodes select which producer preview feeds the image dock;
  unrelated producer previews update their cable pills without replacing the dock contents.
- Preview publication happens after node processing and does not block the graph if the UI has not consumed the previous preview.
- Conversion to uint8 applies finite-value sanitization and the colour-space display transform.
- Per-connection preview visibility is persisted and undoable. A visible pill can demand an otherwise
  unused producer; hiding it removes that demand unless another sink or visualizer needs the chain.

### 10.7 Process supervision

The UI starts and supervises the engine. If the engine exits unexpectedly:

- the UI remains open;
- the graph is marked stopped;
- the crash log path is shown;
- the MIDI watchdog performs best-effort panic if it owns any port in the UI process; otherwise the restarted engine opens the port and sends panic before resuming;
- the user may restart the engine without reopening the document.

---
## 11. Media subsystem

### 11.1 Load Video

Use PyAV for container opening, stream selection, decoding, and frame timestamps. PyAV exposes frame presentation timestamps and time bases and provides precise access to FFmpeg-backed media. [R4][R11]

#### Inputs and outputs

- No graph input.
- Output `image: IMAGE`.
- Output `processed_index: INT`.

#### Parameters

- `file_path: path`, not connectable.
- `process_every_nth_frame: int`, minimum 1, live-updateable after a source reset.
- `loop: bool`, default false.
- `stream_index: int`, hidden unless multiple video streams exist.

#### Commands

Play, Pause, Stop, Reload. Seeking is deferred from the minimum vertical slice but the source API must reserve it.

#### Behaviour

- Opening reads metadata without starting playback.
- Play starts from the current position; Stop resets to the start and resets emitted `processed_index`.
- Pause preserves position and node states; Stop resets the source component's stateful nodes.
- Real-time pacing uses presentation timestamps relative to a monotonic playback anchor.
- Variable-frame-rate video is paced from actual timestamps, not nominal FPS.
- Decode occurs on a source-owned thread and uses a small bounded decoded-frame queue.
- PyAV frame conversion requests RGB and immediately converts to normalized float32.
- When looping, the tick index resets to 1 and the component receives a loop reset reason.
- At natural non-looping end, the last presented frame is processed before a source-ended reset
  silences that source component and clears its stateful runtimes.
- Missing/corrupt frames produce warnings and are skipped; repeated decode failure stops the source.

### 11.2 Load Camera

Use OpenCV `VideoCapture` with an explicit platform backend preference: Media Foundation with a DirectShow fallback on Windows and V4L2 on Linux. OpenCV's Video I/O layer supports multiple capture backends behind `VideoCapture`. [R6]

#### Parameters

- stable camera device identifier where available;
- requested width and height;
- requested FPS;
- backend preference: Auto, Media Foundation, DirectShow;
- process every Nth captured frame;
- reconnect automatically, default true.

#### Behaviour

- Device enumeration is performed in a background probe and cached.
- Device identities and display labels cross to the UI through the engine-owned device catalogue;
  the editor persists the ID and shows an unavailable saved selection without replacing it.
- Opening and capture never occur on the UI thread.
- The source reports actual negotiated width, height, and FPS.
- Capture buffers are kept minimal; stale frames are discarded.
- Camera timestamps use `perf_counter_ns()` at receipt because many webcams do not expose dependable PTS.
- On disconnection, publish `NoData`, report status, and retry with exponential backoff capped at five seconds.
- A camera-specific property panel may be added later; version 1 only exposes portable settings.

### 11.3 Colour spaces

Initial supported colour spaces:

- RGB;
- RGBA;
- HSV;
- HSL;
- CIE Lab;
- YCrCb;
- grayscale channel.

The exact channel names and normalized ranges live in one registry. Node implementations must never hand-code channel indexes without consulting the descriptor.

### 11.4 Image-size policy

- Width and height must be positive integers.
- General maximum is 8192 per dimension to prevent accidental allocation attacks; nodes may define lower limits.
- Resize supports explicit width/height and optional aspect-ratio preservation.
- Crop parameters are normalized or pixel-based via an enum; the initial default is normalized bounds so graphs adapt to source resolution.
- Nodes with multiple image/channel inputs require matching dimensions unless they document a resize policy. Blend Images defaults to rejecting mismatches rather than silently resizing.

---

## 12. MIDI and audio subsystem

### 12.1 MIDI output service

`MidiOutputService` owns port enumeration, opening, closing, note-state diffing, panic, and a dedicated sender thread.

The engine sends the service desired `MidiStateFrame` values through a latest-state mailbox. The sender does not queue an unbounded history of frame states.

For each update:

- notes absent from the new state receive `note_off`;
- newly present notes receive `note_on`;
- notes whose velocity changed follow the output node's velocity-update policy;
- messages are sorted so note-offs are sent before note-ons at the same update;
- the tracked sent-state is updated only after successful sends.

### 12.2 Velocity update policies

The Send MIDI node exposes:

- `Ignore while held` — default; velocity is used only at note onset.
- `Retrigger` — send note-off followed by note-on when velocity changes beyond a threshold.
- `Repeat note-on` — send note-on with the new velocity without an explicit note-off; provided for devices that interpret it usefully.

The threshold defaults to four MIDI velocity units to avoid noise.

### 12.3 Panic and lifecycle safety

On Stop, graph close, node deletion, port switch, engine shutdown, or output error:

1. send explicit note-off for every tracked active note;
2. send Control Change 123 (All Notes Off) on channels used by the node;
3. clear tracked state;
4. close the port when appropriate.

A visible global **Silence All Outputs** action performs the same operation for every output and
debug synth.

### 12.4 Port handling

- Enumerate outputs asynchronously.
- Persist the selected port by engine-owned backend ID, display its separate friendly label, and
  continue accepting unambiguous friendly names saved by earlier versions.
- Do not auto-connect to an arbitrary replacement.
- When the selected port disappears, panic if possible, mark the node unavailable, and periodically refresh enumeration.
- UI channel numbers are 1–16; runtime channel numbers are 0–15.

### 12.5 MIDI Merge

Add an essential variadic utility node not present in the original list:

**MIDI Merge** accepts two or more `MIDI_STATE` inputs and outputs one state. Duplicate note/channel keys take the maximum velocity. All inputs must share a clock. This node is required to combine multiple synesthesia branches before a single output.

### 12.6 Generate Audio debug synthesizer

Use `sounddevice`/PortAudio with a callback-driven stereo output stream. The Windows wheel includes the necessary PortAudio binary; on Linux the runtime loads the system PortAudio (`libportaudio2`). The audio callback must not allocate, block, log, or perform graph operations. [R12][R13]

The synth is intentionally simple:

- sine, triangle, or square waveform;
- polyphonic voices keyed by MIDI note/channel;
- equal-tempered frequency conversion using A4 = 440 Hz;
- velocity-scaled amplitude;
- short attack and release envelopes to prevent clicks;
- master volume;
- fixed maximum voices, default 32, stealing the quietest/oldest voice;
- limiter or conservative normalization to prevent clipping.

The graph thread publishes a coalesced desired-note snapshot to a dedicated audio-output service.
Device opening, replacement, and closure occur on that service thread; a failed configuration is
latched until the selection changes or the node is disabled. The audio callback reads the latest
snapshot and evolves voices. It must not consume raw graph objects.

This node is diagnostic, not a production synthesizer.

---

## 13. User-interface architecture

### 13.1 Main window layout

Use a `QMainWindow` with:

- menu bar: File, Edit, View, Graph, MIDI, Help;
- optional compact toolbar for transport and panic;
- left dock: searchable node library, grouped by category;
- central widget: graph view;
- right dock: inspector and node documentation, collapsible;
- bottom dock: diagnostics/profiler/log, hidden by default;
- status bar: engine status, source FPS, processed FPS, latency, dropped frames, CPU, RAM, and MIDI port state.

The original vision places the primary node panel on the left and graph canvas centrally. The right inspector is added because detailed parameters, errors, and profiling data should not make every node excessively tall.

### 13.2 Visual language

- dark neutral background;
- restrained accent colours used primarily for data types and selection;
- rounded node panels with subtle borders, not heavy shadows;
- compact typography with a clear hierarchy;
- consistent port colours by resolved type;
- animations limited to short connection/selection feedback;
- no constantly moving decorative effects.

All colours come from a theme token file. No widget may hard-code RGB values.

### 13.3 Node item composition

Each node item contains:

- header: icon, display name, status indicator, optional processing-time badge;
- input rows;
- connectable parameter rows and literal editors;
- ordinary parameter rows;
- output rows;
- compact error/warning indicator;
- compact live image or scalar pills on eligible connections.

Node graphics and domain state are separate. The scene item observes a `NodeViewModel`; it does not mutate the graph model directly.

### 13.4 Port interaction

- Start drag from any port.
- Compatible targets highlight; incompatible targets dim.
- A temporary Bézier cable follows the pointer.
- Releasing on empty canvas opens a filtered node search showing nodes with a compatible opposite port. Selecting a node inserts and connects it in one undoable command.
- Hovering shows type and description.
- Connected connectable parameters display the live-input state clearly.

### 13.5 Canvas interaction

- middle mouse or Space+left-drag pans;
- wheel zooms under cursor;
- marquee selection;
- Delete removes selection;
- Ctrl+D duplicates;
- F frames selection; Home frames all;
- optional grid snapping, disabled by default;
- alignment guides and tidy-selection command;
- comments/groups for organization;
- dropping an operating-system video file creates a Load Video node at the drop
  position with its file path already set (one undo step);
- dropping a saved graph file opens it through the same flow as File → Open
  (including the unsaved-changes confirmation);
- minimap is deferred unless large-graph testing demonstrates need.

### 13.6 Node creation

Nodes can be created by:

- drag from the left library;
- drop a video file from the operating system;
- double-click or Enter from the library;
- right-click/Space on empty graph to open fuzzy search;
- dropping a connection on empty graph to open type-filtered search.

Search indexes display name, category, aliases, and description keywords.

### 13.7 Transport ownership

Transport buttons operate on selected source nodes. If exactly one source exists, it is automatically targeted. If several exist and none is selected, the UI requests a source selection rather than controlling all unexpectedly.

### 13.8 UI update throttling

The engine may process at 60+ fps, but the UI must not repaint all metrics at that rate.

- status metrics: at most 10 Hz;
- per-node badges: at most 4 Hz;
- image previews: default 30 Hz;
- note visualizer: at most 60 Hz, with coalesced states;
- log updates: batched.

### 13.9 Accessibility and scaling

- honour Windows display scaling;
- test at 100%, 125%, 150%, and 200%;
- all actions have accessible names and keyboard shortcuts where practical;
- type distinction must not rely on colour alone; port shape/icon also indicates class;
- font sizes use Qt point sizing, not fixed pixels.

---

## 14. Persistence, commands, and document lifecycle

### 14.1 Graph JSON structure

Top-level structure:

```json
{
  "schema_version": 1,
  "application_version": "0.1.0",
  "document_id": "uuid",
  "nodes": [],
  "connections": [],
  "groups": [],
  "document_settings": {},
  "ui_state": {}
}
```

Each node stores:

- instance UUID;
- type ID and implementation version;
- position and optional size;
- literal parameters;
- node-specific persisted UI state;
- optional user label;
- collapsed state.

Connections reference source node/port and destination node/port by stable IDs.

### 14.2 File paths

- Save relative media paths in normalized POSIX form (ADR-0012).
- Prefer paths relative to the graph file when the media lies inside the graph's directory tree.
- Preserve an absolute fallback and a media fingerprint when feasible.
- On missing files, offer locate/relink; do not silently substitute by filename.

### 14.3 Atomic save

Serialize to a temporary sibling file, flush, then use atomic replacement. Keep one `.bak` generation after successful save.

### 14.4 Autosave

- Autosave dirty documents every 60 seconds after inactivity and before risky operations.
- Store recovery files in the user's local application-data directory, not beside the original.
- On startup, offer recovery when an autosave is newer than the last explicit save.
- Delete recovery only after successful explicit save or deliberate discard.

### 14.5 Undo/redo

Use command objects with `redo()` and `undo()` against `GraphDocument`:

- add/delete/move/resize node;
- add/remove/replace connection;
- parameter change;
- paste/duplicate;
- group/comment operations;
- rename/collapse.

Continuous drags and slider changes merge into one command when they share a merge key and occur within a short interval.

Loading a document clears the undo stack. Saving marks the current stack index as clean.

### 14.6 Clipboard

Clipboard content is a JSON fragment with its own version. Paste generates new UUIDs, remaps internal connections, offsets positions, and excludes runtime state. External file paths remain unchanged and may become invalid; pasted nodes show normal missing-file errors.

### 14.7 Migrations

Graph schema migrations are pure functions from one versioned JSON dictionary to the next. Node-specific migrations transform parameter names/values based on node implementation versions.

Never load older graphs by scattering compatibility branches throughout runtime node code.

---

## 15. Diagnostics and performance monitoring

### 15.1 Metrics collected

Global:

- source input FPS;
- processed FPS per source component;
- preview FPS;
- end-to-end age of processed frame;
- frames skipped by Process Every N;
- frames dropped by backpressure;
- engine CPU percentage;
- process and system RAM;
- queue/mailbox occupancy;
- engine uptime and restarts.

Per node:

- last duration;
- exponential moving average;
- p50, p95, and maximum over a rolling window;
- invocation count;
- error count;
- output shape/type summary;
- estimated bytes held by output where inexpensive to calculate.

Hardware:

- CPU logical/physical count and utilization;
- total/available RAM through psutil;
- NVIDIA VRAM through optional NVML support when present;
- GPU metrics show “unavailable” rather than failing on unsupported hardware.

psutil supports Windows and Linux CPU, memory, process, disk, and other system metrics. [R14]

### 15.2 Profiler UI

Provide:

- sortable node timing table;
- graph heatmap mode that maps normalized node cost to border intensity;
- pause/freeze metrics;
- reset statistics;
- copy diagnostic report;
- performance warning when one node exceeds a configurable fraction of the frame budget.

### 15.3 Logging

Use Python `logging` with:

- rotating application log files;
- separate UI and engine logger names;
- session ID, process ID, graph revision, and node ID fields;
- INFO default, DEBUG opt-in;
- exception tracebacks in files, concise messages in UI;
- privacy rule: do not log frame pixel data; paths may be included because this is a local technical tool, but diagnostic export should offer path redaction.

### 15.4 Diagnostic bundle

A Help → Export Diagnostic Bundle action creates a zip containing:

- application/build versions;
- dependency versions;
- Windows version and hardware summary;
- recent logs;
- current graph with absolute file paths optionally redacted;
- performance snapshot;
- no video frames unless the user explicitly opts in.

---
## 16. Initial node catalogue specification

This section defines semantic behaviour. Exact visual layout belongs to the UI phase, and low-level OpenCV calls belong to node implementation modules.

### 16.1 Common image-node rules

- Unless stated otherwise, one `IMAGE` input produces one `IMAGE` output with the same clock and colour-space metadata.
- A node must document whether alpha is processed, preserved, created, or rejected.
- Parameters are validated before execution and clamped only when the specification explicitly says so.
- Border handling for spatial filters is an enum: Reflect 101 (default), Reflect, Replicate, Constant, Wrap where supported.
- Kernel sizes that must be odd are normalized in the UI but rejected by the runtime if invalid data reaches it.
- Image nodes preserve normalized float32 values and do not clip unless clipping is part of their stated operation.
- Every node has unit tests for one-channel or alpha behaviour where applicable.

### 16.2 Input nodes

#### Load Video

Specified in section 11.1.

#### Load Camera

Specified in section 11.2.

### 16.3 Image Dimension nodes

#### Resize

Inputs: `image: IMAGE`.  
Output: `image: IMAGE`.

Parameters:

- width and height, connectable integers;
- preserve aspect ratio;
- fit mode: Stretch, Contain, Cover;
- interpolation: Auto, Nearest, Linear, Area, Cubic, Lanczos.

Auto chooses Area when downscaling and Linear when upscaling. Contain may add transparent/black padding according to image alpha; Cover crops centrally after scaling.

#### Crop

Inputs: `image: IMAGE`.  
Output: `image: IMAGE`.

Parameters:

- coordinate mode: Normalized or Pixels;
- left, top, right, bottom;
- out-of-bounds policy: Clamp (default), Pad Constant, Error;
- pad colour.

Normalized bounds are in `[0,1]` and adapt to resolution. `right` and `bottom` are exclusive in pixel mode. Empty crops produce a recoverable error, not a zero-sized array.

### 16.4 Image Adjustment nodes

#### Brightness

Adds a connectable float offset to RGB/luminance-like channels. Default 0.0. Alpha is preserved. The node may produce values outside `[0,1]`; use Clamp when required.

#### Contrast

Applies `(x - pivot) * factor + pivot`. Factor is connectable, default 1.0. Pivot is connectable, default 0.5. Alpha is preserved.

#### Clamp

Connectable minimum and maximum, default 0 and 1. Applied to selected channels; alpha is preserved.

#### Colour Levels

Parameters: input black, input white, gamma, output black, output white, independently connectable where useful. Normalize and clamp selected channels to 0..1 before applying gamma and mapping to output levels. Reject `input_white <= input_black` and gamma <= 0.

#### Hue

Rotates hue by a normalized turn value where 1.0 is 360 degrees. Convert RGB-family input to HSV internally and back to the original RGB-family colour space. For HSV/HSL input, modify the hue channel directly. Preserve alpha.

#### Saturation

Multiplies saturation by a non-negative factor. Convert as required. Default 1.0.

#### Invert Colour

For normalized non-alpha colour channels, output `1 - x`. Alpha is preserved.

#### Stretch Contrast

Maps selected channels from observed or percentile bounds to `[0,1]`.

Parameters:

- mode: Per Channel or Combined;
- lower percentile, default 0;
- upper percentile, default 100;
- ignore non-finite values;
- constant-channel policy: Preserve (default), Zero, Midpoint.

#### Threshold

Supports Binary, Binary Inverse, Truncate, To Zero, and To Zero Inverse. Threshold and maximum are connectable. For multi-channel images, mode is Per Channel or Luminance; luminance mode outputs `CHANNEL` and is therefore implemented as a separate definition variant if necessary to keep port types fixed. The initial node should take `CHANNEL` input and output `CHANNEL`; an image threshold convenience node may be added later.

#### Gamma

Applies `max(x, 0) ** gamma` to selected channels. Gamma must be positive. Negative inputs are clamped to zero for this operation and generate a low-severity diagnostic count.

#### Add / Divide / Multiply

These are image arithmetic nodes with two compatible image/channel inputs or one data input plus a connectable scalar parameter. To avoid ambiguous polymorphic ports, implement explicit definitions:

- Image Add Scalar;
- Image Multiply Scalar;
- Image Divide Scalar;
- Blend/Add Images where two images are required.

Division policy for near-zero divisor: Replace With Zero (default), Clamp Epsilon, or Error. The common Math node covers scalar arithmetic.

### 16.5 Image Filter nodes

#### Gaussian Blur

Parameters: kernel width/height, sigma X/Y, border mode. Width/height are odd positive integers. Zero sigma lets OpenCV derive it from kernel size.

#### Sharpen

Parameters: amount, radius/sigma, threshold. Implement unsharp masking: `image + amount * (image - blurred)`, with optional threshold suppressing small differences. No implicit clipping.

#### Add Noise

Parameters:

- type: Gaussian, Uniform, Salt and Pepper;
- amount/standard deviation;
- seed;
- monochrome versus independent channels;
- animate seed boolean.

When animate seed is false, identical input and seed produce identical output. When true, derive a deterministic per-tick seed from the node seed and tick index.

#### Posterize

Quantizes selected normalized channels to `levels`, minimum 2. Formula: `round(x*(levels-1))/(levels-1)` after optional clamp to `[0,1]`.

#### Canny Edge Detection

Input: `IMAGE` or preferably `CHANNEL`; choose one fixed definition. Initial implementation takes `IMAGE`, converts to luminance when needed, and outputs `CHANNEL` with values 0 or 1.

Parameters: low threshold, high threshold, aperture size 3/5/7, L2 gradient boolean, optional pre-blur sigma.

#### Convolve

Input and output `IMAGE`.

Parameters:

- user-editable odd 2D kernel;
- normalization mode: None, Sum to One, Absolute Sum to One;
- scale and delta;
- border mode.

Kernel editor has maximum 15×15 in version 1. Persist as nested numeric arrays. Reject non-finite kernels.

#### Dilate / Erode

Parameters: kernel shape Rectangle/Ellipse/Cross, kernel width/height, iterations, anchor, border mode. Operate on every non-alpha channel unless configured otherwise.

#### High Pass

`image - GaussianBlur(image, sigma)`, optionally shifted by 0.5 for display. Parameters: sigma, display offset, gain. Preserve float range.

#### Low Pass

Alias-like dedicated node using Gaussian blur with simplified sigma/radius controls. It may share the implementation with Gaussian Blur but has its own stable type ID and UX.

### 16.6 Image Utility nodes

#### Flip

Mode: Horizontal, Vertical, Both.

#### Rotate

Parameters:

- angle degrees, connectable float;
- centre X/Y normalized, connectable;
- expand canvas boolean;
- interpolation;
- border mode and colour.

Positive angles rotate counter-clockwise. Repeated modulation always rotates the current input relative to its own orientation; graph-level feedback is not implied.

#### Blend Images

Inputs: `a: IMAGE`, `b: IMAGE`, optional `mask: CHANNEL`.  
Output: `image: IMAGE`.

Parameters: blend mode Normal, Add, Multiply, Screen, Difference, Lighten, Darken; opacity connectable; alpha policy.

Images must have equal dimensions and compatible colour spaces. No silent conversion or resize. Normal mode computes alpha-aware interpolation. Mask multiplies opacity and must share dimensions/clock.

#### Change Colour Space

Input/output `IMAGE`; target colour space is a non-connectable enum. The node updates channel descriptors. Grayscale target should use a separate Image to Luminance node returning `CHANNEL`, because output types are fixed.

### 16.7 Image Channel nodes

#### Separate Channels

Because output labels vary by selected colour space, use a definition with three fixed outputs: `channel_1` through `channel_3`, plus metadata labels displayed dynamically. Sources never carry an alpha channel, so the fourth (alpha) descriptor channel is never surfaced (ADR-0015).

The node outputs read-only 2D views when possible. The UI labels sockets R/G/B/A, H/S/V, L/a/b, etc., based on the latest static colour-space information; runtime metadata remains authoritative.

#### Combine Channels

Inputs: up to three `CHANNEL` ports. Parameter: target colour space (RGBA is not offered; the sources never carry an alpha channel). Required input count comes from the target descriptor. All channels must share dimensions and clock. Output is `IMAGE`, preserving channel values and declaring the chosen colour space.

This is “colour-space agnostic” in the sense that the same implementation uses descriptors rather than RGB-specific code; it does not infer arbitrary semantics from unlabeled channels.

### 16.8 General Utility nodes

#### Conditional

Inputs: `condition: BOOL`, `if_true: T`, `if_false: T`.  
Output: `value: T`.

The compiler resolves `T`. Version 1 evaluates upstream branches normally; the node selects the already available value. Lazy branch execution is deferred.

#### Number

Output type is selected as Float or Integer. Value is a literal parameter. This is a static node.

#### Pass Through

Input/output type variable `T`. No runtime copy. Optional user label and colour make it useful for graph organization.

#### Compare

Inputs numeric A and B. Operations: `==`, `!=`, `<`, `<=`, `>`, `>=`, approximately equal. Approximate equality has connectable absolute and relative tolerances. Output BOOL.

#### Logic Operation

Inputs A/B BOOL. Operations: AND, OR, XOR, NAND, NOR, XNOR. A separate Not node may be added, or unary mode may hide B.

#### Math

Numeric scalar inputs A and optional B. Operations:

- add, subtract, multiply, divide, modulo, power;
- minimum, maximum;
- logarithm with configurable base;
- absolute, negate, floor, ceil, round;
- sine, cosine, tangent;
- clamp and remap.

Use separate unary/binary parameter visibility but stable ports. Domain errors produce a recoverable error or configured fallback, never an uncaught exception.

#### Color

Static `COLOR` output using an sRGB UI picker and normalized runtime value.

#### Hold Image

Input/output `IMAGE`. Parameter `delay_frames`, connectable integer is not recommended; make it literal and `RECOMPILE`/state-resetting, range 1–600.

The node stores a deque of references to the last N images and outputs the image from N processed ticks earlier. Until enough frames exist, output `NoData`. It stores N frames, not “N-1” internally; a delay of 1 outputs the immediately preceding processed frame. This wording removes the off-by-one ambiguity.

Memory estimate is shown in the inspector. State resets on stop, seek, source loop, dimension/colour-space change, or delay change.

### 16.9 Synesthesia-node common parameters

Every synesthesia node has:

- root note;
- scale;
- inclusive MIDI minimum and maximum;
- MIDI channel, shown 1–16;
- maximum polyphony, default 16;
- minimum velocity, default 1;
- maximum velocity, default 127.

After producing candidate `(note, normalized_strength)` pairs:

1. map strength to the configured velocity range;
2. merge duplicate notes by maximum strength;
3. keep the strongest notes up to maximum polyphony;
4. return a `MidiStateFrame`.

No node directly opens a MIDI port.

### 16.10 Channel to Pitch

Inputs:

- `value: CHANNEL` required;
- `parameter_a: CHANNEL` optional;
- `parameter_b: CHANNEL` optional.

Output: `midi: MIDI_STATE`.

Parameters:

- common musical selector;
- occupancy threshold percent, 0–100;
- minimum A and minimum B, connectable floats;
- optional maximum A/B filters;
- ignore non-finite values;
- binning mode: Linear by nominal range (initially only mode).

Algorithm:

1. Resolve ordered allowed MIDI notes. Let count be N.
2. Require all connected channels to have identical shape and clock.
3. Build a valid-pixel mask from finite values and optional A/B bounds.
4. If no pixels are valid, output an empty state.
5. Normalize/clamp the value channel to its nominal range for bin selection.
6. Compute an N-bin histogram.
7. Occupancy for a bin is `bin_count / valid_pixel_count`.
8. A note is inactive when occupancy is below threshold.
9. Above threshold, strength is linearly remapped from threshold→0 to 1.0→1.0. When threshold is 100%, only full occupancy activates at maximum strength.
10. Apply common velocity and polyphony rules.

This exactly separates pixel filtering from the denominator and avoids treating excluded pixels as evidence against every note.

### 16.11 Optical Flow

Inputs: `current: IMAGE`, `reference: IMAGE`.  
Output: `midi: MIDI_STATE`.

The intended graph normally uses Hold Image for the reference.

Initial algorithm: dense Farnebäck optical flow on luminance.

Parameters:

- flow preset: Fast, Balanced, Accurate;
- minimum motion magnitude;
- pitch feature: Direction, Horizontal Position, Vertical Position, Magnitude;
- velocity feature: Mean Magnitude, Maximum Magnitude, Moving Pixel Fraction;
- spatial grid rows/columns, default 4×4;
- aggregation: one note per active cell or global histogram;
- fixed normalization ranges for magnitude;
- common musical parameters.

Balanced cell algorithm:

1. convert both images to luminance and require equal dimensions;
2. compute flow vectors;
3. reject vectors below minimum magnitude;
4. partition into grid cells;
5. derive selected pitch feature per cell and map it to allowed notes;
6. derive velocity strength from configured fixed range;
7. merge notes and apply polyphony.

Do not normalize magnitude solely to each frame's maximum by default, because that makes identical physical motion produce unstable velocities across frames. Frame-adaptive normalization may be an explicit option.

### 16.12 Edges to Pitch

Input: `edges: CHANNEL`.  
Output: `midi: MIDI_STATE`.

The input is expected to be an edge mask, usually from Canny.

Parameters:

- minimum contour area/perimeter;
- retrieval mode External or Tree;
- pitch feature: Contour Perimeter, Area, Centroid X, Centroid Y, Orientation, Circularity;
- velocity feature: any different/same feature plus Edge Strength where available;
- explicit min/max range for each selected feature;
- contour count limit before strongest selection;
- common musical parameters.

Algorithm extracts contours, computes features, maps the pitch feature linearly into allowed-note indexes, maps velocity feature into normalized strength, and applies common polyphony. Degenerate contours are skipped. Orientation is defined over a fixed half-turn range to avoid discontinuity.

### 16.13 Scanline

Input: `value: CHANNEL`.  
Output: `midi: MIDI_STATE`.

Parameters:

- direction: Bottom to Top, Top to Bottom, Ping-Pong;
- advance rows per processed tick, default 1;
- line thickness and aggregation Mean/Maximum;
- activation threshold;
- velocity curve exponent;
- common musical parameters.

For each processed tick, choose the current horizontal scan row, aggregate the selected thickness, resize the 1D line to exactly the number of allowed notes using area interpolation, and map sample index to ordered pitch. Samples below threshold are inactive. Remaining sample value maps to velocity.

The scan position advances on processed ticks, not source frames that were skipped or dropped. State resets on stop/seek/loop or image-height change.

### 16.14 Fourier

Input: `value: CHANNEL`.  
Output: `midi: MIDI_STATE`.

Parameters:

- window: None, Hann, Hamming;
- subtract mean, default true;
- frequency mapping: Radial Magnitude (initial mode), Horizontal, Vertical;
- minimum/maximum normalized spatial frequency;
- amplitude floor and ceiling in log magnitude;
- DC exclusion radius;
- common musical parameters.

Radial algorithm:

1. sanitize and optionally subtract mean;
2. apply window;
3. compute `rfft2` or `fft2` magnitude;
4. convert magnitude to `log1p` scale;
5. divide selected frequency interval into N radial bands, where N is allowed-note count;
6. aggregate mean or percentile magnitude per band;
7. map configured amplitude range to velocity strength;
8. apply threshold/polyphony.

Precompute radial band index maps for each input shape and note count, and cache them until either changes.

### 16.15 MIDI Utility nodes

#### Multiply Velocity

Input/output `MIDI_STATE`. Factor is a connectable non-negative float. Each velocity is rounded and clamped to 1–127. A factor of zero produces an empty state rather than velocity-zero entries.

#### Pitch Up or Down

Input/output `MIDI_STATE`. Connectable semitone offset integer. Notes outside 0–127 after transposition are discarded. Preserve channels and velocities. UI label may be “Transpose” while retaining a migration alias for the original name.

#### MIDI Merge

Specified in section 12.5.

### 16.16 Output and visualization nodes

#### Send MIDI to MIDI Output

Input `MIDI_STATE`. Parameters: output port, velocity-update policy, velocity-change threshold. Node owns a logical output session but delegates actual I/O to `MidiOutputService`. Displays connection and active-note status.

#### Generate Audio

Input `MIDI_STATE`. Parameters: waveform, volume, attack, release, max voices, output audio device. Specified in section 12.6.

#### Note Visualizer

Input `MIDI_STATE`. No engine-side heavy rendering. Engine publishes compact note/channel/velocity summaries; UI renders a 128-key strip or piano-like view. Optional range follows active notes.

#### Display Image Data

Input `IMAGE` or a separate Channel Display definition for `CHANNEL`. Parameters: fit mode,
checkerboard alpha, value display mode, histogram toggle. The node selects the producer-port preview
shown in the image dock; the same fixed-rate preview also feeds every matching connection pill. It is
a demand root while the image dock is visible. A visible connection pill can independently demand its
producer chain.

---
## 17. Repository structure and coding boundaries

```text
synesthesia-machine/
├─ pyproject.toml
├─ uv.lock
├─ README.md
├─ LICENSE-or-NOTICE.md
├─ src/synesthesia_machine/
│  ├─ __main__.py
│  ├─ app/
│  │  ├─ bootstrap.py
│  │  ├─ application.py
│  │  └─ settings.py
│  ├─ contracts/
│  │  ├─ data_types.py
│  │  ├─ node_definition.py
│  │  ├─ engine_messages.py
│  │  └─ errors.py
│  ├─ graph/
│  │  ├─ model.py
│  │  ├─ validation.py
│  │  ├─ type_system.py
│  │  ├─ compiler.py
│  │  └─ migrations.py
│  ├─ runtime/
│  │  ├─ engine_server.py
│  │  ├─ engine_client.py
│  │  ├─ execution_plan.py
│  │  ├─ scheduler.py
│  │  ├─ lifecycle.py
│  │  ├─ shared_preview.py
│  │  └─ profiling.py
│  ├─ media/
│  │  ├─ video_source.py
│  │  ├─ camera_source.py
│  │  ├─ color_spaces.py
│  │  └─ conversions.py
│  ├─ midi/
│  │  ├─ models.py
│  │  ├─ scales.py
│  │  ├─ output_service.py
│  │  └─ debug_synth.py
│  ├─ nodes/
│  │  ├─ registry.py
│  │  ├─ base.py
│  │  ├─ input/
│  │  ├─ image/
│  │  ├─ utility/
│  │  ├─ synesthesia/
│  │  ├─ midi/
│  │  └─ output/
│  ├─ persistence/
│  │  ├─ schemas.py
│  │  ├─ graph_io.py
│  │  ├─ autosave.py
│  │  └─ clipboard.py
│  ├─ diagnostics/
│  │  ├─ metrics.py
│  │  ├─ hardware.py
│  │  ├─ logging_setup.py
│  │  └─ bundle.py
│  └─ ui/
│     ├─ main_window.py
│     ├─ commands/
│     ├─ graph/
│     ├─ palette/
│     ├─ inspector/
│     ├─ previews/
│     ├─ diagnostics/
│     ├─ theme/
│     └─ viewmodels/
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  ├─ ui/
│  ├─ performance/
│  └─ fixtures/
├─ tools/
│  ├─ benchmark_graph.py
│  ├─ generate_test_video.py
│  └─ validate_graph_files.py
├─ docs/
│  ├─ architecture/
│  ├─ adr/
│  ├─ node_reference/
│  └─ agent_packets/
└─ packaging/
   ├─ pysidedeploy.spec
   ├─ windows_version_info.txt
   └─ smoke_test.ps1
```

### 17.1 Import rules

- `contracts` imports only standard library, NumPy typing, and narrowly approved schema dependencies.
- `graph` may import `contracts`, never `ui`, `media`, or concrete node implementations except through registry interfaces.
- `nodes` may import `contracts`, `media`, and `midi`; never `ui`.
- `ui` may import graph models, application services, and engine client; never OpenCV/PyAV algorithm modules.
- `persistence` serializes domain models and never serializes runtime instances.
- Circular imports are architectural defects, not problems to solve with local imports.

### 17.2 Public-module rule

Every package exposes its supported API through `__init__.py` or an explicit facade. Coding agents must not import private implementation modules from unrelated packages.

### 17.3 Node file organization

A node implementation should normally have:

```text
nodes/image/gaussian_blur/
├─ definition.py
├─ runtime.py
├─ README.md
└─ tests/test_gaussian_blur.py
```

Very small related nodes may share an implementation module, but each retains its own definition and stable type ID.

### 17.4 Style and static analysis

Use:

- Ruff for formatting and linting;
- Pyright in strict mode for application code, with narrowly documented exceptions around NumPy/OpenCV typing;
- pytest for tests;
- pre-commit or an equivalent `uv run check` script;
- conventional, descriptive commit messages; phase completion commits should be tagged or clearly named.

Do not introduce a framework merely to reduce ten lines of explicit code. The project should remain readable to coding agents and human maintainers.

---

## 18. Testing strategy

### 18.1 Test pyramid

1. **Pure unit tests** for type compatibility, graph validation, migrations, scale generation, scalar utilities, and node algorithms.
2. **Engine integration tests** using synthetic sources, mock MIDI ports, and in-process engine transport.
3. **Process integration tests** for spawn, IPC, shared-memory cleanup, crash recovery, and graph plan swaps.
4. **UI tests** for commands and key workflows using `pytest-qt` or Qt test helpers.
5. **Performance tests** run on designated hardware and reported separately from correctness CI.
6. **Packaged smoke tests** on a clean Windows or Linux machine.

pytest is the standard test runner and supports scalable fixtures and configuration through `pyproject.toml`. [R15]

### 18.2 Synthetic fixtures

Create deterministic fixtures:

- solid colours and gradients;
- colour bars;
- moving square and rotating line sequences;
- known edge shapes;
- sinusoidal 2D patterns with known Fourier frequencies;
- optical-flow translations with known displacement;
- small VFR and CFR test videos generated by tools;
- fake camera source;
- fake MIDI backend recording sent messages.

Do not make unit tests depend on a physical webcam, MIDI device, or audio device.

### 18.3 Numerical assertions

- Use exact equality for discrete graph/MIDI results when possible.
- Use `numpy.testing.assert_allclose` with node-specific tolerances for image algorithms.
- Avoid giant opaque golden images as the only assertion. Pair any golden fixture with semantic checks such as shape, range, expected edge coordinates, or frequency peak.

### 18.4 Property tests

Use property-based tests selectively for:

- arbitrary valid note ranges/scales never producing out-of-range notes;
- graph type unification and cycle rejection;
- image nodes preserving expected shape/metadata;
- arithmetic nodes handling non-finite values according to policy;
- serialization round trips.

### 18.5 MIDI safety tests

Required cases:

- note appears → one note-on;
- unchanged state → no duplicate by default;
- note disappears → note-off;
- port switch → old notes off before close;
- stop/delete/crash-recovery command → panic;
- velocity policy behaviours;
- transpose drops out-of-range notes;
- merge resolves duplicates by maximum velocity.

### 18.6 Performance benchmark methodology

The benchmark harness must:

- disable image preview unless the tested graph includes it;
- warm up for five seconds;
- measure at least 30 seconds;
- report source FPS, processed FPS, drop count, p50/p95/p99 latency, and per-node timing;
- record hardware, Windows power mode, dependency versions, and input resolution;
- avoid claiming a regression from a single noisy run;
- compare median of at least three runs for release gates.

Performance CI should warn rather than fail on shared cloud runners. Release benchmarking runs on designated local hardware.

### 18.7 Definition of done for a node

A node is not complete until it has:

- registered definition metadata;
- implementation and reset behaviour;
- parameter validation;
- `NoData` and non-finite handling;
- unit tests;
- a short node reference page;
- one sample graph or inclusion in a catalogue smoke graph;
- profiler visibility;
- no UI-specific algorithm code.

---

## 19. Historical v0 delivery roadmap (non-normative)

This completed delivery sequence is retained to explain the provenance of acceptance reports and ADRs. It does not define current package, test, example, tool, or work-item boundaries. New work follows domain ownership and the active agent playbooks.

### Phase 0 — Foundation and risk spikes

**Goal:** prove the selected dependencies on the exact Windows development machine and establish repository discipline.

Deliverables:

- `uv` project using Python 3.12;
- pinned initial dependencies and lockfile;
- empty PySide6 application window;
- PyAV test decoding a supplied video with timestamps;
- OpenCV camera probe;
- Mido/python-rtmidi port enumeration and mock backend;
- sounddevice callback producing a quiet sine tone;
- process spawn/IPC/shared-memory proof;
- benchmark and logging skeleton;
- ADRs confirming stack choices.

Exit criteria:

- all spikes run from `uv run` on Windows 11 x64;
- no dependency requires Python 3.14;
- app and child engine process terminate cleanly;
- a preview frame can cross shared memory without pickling the array;
- test MIDI messages can be sent to an available test/loopback port or validated through mock backend.

### Phase 1 — Contracts, graph model, and headless compiler

**Goal:** implement the stable core without UI or real media.

Deliverables:

- runtime data types and `NoData`;
- node definition registry;
- graph/domain models;
- port type compatibility and generic unification;
- validation, cycle detection, clock analysis;
- compiler and deterministic in-process execution plan;
- static Number, Math, Compare, Logic, Pass Through, and Conditional nodes;
- JSON schemas and round-trip persistence;
- comprehensive unit tests.

Exit criteria:

- synthetic graphs compile and execute;
- fan-out executes a node once per tick;
- invalid types/cycles are rejected with structured errors;
- static subgraphs cache correctly;
- graph JSON round-trips without semantic change.

### Phase 2 — Minimal node editor and document commands

**Goal:** edit valid graphs visually while still using the in-process engine facade.

Deliverables:

- main window, node palette, canvas, nodes, ports, and cables;
- add/move/delete/connect/select;
- compatible-port highlighting and search creation;
- parameter editors including connectable parameters;
- GraphDocument/view-model separation;
- undo/redo, copy/paste, save/load, dirty state;
- basic theme and high-DPI behaviour;
- UI command tests.

Exit criteria:

- a user can construct and persist a utility-only graph;
- undo/redo restores exact graph state;
- invalid connections cannot be created;
- no algorithm code exists under `ui/`.

### Phase 3 — First end-to-end vertical slice

**Goal:** prove a usable instrument path before catalogue expansion.

Implement only:

- Load Video;
- Resize;
- Change Colour Space;
- Separate Channels;
- Channel to Pitch;
- MIDI Note Visualizer;
- Generate Audio;
- Display Image Data;
- transport controls and source status.

Use the in-process engine facade if the process engine is not yet complete, but all code must use the final EngineClient API.

Exit criteria:

- a saved graph plays a real video in real time;
- processed index semantics are correct under Nth-frame processing;
- channel histogram creates stable note state;
- debug audio starts/stops without stuck voices;
- UI remains responsive at 500×500;
- an automated integration test uses a generated video and mock audio sink.

### Phase 4 — Production engine process, camera, and MIDI output

**Goal:** establish isolation, live input, and real DAW/hardware output.

Deliverables:

- EngineServer/EngineClient process transport;
- graph snapshot compile/swap;
- shared-memory previews;
- bounded source mailboxes and latest-frame-wins policy;
- Load Camera;
- Send MIDI to MIDI Output;
- MIDI output service, panic, and port-loss handling;
- crash supervision and engine restart;
- global status metrics.

Exit criteria:

- UI remains responsive during deliberately slow nodes;
- killing the engine does not kill the UI;
- shared memory is cleaned after normal and abnormal exits;
- camera reconnect works in a simulated integration test;
- MIDI lifecycle safety tests pass.

### Phase 5 — Utility and image-processing library

**Goal:** implement the broad reusable visual processing vocabulary.

Deliverables:

- all Image Dimension, Adjustment, Filter, Utility, and Channel nodes in section 16;
- Hold Image;
- image/channel display variants;
- node documentation and catalogue sample graphs;
- conformance tests for metadata, alpha, shape, ranges, and non-finite values.

Implementation order inside this phase:

1. crop/flip/rotate;
2. brightness/contrast/clamp/gamma/levels;
3. blur/sharpen/noise/posterize;
4. threshold/Canny/morphology/convolution/high/low pass;
5. blend and channel combine/separate refinements.

Exit criteria:

- every node meets the node definition of done;
- catalogue smoke graph can instantiate every node;
- no input mutation is detected by aliasing tests;
- memory remains bounded across a 30-minute looping graph test.

### Phase 6 — Synesthesia algorithms and MIDI utilities

**Goal:** complete the distinctive conversion algorithms.

Deliverables:

- Optical Flow;
- Edges to Pitch;
- Scanline;
- Fourier;
- Multiply Velocity;
- Transpose;
- MIDI Merge;
- common scale/root/range editor;
- algorithm visual diagnostics where useful, such as flow-field preview in inspector.

Exit criteria:

- synthetic motion, edge, scanline, and Fourier fixtures produce expected notes;
- all nodes respect scale/range/polyphony/channel contracts;
- no node directly emits or opens a MIDI port;
- representative graphs remain stable under frame skipping and drops.

### Phase 7 — Editing productivity and robustness

**Goal:** make the application comfortable for repeated technical use.

Deliverables:

- groups/comments;
- type-filtered insertion from a cable;
- tidy/alignment commands;
- autosave/recovery and backups;
- missing-media relink workflow;
- robust graph/node migrations;
- source selection transport behaviour;
- improved errors and inline help;
- recent-files menu and application settings.

Exit criteria:

- forced-crash recovery test restores unsaved graph;
- old fixture graph versions migrate correctly;
- large-graph editing remains responsive;
- all document commands preserve undo semantics.

### Phase 8 — Performance, diagnostics, and optimization

**Goal:** meet the measurable reference target and expose bottlenecks.

Deliverables:

- per-node rolling metrics;
- profiler table and graph heatmap;
- psutil hardware metrics and optional NVML adapter;
- diagnostic bundle export;
- optimized preview throttling and demand-root pruning;
- shape/frequency-map caches;
- profiling-driven copy reduction;
- optional scheduler experiments behind feature flags.

Optimization order:

1. remove accidental copies and unnecessary colour conversions;
2. ensure disconnected/hidden branches do not run;
3. cache static and shape-dependent data;
4. tune source queues and preview rates;
5. tune OpenCV thread count on benchmark hardware;
6. only then test independent-branch parallelism;
7. investigate optional GPU backends only when a specific node remains the bottleneck.

Exit criteria:

- reference benchmark target is met on designated hardware or the measured limitation and revised target are formally recorded;
- profiler overhead is below 3% in normal summary mode;
- a 60-minute soak test has no meaningful memory growth or stuck notes.

### Phase 9 — Packaging and release hardening

**Goal:** produce a reproducible Windows build usable without a development environment.

Deliverables:

- `pyside6-deploy` standalone configuration;
- version metadata and application icon;
- bundled licences/notices and dependency inventory;
- clean-machine smoke-test script;
- crash-log and settings locations;
- optional installer wrapper after standalone output is proven;
- release checklist and rollback procedure.

Exit criteria:

- packaged app runs on a clean supported Windows 11 x64 machine;
- video decode, camera, MIDI enumeration, and debug audio work from the package;
- no Python installation is required;
- startup/shutdown and panic behaviour pass smoke tests;
- build is reproducible from the committed lockfile and documented toolchain.

---

## 20. Coding-agent work protocol

Every current coding work item should establish:

1. objective and explicitly excluded work;
2. architecture contracts copied from this document;
3. repository paths the agent may create or modify;
4. public interfaces to implement before internal details;
5. ordered tasks small enough for review;
6. required tests and commands;
7. acceptance criteria;
8. a completion report template.

### 20.1 Agent rules

The coding agent must:

- inspect existing public interfaces before changing them;
- avoid broad refactors outside the requested scope;
- preserve stable IDs and serialized fields;
- add tests with every functional change;
- run formatting, type checking, and relevant tests;
- document assumptions and deviations;
- never hide an architectural mismatch with `Any`, blanket exception handling, or circular imports;
- not add a dependency without recording why the standard library/current stack is insufficient;
- not implement Python per-pixel loops for frame operations;
- not access Qt widgets from engine threads/processes;
- not send raw image arrays through normal IPC queues.

### 20.2 Required completion report

Each substantial work item ends with:

```text
Implemented:
- ...

Public interfaces added/changed:
- ...

Tests added and results:
- ...

Known limitations:
- ...

Architecture deviations:
- none / ADR reference

Recommended next work:
- ...
```

### 20.3 Context management

To keep work compatible with a 131k-token coding-agent context:

- each package has a local README and explicit facade;
- task briefs reference files, not entire directories, where possible;
- generated node reference pages state semantics independently of UI code;
- large test fixtures are generated, not pasted into prompts;
- agents should be asked to implement one coherent domain change at a time, such as three related image nodes, rather than the entire catalogue in one run.

---

## 21. Risks and mitigations

### Risk 1 — Python orchestration cannot meet the frame budget

Mitigation: native array operations, separate engine process, sequential low-overhead scheduler, demand pruning, static caches, latest-frame-wins backpressure, and a defined benchmark. If profiling identifies Python dispatch as material, move only the scheduler hot loop or specific algorithms to a compiled extension through a stable interface.

### Risk 2 — Image memory grows rapidly through graph branches and Hold Image

Mitigation: immutable per-tick cache with prompt release, view-based channels/crops where safe, Hold Image memory estimate and limit, no unbounded queues, soak tests, and later ownership-aware pools only after correctness.

### Risk 3 — Stuck MIDI notes

Mitigation: state-frame abstraction, centralized diffing, explicit tracked notes, off-before-on ordering, panic on all lifecycle transitions, global panic action, mock-backend tests, and no direct port access from synesthesia nodes.

### Risk 4 — UI graph and engine graph diverge during live edits

Mitigation: immutable graph revisions, compile-then-atomic-swap, acknowledgements with revision IDs, engine auto-stop when a broken graph is activated (ADR 0013), and commands as the only graph mutation path.

### Risk 5 — Windows device APIs are inconsistent

Mitigation: abstraction layers, Media Foundation/DirectShow fallback, asynchronous enumeration, explicit negotiated settings, mocked tests, and clear unavailable/reconnect states.

### Risk 6 — Dependency packaging/licensing issues

Mitigation: dependency lock, early packaging probes, standalone build before installer work, bundled notices, and a formal licensing review before external distribution. FFmpeg codec configuration and Qt LGPL obligations must be reviewed for the chosen distribution model; this document is not legal advice.

### Risk 7 — Node semantics become inconsistent as catalogue expands

Mitigation: shared descriptors, common base contracts, node definition-of-done checklist, generated reference docs, conformance tests, stable IDs, and review in small related batches.

### Risk 8 — “60 fps” becomes a misleading promise

Mitigation: reference graph and hardware report, separate input/processed/preview FPS, visible dropped frames, user-controlled downscaling/Nth-frame processing, and performance warnings based on actual frame budget.

---

## 22. Deferred product decisions

These decisions do not block architecture and should be resolved before public release:

- application source-code licence and commercial distribution model;
- exact Windows 11 minimum build;
- whether graphs may embed small assets or always reference files;
- whether a future plug-in SDK is supported and how plug-ins are signed/versioned;
- whether Windows MIDI Services becomes a second backend;
- whether offline non-real-time processing/export is added;
- whether GPU backends are worth maintaining;
- whether user-defined Python nodes are allowed in a sandboxed developer mode.

---

## 23. Recommended first sample graphs

1. **Hue Chord**  
   Load Video → Resize → HSV → Separate Hue → Channel to Pitch → Generate Audio.

2. **Motion Grid**  
   Load Camera → Resize → Hold Image(1) + current → Optical Flow → MIDI Output.

3. **Edge Ensemble**  
   Load Video → Canny → Edges to Pitch → Multiply Velocity → MIDI Output.

4. **Scanning Score**  
   Load Video → HSV/Saturation channel → Scanline → Note Visualizer + MIDI Output.

5. **Spatial Spectrum**  
   Load Camera → Luminance → Fourier → Transpose → Generate Audio.

6. **Self-Modulated Rotation**  
   Load Video → Saturation channel → a scalar reduction node added in a later small extension → map to Rotate angle → Display.  
   Note: the original vision uses average saturation to modulate rotation, so the catalogue should include general Channel Statistics nodes—Mean, Minimum, Maximum, Standard Deviation, Percentile—even though they were not explicitly listed. These output FLOAT and are broadly necessary for parameter modulation.

---

## 24. Additional recommended nodes

The following additions fill structural gaps in the original catalogue without turning the application into a DAW:

- **Channel Statistics**: Mean, Median, Min, Max, Standard Deviation, Percentile → FLOAT.
- **Remap Number**: explicit input/output range mapping, optionally clamped.
- **Float to Integer**: round/floor/ceil/truncate.
- **Image to Luminance**: IMAGE → CHANNEL.
- **Channel Display**: CHANNEL visualizer.
- **MIDI Merge**: required for many-to-one musical graphs.
- **Frame Index / Time**: expose tick index and source time independently of a specific source cable when useful.
- **Comment/Group**: editor objects rather than runtime nodes.

Channel Statistics is the highest-priority addition because connectable image parameters require a principled way to reduce image/channel data to scalars.

---

## 25. References

The architecture was checked against current official documentation on 29 July 2026.

- **[R1]** Python Developer's Guide, “Status of Python versions.” Python 3.12 security support is listed through October 2028. https://devguide.python.org/versions/
- **[R2]** Qt for Python documentation. PySide6 is the official Qt 6 Python binding. https://doc.qt.io/qtforpython-6/
- **[R3]** Qt `QGraphicsScene` / `QGraphicsView` documentation. The framework manages and visualizes custom 2D items. https://doc.qt.io/qt-6/qgraphicsscene.html and https://doc.qt.io/qt-6/qgraphicsview.html
- **[R4]** PyAV documentation. PyAV provides direct access to FFmpeg containers, streams, codecs, frames, and timestamps. https://pyav.basswood-io.com/docs/stable/
- **[R5]** PyAV installation documentation. Binary wheels are provided for Windows and linked against FFmpeg. https://pyav.basswood-io.com/docs/stable/overview/installation.html
- **[R6]** OpenCV Video I/O and module documentation. `VideoCapture` provides a common layer over capture backends; image-processing and acceleration modules are available. https://docs.opencv.org/4.13.0/d0/da7/videoio_overview.html and https://docs.opencv.org/4.13.0/
- **[R7]** Mido documentation. Mido provides MIDI 1.0 messages and port APIs. https://mido.readthedocs.io/en/stable/
- **[R8]** python-rtmidi project documentation/PyPI. The binding wraps RtMidi and supports the Windows Multimedia MIDI API. https://pypi.org/project/python-rtmidi/
- **[R9]** uv project documentation. Project execution can keep the environment synchronized with a committed lockfile; Windows x86-64 is Tier 1. https://docs.astral.sh/uv/guides/projects/ and https://docs.astral.sh/uv/reference/policies/platforms/
- **[R10]** Qt for Python `pyside6-deploy` documentation. The tool wraps Nuitka and can produce standalone Windows executables. https://doc.qt.io/qtforpython-6/deployment/deployment-pyside6-deploy.html
- **[R11]** PyAV Frame API. Frames expose PTS, DTS, time base, and presentation time. https://pyav.basswood-io.com/docs/stable/api/frame.html
- **[R12]** python-sounddevice documentation. It provides PortAudio output streams on Windows. https://python-sounddevice.readthedocs.io/en/latest/
- **[R13]** python-sounddevice stream callback guidance. Callbacks must avoid allocation and blocking work. https://python-sounddevice.readthedocs.io/en/latest/api/streams.html
- **[R14]** psutil project documentation/PyPI. psutil retrieves process and system utilization metrics on Windows. https://pypi.org/project/psutil/
- **[R15]** pytest documentation. https://docs.pytest.org/en/stable/

---

## 26. Final architectural position

The recommended architecture is not “a Python program that loops over frames.” It is a typed, source-clocked dataflow system whose Python layer coordinates immutable graph values while mature native libraries perform the computational work.

The most important constraints are:

1. keep Qt and processing in separate processes;
2. keep image arrays inside the engine;
3. define graph and node semantics before UI convenience;
4. use desired MIDI state rather than repeated raw messages;
5. prefer recent frames over queued latency;
6. prohibit arbitrary cycles;
7. measure a representative graph rather than promising unlimited 60 fps;
8. expand the node catalogue only through stable definitions and conformance tests.

The domain-oriented structure keeps that proven architecture extensible without making the original delivery chronology part of the product design.
