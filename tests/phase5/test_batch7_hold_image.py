"""Phase 5 Batch 7 Hold Image state, memory, and transport conformance."""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

import numpy as np
import pytest

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    AlphaMode,
    ColorSpace,
    FrameContext,
    FrameProvenance,
    ImageFrame,
    NoData,
    NodeMemoryDiagnostic,
    PortType,
    RuntimeValue,
    read_only_float32,
)
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.nodes import ExecutionKind, ParameterUpdateMode, ResetReason
from synesthesia_machine.nodes.image import (
    HOLD_IMAGE_TYPE_ID,
    create_image_definitions,
    create_temporal_definitions,
)
from synesthesia_machine.runtime import (
    CompiledNode,
    EngineFacade,
    ExecutionPlan,
    InputBinding,
    PortKey,
    ProcessEngineClient,
    Scheduler,
    TickResult,
)

DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000005700")
SOURCE_ID = UUID("00000000-0000-0000-0000-000000005701")
ALTERNATE_SOURCE_ID = UUID("00000000-0000-0000-0000-000000005702")
HOLD_ID = UUID("00000000-0000-0000-0000-000000005710")
SECOND_HOLD_ID = UUID("00000000-0000-0000-0000-000000005711")
_MEBIBYTE = 1024 * 1024


def _frame(
    tick_index: int,
    *,
    shape: tuple[int, int, int] = (2, 3, 3),
    clock_id: UUID = SOURCE_ID,
    color_space: ColorSpace = ColorSpace.SRGB,
    channel_names: tuple[str, ...] | None = None,
    alpha_mode: AlphaMode = AlphaMode.NONE,
    data: np.ndarray[tuple[int, ...], np.dtype[np.float32]] | None = None,
) -> ImageFrame:
    values = (
        np.full(shape, np.float32(tick_index), dtype=np.float32)
        if data is None
        else np.asarray(data, dtype=np.float32)
    )
    names = channel_names or tuple(f"C{index}" for index in range(values.shape[2]))
    context = FrameContext(
        clock_id,
        tick_index,
        tick_index - 1,
        (tick_index - 1) / 60.0,
        tick_index,
        None,
        False,
    )
    return ImageFrame(
        read_only_float32(values),
        color_space,
        names,
        alpha_mode,
        context,
        FrameProvenance(clock_id, "phase5-hold-test"),
    )


def _scheduler(
    *,
    delay_frames: int = 1,
    memory_limit_mb: int = 256,
    node_ids: tuple[UUID, ...] = (HOLD_ID,),
    node_clock_id: UUID | None = SOURCE_ID,
) -> Scheduler:
    definition = create_temporal_definitions()[0]
    parameters, errors = definition.parameter_values(
        {"delay_frames": delay_frames, "memory_limit_mb": memory_limit_mb}
    )
    assert not errors
    nodes = tuple(
        CompiledNode(
            node_id,
            definition,
            parameters=parameters,
            input_bindings={"image": InputBinding(PortKey(SOURCE_ID, "image"))},
            input_types={"image": PortType.IMAGE},
            output_types={"image": PortType.IMAGE},
            clock_id=node_clock_id,
            is_static=False,
        )
        for node_id in node_ids
    )
    return Scheduler(ExecutionPlan(DOCUMENT_ID, 1, nodes, frozenset(node_ids)))


def _tick(
    scheduler: Scheduler, image: ImageFrame, node_id: UUID = HOLD_ID
) -> tuple[TickResult, RuntimeValue]:
    result = scheduler.execute_tick(
        image.context,
        source_values={PortKey(SOURCE_ID, "image"): image},
    )
    return result, result.values[PortKey(node_id, "image")]


def _source_hold_document(*, delay_frames: int = 1, memory_limit_mb: int = 256) -> GraphDocument:
    document = GraphDocument(document_id=DOCUMENT_ID)
    document.add_node("synmachine.input.load_camera", node_id=SOURCE_ID)
    document.add_node(
        HOLD_IMAGE_TYPE_ID,
        node_id=HOLD_ID,
        parameters={
            "delay_frames": delay_frames,
            "memory_limit_mb": memory_limit_mb,
        },
    )
    document.add_connection(SOURCE_ID, "image", HOLD_ID, "image")
    return document


def _facade_tick(facade: EngineFacade, image: ImageFrame):
    result = facade.tick(
        image.context,
        source_values={
            PortKey(SOURCE_ID, "image"): image,
            PortKey(SOURCE_ID, "processed_index"): image.context.tick_index,
        },
    )
    return result.values[PortKey(HOLD_ID, "image")]


