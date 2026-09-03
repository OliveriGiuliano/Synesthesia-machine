"""Seedable construction of complete, compiler-valid built-in graphs."""

from __future__ import annotations

import random
from collections.abc import Iterable
from dataclasses import dataclass, replace
from math import ceil, floor
from uuid import UUID

from synesthesia_machine.contracts import ColorValue, ParameterValue, PortType
from synesthesia_machine.graph.compiler import GraphCompiler
from synesthesia_machine.graph.model import (
    ConnectionModel,
    GraphDocument,
    GraphSnapshot,
    LiteralValue,
    NodeModel,
)
from synesthesia_machine.nodes import ExecutionKind, NodeDefinition, NodeRegistry, ParameterSpec


@dataclass(frozen=True, slots=True)
class _Transform:
    type_id: str
    input_port: str
    output_port: str


_IMAGE_TRANSFORMS = (
    _Transform("synmachine.image.resize", "image", "image"),
    _Transform("synmachine.image.crop", "image", "image"),
    _Transform("synmachine.image.flip", "image", "image"),
    _Transform("synmachine.image.rotate", "image", "image"),
    _Transform("synmachine.image.clamp", "image", "image"),
    _Transform("synmachine.image.add_scalar", "image", "image"),
    _Transform("synmachine.image.multiply_scalar", "image", "image"),
    _Transform("synmachine.image.gaussian_blur", "image", "image"),
    _Transform("synmachine.image.sharpen", "image", "image"),
    _Transform("synmachine.image.posterize", "image", "image"),
    _Transform("synmachine.image.high_pass", "image", "image"),
    _Transform("synmachine.image.low_pass", "image", "image"),
    _Transform("synmachine.image.posterize_time", "image", "image"),
    _Transform("synmachine.utility.normalize", "value", "value"),
    _Transform("synmachine.utility.curve", "value", "value"),
    _Transform("synmachine.utility.modulo_accumulator", "value", "value"),
)

_CHANNEL_TRANSFORMS = (
    _Transform("synmachine.image.clamp", "image", "image"),
    _Transform("synmachine.image.add_scalar", "image", "image"),
    _Transform("synmachine.image.multiply_scalar", "image", "image"),
    _Transform("synmachine.image.gaussian_blur", "image", "image"),
    _Transform("synmachine.image.threshold", "channel", "channel"),
    _Transform("synmachine.utility.normalize", "value", "value"),
    _Transform("synmachine.utility.curve", "value", "value"),
    _Transform("synmachine.utility.modulo_accumulator", "value", "value"),
)

_CONTEXT_DEPENDENT_PARAMETERS = {
    "synmachine.input.load_video": frozenset({"file_path", "stream_index"}),
    "synmachine.input.load_camera": frozenset(
        {"device_id", "backend", "requested_width", "requested_height", "requested_fps"}
    ),
    "synmachine.output.send_midi": frozenset({"output_port"}),
    "synmachine.image.crop": frozenset({"left", "top", "right", "bottom"}),
}


