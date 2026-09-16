"""Shared JSON value types.

These are the value types of persisted graph JSON. They live in ``contracts`` (the
lowest layer) so that both ``persistence`` (which serialises graphs) and ``nodes``
(which owns each node's implementation-version migration chain) can reference them
without ``nodes`` depending on ``persistence``.
"""

from __future__ import annotations

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type JsonObject = dict[str, JsonValue]