def test_hold_image_metadata_is_exact_and_registered_last() -> None:
    definition = create_temporal_definitions()[0]
    parameters = definition.parameters

    assert definition.type_id == HOLD_IMAGE_TYPE_ID
    assert definition.display_name == "Hold Image"
    assert definition.execution_kind is ExecutionKind.STATEFUL
    assert [(port.id, port.value_type) for port in definition.inputs] == [("image", PortType.IMAGE)]
    assert [(port.id, port.value_type) for port in definition.outputs] == [
        ("image", PortType.IMAGE)
    ]
    assert [parameter.id for parameter in parameters] == ["delay_frames", "memory_limit_mb"]
    assert [
        (parameter.default, parameter.minimum, parameter.maximum) for parameter in parameters
    ] == [
        (1, 1, 600),
        (256, 1, 4096),
    ]
    assert all(parameter.update_mode is ParameterUpdateMode.RECOMPILE for parameter in parameters)
    assert create_image_definitions()[-1].type_id == HOLD_IMAGE_TYPE_ID
    registry = create_application_registry()
    assert len(registry.definitions()) >= 51
    assert registry.require(HOLD_IMAGE_TYPE_ID).type_id == HOLD_IMAGE_TYPE_ID


@pytest.mark.parametrize("delay_frames", [1, 3])
def test_delay_selects_exact_immutable_reference_before_append(delay_frames: int) -> None:
    scheduler = _scheduler(delay_frames=delay_frames)
    frames = tuple(_frame(index) for index in range(1, delay_frames + 3))
    before = tuple(frame.data.copy() for frame in frames)
    try:
        outputs = tuple(_tick(scheduler, frame)[1] for frame in frames)
    finally:
        scheduler.close()

    assert all(output is NoData for output in outputs[:delay_frames])
    assert outputs[delay_frames] is frames[0]
    assert outputs[delay_frames + 1] is frames[1]
    selected = outputs[delay_frames]
    assert isinstance(selected, ImageFrame)
    assert selected.data is frames[0].data
    assert all(
        np.array_equal(frame.data, expected) for frame, expected in zip(frames, before, strict=True)
    )
    assert all(not frame.data.flags.writeable for frame in frames)


def test_non_finite_values_are_retained_without_processing() -> None:
    values = np.array(
        [[[np.nan, np.inf, -np.inf], [0.0, -1.0, 1.0]]],
        dtype=np.float32,
    )
    first = _frame(1, shape=(1, 2, 3), data=values)
    second = _frame(2, shape=(1, 2, 3))
    scheduler = _scheduler()
    try:
        assert _tick(scheduler, first)[1] is NoData
        output = _tick(scheduler, second)[1]
    finally:
        scheduler.close()

    assert output is first
    assert isinstance(output, ImageFrame)
    assert np.array_equal(output.data, values, equal_nan=True)


@pytest.mark.parametrize("reason", list(ResetReason))
def test_every_source_component_reset_reason_clears_history(reason: ResetReason) -> None:
    scheduler = _scheduler(delay_frames=2)
    try:
        assert _tick(scheduler, _frame(1))[1] is NoData
        assert _tick(scheduler, _frame(2))[1] is NoData
        assert _tick(scheduler, _frame(3))[1] is not NoData

        scheduler.reset_source(SOURCE_ID, reason)

        assert _tick(scheduler, _frame(4))[1] is NoData
        diagnostic = scheduler.node_memory_diagnostics(HOLD_ID)[0]
        assert diagnostic.retained_frame_count == 1
    finally:
        scheduler.close()


@pytest.mark.parametrize("change", ["shape", "color_space", "channel_names", "alpha_mode", "clock"])
def test_runtime_shape_descriptor_and_clock_changes_start_new_history(change: str) -> None:
    first = _frame(1)
    changes: dict[str, dict[str, object]] = {
        "shape": {"shape": (3, 2, 3)},
        "color_space": {"color_space": ColorSpace.LINEAR_RGB},
        "channel_names": {"channel_names": ("X", "Y", "Z")},
        "alpha_mode": {"alpha_mode": AlphaMode.STRAIGHT},
        "clock": {"clock_id": ALTERNATE_SOURCE_ID},
    }
    options = changes[change]
    changed = _frame(2, **options)  # type: ignore[arg-type]
    following = _frame(3, **options)  # type: ignore[arg-type]
    scheduler = _scheduler(node_clock_id=None if change == "clock" else SOURCE_ID)
    try:
        assert _tick(scheduler, first)[1] is NoData
        assert _tick(scheduler, changed)[1] is NoData
        assert _tick(scheduler, following)[1] is changed
    finally:
        scheduler.close()