def generate_random_graph(
    registry: NodeRegistry,
    *,
    seed: int | None = None,
) -> GraphSnapshot:
    """Build a connected source→image→channel→MIDI→sink pipeline."""

    generator = random.Random(seed)
    document = GraphDocument()
    x_position = 0.0

    def add(type_id: str) -> UUID:
        nonlocal x_position
        definition = registry.require(type_id)
        node_id = document.add_node(
            type_id,
            implementation_version=definition.implementation_version,
            position=(x_position, generator.uniform(-72.0, 72.0)),
        )
        x_position += 300.0
        return node_id

    source_id = add(
        generator.choice(("synmachine.input.load_video", "synmachine.input.load_camera"))
    )
    current_id = source_id
    current_port = "image"

    image_candidates = list(_available(_IMAGE_TRANSFORMS, registry))
    generator.shuffle(image_candidates)
    for transform in image_candidates[: generator.randint(1, min(4, len(image_candidates)))]:
        node_id = add(transform.type_id)
        document.add_connection(current_id, current_port, node_id, transform.input_port)
        current_id, current_port = node_id, transform.output_port

    if (
        registry.get("synmachine.image.difference") is not None
        and registry.get("synmachine.image.flip") is not None
        and generator.random() < 0.35
    ):
        mirrored_id = add("synmachine.image.flip")
        difference_id = add("synmachine.image.difference")
        document.add_connection(current_id, current_port, difference_id, "a")
        document.add_connection(current_id, current_port, mirrored_id, "image")
        document.add_connection(mirrored_id, "image", difference_id, "b")
        current_id, current_port = difference_id, "image"

    luminance_id = add("synmachine.image.to_luminance")
    document.add_connection(current_id, current_port, luminance_id, "image")
    current_id, current_port = luminance_id, "channel"

    channel_candidates = list(_available(_CHANNEL_TRANSFORMS, registry))
    generator.shuffle(channel_candidates)
    for transform in channel_candidates[: generator.randint(0, min(3, len(channel_candidates)))]:
        node_id = add(transform.type_id)
        document.add_connection(current_id, current_port, node_id, transform.input_port)
        current_id, current_port = node_id, transform.output_port

    if (
        registry.get("synmachine.utility.buffer") is not None
        and registry.get("synmachine.utility.statistics") is not None
        and generator.random() < 0.45
    ):
        buffer_id = add("synmachine.utility.buffer")
        document.add_connection(current_id, current_port, buffer_id, "value")
        statistics_id = add("synmachine.utility.statistics")
        document.add_connection(buffer_id, "values", statistics_id, "values_1")
        current_id, current_port = statistics_id, "value"

    musical_id = add(
        generator.choice(
            ("synmachine.synesthesia.channel_to_pitch", "synmachine.synesthesia.edges_to_pitch")
        )
    )
    musical = document.node(musical_id)
    if musical is None:
        raise RuntimeError("Random musical node was not added")
    musical_input = (
        "value" if musical.type_id == "synmachine.synesthesia.channel_to_pitch" else "edges"
    )
    document.add_connection(current_id, current_port, musical_id, musical_input)

    sink_id = add(
        generator.choice(("synmachine.output.generate_audio", "synmachine.output.send_midi"))
    )
    document.add_connection(musical_id, "midi", sink_id, "midi")

    snapshot = _randomize_graph_parameters(
        document.snapshot(),
        registry,
        (node.id for node in document.nodes),
        generator,
    )
    compilation = GraphCompiler(registry).compile(snapshot)
    if not compilation.report.is_valid:
        details = "; ".join(issue.message for issue in compilation.report.errors)
        raise RuntimeError(f"Random graph generation produced an invalid graph: {details}")
    return snapshot


def randomize_graph_parameters(
    snapshot: GraphSnapshot,
    registry: NodeRegistry,
    node_ids: Iterable[UUID] | None = None,
    *,
    seed: int | None = None,
) -> GraphSnapshot:
    """Randomize selected node literals while preserving a valid graph when it started valid."""

    targets = {node.id for node in snapshot.nodes} if node_ids is None else set(node_ids)
    return _randomize_graph_parameters(snapshot, registry, targets, random.Random(seed))


def randomize_graph_nodes(
    snapshot: GraphSnapshot,
    registry: NodeRegistry,
    node_ids: Iterable[UUID],
    *,
    seed: int | None = None,
) -> tuple[GraphSnapshot, frozenset[UUID]]:
    """Replace selected nodes, preserving the surrounding graph and compiler validity."""

    targets = frozenset(node_ids)
    if not targets:
        return snapshot, frozenset()
    present = {node.id for node in snapshot.nodes}
    missing = targets - present
    if missing:
        raise KeyError(f"Unknown selected node: {min(missing, key=str)}")
    compiler = GraphCompiler(registry)
    if not compiler.compile(snapshot).report.is_valid:
        raise ValueError("Randomize Nodes requires a valid graph")

    generator = random.Random(seed)
    replacement = _replace_node_set(snapshot, registry, targets, generator, compiler)
    if replacement is None:
        raise RuntimeError("Could not produce a valid randomized node replacement")
    candidate, replacement_ids = replacement

    mutation_count = generator.randint(1, 3)
    if generator.choice(("add", "remove")) == "add":
        for _index in range(mutation_count):
            inserted = _insert_random_node(
                candidate, registry, replacement_ids, generator, compiler
            )
            if inserted is None:
                inserted = _add_random_standalone_node(
                    candidate,
                    registry,
                    replacement_ids,
                    generator,
                    compiler,
                )
            if inserted is None:
                break
            candidate, inserted_id = inserted
            replacement_ids = frozenset((*replacement_ids, inserted_id))
    else:
        for _index in range(mutation_count):
            removed = _remove_random_node(candidate, replacement_ids, generator, compiler)
            if removed is None:
                break
            candidate, removed_id = removed
            replacement_ids -= {removed_id}
    return candidate, replacement_ids


