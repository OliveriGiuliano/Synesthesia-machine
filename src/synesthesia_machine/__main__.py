"""Allow ``python -m synesthesia_machine`` to launch the application."""

from synesthesia_machine.app.bootstrap import main

if __name__ == "__main__":
    raise SystemExit(main())
