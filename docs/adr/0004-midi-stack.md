# ADR-0004: Use Mido with python-rtmidi

- **Status:** Accepted
- **Date:** 2026-07-29

## Context

Version 1 needs MIDI 1.0 output to enumerated Windows hardware and virtual ports without shipping
a custom MIDI driver.

## Decision

Use Mido for message/port abstraction and python-rtmidi as the first Windows backend. Keep the
backend behind a mockable interface and use mocks for automated tests.

## Consequences

The application does not create virtual ports. Missing hardware is a normal unavailable state,
not an application failure. Synesthesia nodes will output desired note state and never open ports.