def _randomize_graph_parameters(
    snapshot: GraphSnapshot,
    registry: NodeRegistry,
    node_ids: Iterable[UUID],
    generator: random.Random,
) -> GraphSnapshot:
    targets = frozenset(node_ids)
    if not targets:
        return snapshot
    compiler = GraphCompiler(registry)
    require_valid = compiler.compile(snapshot).report.is_valid
    for _attempt in range(128):
        nodes = tuple(
            _randomized_node(node, registry.require(node.type_id), generator)
            if node.id in targets
            else node
            for node in snapshot.nodes
        )
        candidate = replace(snapshot, nodes=nodes)
        if candidate.nodes == snapshot.nodes:
            continue
        if not require_valid or compiler.compile(candidate).report.is_valid:
            return candidate
    return snapshot


def _randomized_node(
    node: NodeModel,
    definition: NodeDefinition,
    generator: random.Random,
) -> NodeModel:
    current, _errors = definition.parameter_values(node.parameters)
    for _attempt in range(32):
        candidate = {
            parameter.id: _random_parameter_value(
                definition.type_id,
                parameter,
                current[parameter.id],
                generator,
            )
            for parameter in definition.parameters
        }
        values, errors = definition.parameter_values(candidate)
        if not errors and values != current:
            return replace(node, parameters=values)
    return node


def _random_parameter_value(
    type_id: str,
    parameter: ParameterSpec,
    current: ParameterValue,
    generator: random.Random,
) -> ParameterValue:
    # Random graphs must never opt into hardware output by accident.
    if type_id == "synmachine.output.generate_audio" and parameter.id == "enabled":
        return False
    if parameter.id == "channels":
        return current
    if parameter.id in _CONTEXT_DEPENDENT_PARAMETERS.get(type_id, ()):
        return current
    if parameter.choices:
        return generator.choice(parameter.choices)
    if parameter.value_type is PortType.BOOL:
        return bool(generator.getrandbits(1))
    if parameter.value_type is PortType.INT:
        return (
            _random_integer(parameter, current, generator)
            if isinstance(current, int) and not isinstance(current, bool)
            else current
        )
    if parameter.value_type is PortType.FLOAT:
        return (
            _random_float(parameter, current, generator) if isinstance(current, float) else current
        )
    if parameter.value_type is PortType.COLOR:
        return ColorValue(
            generator.random(),
            generator.random(),
            generator.random(),
            generator.random(),
        )
    # Free-form strings (paths, device IDs, masks) and matrices need authored semantics.
    return current


def _random_integer(
    parameter: ParameterSpec,
    current: int,
    generator: random.Random,
) -> int:
    radius = max(3, min(100, abs(current)))
    lower = current - radius
    upper = current + radius
    if parameter.minimum is not None:
        lower = max(lower, ceil(parameter.minimum))
    if parameter.maximum is not None:
        upper = min(upper, floor(parameter.maximum))
    if lower > upper:
        return current
    randomized = parameter.sanitize_value(generator.randint(lower, upper))
    return (
        randomized if isinstance(randomized, int) and not isinstance(randomized, bool) else current
    )


def _random_float(
    parameter: ParameterSpec,
    current: float,
    generator: random.Random,
) -> float:
    span = max(1.0, abs(current) * 0.75)
    lower = current - span
    upper = current + span
    if parameter.minimum is not None:
        lower = max(lower, float(parameter.minimum))
    if parameter.maximum is not None:
        upper = min(upper, float(parameter.maximum))
    if lower > upper:
        return current
    return round(generator.uniform(lower, upper), 6)


def _available(
    transforms: tuple[_Transform, ...], registry: NodeRegistry
) -> tuple[_Transform, ...]:
    return tuple(
        transform for transform in transforms if registry.get(transform.type_id) is not None
    )


