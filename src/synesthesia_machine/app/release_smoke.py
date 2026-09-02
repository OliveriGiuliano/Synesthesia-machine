"""Self-contained release checks executed from the packaged application."""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

import av
import numpy as np
import sounddevice as sd

from synesthesia_machine import __version__
from synesthesia_machine.app.registry import create_application_registry
from synesthesia_machine.app.settings import ApplicationPaths
from synesthesia_machine.contracts import FrameContext, MidiNoteKey, MidiStateFrame
from synesthesia_machine.diagnostics import create_diagnostic_bundle
from synesthesia_machine.graph import GraphCompiler, GraphDocument
from synesthesia_machine.media import enumerate_cameras, open_camera
from synesthesia_machine.midi import (
    DebugSynth,
    MidiMessageType,
    MidiOutputConfiguration,
    MidiOutputService,
    MidoRtMidiBackend,
    MockMidiOutputBackend,
    SynthConfiguration,
)
from synesthesia_machine.midi.debug_synth import AudioCallback
from synesthesia_machine.persistence import load_graph, save_graph
from synesthesia_machine.persistence.autosave import AutosaveStore
from synesthesia_machine.runtime import ProcessEngineClient


@dataclass(frozen=True, slots=True)
class SmokeCheck:
    name: str
    status: str
    detail: str


class _MemoryAudioStream:
    def __init__(self, callback: AudioCallback) -> None:
        self.callback = callback

    def start(self) -> object:
        return self

    def abort(self, ignore_errors: bool = True) -> object:
        del ignore_errors
        return self

    def close(self, ignore_errors: bool = True) -> object:
        del ignore_errors
        return self


def _frame() -> MidiStateFrame:
    source_id = UUID("00000000-0000-0000-0000-000000000901")
    context = FrameContext(source_id, 1, 0, 0.0, 1, None, False)
    return MidiStateFrame({MidiNoteKey(0, 69): 96}, context, source_id)


def _graph_round_trip(root: Path) -> str:
    registry = create_application_registry()
    document = GraphDocument()
    definition = registry.require("synmachine.utility.number")
    node_id = document.add_node(
        definition.type_id,
        implementation_version=definition.implementation_version,
        parameters={"number_type": "FLOAT", "float_value": 42.0},
    )
    graph_path = root / "release-smoke.synmachine.json"
    save_graph(graph_path, document.snapshot())
    loaded = load_graph(graph_path, registry)
    node = loaded.node(node_id)
    if node is None or node.parameters.get("float_value") != 42.0:
        raise RuntimeError("saved graph did not round-trip")
    compilation = GraphCompiler(registry).compile(loaded)
    if not compilation.report.is_valid or compilation.plan is None:
        details = "; ".join(issue.message for issue in compilation.report.errors)
        raise RuntimeError(f"round-tripped smoke graph did not compile: {details}")
    return f"saved, opened, and compiled {len(loaded.nodes)} node"


