"""Phase 7 batch 6 portable media identity and undoable relinking."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.persistence import (
    MEDIA_ABSOLUTE_FALLBACK_KEY,
    MEDIA_FINGERPRINT_KEY,
    MEDIA_SIZE_KEY,
    RelinkMatch,
    find_missing_media,
    load_graph,
    save_graph,
    verify_relink_candidate,
)
from synesthesia_machine.ui.session import DocumentSession


def _saved_video_graph(root: Path) -> tuple[Path, Path]:
    media_path = root / "media" / "clip.mp4"
    media_path.parent.mkdir(parents=True)
    media_path.write_bytes(b"phase-7-video-identity" * 4096)
    graph_path = root / "workspace.synmachine.json"
    document = GraphDocument()
    document.add_node(
        "synmachine.input.load_video",
        parameters={"file_path": str(media_path)},
    )
    save_graph(graph_path, document.snapshot())
    return graph_path, media_path


def test_saved_media_is_relative_and_survives_project_tree_relocation(tmp_path: Path) -> None:
    graph_path, media_path = _saved_video_graph(tmp_path / "original")
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    source = payload["nodes"][0]

    assert Path(source["parameters"]["file_path"]) == Path("media/clip.mp4")
    assert source["ui_state"][MEDIA_ABSOLUTE_FALLBACK_KEY] == str(media_path.resolve())
    assert source["ui_state"][MEDIA_FINGERPRINT_KEY].startswith("sha256-sampled-v1:")
    assert source["ui_state"][MEDIA_SIZE_KEY] == media_path.stat().st_size

    relocated_root = tmp_path / "relocated"
    shutil.copytree(graph_path.parent, relocated_root)
    relocated_graph = relocated_root / graph_path.name
    snapshot = load_graph(relocated_graph, create_application_registry())

    assert not find_missing_media(snapshot)
    assert (
        Path(str(snapshot.nodes[0].parameters["file_path"]))
        == (relocated_root / "media" / "clip.mp4").resolve()
    )


def test_shell_quoted_media_path_is_normalized_for_save_and_missing_checks(
    tmp_path: Path,
) -> None:
    media_path = tmp_path / "media" / "quoted clip.mkv"
    media_path.parent.mkdir()
    media_path.write_bytes(b"quoted-media")
    graph_path = tmp_path / "quoted.synmachine.json"
    document = GraphDocument()
    document.add_node(
        "synmachine.input.load_video",
        parameters={"file_path": f'"{media_path}"'},
    )

    assert not find_missing_media(document.snapshot())
    save_graph(graph_path, document.snapshot())
    payload = json.loads(graph_path.read_text(encoding="utf-8"))
    loaded = load_graph(graph_path, create_application_registry())

    assert Path(payload["nodes"][0]["parameters"]["file_path"]) == Path("media/quoted clip.mkv")
    assert loaded.nodes[0].parameters["file_path"] == str(media_path.resolve())
    assert not find_missing_media(loaded)


def test_missing_media_relink_verifies_identity_and_undoes_exactly(tmp_path: Path) -> None:
    graph_path, media_path = _saved_video_graph(tmp_path / "project")
    relocated_media = tmp_path / "library" / "renamed.mp4"
    relocated_media.parent.mkdir()
    media_path.replace(relocated_media)
    registry = create_application_registry()
    session = DocumentSession(registry)
    session.open_document(graph_path)
    old_node = session.document.nodes[0]
    reference = find_missing_media(session.document.snapshot())[0]

    verification = verify_relink_candidate(reference, relocated_media)
    assert verification.match is RelinkMatch.EXACT
    session.relink_media(reference.node_id, relocated_media)
    relinked_node = session.document.node(reference.node_id)
    assert relinked_node is not None
    assert relinked_node.parameters["file_path"] == str(relocated_media.resolve())
    assert not find_missing_media(session.document.snapshot())

    session.undo_stack.undo()
    assert session.document.node(reference.node_id) == old_node
    assert find_missing_media(session.document.snapshot()) == (reference,)
    session.undo_stack.redo()
    assert session.document.node(reference.node_id) == relinked_node


def test_relink_mismatch_is_reported_without_silent_filename_matching(tmp_path: Path) -> None:
    graph_path, media_path = _saved_video_graph(tmp_path / "project")
    media_path.unlink()
    impostor = tmp_path / "elsewhere" / media_path.name
    impostor.parent.mkdir()
    impostor.write_bytes(b"different-media")
    reference = find_missing_media(load_graph(graph_path, create_application_registry()))[0]

    verification = verify_relink_candidate(reference, impostor)

    assert verification.match is RelinkMatch.MISMATCH
    assert "differs" in verification.message
