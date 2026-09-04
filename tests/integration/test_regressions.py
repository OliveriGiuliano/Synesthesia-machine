"""Regression coverage for dynamic nodes, new utilities, inspection, and random graphs."""

from __future__ import annotations

from uuid import UUID

import numpy as np
import pytest
from tests.support.graph_factories import frame_context, make_definition, make_tv_image_producer

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.contracts import (
    AlphaMode,
    ChannelFrame,
    ChannelSemantic,
    ColorSpace,
    FrameProvenance,
    ImageFrame,
    PortType,
    ValueArray,
    read_only_float32,
)
from synesthesia_machine.graph import (
    GraphCompiler,
    GraphDocument,
    generate_random_graph,
    randomize_graph_nodes,
    randomize_graph_parameters,
)
from synesthesia_machine.nodes import ExecutionKind, ExpectedNodeError, NodeRegistry
from synesthesia_machine.runtime import (
    CompiledNode,
    ExecutionPlan,
    InputBinding,
    PortKey,
    Scheduler,
)
from synesthesia_machine.ui.theme import DEFAULT_THEME
from synesthesia_machine.ui.view_models import project_graph

NODE_ID = UUID("00000000-0000-0000-0000-00000000a001")


def _channel(clock_id: UUID = NODE_ID) -> ChannelFrame:
    return ChannelFrame(
        read_only_float32(np.asarray([[0.0, 0.5], [1.0, 1.5]], dtype=np.float32)),
        ChannelSemantic.GENERIC,
        0.0,
        1.0,
        False,
        frame_context(clock_id=clock_id),
    )


def test_requested_nodes_are_registered_with_stable_ids() -> None:
    registry = create_application_registry()
    expected = {
        "synmachine.image.difference",
        "synmachine.image.posterize_time",
        "synmachine.utility.modulo_accumulator",
        "synmachine.utility.buffer",
        "synmachine.utility.statistics",
        "synmachine.utility.normalize",
        "synmachine.utility.curve",
    }
    assert expected <= {definition.type_id for definition in registry.definitions()}
    assert registry.get("synmachine.utility.channel_statistics") is None


def test_live_scalar_parameters_are_exposed_with_safe_exclusions() -> None:
    registry = create_application_registry()
    resize = registry.require("synmachine.image.resize")
    assert resize.parameter("width").connected_port_type is PortType.FLOAT  # type: ignore[union-attr]
    assert resize.parameter("preserve_aspect").connected_port_type is PortType.BOOL  # type: ignore[union-attr]
    aperture = registry.require("synmachine.image.canny").parameter("aperture_size")
    assert aperture is not None and aperture.connectable
    assert aperture.connected_value(4.8) == 5

    statistics = registry.require("synmachine.utility.statistics")
    assert statistics.parameter("percentile").connectable  # type: ignore[union-attr]
    assert not statistics.parameter("statistic").connectable  # type: ignore[union-attr]
    assert not registry.require("synmachine.utility.buffer").parameter("capacity").connectable  # type: ignore[union-attr]
    assert not any(
        parameter.connectable
        for parameter in registry.require("synmachine.input.load_video").parameters
    )


def test_scheduler_applies_rounds_and_bounds_connected_numeric_parameter() -> None:
    definition = create_application_registry().require("synmachine.image.resize")
    parameters, errors = definition.parameter_values(
        {"width": 500, "height": 4, "preserve_aspect": False}
    )
    assert not errors
    external = UUID("00000000-0000-0000-0000-00000000a002")
    compiled = CompiledNode(
        NODE_ID,
        definition,
        parameters=parameters,
        input_bindings={
            "image": InputBinding(PortKey(external, "image")),
            "width": InputBinding(PortKey(external, "width")),
        },
        input_types={"image": PortType.CHANNEL, "width": PortType.FLOAT},
        output_types={"image": PortType.CHANNEL},
        clock_id=external,
        is_static=False,
    )
    scheduler = Scheduler(ExecutionPlan(external, 1, (compiled,), frozenset({NODE_ID})))
    source_channel = _channel(external)
    try:
        rounded = scheduler.execute_tick(
            source_channel.context,
            source_values={
                PortKey(external, "image"): source_channel,
                PortKey(external, "width"): 12.6,
            },
        ).values[PortKey(NODE_ID, "image")]
        bounded = scheduler.execute_tick(
            source_channel.context,
            source_values={
                PortKey(external, "image"): source_channel,
                PortKey(external, "width"): -20.0,
            },
        ).values[PortKey(NODE_ID, "image")]
    finally:
        scheduler.close()
    assert isinstance(rounded, ChannelFrame) and rounded.data.shape == (4, 13)
    assert isinstance(bounded, ChannelFrame) and bounded.data.shape == (4, 1)


