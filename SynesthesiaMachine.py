"""Nuitka entry point for the standalone distribution (Windows and Linux)."""

from synesthesia_machine.app.bootstrap import main

if __name__ == "__main__":
    raise SystemExit(main())
