# 21: One preview-family taxonomy

**What to build:** The mapping 'resolved port type -> pill/dock preview family -> display visualizer type + input port -> theme token' is re-encoded in five-plus modules as string literals: PILL_PREVIEW_TYPES in demand_roots.py, _pill_family() type-name parsing in graphics.py, port_color_name in theme.py, the visualizer-to-dock map with hard-coded input port names in main_window.py, a second display-visualizer type list for dock sources, and private frozensets in session.py. Adding a payload type or a preview family means hunting all of them.

**Solution:** One Qt-free taxonomy module — an enum plus pure lookup functions (port type -> family; family -> display visualizer type, input port, dock, theme token) — that every consumer consults, with the family published on the view model where the consumer is a renderer.

**Status:** open

**Files:**
- `src/synesthesia_machine/ui/demand_roots.py`
- `src/synesthesia_machine/ui/graphics.py`
- `src/synesthesia_machine/ui/theme.py`
- `src/synesthesia_machine/ui/main_window.py`
- `src/synesthesia_machine/ui/preview_router.py`
- `src/synesthesia_machine/ui/session.py`

**Acceptance:**
- [ ] New payload family = one edit
- [ ] Pure mapping, trivially testable
- [ ] Visualizer-to-dock invariant named once
- [ ] String-literal drift channel closed
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
