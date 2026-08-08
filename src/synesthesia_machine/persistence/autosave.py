"""Qt-free recovery-file storage and discovery for editor autosave."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

from synesthesia_machine.graph import GraphSnapshot
from synesthesia_machine.persistence.graph_io import save_graph, write_text_atomically

RECOVERY_MANIFEST_VERSION = 1


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    """One validly named recovery file, newest records sorting first."""

    document_id: UUID
    path: Path
    modified_time_ns: int
    explicit_path: Path | None = None

    def is_newer_than(self, explicit_path: Path | None) -> bool:
        if explicit_path is None or not explicit_path.exists():
            return True
        return self.modified_time_ns > explicit_path.stat().st_mtime_ns


class AutosaveStore:
    def __init__(self, recovery_directory: Path) -> None:
        self.recovery_directory = recovery_directory

    def path_for(self, document_id: UUID) -> Path:
        return self.recovery_directory / f"{document_id}.recovery.synmachine.json"

    def metadata_path_for(self, document_id: UUID) -> Path:
        return self.recovery_directory / f"{document_id}.recovery.json"

    def save(
        self,
        snapshot: GraphSnapshot,
        *,
        explicit_path: str | Path | None = None,
    ) -> Path:
        self.recovery_directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(snapshot.document_id)
        save_graph(path, snapshot, retain_backup=False)
        resolved_explicit_path = (
            None if explicit_path is None else str(Path(explicit_path).expanduser().resolve())
        )
        manifest = {
            "document_id": str(snapshot.document_id),
            "explicit_path": resolved_explicit_path,
            "recovery_file": path.name,
            "schema_version": RECOVERY_MANIFEST_VERSION,
        }
        write_text_atomically(
            self.metadata_path_for(snapshot.document_id),
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            retain_backup=False,
        )
        return path

    def discover(self) -> tuple[RecoveryRecord, ...]:
        """Return recovery candidates without parsing their graph payloads."""

        if not self.recovery_directory.exists():
            return ()
        records: list[RecoveryRecord] = []
        suffix = ".recovery.synmachine.json"
        for path in self.recovery_directory.glob(f"*{suffix}"):
            try:
                document_id = UUID(path.name.removesuffix(suffix))
                modified_time_ns = path.stat().st_mtime_ns
            except (OSError, ValueError):
                continue
            explicit_path = self._read_explicit_path(document_id, path)
            records.append(RecoveryRecord(document_id, path, modified_time_ns, explicit_path))
        return tuple(sorted(records, key=lambda item: item.modified_time_ns, reverse=True))

    def discard(self, document_id: UUID) -> None:
        self.path_for(document_id).unlink(missing_ok=True)
        self.metadata_path_for(document_id).unlink(missing_ok=True)
        self.path_for(document_id).with_name(f"{self.path_for(document_id).name}.bak").unlink(
            missing_ok=True
        )

    def _read_explicit_path(self, document_id: UUID, recovery_path: Path) -> Path | None:
        metadata_path = self.metadata_path_for(document_id)
        try:
            payload_object: object = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload_object, dict):
            return None
        payload = cast("dict[str, object]", payload_object)
        if payload.get("schema_version") != RECOVERY_MANIFEST_VERSION:
            return None
        if payload.get("document_id") != str(document_id):
            return None
        if payload.get("recovery_file") != recovery_path.name:
            return None
        raw_path = payload.get("explicit_path")
        if raw_path is None:
            return None
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None
        return Path(raw_path).expanduser().resolve()