def test_dynamic_image_node_resolves_to_channel_and_hides_image_only_parameters() -> None:
    registry = create_application_registry()
    document = GraphDocument()
    source = document.add_node("synmachine.input.load_video")
    luminance = document.add_node("synmachine.image.to_luminance")
    add = document.add_node("synmachine.image.add_scalar", implementation_version=2)
    # GraphDocument.add_node defaults to implementation version 1; the editor
    # stamps the definition's current version, but raw documents do not.
    display = document.add_node(
        "synmachine.visualization.channel_display", implementation_version=2
    )
    document.add_connection(source, "image", luminance, "image")
    document.add_connection(luminance, "channel", add, "image")
    document.add_connection(add, "image", display, "channel")

    result = GraphCompiler(registry).compile(document.snapshot())
    assert result.plan is not None
    compiled = result.plan.node(add)
    assert compiled is not None
    assert compiled.input_types["image"] is PortType.CHANNEL
    assert compiled.output_types["image"] is PortType.CHANNEL

    view = project_graph(document.snapshot(), registry, result.report)
    node = next(item for item in view.nodes if item.node_id == add)
    assert node.inputs[0].type_name == "CHANNEL"
    assert node.outputs[0].type_name == "CHANNEL"
    assert "channels" not in {parameter.spec.id for parameter in node.parameters}


def test_view_model_keeps_resolved_link_type_when_graph_is_invalid() -> None:
    """A type-variable image link keeps its resolved IMAGE type in the projection even when an
    unrelated floating node makes the graph invalid, so the connection pill is neither re-typed
    to the raw variable name nor dropped (the still-running plan keeps feeding the pill)."""
    registry = NodeRegistry(
        (
            make_tv_image_producer(),
            make_definition(
                "test.image_sink", input_type=PortType.IMAGE, output_type=PortType.IMAGE
            ),
            make_definition("test.floaty", input_type=PortType.IMAGE, output_type=PortType.IMAGE),
        )
    )
    document = GraphDocument()
    source = document.add_node("test.tv_image_producer")
    sink = document.add_node("test.image_sink")
    document.add_connection(source, "value", sink, "value")
    document.add_node("test.floaty")  # floating: required input missing -> invalid graph

    result = GraphCompiler(registry).compile(document.snapshot())
    assert result.report.is_valid is False
    assert result.plan is None

    view = project_graph(document.snapshot(), registry, result.report)
    (connection,) = view.connections
    assert connection.type_name == "IMAGE"


def test_buffer_statistics_and_modulo_accumulator_process_scalars() -> None:
    registry = create_application_registry()
    context = frame_context(clock_id=NODE_ID)
    buffer_definition = registry.require("synmachine.utility.buffer")
    buffer_parameters, errors = buffer_definition.parameter_values({"capacity": 3})
    assert not errors
    buffer_runtime = buffer_definition.runtime_factory(NODE_ID)
    latest = None
    for value in (1.0, 2.0, 4.0, 8.0):
        latest = buffer_runtime.process({"value": value}, buffer_parameters, context)["values"]
    assert isinstance(latest, ValueArray)
    assert latest.values == (2.0, 4.0, 8.0)

    statistics = registry.require("synmachine.utility.statistics")
    statistics_parameters, errors = statistics.parameter_values({"statistic": "MEAN"})
    assert not errors
    mean = statistics.runtime_factory(NODE_ID).process(
        {"values_1": latest}, statistics_parameters, context
    )["value"]
    assert mean == 14.0 / 3.0

    accumulator = registry.require("synmachine.utility.modulo_accumulator")
    accumulator_parameters, errors = accumulator.parameter_values({"modulo": 5.0})
    assert not errors
    accumulator_runtime = accumulator.runtime_factory(NODE_ID)
    assert (
        accumulator_runtime.process({"value": 3.0}, accumulator_parameters, context)["value"] == 3.0
    )
    assert (
        accumulator_runtime.process({"value": 4.0}, accumulator_parameters, context)["value"] == 2.0
    )


