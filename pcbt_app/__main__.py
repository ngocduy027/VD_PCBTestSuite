"""Allow ``python -m pcbt_app`` to launch the GUI."""

from pcbt_app.app import main

if __name__ == "__main__":
    raise SystemExit(main())
