# ADR 0022: Publish generation orders desired state against panic

- Status: Accepted
- Date: 2026-09-17

## Context

A sink that can panic (the debug synth, the MIDI output service) must not be
re-armed by a desired state that was generated before the panic and delivered
after it: a tick that loses the race against a panic would otherwise re-arm
the exact voices or notes the panic just silenced.

The first occurrence of this race was fixed in the debug synth by recording
the monotonic wall-clock instant of each panic and dropping state frames whose
`received_monotonic_ns` precedes it. That protocol has two costs:

1. It embeds a cross-thread ordering clock inside the renderer. Every new
   panic-capable sink must rediscover the panic race against the wall clock on
   its own, and the MIDI output service — which has the identical race on the
   wire — had no protection at all.
2. Ordering depends on two threads observing the same monotonic clock
   consistently, which tests can only approximate with a fake clock.

## Decision

- `FrameContext` gains `publish_generation: int`. A value of 0 means "not
  ordered by the engine": contexts built by sources and by direct scheduler use
  carry 0. The `EngineFacade` owns a monotonically increasing counter and
  stamps each tick it executes with the next generation, so every value
  produced by the tick carries its tick's generation through its context
  identity. The counter lives on the facade, not the scheduler, so
  generations stay monotonic across plan replacements that preserve node
  runtimes.
- The runtime panic protocol becomes `panic(publish_generation: int)`: the
  scheduler passes the generation the engine had observed when the panic was
  issued. A tick still executing when the panic lands has already been
  stamped, so its generation is at or below the watermark.
- A panic-capable sink rejects an incoming desired state whose
  `publish_generation` is at or below the watermark of its most recent
  panic, provided the state was ordered by the engine: generation-0 values
  are source-built or directly scheduled, carry no tick ordering, and are
  never rejected, whatever the watermark.
- The MIDI output service adopts the same watermark rule for its one-slot
  state mailbox, closing the wire-side re-arm race that only the debug synth
  previously guarded.

## Consequences

- Stale-state rejection is a generation comparison owned by the state model
  and the panic protocol; no wall clock participates in ordering, so tests
  drive the race with explicit generations and future panic-capable sinks
  inherit correct ordering for free.
- A panic issued before any tick (watermark 0) and resets keep their
  semantics: they silence the sink without filtering later, newer states.