def _replace_node_set(
    snapshot: GraphSnapshot,
    registry: NodeRegistry,
    targets: frozenset[UUID],
    generator: random.Random,
    compiler: GraphCompiler,
) -> tuple[GraphSnapshot, frozenset[UUID]] | None:
    definitions = registry.definitions()
    for _attempt in range(192):
        id_map = {node_id: _random_uuid(generator) for node_id in sorted(targets, key=str)}
        replacement_nodes: list[NodeModel] = []
        for node in snapshot.nodes:
            if node.id not in targets:
                continue
            original_definition = registry.require(node.type_id)
            candidates = _replacement_definitions(original_definition, definitions)
            definition = generator.choice(candidates)
            parameters: dict[str, LiteralValue] = (
                dict(node.parameters) if definition.type_id == node.type_id else {}
            )
            replacement_node = NodeModel(
                id_map[node.id],
                definition.type_id,
                definition.implementation_version,
                parameters=parameters,
                position=node.position,
                size=node.size,
                user_label=node.user_label,
                collapsed=node.collapsed,
                ui_state=node.ui_state,
            )
            replacement_nodes.append(_randomized_node(replacement_node, definition, generator))

        untouched_nodes = tuple(node for node in snapshot.nodes if node.id not in targets)
        connections = tuple(
            connection
            if connection.source_node_id not in targets
            and connection.destination_node_id not in targets
            else ConnectionModel(
                _random_uuid(generator),
                id_map.get(connection.source_node_id, connection.source_node_id),
                connection.source_port_id,
                id_map.get(connection.destination_node_id, connection.destination_node_id),
                connection.destination_port_id,
            )
            for connection in snapshot.connections
        )
        candidate = replace(
            snapshot,
            nodes=(*untouched_nodes, *replacement_nodes),
            connections=connections,
        )
        if compiler.compile(candidate).report.is_valid:
            return candidate, frozenset(id_map.values())

    # A valid graph can always be structurally replaced with fresh IDs while retaining its
    # definitions. This deterministic fallback protects heavily parameter-connected selections
    # whose exact socket sets make alternative node types exceptionally unlikely.
    id_map = {node_id: _random_uuid(generator) for node_id in sorted(targets, key=str)}
    fallback_nodes = tuple(
        replace(node, id=id_map[node.id]) for node in snapshot.nodes if node.id in targets
    )
    fallback = replace(
        snapshot,
        nodes=(
            *(node for node in snapshot.nodes if node.id not in targets),
            *fallback_nodes,
        ),
        connections=tuple(
            connection
            if connection.source_node_id not in targets
            and connection.destination_node_id not in targets
            else ConnectionModel(
                _random_uuid(generator),
                id_map.get(connection.source_node_id, connection.source_node_id),
                connection.source_port_id,
                id_map.get(connection.destination_node_id, connection.destination_node_id),
                connection.destination_port_id,
            )
            for connection in snapshot.connections
        ),
    )
    replacement_ids = frozenset(id_map.values())
    randomized = _randomize_graph_parameters(fallback, registry, replacement_ids, generator)
    return randomized, replacement_ids


def _replacement_definitions(
    original: NodeDefinition,
    definitions: tuple[NodeDefinition, ...],
) -> tuple[NodeDefinition, ...]:
    if original.execution_kind is ExecutionKind.SOURCE or original.variadic_input is not None:
        return (original,)
    input_ids = tuple(port.id for port in original.inputs)
    output_ids = tuple(port.id for port in original.outputs)
    candidates = tuple(
        definition
        for definition in definitions
        if definition.execution_kind is original.execution_kind
        and definition.variadic_input is None
        and tuple(port.id for port in definition.inputs) == input_ids
        and tuple(port.id for port in definition.outputs) == output_ids
    )
    return candidates or (original,)


def _insert_random_node(
    snapshot: GraphSnapshot,
    registry: NodeRegistry,
    replacement_ids: frozenset[UUID],
    generator: random.Random,
    compiler: GraphCompiler,
) -> tuple[GraphSnapshot, UUID] | None:
    eligible_connections = [
        connection
        for connection in snapshot.connections
        if connection.source_node_id in replacement_ids
        or connection.destination_node_id in replacement_ids
    ]
    generator.shuffle(eligible_connections)
    unary_definitions = [
        definition
        for definition in registry.definitions()
        if definition.execution_kind is not ExecutionKind.SOURCE
        and definition.variadic_input is None
        and len(definition.inputs) == 1
        and len(definition.outputs) == 1
        and definition.required_inputs(definition.parameter_values({})[0])
        == frozenset({definition.inputs[0].id})
    ]
    generator.shuffle(unary_definitions)
    nodes = {node.id: node for node in snapshot.nodes}
    for connection in eligible_connections:
        source = nodes[connection.source_node_id]
        destination = nodes[connection.destination_node_id]
        position = (
            (source.position[0] + destination.position[0]) / 2.0,
            (source.position[1] + destination.position[1]) / 2.0 + generator.uniform(-50.0, 50.0),
        )
        for definition in unary_definitions:
            node_id = _random_uuid(generator)
            node = _randomized_node(
                NodeModel(
                    node_id,
                    definition.type_id,
                    definition.implementation_version,
                    position=position,
                ),
                definition,
                generator,
            )
            retained = tuple(item for item in snapshot.connections if item.id != connection.id)
            candidate = replace(
                snapshot,
                nodes=(*snapshot.nodes, node),
                connections=(
                    *retained,
                    ConnectionModel(
                        _random_uuid(generator),
                        connection.source_node_id,
                        connection.source_port_id,
                        node_id,
                        definition.inputs[0].id,
                    ),
                    ConnectionModel(
                        _random_uuid(generator),
                        node_id,
                        definition.outputs[0].id,
                        connection.destination_node_id,
                        connection.destination_port_id,
                    ),
                ),
            )
            if compiler.compile(candidate).report.is_valid:
                return candidate, node_id
    return None