def test_buffer_enforces_memory_limit_and_resets_on_descriptor_change() -> None:
    definition = create_application_registry().require("synmachine.utility.buffer")
    parameters, errors = definition.parameter_values({"capacity": 600})
    assert not errors
    runtime = definition.runtime_factory(NODE_ID)
    context = frame_context(clock_id=NODE_ID)
    image = ImageFrame(
        read_only_float32(np.zeros((512, 512, 3), dtype=np.float32)),
        ColorSpace.SRGB,
        ("R", "G", "B"),
        AlphaMode.NONE,
        context,
        FrameProvenance(NODE_ID, "buffer-limit-test"),
    )

    with pytest.raises(ExpectedNodeError, match="256 MiB safety limit"):
        runtime.process({"value": image}, parameters, context)
    diagnostic = runtime.node_memory_diagnostic()  # type: ignore[attr-defined]
    assert diagnostic.retained_bytes == 0
    assert diagnostic.estimated_retained_bytes > diagnostic.memory_limit_bytes

    small_parameters, errors = definition.parameter_values({"capacity": 3})
    assert not errors
    generic = _channel()
    hue = ChannelFrame(
        generic.data,
        ChannelSemantic.HUE,
        0.0,
        360.0,
        True,
        generic.context,
    )
    runtime.process({"value": generic}, small_parameters, generic.context)
    result = runtime.process({"value": hue}, small_parameters, hue.context)["values"]
    assert isinstance(result, ValueArray)
    assert result.values == (hue,)


def test_dynamic_statistics_and_accumulator_respect_channel_descriptors() -> None:
    registry = create_application_registry()
    generic = _channel()
    hue = ChannelFrame(
        generic.data,
        ChannelSemantic.HUE,
        0.0,
        360.0,
        True,
        generic.context,
    )
    statistics = registry.require("synmachine.utility.statistics")
    statistics_parameters, errors = statistics.parameter_values({"statistic": "MEAN"})
    assert not errors
    with pytest.raises(ExpectedNodeError, match="matching descriptors"):
        statistics.runtime_factory(NODE_ID).process(
            {"values_1": ValueArray(PortType.CHANNEL, (generic, hue))},
            statistics_parameters,
            generic.context,
        )

    accumulator = registry.require("synmachine.utility.modulo_accumulator")
    accumulator_parameters, errors = accumulator.parameter_values({"modulo": 5.0})
    assert not errors
    runtime = accumulator.runtime_factory(NODE_ID)
    runtime.process({"value": generic}, accumulator_parameters, generic.context)
    reset_value = runtime.process({"value": hue}, accumulator_parameters, hue.context)["value"]
    assert isinstance(reset_value, ChannelFrame)
    assert reset_value.semantic is ChannelSemantic.HUE
    assert np.array_equal(reset_value.data, np.mod(hue.data, 5.0))


def test_dynamic_normalize_curve_and_filter_preserve_channel_metadata() -> None:
    registry = create_application_registry()
    source = _channel()
    for type_id, overrides in (
        ("synmachine.utility.normalize", {"mode": "DATA_RANGE"}),
        ("synmachine.utility.curve", {"curve": "SMOOTHSTEP"}),
    ):
        definition = registry.require(type_id)
        parameters, errors = definition.parameter_values(overrides)
        assert not errors
        output = definition.runtime_factory(NODE_ID).process(
            {"value": source}, parameters, source.context
        )["value"]
        assert isinstance(output, ChannelFrame)
        assert output.semantic is source.semantic
        assert output.context is source.context
        assert not output.data.flags.writeable

    blur = registry.require("synmachine.image.gaussian_blur")
    parameters, errors = blur.parameter_values({})
    assert not errors
    output = blur.runtime_factory(NODE_ID).process({"image": source}, parameters, source.context)[
        "image"
    ]
    assert isinstance(output, ChannelFrame)
    assert output.data.shape == source.data.shape


