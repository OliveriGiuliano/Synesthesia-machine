"""Qt-free recovery-file storage and discovery for editor autosave."""

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from synesthesia_machine.graph import GraphSnapshot
from synesthesia_machine.persistence.graph_io import save_graph


@dataclass(frozen=True, slots=True)
class RecoveryRecord:
    """One validly named recovery file, newest records sorting first."""

    document_id: UUID
    path: Path
    modified_time_ns: int

    def is_newer_than(self, explicit_path: Path | None) -> bool:
        if explicit_path is None or not explicit_path.exists():
            return True
        return self.modified_time_ns > explicit_path.stat().st_mtime_ns


class AutosaveStore:
    def __init__(self, recovery_directory: Path) -> None:
        self.recovery_directory = recovery_directory

    def path_for(self, document_id: UUID) -> Path:
        return self.recovery_directory / f"{document_id}.recovery.synmachine.json"

    def save(self, snapshot: GraphSnapshot) -> Path:
        self.recovery_directory.mkdir(parents=True, exist_ok=True)
        path = self.path_for(snapshot.document_id)
        save_graph(path, snapshot)
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
            records.append(RecoveryRecord(document_id, path, modified_time_ns))
        return tuple(sorted(records, key=lambda item: item.modified_time_ns, reverse=True))

    def discard(self, document_id: UUID) -> None:
        self.path_for(document_id).unlink(missing_ok=True)
