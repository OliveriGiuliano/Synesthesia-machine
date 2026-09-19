# Architecture deepening, third run

**Date:** 2026-09-18
**Method:** five parallel read-only scouts over the engine transport/session cluster, the video source subsystem, the UI document-lifecycle cluster, the node scaffolding + image cluster, and the MIDI stack + offline export; hot-spot weighted by the last 45 commits (nodes 102, ui 91, runtime 52, media 19, contracts 16, persistence 15 file touches). Findings consolidated against docs/architecture/master.md and ADRs 0019–0024; the deletion test and the deep-module vocabulary (module, interface, depth, seam, adapter, leverage, locality) were applied to every candidate.
**Report:** /tmp/architecture-review-20260918-193020.html
**Prior run:** .scratch/architecture-deepening-2 (tickets 01–31, resolved) and ADRs 0019–0024 (offline export over looping videos, auto-resume after broken-graph stop, video seek/loop segments, publish-generation panic ordering, pre-decoded loop head) are the new baseline; this run finds the friction that remains around and under them, including friction introduced by the newest ADRs.
**Note:** the repository has no CONTEXT.md glossary yet; ticket names use AGENTS.md / package README vocabulary. The glossary is created lazily as ticket design conversations name new concepts.

## Top recommendation

**06 — The video source pass, once.** Three of the last five ADRs land in this module, and the ADR-0024 implementation contradicts the ADR's stated properties: the loop-head worker treats its trigger-wait timeout as a trigger and re-decodes the head continuously instead of idling at zero CPU, and the "one pass over the region" concept exists twice (live decode + head pre-decode), coupling two independent decoders by frame-index faith. One shared region-pass module behind a container adapter makes mirror drift structurally impossible, and the fake-container seam makes the three-thread design testable headlessly. Ticket 05 (seek-to-region-end is a fatal error, triggered by the UI's own nudge buttons) is the safe early step in the same module.

## Tickets (execution order)

| # | Ticket | Area | Strength |
|---|---|---|---|
| 01 | [engine-driver-surface] — Shrink the engine driver to the depth of its real behaviours | Process & engine | Strong |
| 02 | [simulation-observation-contract] — Reliable observation for run-to-end simulation | Process & engine | Worth exploring |
| 03 | [in-process-client-hats] — One hat per placement for the in-process client | Process & engine | Speculative |
| 04 | [metrics-supervisor-split] — Split the supervisor record out of EngineMetrics | Process & engine | Worth exploring |
| 05 | [empty-pass-seek-end] — An empty pass is an end, not an error | Video & media | Strong |
| 06 | [shared-region-pass] — The video source pass, once | Video & media | Strong |
| 07 | [event-driven-loop-head] — Event-driven loop-head protocol | Video & media | Strong |
| 08 | [single-pass-state-reset] — One pass-state reset, one state writer | Video & media | Worth exploring |
| 09 | [publish-resolved-region] — Publish the resolved loop region | Video & media | Worth exploring |
| 10 | [engine-status-presenter] — Headless engine-status presenter | UI | Strong |
| 11 | [preview-pump-owner] — One owner for the preview pump | UI | Strong |
| 12 | [replacement-sequence-owner] — Replacement sequence through the controller on every path | UI | Strong |
| 13 | [autosave-trigger-owner] — Autosave trigger policy with the autosave controller | UI | Worth exploring |
| 14 | [derived-view-slices] — Publish derived view slices from the projection | UI | Worth exploring |
| 15 | [node-record-consumer-split] — Split the node record along its consumers | Nodes | Worth exploring |
| 16 | [registry-admission-validation] — Registration-time definition validation | Nodes | Strong |
| 17 | [no-data-cache-contract] — Name the NoData and cache contract in the scaffold | Nodes | Worth exploring |
| 18 | [panic-ordering-policy] — One state-ordering policy for panic-capable sinks | MIDI | Strong |

Dependencies: 02, 03, 04 after 01; 05 before 06; 07, 08, 09 after 06 (09 also after 07); 10 after 01; 14 after 11; 15 after 16; 17 after 15.

ADR gates: 02 (reliable observation semantics change the engine contract), 03 (engine-body placement is architectural), 04 (a metrics record crosses the wire), and 09 (SourceStatus gains the resolved region: protocol 16→17, extending ADR-0023) require an ADR written during the ticket, before implementation. 07 restores ADR-0024's stated idling property — no ADR change, but the ADR's description is amended to note the implementation gap that was closed.