def test_normalize_definition_and_randomization_reject_reversed_output_range() -> None:
    registry = create_application_registry()
    definition = registry.require("synmachine.utility.normalize")

    _values, errors = definition.parameter_values({"output_minimum": 1.0, "output_maximum": 0.0})

    assert errors == ["output maximum must not be below output minimum"]

    document = GraphDocument()
    normalize_id = document.add_node(definition.type_id)
    randomized = randomize_graph_parameters(document.snapshot(), registry, {normalize_id}, seed=24)
    normalize = randomized.node(normalize_id)

    assert normalize is not None
    output_maximum = normalize.parameters["output_maximum"]
    output_minimum = normalize.parameters["output_minimum"]
    assert isinstance(output_maximum, float)
    assert isinstance(output_minimum, float)
    assert output_maximum >= output_minimum


def test_random_graphs_are_complete_and_valid_across_seeds() -> None:
    registry = create_application_registry()
    compiler = GraphCompiler(registry)
    for seed in range(20):
        snapshot = generate_random_graph(registry, seed=seed)
        result = compiler.compile(snapshot)
        assert result.report.is_valid
        assert result.plan is not None
        source = next(
            node
            for node in snapshot.nodes
            if registry.require(node.type_id).execution_kind is ExecutionKind.SOURCE
        )
        context = frame_context(clock_id=source.id)
        image = ImageFrame(
            read_only_float32(
                np.linspace(0.0, 1.0, 24 * 32 * 3, dtype=np.float32).reshape(24, 32, 3)
            ),
            ColorSpace.SRGB,
            ("R", "G", "B"),
            AlphaMode.NONE,
            context,
            FrameProvenance(source.id, "test"),
        )
        scheduler = Scheduler(result.plan)
        try:
            tick = scheduler.execute_tick(
                context,
                source_values={
                    PortKey(source.id, "image"): image,
                    PortKey(source.id, "processed_index"): 1,
                },
            )
        finally:
            scheduler.close()
        assert tick.errors == ()
        definitions = [registry.require(node.type_id) for node in snapshot.nodes]
        assert any(item.execution_kind is ExecutionKind.SOURCE for item in definitions)
        assert any(item.execution_kind is ExecutionKind.SINK for item in definitions)
        assert snapshot.connections
        assert any(
            dict(node.parameters) != registry.require(node.type_id).parameter_values({})[0]
            for node in snapshot.nodes
        )
        for node in snapshot.nodes:
            if node.type_id == "synmachine.output.generate_audio":
                assert node.parameters["enabled"] is False


def test_parameter_randomization_is_seeded_and_scoped_to_selected_nodes() -> None:
    registry = create_application_registry()
    document = GraphDocument()
    first = document.add_node("synmachine.utility.number")
    second = document.add_node("synmachine.utility.number")
    before = document.snapshot()

    randomized = randomize_graph_parameters(before, registry, {first}, seed=41)
    repeated = randomize_graph_parameters(before, registry, {first}, seed=41)

    assert randomized.node(first).parameters == repeated.node(first).parameters  # type: ignore[union-attr]
    assert randomized.node(first).parameters != before.node(first).parameters  # type: ignore[union-attr]
    assert randomized.node(second) == before.node(second)


def test_parameter_randomization_preserves_context_dependent_video_identity() -> None:
    registry = create_application_registry()
    document = GraphDocument()
    video = document.add_node(
        "synmachine.input.load_video",
        parameters={"file_path": "clip.mkv", "stream_index": 3},
    )

    randomized = randomize_graph_parameters(document.snapshot(), registry, {video}, seed=11)
    node = randomized.node(video)

    assert node is not None
    assert node.parameters["file_path"] == "clip.mkv"
    assert node.parameters["stream_index"] == 3
    assert (
        node.parameters["process_every_nth_frame"] != 1
        or node.parameters["loop"] is True
        or node.parameters["playback_speed"] != 1.0
    )