def _decode_h264(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    with av.open(str(path), mode="r") as container:
        if not container.streams.video:
            raise RuntimeError("test MP4 has no video stream")
        stream = container.streams.video[0]
        codec = stream.codec_context.name or "unknown"
        if codec != "h264":
            raise RuntimeError(f"expected H.264, found {codec}")
        frame = next(container.decode(stream), None)  # pyright: ignore[reportUnknownMemberType]
        if frame is None:
            raise RuntimeError("H.264 stream yielded no frame")
        array = frame.to_ndarray(format="rgb24")
    return f"decoded H.264 frame {array.shape[1]}x{array.shape[0]}"


def _camera_probe() -> tuple[str, str]:
    devices = enumerate_cameras()
    if not devices:
        return "skipped", "enumeration succeeded; no camera hardware was available"
    device = devices[0]
    opened = open_camera(device.index)
    try:
        ok, frame = opened.capture.read()
        if not ok or frame is None:
            raise RuntimeError(f"camera {device.device_id} opened but returned no frame")
    finally:
        opened.capture.release()
    return "passed", f"enumerated {len(devices)} camera(s) and captured from {device.device_id}"


def _midi_probe() -> str:
    real_names = tuple(MidoRtMidiBackend().output_names())
    backend = MockMidiOutputBackend()
    service = MidiOutputService(backend, refresh_interval_s=60.0)
    try:
        if not service.wait_until_idle():
            raise TimeoutError("mock MIDI enumeration did not become idle")
        service.publish(_frame(), MidiOutputConfiguration(output_port="Mock MIDI Out"))
        if not service.wait_until_idle():
            raise TimeoutError("mock MIDI send did not become idle")
        if not backend.opened_ports:
            raise RuntimeError("mock MIDI port was not opened")
        sent_types = tuple(message.message_type for message in backend.opened_ports[0].sent)
        if MidiMessageType.NOTE_ON not in sent_types:
            raise RuntimeError("mock MIDI port received no note-on")
        service.panic()
    finally:
        service.close()
    return f"enumerated {len(real_names)} real output(s); mock send and panic passed"


def _audio_probe() -> tuple[str, str]:
    try:
        raw_devices = sd.query_devices()  # pyright: ignore[reportUnknownMemberType]
    except OSError as error:
        # Linux resolves PortAudio from the system (libportaudio2); a machine
        # without it cannot use the optional debug-audio feature but every other
        # release check still applies, so this is a skip, not a failure.
        return "skipped", f"PortAudio runtime unavailable: {error}"
    devices = cast("list[dict[str, object]]", raw_devices)
    stream_holder: list[_MemoryAudioStream] = []

    def stream_factory(
        configuration: SynthConfiguration, callback: AudioCallback
    ) -> _MemoryAudioStream:
        del configuration
        stream = _MemoryAudioStream(callback)
        stream_holder.append(stream)
        return stream

    configuration = SynthConfiguration(block_size=64, attack_ms=0.0)
    synth = DebugSynth(configuration, stream_factory=stream_factory)
    try:
        synth.update(_frame())
        output = np.zeros((configuration.block_size, 2), dtype=np.float32)
        stream_holder[0].callback(output, configuration.block_size, object(), object())
        if not np.isfinite(output).all() or not np.any(output):
            raise RuntimeError("debug synth did not produce a finite audio block")
    finally:
        synth.close()

    output_devices = [
        device for device in devices if int(cast("int", device.get("max_output_channels", 0))) > 0
    ]
    hardware_detail = "no hardware output available"
    if output_devices:
        audible = DebugSynth(SynthConfiguration(volume=0.03, block_size=256))
        try:
            audible.update(_frame())
            time.sleep(0.15)
            audible.panic()
        finally:
            audible.close()
        hardware_detail = "brief hardware output stream passed"
    return (
        "passed",
        f"PortAudio loaded with {len(devices)} device record(s); "
        f"synth render passed; {hardware_detail}",
    )


def _engine_restart(root: Path) -> str:
    client = ProcessEngineClient(
        request_timeout_s=5.0,
        close_timeout_s=1.0,
        crash_log_path=root / "engine-crash.log",
    )
    try:
        first_pid = client.status().child_process_id
        if first_pid is None:
            raise RuntimeError("engine child did not publish a process ID")
        client.force_terminate()
        client.restart()
        second_pid = client.status().child_process_id
        if second_pid is None or second_pid == first_pid:
            raise RuntimeError("engine restart did not create a replacement child")
    finally:
        client.close()
    return f"engine restarted from PID {first_pid} to PID {second_pid}"


def _autosave_and_diagnostics(root: Path) -> str:
    registry = create_application_registry()
    document = GraphDocument()
    definition = registry.require("synmachine.utility.number")
    document.add_node(definition.type_id, implementation_version=definition.implementation_version)
    store = AutosaveStore(root / "recovery")
    recovery_path = store.save(document.snapshot())
    discovered = store.discover()
    if len(discovered) != 1 or discovered[0].path != recovery_path:
        raise RuntimeError("autosave recovery record was not discovered")
    recovered = load_graph(recovery_path, registry)
    bundle = create_diagnostic_bundle(root / "diagnostics.zip", recovered)
    if not bundle.path.is_file() or "manifest.json" not in bundle.included_files:
        raise RuntimeError("diagnostic export was incomplete")
    return "autosave discovery/load and redacted diagnostic export passed"


def _run_check(name: str, operation: Callable[[], str]) -> SmokeCheck:
    try:
        return SmokeCheck(name, "passed", operation())
    except Exception as error:
        return SmokeCheck(name, "failed", f"{type(error).__name__}: {error}")


def _run_optional_check(name: str, operation: Callable[[], tuple[str, str]]) -> SmokeCheck:
    try:
        status, detail = operation()
        return SmokeCheck(name, status, detail)
    except Exception as error:
        return SmokeCheck(name, "failed", f"{type(error).__name__}: {error}")


def run_packaged_smoke(
    report_path: str | Path,
    *,
    h264_video: str | Path | None = None,
) -> int:
    """Run native/runtime release checks and emit a machine-readable report."""

    output = Path(report_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    paths = ApplicationPaths.for_current_user()
    paths.ensure_exists()
    with tempfile.TemporaryDirectory(prefix="synmachine-release-smoke-") as temporary:
        root = Path(temporary)
        checks = [
            _run_check("create_save_open_graph", lambda: _graph_round_trip(root)),
            _run_optional_check("camera_enumeration_capture", _camera_probe),
            _run_check("midi_enumeration_mock_send", _midi_probe),
            _run_optional_check("debug_audio", _audio_probe),
            _run_check("engine_crash_restart", lambda: _engine_restart(root)),
            _run_check(
                "autosave_recovery_diagnostics",
                lambda: _autosave_and_diagnostics(root),
            ),
        ]
        if h264_video is None:
            checks.insert(1, SmokeCheck("h264_mp4_decode", "failed", "no test video supplied"))
        else:
            video_path = Path(h264_video).expanduser().resolve()
            checks.insert(1, _run_check("h264_mp4_decode", lambda: _decode_h264(video_path)))

    passed = all(check.status != "failed" for check in checks)
    payload = {
        "schema_version": 1,
        "application_version": __version__,
        "passed": passed,
        "application_paths": {
            "data": str(paths.data),
            "logs": str(paths.logs),
            "recovery": str(paths.recovery),
        },
        "checks": [asdict(check) for check in checks],
    }
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if passed else 1


__all__ = ["SmokeCheck", "run_packaged_smoke"]
