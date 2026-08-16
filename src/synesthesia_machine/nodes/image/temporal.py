"""Stateful Phase 5 temporal image nodes."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from threading import Lock
from uuid import UUID

from synesthesia_machine.contracts import (
    FrameContext,
    ImageFrame,
    NoData,
    NodeMemoryDiagnostic,
    ParameterValue,
    PortType,
    RuntimeValue,
)
from synesthesia_machine.nodes import (
    ExecutionKind,
    ExpectedNodeError,
    InputPortSpec,
    NodeDefinition,
    OutputPortSpec,
    ParameterSpec,
    ParameterUpdateMode,
    ResetReason,
)

HOLD_IMAGE_TYPE_ID = "synmachine.image.hold_image"
POSTERIZE_TIME_TYPE_ID = "synmachine.image.posterize_time"
_MEBIBYTE = 1024 * 1024
type _FrameSignature = tuple[object, ...]


class HoldImageRuntime:
    """Retain immutable image references for a bounded number of processed frames."""

    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id
        self._lock = Lock()
        self._history: deque[ImageFrame] = deque()
        self._signature: _FrameSignature | None = None
        self._delay_frames = 0
        self._memory_limit_bytes = 0
        self._estimated_retained_bytes = 0

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        image = inputs["image"]
        if not isinstance(image, ImageFrame):
            raise ExpectedNodeError("invalid_image", "Hold Image requires an image input")
        delay_frames = _integer(parameters["delay_frames"], "delay_frames")
        memory_limit_mb = _integer(parameters["memory_limit_mb"], "memory_limit_mb")
        memory_limit_bytes = memory_limit_mb * _MEBIBYTE
        estimated_retained_bytes = image.data.nbytes * delay_frames
        signature = _frame_signature(image)

        with self._lock:
            if self._delay_frames != delay_frames:
                self._clear_locked()
                self._history = deque(maxlen=delay_frames)
            self._delay_frames = delay_frames
            self._memory_limit_bytes = memory_limit_bytes
            self._estimated_retained_bytes = estimated_retained_bytes
            if self._signature != signature:
                self._clear_locked()
                self._signature = signature
            if estimated_retained_bytes > memory_limit_bytes:
                self._clear_locked()
                raise ExpectedNodeError(
                    "hold_image_memory_limit",
                    "Hold Image estimated retained memory exceeds memory_limit_mb",
                    details=(
                        f"estimated_retained_bytes={estimated_retained_bytes}; "
                        f"memory_limit_bytes={memory_limit_bytes}"
                    ),
                )
            output: RuntimeValue = (
                self._history[0] if len(self._history) == delay_frames else NoData
            )
            self._history.append(image)
            return {"image": output}

    def reset(self, reason: ResetReason) -> None:
        del reason
        with self._lock:
            self._clear_locked()

    def close(self) -> None:
        with self._lock:
            self._clear_locked()

    def node_memory_diagnostic(self) -> NodeMemoryDiagnostic:
        with self._lock:
            retained_bytes = sum(frame.data.nbytes for frame in self._history)
            return NodeMemoryDiagnostic(
                node_id=self.node_id,
                estimated_retained_bytes=self._estimated_retained_bytes,
                retained_bytes=retained_bytes,
                retained_frame_count=len(self._history),
                capacity_frame_count=self._delay_frames,
                memory_limit_bytes=self._memory_limit_bytes,
            )

    def _clear_locked(self) -> None:
        self._history.clear()
        self._signature = None


class PosterizeTimeRuntime:
    """Hold the latest sampled frame for a configurable number of input cycles."""

    def __init__(self, node_id: UUID) -> None:
        self.node_id = node_id
        self._lock = Lock()
        self._held: ImageFrame | None = None
        self._phase = 0

    def process(
        self,
        inputs: Mapping[str, RuntimeValue],
        parameters: Mapping[str, ParameterValue],
        context: FrameContext,
    ) -> Mapping[str, RuntimeValue]:
        del context
        image = inputs["image"]
        if not isinstance(image, ImageFrame):
            raise ExpectedNodeError("invalid_image", "Posterize Time requires an image input")
        interval = _integer(parameters["interval_frames"], "interval_frames")
        with self._lock:
            if (
                self._held is None
                or self._phase == 0
                or _frame_signature(self._held) != _frame_signature(image)
            ):
                self._held = image
            result = self._held
            self._phase = (self._phase + 1) % interval
        return {"image": result}

    def reset(self, reason: ResetReason) -> None:
        del reason
        with self._lock:
            self._held = None
            self._phase = 0

    def close(self) -> None:
        self.reset(ResetReason.ENGINE_RESTARTED)


def _frame_signature(image: ImageFrame) -> _FrameSignature:
    return (
        image.data.shape,
        image.color_space,
        image.channel_names,
        image.alpha_mode,
        image.context.clock_id,
    )


def _integer(value: object, parameter_id: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ExpectedNodeError(
            "invalid_hold_image_parameter",
            f"Hold Image parameter {parameter_id!r} must be an integer",
        )
    return value


def create_temporal_definitions() -> tuple[NodeDefinition, ...]:
    """Return Batch 7 temporal definitions in persistent catalogue order."""

    return (
        NodeDefinition(
            HOLD_IMAGE_TYPE_ID,
            1,
            "Hold Image",
            "Image / Utility",
            "Output an immutable image reference from a bounded number of processed frames ago.",
            (InputPortSpec("image", "Image", PortType.IMAGE),),
            (OutputPortSpec("image", "Image", PortType.IMAGE),),
            (
                ParameterSpec(
                    "delay_frames",
                    "Delay frames",
                    PortType.INT,
                    1,
                    minimum=1,
                    maximum=600,
                    update_mode=ParameterUpdateMode.RECOMPILE,
                ),
                ParameterSpec(
                    "memory_limit_mb",
                    "Memory limit (MiB)",
                    PortType.INT,
                    256,
                    minimum=1,
                    maximum=4096,
                    update_mode=ParameterUpdateMode.RECOMPILE,
                ),
            ),
            ExecutionKind.STATEFUL,
            HoldImageRuntime,
            aliases=("delay image", "previous frame", "frame history"),
        ),
        NodeDefinition(
            POSTERIZE_TIME_TYPE_ID,
            1,
            "Posterize Time",
            "Image / Utility",
            "Sample an image every N frames and hold it between samples.",
            (InputPortSpec("image", "Image", PortType.IMAGE),),
            (OutputPortSpec("image", "Image", PortType.IMAGE),),
            (
                ParameterSpec(
                    "interval_frames",
                    "Interval frames",
                    PortType.INT,
                    2,
                    minimum=1,
                    maximum=600,
                ),
            ),
            ExecutionKind.STATEFUL,
            PosterizeTimeRuntime,
            aliases=("temporal posterize", "frame sample and hold", "time quantize"),
        ),
    )


__all__ = [
    "HOLD_IMAGE_TYPE_ID",
    "POSTERIZE_TIME_TYPE_ID",
    "HoldImageRuntime",
    "PosterizeTimeRuntime",
    "create_temporal_definitions",
]
