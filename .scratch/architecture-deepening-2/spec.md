# Architecture deepening, second run

**Date:** 2026-09-17
**Method:** six parallel read-only scouts over contracts/graph, nodes, runtime, persistence/media/midi, ui, and app/diagnostics/tools; hot-spot weighted by the last 45 commits (nodes 92, ui 62, runtime 37 file touches). Findings consolidated against docs/architecture/master.md and docs/adr/; the deletion test and the deep-module vocabulary (module, interface, depth, seam, adapter, leverage, locality) were applied to every candidate.
**Report:** /tmp/architecture-review-20260917T014100.html
**Prior run:** .scratch/architecture-deepening (12 tickets, resolved in 38bccff) — those deepened modules (EngineBridge, PreviewRouter, EngineSession, demand roots, transport resolver, typed source configs) are treated as the new baseline; this run finds the friction that remains around and under them.

## Top recommendation

**05 — One engine session.** The only candidate where the deep module already exists, is fully tested, and is simply not wired in: `runtime/engine_session.py` (328 L, 19.6 KB of tests) has zero production consumers while EngineBridge, PreviewRouter, and MainWindow each carry a drifted copy of its session semantics.

## Tickets (execution order)

| # | Ticket | Area | Strength |
|---|---|---|---|
| 01 | [contracts-hygiene] — Contracts layer hygiene | Contracts & graph | Strong |
| 02 | [boundary-tests] — Hardened architecture boundary tests | Contracts & graph | Strong |
| 03 | [compiler-entry-points] — Compiler entry points | Contracts & graph | Strong |
| 04 | [wire-parity] — Concentrate the wire-parity mapping | Contracts & graph | Worth exploring |
| 05 | [one-engine-session] — One engine session | Process & engine | Strong |
| 06 | [cursor-owning-polling] — Cursor-owning preview polling | Process & engine | Strong |
| 07 | [ipc-seam-tightening] — Tighten the engine IPC seam | Process & engine | Worth exploring |
| 08 | [preview-handshake-owner] — One owner for the preview-slot handshake | Process & engine | Worth exploring |
| 09 | [source-tick-builder] — Shared presented source-tick builder | Persistence, media & MIDI | Strong |
| 10 | [source-lifecycle-seam] — Deterministic lifecycle seam for source services | Persistence, media & MIDI | Strong |
| 11 | [declarative-source-construction] — Declarative source construction and media references | Persistence, media & MIDI | Strong |
| 12 | [midi-state-diff] — One shared MIDI state-diff primitive | Persistence, media & MIDI | Strong |
| 13 | [desired-state-ordering] — Give desired MIDI state its own ordering | Persistence, media & MIDI | Worth exploring |
| 14 | [persistence-facade-slimming] — Persistence facade slimming and store-owned recovery matching | Persistence, media & MIDI | Worth exploring |
| 15 | [node-runtime-scaffolding] — Shared node runtime scaffolding | Nodes | Strong |
| 16 | [image-adapter-machinery] — Grow runtime_support into the image-node adapter home | Nodes | Strong |
| 17 | [nodes-facade-hygiene] — Nodes seam and facade hygiene | Nodes | Worth exploring |
| 18 | [node-migrations-local] — Move per-node migrations next to their definitions | Nodes | Worth exploring |
| 19 | [video-metadata-off-ui-thread] — Keep video metadata hooks off the UI thread | Nodes | Worth exploring |
| 20 | [difference-node-realign] — Realign the Difference node with its domain | Nodes | Speculative |
| 21 | [preview-family-taxonomy] — One preview-family taxonomy | UI | Strong |
| 22 | [view-model-facade] — Widen the view-model contract and publish the ui facade | UI | Strong |
| 23 | [visualizer-families-headless] — Give visualizer families a headless home | UI | Worth exploring |
| 24 | [midi-export-eligibility] — Move MIDI export eligibility next to the headless exporter | UI | Strong |
| 25 | [group-drag-single-undo] — Group dragging | UI | Worth exploring |
| 26 | [main-window-lifecycle] — Split MainWindow's document-lifecycle cluster | UI | Worth exploring |
| 27 | [app-shell-seam] — One app-shell seam for harnesses and tools | App, diagnostics & tools | Strong |
| 28 | [tools-reporting] — Shared evidence-report infrastructure for tools | App, diagnostics & tools | Worth exploring |
| 29 | [redaction-policy] — Redaction as a registered policy | App, diagnostics & tools | Worth exploring |
| 30 | [release-smoke-registry] — Public check registry for the packaged self-test | App, diagnostics & tools | Worth exploring |

Dependencies: 02 after 01; 06 after 05; 07 after 05+06; 08 after 07; 13 after 12; 22 after 21; 23 after 21; 26 after 05+24; 27 after 26.

ADR gates: 13 (desired-state ordering) and 19 (video metadata off the UI thread) require a new ADR written during the ticket, before implementation.
