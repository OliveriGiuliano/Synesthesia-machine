# Agent playbook routing

These playbooks hold area-specific procedures referenced by the repository-root `AGENTS.md`. They are
mandatory instructions, not optional background reading. Before editing, read every row that matches
the planned paths or behavior; cross-cutting work commonly requires several playbooks.

| Change area or trigger | Required playbook |
| --- | --- |
| Node definitions, graph models, compiler, scheduler, demand roots, runtime planning | `nodes-graph.md` |
| Graph JSON, schemas, migrations, clipboard, copy/paste, duplicate, model field additions | `persistence.md` |
| `EngineClient`, messages, protocol version, client/server, process lifecycle, shared memory | `engine-ipc.md` |
| Qt widgets, canvas, view models, undo commands, translations, editor behavior | `qt-editor.md` |
| Preview contracts, broker, shared-preview transport, canvas pills, visualizer docks, preview metrics | `runtime-previews.md` |
| Media, MIDI, audio, diagnostics, performance evidence, packaging, releases, versions | `system-edges.md` |
| Every code or test change | `testing.md` |
| Repository tools, diagnostics, benchmarks, evidence, hardware probes, release commands | `tools-evidence.md` |

The root cross-cutting audit still applies after reading an area playbook. If a playbook and current
implementation disagree, inspect the master architecture and accepted ADRs and resolve the mismatch
explicitly rather than silently choosing one.
