"""Cross-platform media-path normalization and persistence contracts."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.graph import GraphDocument
from synesthesia_machine.media_path import normalize_media_path
from synesthesia_machine.nodes.input import LOAD_VIDEO_TYPE_ID
from synesthesia_machine.persistence import graph_to_json, load_graph, save_graph


def test_normalize_accepts_shell_quoted_paths() -> None:
    backslash = chr(92)
    windows_form = '"C:' + backslash + "Clip" + backslash + 'movie.mp4"'
    if os.name == "nt":
        assert normalize_media_path(windows_form) == Path(
            "C:" + backslash + "Clip" + backslash + "movie.mp4"
        )
    else:
        # On POSIX the quoted Windows form is reinterpreted with forward slashes.
        assert normalize_media_path(windows_form) == Path("C:/Clip/movie.mp4")
    assert normalize_media_path("  'movie.mp4'  ") == Path("movie.mp4")


@pytest.mark.skipif(os.name == "nt", reason="backslash interop rule applies to POSIX hosts")
def test_normalize_interprets_backslash_separators_on_posix() -> None:
    # Documents saved on Windows persist media paths with backslashes; POSIX hosts
    # must interpret them as separators so the media resolves identically.
    assert normalize_media_path("media\\reference.mp4") == Path("media/reference.mp4")
    assert normalize_media_path("C:\\Users\\dev\\movie.mp4") == Path("C:/Users/dev/movie.mp4")


@pytest.mark.skipif(os.name != "nt", reason="native Windows separators apply on Windows hosts")
def test_normalize_keeps_windows_paths_natively() -> None:
    assert normalize_media_path("media\\reference.mp4") == Path("media/reference.mp4")
    assert normalize_media_path("media/reference.mp4") == Path("media/reference.mp4")


def test_saved_graph_persists_relative_media_paths_in_posix_form(tmp_path: Path) -> None:
    registry = create_application_registry()
    document = GraphDocument()
    definition = registry.require(LOAD_VIDEO_TYPE_ID)
    node_id = document.add_node(
        definition.type_id,
        implementation_version=definition.implementation_version,
        parameters={"file_path": "media/reference.mp4", "loop": False},
    )
    media = tmp_path / "media"
    media.mkdir()
    (media / "reference.mp4").write_bytes(b"video")
    graph_path = tmp_path / "graph.synmachine.json"
    save_graph(graph_path, document.snapshot())

    data = json.loads(graph_path.read_text(encoding="utf-8"))
    source = next(node for node in data["nodes"] if node["type_id"] == LOAD_VIDEO_TYPE_ID)
    # The persisted relative path must be plain JSON text usable on both platforms.
    assert source["parameters"]["file_path"] == "media/reference.mp4"

    reloaded = load_graph(graph_path, registry)
    loaded = reloaded.node(node_id)
    assert loaded is not None
    resolved = Path(str(loaded.parameters["file_path"]))
    assert resolved == (tmp_path / "media" / "reference.mp4").resolve()
    assert resolved.is_file()


@pytest.mark.skipif(os.name == "nt", reason="Windows hosts keep native backslash separators")
def test_windows_saved_document_resolves_on_posix_host(tmp_path: Path) -> None:
    registry = create_application_registry()
    media = tmp_path / "media"
    media.mkdir()
    (media / "reference.mp4").write_bytes(b"video")

    document = GraphDocument()
    definition = registry.require(LOAD_VIDEO_TYPE_ID)
    document.add_node(
        definition.type_id,
        implementation_version=definition.implementation_version,
        parameters={"file_path": "media\\reference.mp4", "loop": False},
    )
    graph_path = tmp_path / "graph.synmachine.json"
    # Persist the backslash form verbatim, as a Windows build would have written it.
    graph_path.write_text(
        graph_to_json(document.snapshot()).replace("media/reference.mp4", "media\\reference.mp4"),
        encoding="utf-8",
    )

    reloaded = load_graph(graph_path, registry)
    source = next(node for node in reloaded.nodes if node.type_id == LOAD_VIDEO_TYPE_ID)
    resolved = Path(str(source.parameters["file_path"]))
    assert resolved == (tmp_path / "media" / "reference.mp4").resolve()
    assert resolved.is_file()


def test_application_paths_follow_the_host_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    from synesthesia_machine.app.settings import data_base

    sandbox = "/home/tester/data"
    monkeypatch.setattr(os, "name", "nt")
    monkeypatch.setenv("LOCALAPPDATA", sandbox)
    assert data_base() == sandbox

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", sandbox)
    assert data_base() == sandbox


def test_application_paths_default_to_xdg_on_posix(monkeypatch: pytest.MonkeyPatch) -> None:
    from synesthesia_machine.app.settings import data_base

    monkeypatch.setattr(os, "name", "posix")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/tester")
    assert data_base() == "/home/tester/.local/share"


def test_application_icon_selection_follows_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    from synesthesia_machine.app import application as app_module

    monkeypatch.setattr(os, "name", "nt")
    assert app_module._application_icon_name() == "synesthesia-machine.ico"  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(os, "name", "posix")
    assert app_module._application_icon_name() == "app-icon-master.png"  # pyright: ignore[reportPrivateUsage]
