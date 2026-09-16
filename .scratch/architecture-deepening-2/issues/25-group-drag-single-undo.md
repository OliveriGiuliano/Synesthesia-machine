# 25: Group dragging

**What to build:** A group drag that also moves overlapping stuck nodes commits two separate commands (MoveNodesCommand then MoveGroupsCommand), so one undo reverts half the gesture; the sticking policy (which nodes stick, Shift suppression, bounded translation) is split between the graphics item and a 15-method GraphSceneProtocol that makes items untestable without a scene implementing all fifteen methods; and no test pins the undo-step granularity.

**Solution:** One session entry point (e.g. move_group_with_contents) or an undo macro that commits nodes plus group as a single step; move the sticking and bounds computation toward the scene (or a small drag-policy helper) to shrink the protocol; add a test that pins stack depth for a stuck drag.

**Status:** open

**Files:**
- `src/synesthesia_machine/ui/graphics.py`
- `src/synesthesia_machine/ui/canvas.py`
- `src/synesthesia_machine/ui/session.py`
- `tests/ui/test_groups_and_comments.py`

**Acceptance:**
- [ ] Undo granularity matches the gesture
- [ ] Items testable against a smaller seam
- [ ] Step granularity pinned by test
- [ ] Drag policy in one place
- [ ] Targeted tests pass; `uv run check` green; no unrelated diff
