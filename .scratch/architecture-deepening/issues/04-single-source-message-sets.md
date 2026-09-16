# 04: Single-source the engine message sets

**What to build:** The set of wire message types the client and the child engine each accept is defined once, in the contract, so adding or removing a protocol message touches one place. A maintainer adds a command and it is automatically valid on both the in-process and child-process paths without hunting for a hand-maintained list.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] The child engine derives its accepted command types from the contract `EngineCommand` union (as the client already derives its response/event types), instead of a hand-written list.
- [x] The hand-written command-type list in the child engine is removed.
- [x] A protocol change (add/remove one message type) requires editing only the contract union; both adapters pick it up.
- [x] Protocol round-trip tests still pass for the in-process and child-process paths.