def _add_random_standalone_node(
    snapshot: GraphSnapshot,
    registry: NodeRegistry,
    replacement_ids: frozenset[UUID],
    generator: random.Random,
    compiler: GraphCompiler,
) -> tuple[GraphSnapshot, UUID] | None:
    definitions = [
        definition
        for definition in registry.definitions()
        if definition.execution_kind is not ExecutionKind.SOURCE
        and definition.outputs
        and not definition.required_inputs(definition.parameter_values({})[0])
    ]
    generator.shuffle(definitions)
    selected_nodes = [node for node in snapshot.nodes if node.id in replacement_ids]
    anchor_x = sum(node.position[0] for node in selected_nodes) / len(selected_nodes)
    anchor_y = sum(node.position[1] for node in selected_nodes) / len(selected_nodes)
    for definition in definitions:
        node_id = _random_uuid(generator)
        node = _randomized_node(
            NodeModel(
                node_id,
                definition.type_id,
                definition.implementation_version,
                position=(
                    anchor_x + generator.uniform(-100.0, 100.0),
                    anchor_y + generator.uniform(-100.0, 100.0),
                ),
            ),
            definition,
            generator,
        )
        candidate = replace(snapshot, nodes=(*snapshot.nodes, node))
        if compiler.compile(candidate).report.is_valid:
            return candidate, node_id
    return None


def _remove_random_node(
    snapshot: GraphSnapshot,
    replacement_ids: frozenset[UUID],
    generator: random.Random,
    compiler: GraphCompiler,
) -> tuple[GraphSnapshot, UUID] | None:
    """Remove a randomized node, bypassing a unary transform when necessary."""

    if len(replacement_ids) <= 1:
        return None
    candidates = [node_id for node_id in replacement_ids if snapshot.node(node_id) is not None]
    generator.shuffle(candidates)
    for node_id in candidates:
        incoming = tuple(
            connection
            for connection in snapshot.connections
            if connection.destination_node_id == node_id
        )
        outgoing = tuple(
            connection
            for connection in snapshot.connections
            if connection.source_node_id == node_id
        )
        # Preserve connected graph boundaries: sources have no incoming edge and demand roots have
        # no outgoing edge. Internal transforms can be bypassed, while disconnected experimental
        # nodes can be removed directly.
        if not ((incoming and outgoing) or (not incoming and not outgoing)):
            continue
        retained_connections = tuple(
            connection
            for connection in snapshot.connections
            if connection.source_node_id != node_id and connection.destination_node_id != node_id
        )
        without_node = replace(
            snapshot,
            nodes=tuple(node for node in snapshot.nodes if node.id != node_id),
            connections=retained_connections,
        )
        if compiler.compile(without_node).report.is_valid:
            return without_node, node_id

        if len(incoming) != 1 or not outgoing:
            continue
        source = incoming[0]
        bypass_connections = tuple(
            ConnectionModel(
                _random_uuid(generator),
                source.source_node_id,
                source.source_port_id,
                connection.destination_node_id,
                connection.destination_port_id,
            )
            for connection in outgoing
        )
        bypassed = replace(
            without_node,
            connections=(*retained_connections, *bypass_connections),
        )
        if compiler.compile(bypassed).report.is_valid:
            return bypassed, node_id
    return None


def _random_uuid(generator: random.Random) -> UUID:
    return UUID(int=generator.getrandbits(128), version=4)


__all__ = ["generate_random_graph", "randomize_graph_nodes", "randomize_graph_parameters"]
