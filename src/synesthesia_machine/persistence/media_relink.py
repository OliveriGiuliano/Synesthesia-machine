"""Missing-media discovery, identity checks, and pure relink transformations."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from synesthesia_machine.graph import GraphSnapshot, NodeModel
from synesthesia_machine.media_path import normalize_media_path
from synesthesia_machine.nodes.input import create_input_definitions

MEDIA_ABSOLUTE_FALLBACK_KEY = "media_absolute_fallback"
MEDIA_FINGERPRINT_KEY = "media_fingerprint"
MEDIA_SIZE_KEY = "media_size_bytes"
_FINGERPRINT_PREFIX = "sha256-sampled-v1:"
_FINGERPRINT_BLOCK_SIZE = 1024 * 1024

# Media-reference parameters declared by the built-in input node definitions;
# persistence resolves them from the declarations instead of special-casing
# one node type and parameter name.
_MEDIA_PARAMETER_IDS: Mapping[str, str] = {
    definition.execution.type_id: parameter_id
    for definition in create_input_definitions()
    if definition.persistence is not None
    and (parameter_id := definition.persistence.media_parameter_id) is not None
}


def media_parameter_id(type_id: str) -> str | None:
    """Return the media reference parameter declared by ``type_id``, if any."""

    return _MEDIA_PARAMETER_IDS.get(type_id)


@dataclass(frozen=True, slots=True)
class MissingMediaReference:
    node_id: UUID
    parameter_id: str
    missing_path: Path
    absolute_fallback: Path | None
    fingerprint: str | None
    size_bytes: int | None


class RelinkMatch(StrEnum):
    EXACT = "EXACT"
    UNVERIFIED = "UNVERIFIED"
    MISMATCH = "MISMATCH"


@dataclass(frozen=True, slots=True)
class RelinkVerification:
    match: RelinkMatch
    message: str


def media_fingerprint(path: str | Path) -> str:
    """Return a bounded-cost content identity using file size plus edge samples."""

    source = normalize_media_path(path).resolve()
    size = source.stat().st_size
    digest = hashlib.sha256()
    digest.update(b"synmachine-media-fingerprint-v1\0")
    digest.update(size.to_bytes(16, byteorder="big", signed=False))
    with source.open("rb") as stream:
        digest.update(stream.read(_FINGERPRINT_BLOCK_SIZE))
        if size > _FINGERPRINT_BLOCK_SIZE * 2:
            stream.seek(size - _FINGERPRINT_BLOCK_SIZE)
            digest.update(stream.read(_FINGERPRINT_BLOCK_SIZE))
        elif size > _FINGERPRINT_BLOCK_SIZE:
            digest.update(stream.read())
    return f"{_FINGERPRINT_PREFIX}{digest.hexdigest()}"


def find_missing_media(snapshot: GraphSnapshot) -> tuple[MissingMediaReference, ...]:
    references: list[MissingMediaReference] = []
    for node in snapshot.nodes:
        parameter_id = media_parameter_id(node.type_id)
        if parameter_id is None:
            continue
        raw_path = node.parameters.get(parameter_id)
        if not isinstance(raw_path, str) or not raw_path:
            continue
        missing_path = normalize_media_path(raw_path).resolve()
        if missing_path.is_file():
            continue
        raw_fallback = node.ui_state.get(MEDIA_ABSOLUTE_FALLBACK_KEY)
        fallback = (
            normalize_media_path(raw_fallback).resolve()
            if isinstance(raw_fallback, str) and raw_fallback
            else None
        )
        raw_fingerprint = node.ui_state.get(MEDIA_FINGERPRINT_KEY)
        fingerprint = raw_fingerprint if isinstance(raw_fingerprint, str) else None
        raw_size = node.ui_state.get(MEDIA_SIZE_KEY)
        size_bytes = (
            raw_size if isinstance(raw_size, int) and not isinstance(raw_size, bool) else None
        )
        references.append(
            MissingMediaReference(
                node.id,
                parameter_id,
                missing_path,
                fallback,
                fingerprint,
                size_bytes,
            )
        )
    return tuple(sorted(references, key=lambda item: str(item.node_id)))


def verify_relink_candidate(
    reference: MissingMediaReference,
    candidate: str | Path,
) -> RelinkVerification:
    path = normalize_media_path(candidate).resolve()
    if not path.is_file():
        return RelinkVerification(RelinkMatch.MISMATCH, f"The selected file does not exist: {path}")
    size = path.stat().st_size
    if reference.size_bytes is not None and size != reference.size_bytes:
        return RelinkVerification(
            RelinkMatch.MISMATCH,
            f"File size differs: expected {reference.size_bytes} bytes, found {size} bytes.",
        )
    if reference.fingerprint is None:
        return RelinkVerification(
            RelinkMatch.UNVERIFIED,
            "No stored fingerprint is available; the explicitly selected file can still be used.",
        )
    if media_fingerprint(path) != reference.fingerprint:
        return RelinkVerification(
            RelinkMatch.MISMATCH,
            "The selected file does not match the stored media fingerprint.",
        )
    return RelinkVerification(RelinkMatch.EXACT, "The selected file matches the stored identity.")


def relinked_media_node(node: NodeModel, candidate: str | Path) -> NodeModel:
    """Return a complete node value with a new verified-on-save media identity."""

    parameter_id = media_parameter_id(node.type_id)
    if parameter_id is None:
        raise ValueError(f"{node.type_id!r} declares no relinkable media reference")
    path = normalize_media_path(candidate).resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    parameters = dict(node.parameters)
    parameters[parameter_id] = str(path)
    ui_state = dict(node.ui_state)
    ui_state[MEDIA_ABSOLUTE_FALLBACK_KEY] = str(path)
    ui_state[MEDIA_FINGERPRINT_KEY] = media_fingerprint(path)
    ui_state[MEDIA_SIZE_KEY] = path.stat().st_size
    return replace(node, parameters=parameters, ui_state=ui_state)


__all__ = [
    "MEDIA_ABSOLUTE_FALLBACK_KEY",
    "MEDIA_FINGERPRINT_KEY",
    "MEDIA_SIZE_KEY",
    "MissingMediaReference",
    "RelinkMatch",
    "RelinkVerification",
    "find_missing_media",
    "media_fingerprint",
    "relinked_media_node",
    "verify_relink_candidate",
]
