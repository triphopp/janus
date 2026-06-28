"""Janus user-facing CLI facade.

Progressive command surface over the existing pipeline. The core user path is
``import once -> run many times``:

    janus import WTI path/to/WTI.csv
    janus run WTI --window 2024Q4
    janus doctor WTI
    janus explain WTI --window 2024Q4
    janus list

Advanced pipeline knobs remain available through ``--advanced`` overrides but do
not appear in the default happy path. See ``issues`` 026 for the full design.
"""

__all__ = ["main"]


def main(argv=None):  # pragma: no cover - thin re-export
    from cli.main import main as _main

    return _main(argv)