def test_randomize_nodes_replaces_selection_with_random_sized_valid_subgraph() -> None:
    registry = create_application_registry()
    size_deltas: list[int] = []
    # Seeded runs are process-stable (node IDs derive from the seed), so a
    # wide seed set makes the both-directions size property robust.
    for seed in range(20):
        original = generate_random_graph(registry, seed=seed)
        selected = {node.id for node in original.nodes}

        randomized, replacement_ids = randomize_graph_nodes(
            original,
            registry,
            selected,
            seed=seed + 29,
        )

        assert GraphCompiler(registry).compile(randomized).report.is_valid
        assert not selected & replacement_ids
        assert selected.isdisjoint(node.id for node in randomized.nodes)
        assert replacement_ids == frozenset(node.id for node in randomized.nodes)
        definitions = tuple(registry.require(node.type_id) for node in randomized.nodes)
        assert any(item.execution_kind is ExecutionKind.SOURCE for item in definitions)
        assert any(item.execution_kind is ExecutionKind.SINK for item in definitions)
        size_deltas.append(len(replacement_ids) - len(selected))

    assert any(delta > 0 for delta in size_deltas)
    assert any(delta < 0 for delta in size_deltas)


def test_non_native_video_dialog_has_explicit_dark_palette_rules() -> None:
    style = DEFAULT_THEME.style_sheet()
    assert "QFileDialog QAbstractItemView" in style
    assert "QFileDialog QHeaderView::section" in style
    assert "QTableWidget, QTableView" in style
    assert f"background: {DEFAULT_THEME.colors.canvas}" in style
    assert f"color: {DEFAULT_THEME.colors.text}" in style


def test_statistics_combines_multiple_direct_scalar_connections() -> None:
    registry = create_application_registry()
    context = frame_context(clock_id=NODE_ID)
    statistics = registry.require("synmachine.utility.statistics")
    parameters, errors = statistics.parameter_values({"statistic": "MEAN"})
    assert not errors
    runtime = statistics.runtime_factory(NODE_ID)

    # Several independent single-value connections combine into one statistic.
    mean = runtime.process({"values_1": 1.0, "values_2": 3.0}, parameters, context)["value"]
    assert mean == 2.0

    # Family-level minimum: the scheduler binds only connected sockets, so a
    # node whose only connection is values_2 passes the runtime a values_2-only
    # mapping; the statistic is computed over exactly those samples.
    assert runtime.process({"values_2": 3.0}, parameters, context)["value"] == 3.0

    min_parameters, _ = statistics.parameter_values({"statistic": "MINIMUM"})
    minimum = runtime.process(
        {"values_1": 5, "values_2": 2, "values_3": 9}, min_parameters, context
    )["value"]
    assert minimum == 2
    assert isinstance(minimum, int)

    # A mixed INT+FLOAT set resolves to FLOAT in the compiler, so MINIMUM /
    # MAXIMUM must emit a float even when the first sample is an int (the
    # int coercion applies only when every sample is an int).
    mixed_min = runtime.process({"values_1": 5, "values_2": 2.5}, min_parameters, context)["value"]
    assert mixed_min == 2.5
    assert isinstance(mixed_min, float)

    max_parameters, _ = statistics.parameter_values({"statistic": "MAXIMUM"})
    mixed_max = runtime.process({"values_1": 1, "values_2": 7.5}, max_parameters, context)["value"]
    assert mixed_max == 7.5
    assert isinstance(mixed_max, float)


def test_statistics_combines_multiple_direct_image_connections() -> None:
    registry = create_application_registry()
    context = frame_context(clock_id=NODE_ID)

    def make_image(fill: float) -> ImageFrame:
        return ImageFrame(
            read_only_float32(np.full((4, 4, 3), fill, dtype=np.float32)),
            ColorSpace.SRGB,
            ("R", "G", "B"),
            AlphaMode.NONE,
            context,
            FrameProvenance(NODE_ID, "statistics-test"),
        )

    dark = make_image(0.0)
    bright = make_image(2.0)
    statistics = registry.require("synmachine.utility.statistics")
    parameters, errors = statistics.parameter_values({"statistic": "MEAN"})
    assert not errors
    result = statistics.runtime_factory(NODE_ID).process(
        {"values_1": dark, "values_2": bright}, parameters, context
    )["value"]

    assert isinstance(result, ImageFrame)
    assert result.data.shape == (4, 4, 3)
    assert np.allclose(result.data, 1.0)
    assert result.color_space is dark.color_space
    assert result.channel_names == dark.channel_names
