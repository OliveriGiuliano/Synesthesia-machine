"""Headless tests for the engine-status presenter.

The presenter is the interpretation layer between the bridge's published
``EngineBridgeState`` and the window's display decisions.  These tests drive
it with synthetic published states — no Qt, no engine, no window — and pin:
the periodic-refresh plan, the telemetry intents (error-digest announcements
and the healthy tooltip), the identity dedup of settled facts, the crash
dialog dedup/re-arm, the engine-status intents, and the status-message key
mapping.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from synesthesia_machine.contracts import (
    DeviceCatalogue,
    EngineActivation,
    EngineConnectionState,
    EngineMetrics,
    EngineState,
    EngineStatus,
    MidiOutputConnectionState,
    MidiOutputStatus,
    SourceState,
    SourceStatus,
    ValidationReport,
)
from synesthesia_machine.ui.engine_bridge import (
    EngineBridgeState,
    EngineRestartOutcome,
    TransportOutcome,
)
from synesthesia_machine.ui.engine_status_presenter import (
    EngineStatusPresenter,
    ErrorTooltip,
    TelemetryTooltip,
)


def _state(
    *,
    connection: EngineConnectionState = EngineConnectionState.CONNECTED,
    engine: EngineState = EngineState.STOPPED,
    status: EngineStatus | None = None,
    metrics: EngineMetrics | None = None,
    sources: tuple[SourceStatus, ...] = (),
    midi: tuple[MidiOutputStatus, ...] = (),
    known_stopped: bool = False,
) -> EngineBridgeState:
    return EngineBridgeState(
        connection_state=connection,
        engine_state=engine,
        status=status or EngineStatus(connection),
        metrics=metrics,
        source_statuses=sources,
        midi_output_statuses=midi,
        known_stopped=known_stopped,
    )


def _source(state: SourceState = SourceState.READY, last_error: str | None = None) -> SourceStatus:
    return SourceStatus(node_id=uuid4(), state=state, last_error=last_error)


def _midi(
    state: MidiOutputConnectionState = MidiOutputConnectionState.CONNECTED,
    selected_port: str = "Mock Port",
    last_error: str | None = None,
) -> MidiOutputStatus:
    return MidiOutputStatus(
        node_id=uuid4(),
        connection_state=state,
        selected_port=selected_port,
        last_error=last_error,
    )


def _activation(revision: int = 1, activated: bool = True) -> EngineActivation:
    return EngineActivation(graph_revision=revision, report=ValidationReport(), activated=activated)


# -- periodic refresh plan ----------------------------------------------------


class TestPlanTelemetryRefresh:
    def test_known_stopped_without_selection_skips(self) -> None:
        presenter = EngineStatusPresenter()
        plan = presenter.plan_telemetry_refresh(_state(known_stopped=True), frozenset())
        assert plan.skip
        assert plan.node is None

    def test_known_stopped_with_single_selection_fetches_that_node(self) -> None:
        presenter = EngineStatusPresenter()
        node = uuid4()
        plan = presenter.plan_telemetry_refresh(_state(known_stopped=True), frozenset({node}))
        assert not plan.skip
        assert plan.node == node

    def test_running_engine_fetches(self) -> None:
        presenter = EngineStatusPresenter()
        plan = presenter.plan_telemetry_refresh(_state(known_stopped=False), frozenset())
        assert not plan.skip
        assert plan.node is None

    def test_known_stopped_with_multiple_selections_skips(self) -> None:
        # Only an exactly-one selection fetches node-specific data; anything
        # else leaves the window-side selection out of the round trip, so a
        # known-stopped engine skips entirely.
        presenter = EngineStatusPresenter()
        plan = presenter.plan_telemetry_refresh(
            _state(known_stopped=True), frozenset({uuid4(), uuid4()})
        )
        assert plan.skip

    def test_restarting_engine_skips(self) -> None:
        presenter = EngineStatusPresenter()
        plan = presenter.plan_telemetry_refresh(
            _state(
                connection=EngineConnectionState.RESTARTING,
                status=EngineStatus(EngineConnectionState.RESTARTING),
            ),
            frozenset(),
        )
        assert plan.skip

    def test_missing_status_skips(self) -> None:
        presenter = EngineStatusPresenter()
        state = EngineBridgeState(
            connection_state=EngineConnectionState.CONNECTED,
            engine_state=EngineState.STOPPED,
            status=None,
        )
        assert presenter.plan_telemetry_refresh(state, frozenset()).skip


class TestTelemetryIntents:
    def test_healthy_publish_reports_figures_and_no_announcement(self) -> None:
        presenter = EngineStatusPresenter()
        metrics = EngineMetrics(
            EngineState.RUNNING,
            processed_ticks=42,
            dropped_before_processing=3,
            input_fps=30.0,
            processed_fps=30.0,
            preview_fps=15.0,
            p95_graph_execution_ms=1.25,
            mailbox_occupancy=1,
            mailbox_capacity=12,
            frame_age_ms=4.0,
            memory_bytes=2 * 1024 * 1024,
        )
        source = _source()
        midi = _midi()
        intents = presenter.present_state(
            _state(engine=EngineState.RUNNING, metrics=metrics, sources=(source,), midi=(midi,))
        )
        telemetry = intents.telemetry
        assert telemetry is not None
        assert telemetry.engine_state == "RUNNING"
        assert telemetry.source_states == ("READY",)
        assert telemetry.midi_states == ("CONNECTED",)
        assert telemetry.ticks == 42
        assert telemetry.dropped == 3
        assert telemetry.announce_errors is False
        assert telemetry.source_statuses == (source,)
        tooltip = telemetry.tooltip
        assert isinstance(tooltip, TelemetryTooltip)
        # 8 pre-formatted figures: 3 fps, p95, queue, frame age, cpu, ram.
        assert len(tooltip.figures) == 8
        assert tooltip.figures[0] == "30.0"
        assert tooltip.figures[3] == "1.25"
        assert tooltip.figures[4] == "1/12"
        assert tooltip.midi == (("Mock Port", "CONNECTED", 0),)

    def test_unselected_port_is_left_for_the_window_to_localize(self) -> None:
        presenter = EngineStatusPresenter()
        intents = presenter.present_state(
            _state(metrics=EngineMetrics(EngineState.RUNNING), midi=(_midi(selected_port=""),))
        )
        assert intents.telemetry is not None
        tooltip = intents.telemetry.tooltip
        assert isinstance(tooltip, TelemetryTooltip)
        assert tooltip.midi[0][0] is None

    def test_new_error_digest_announces_and_repeats_do_not(self) -> None:
        presenter = EngineStatusPresenter()
        source = _source(SourceState.ERROR, "decode failed")
        first = presenter.present_state(
            _state(metrics=EngineMetrics(EngineState.RUNNING), sources=(source,))
        )
        assert first.telemetry is not None
        assert isinstance(first.telemetry.tooltip, ErrorTooltip)
        assert first.telemetry.tooltip.sources == ((source.node_id, "decode failed"),)
        assert first.telemetry.announce_errors is True

        same = presenter.present_state(
            _state(metrics=EngineMetrics(EngineState.RUNNING), sources=(source,))
        )
        assert same.telemetry is not None
        assert same.telemetry.announce_errors is False

        changed = presenter.present_state(
            _state(
                metrics=EngineMetrics(EngineState.RUNNING),
                sources=(_source(SourceState.ERROR, "seek failed"),),
            )
        )
        assert changed.telemetry is not None
        assert changed.telemetry.announce_errors is True

    def test_error_cleared_returns_to_telemetry_tooltip(self) -> None:
        presenter = EngineStatusPresenter()
        presenter.present_state(
            _state(
                metrics=EngineMetrics(EngineState.RUNNING),
                sources=(_source(SourceState.ERROR, "decode failed"),),
            )
        )
        intents = presenter.present_state(
            _state(metrics=EngineMetrics(EngineState.RUNNING), sources=(_source(),))
        )
        assert intents.telemetry is not None
        assert isinstance(intents.telemetry.tooltip, TelemetryTooltip)
        assert intents.telemetry.announce_errors is False

    def test_midi_error_digest_announces(self) -> None:
        presenter = EngineStatusPresenter()
        broken = _midi(MidiOutputConnectionState.UNAVAILABLE, last_error="no output devices")
        intents = presenter.present_state(
            _state(metrics=EngineMetrics(EngineState.RUNNING), midi=(broken,))
        )
        assert intents.telemetry is not None
        assert isinstance(intents.telemetry.tooltip, ErrorTooltip)
        assert intents.telemetry.announce_errors is True

    def test_empty_source_error_keeps_raw_none_for_window_localization(self) -> None:
        presenter = EngineStatusPresenter()
        broken = _source(SourceState.ERROR)  # no engine-provided message
        intents = presenter.present_state(
            _state(metrics=EngineMetrics(EngineState.RUNNING), sources=(broken,))
        )
        assert intents.telemetry is not None
        assert isinstance(intents.telemetry.tooltip, ErrorTooltip)
        assert intents.telemetry.tooltip.sources == ((broken.node_id, None),)
        assert intents.telemetry.announce_errors is True


# -- identity dedup of settled facts -------------------------------------------


class TestPresentStateIdentityDedup:
    def test_repeated_object_is_not_reannounced(self) -> None:
        presenter = EngineStatusPresenter()
        state = _state(metrics=EngineMetrics(EngineState.RUNNING))
        assert presenter.present_state(state).telemetry is not None
        assert presenter.present_state(state).telemetry is None

    def test_new_object_is_announced(self) -> None:
        presenter = EngineStatusPresenter()
        first = presenter.present_state(_state(metrics=EngineMetrics(EngineState.RUNNING)))
        assert first.telemetry is not None
        second = presenter.present_state(_state(metrics=EngineMetrics(EngineState.RUNNING)))
        assert second.telemetry is not None

    def test_activation_and_restart_identity_dedup(self) -> None:
        presenter = EngineStatusPresenter()
        activation = _activation(revision=1)
        restart = EngineRestartOutcome(outcome="restarted", detail="")
        state = replace(_state(), last_activation=activation, last_restart=restart)
        first = presenter.present_state(state)
        assert first.activation is activation
        assert first.restart is restart
        again = presenter.present_state(state)
        assert again.activation is None
        assert again.restart is None
        updated = replace(state, last_activation=_activation(revision=2, activated=False))
        after = presenter.present_state(updated)
        assert after.activation is updated.last_activation
        assert after.restart is None

    def test_device_catalogue_and_transport_identity_dedup(self) -> None:
        presenter = EngineStatusPresenter()
        catalogue = DeviceCatalogue()
        transport = TransportOutcome(verb="play", target=uuid4())
        state = replace(_state(), device_catalogue=catalogue, last_transport=transport)
        first = presenter.present_state(state)
        assert first.device_catalogue is catalogue
        assert first.transport is transport
        again = presenter.present_state(state)
        assert again.device_catalogue is None
        assert again.transport is None


# -- engine status intents and crash dialog dedup ------------------------------


class TestEngineStatusAndFailure:
    def test_status_intents_per_connection_state(self) -> None:
        presenter = EngineStatusPresenter()
        crashed = presenter.present_engine_status(
            EngineStatus(EngineConnectionState.CRASHED, exit_code=139)
        )
        assert crashed.restart_enabled is True
        assert crashed.restarting is False
        restarting = presenter.present_engine_status(EngineStatus(EngineConnectionState.RESTARTING))
        assert restarting.restart_enabled is False
        assert restarting.restarting is True
        connected = presenter.present_engine_status(EngineStatus(EngineConnectionState.CONNECTED))
        assert connected.restart_enabled is False
        assert connected.restarting is False

    def test_crash_dialog_shows_once_per_signature_and_rearms_on_reset(
        self, tmp_path: Path
    ) -> None:
        presenter = EngineStatusPresenter()
        report = tmp_path / "crash.log"
        report.write_text("traceback", encoding="utf-8")
        status = EngineStatus(
            EngineConnectionState.CRASHED,
            child_process_id=111,
            exit_code=139,
            last_error="boom",
            crash_log_path=str(report),
        )
        first = presenter.present_engine_failure(status)
        assert first.dialog is True
        assert first.crash_report_available is True
        second = presenter.present_engine_failure(status)
        assert second.dialog is False
        assert second.crash_report_available is True
        presenter.failure_reset()
        assert presenter.present_engine_failure(status).dialog is True

    def test_missing_crash_report_is_reported_unavailable(self) -> None:
        presenter = EngineStatusPresenter()
        status = EngineStatus(
            EngineConnectionState.CRASHED,
            exit_code=139,
            crash_log_path="/nonexistent/crash-report.log",
        )
        assert presenter.present_engine_failure(status).crash_report_available is False

    def test_none_crash_path_is_unavailable(self) -> None:
        presenter = EngineStatusPresenter()
        intent = presenter.present_engine_failure(EngineStatus(EngineConnectionState.UNRESPONSIVE))
        assert intent.crash_report_available is False
        assert intent.dialog is True


# -- status message mapping ------------------------------------------------------


class TestStatusMessageIntent:
    def test_known_keys_map_to_their_key_and_timeout(self) -> None:
        presenter = EngineStatusPresenter()
        for key in (
            "panic_ok",
            "activation_failed",
            "devices_refreshing",
            "devices_failed",
            "transport_failed",
            "panic_failed",
        ):
            intent = presenter.present_status_message(key, 4000)
            assert intent is not None
            assert intent.key == key
            assert intent.timeout_ms == 4000
            assert intent.needs_status_refresh is False

    def test_restart_failed_needs_a_status_refresh(self) -> None:
        presenter = EngineStatusPresenter()
        intent = presenter.present_status_message("restart_failed", 8000)
        assert intent is not None
        assert intent.key == "restart_failed"
        assert intent.needs_status_refresh is True

    def test_state_rendered_messages_and_unknown_keys_are_skipped(self) -> None:
        presenter = EngineStatusPresenter()
        assert presenter.present_status_message("activated", 3000) is None
        assert presenter.present_status_message("activation_rejected", 3000) is None
        assert presenter.present_status_message("something_new", 3000) is None


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