def test_memory_limit_error_is_recoverable_exact_and_releases_history() -> None:
    one_mebibyte = _frame(
        1,
        shape=(256, 256, 4),
        color_space=ColorSpace.RGBA,
        channel_names=("R", "G", "B", "A"),
        alpha_mode=AlphaMode.STRAIGHT,
    )
    assert one_mebibyte.data.nbytes == _MEBIBYTE

    allowed = _scheduler(delay_frames=1, memory_limit_mb=1)
    rejected = _scheduler(delay_frames=2, memory_limit_mb=1)
    try:
        allowed_result, allowed_output = _tick(allowed, one_mebibyte)
        rejected_result, rejected_output = _tick(rejected, one_mebibyte)

        assert allowed_result.errors == ()
        assert allowed_output is NoData
        assert allowed.node_memory_diagnostics()[0].retained_bytes == _MEBIBYTE

        assert rejected_output is NoData
        assert len(rejected_result.errors) == 1
        error = rejected_result.errors[0]
        assert error.code == "hold_image_memory_limit"
        assert error.recoverable
        assert error.details == (
            f"estimated_retained_bytes={2 * _MEBIBYTE}; memory_limit_bytes={_MEBIBYTE}"
        )
        assert rejected.node_memory_diagnostics()[0] == NodeMemoryDiagnostic(
            HOLD_ID,
            estimated_retained_bytes=2 * _MEBIBYTE,
            retained_bytes=0,
            retained_frame_count=0,
            capacity_frame_count=2,
            memory_limit_bytes=_MEBIBYTE,
        )
    finally:
        allowed.close()
        rejected.close()


def test_diagnostics_are_exact_bounded_resettable_sorted_and_filterable() -> None:
    scheduler = _scheduler(delay_frames=3, node_ids=(SECOND_HOLD_ID, HOLD_ID))
    frame_bytes = _frame(1).data.nbytes
    try:
        initial = scheduler.node_memory_diagnostics()
        assert [item.node_id for item in initial] == [HOLD_ID, SECOND_HOLD_ID]
        assert scheduler.node_memory_diagnostics(HOLD_ID) == (initial[0],)
        assert scheduler.node_memory_diagnostics(UUID(int=0)) == ()
        assert initial[0] == NodeMemoryDiagnostic(HOLD_ID)

        for tick_index in range(1, 11):
            result, _ = _tick(scheduler, _frame(tick_index))
            assert result.errors == ()
            diagnostic = scheduler.node_memory_diagnostics(HOLD_ID)[0]
            expected_count = min(tick_index, 3)
            assert diagnostic == NodeMemoryDiagnostic(
                HOLD_ID,
                estimated_retained_bytes=frame_bytes * 3,
                retained_bytes=frame_bytes * expected_count,
                retained_frame_count=expected_count,
                capacity_frame_count=3,
                memory_limit_bytes=256 * _MEBIBYTE,
            )

        scheduler.reset_source(SOURCE_ID, ResetReason.SEEK)
        reset = scheduler.node_memory_diagnostics(HOLD_ID)[0]
        assert reset.retained_bytes == 0
        assert reset.retained_frame_count == 0
        assert reset.estimated_retained_bytes == frame_bytes * 3
        assert reset.capacity_frame_count == 3
    finally:
        scheduler.close()


@pytest.mark.parametrize(
    ("parameter_id", "replacement"),
    [("delay_frames", 2), ("memory_limit_mb", 128)],
)
def test_plan_replacement_retains_unrelated_state_but_recompile_parameters_clear_it(
    parameter_id: str,
    replacement: int,
) -> None:
    document = _source_hold_document()
    facade = EngineFacade(create_application_registry())
    first = _frame(1)
    second = _frame(2)
    third = _frame(3)
    try:
        assert facade.activate(document.snapshot()).plan is not None
        assert _facade_tick(facade, first) is NoData

        document.set_position(HOLD_ID, (20.0, 30.0))
        assert facade.activate(document.snapshot()).plan is not None
        assert _facade_tick(facade, second) is first

        document.set_parameter(HOLD_ID, parameter_id, replacement)
        assert facade.activate(document.snapshot()).plan is not None
        assert _facade_tick(facade, third) is NoData
    finally:
        facade.close()


def test_process_client_publishes_compact_initial_diagnostic_without_opening_camera() -> None:
    document = _source_hold_document(delay_frames=5, memory_limit_mb=64)
    client = ProcessEngineClient(request_timeout_s=1.5, close_timeout_s=0.5)
    try:
        activation = client.activate(document.snapshot())
        diagnostics = client.node_memory_diagnostics(HOLD_ID)

        assert activation.activated
        assert diagnostics == (NodeMemoryDiagnostic(HOLD_ID),)
        assert client.node_memory_diagnostics(UUID(int=0)) == ()
    finally:
        client.close()


@pytest.mark.parametrize(
    "changes",
    [
        {"estimated_retained_bytes": -1},
        {"retained_bytes": -1},
        {"retained_frame_count": -1},
        {"capacity_frame_count": -1},
        {"memory_limit_bytes": -1},
    ],
)
def test_node_memory_diagnostic_rejects_negative_values(changes: dict[str, int]) -> None:
    with pytest.raises(ValueError, match="negative"):
        replace(NodeMemoryDiagnostic(HOLD_ID), **changes)


def test_node_memory_diagnostic_rejects_count_above_capacity() -> None:
    with pytest.raises(ValueError, match="capacity"):
        NodeMemoryDiagnostic(HOLD_ID, retained_frame_count=2, capacity_frame_count=1)
