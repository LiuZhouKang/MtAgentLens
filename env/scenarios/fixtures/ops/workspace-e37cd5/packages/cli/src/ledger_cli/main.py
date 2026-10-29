"""Argument parsing for the ``ledger`` command.

The parser is deliberately small: it understands ``--filter`` (repeatable),
``--output-format`` and the trailing input path. Usage errors raise
:class:`UsageError`, which the entry point turns into exit status 2.
"""

from __future__ import annotations

from dataclasses import dataclass, field

OUTPUT_FORMATS = ("csv", "table")


class UsageError(ValueError):
    """Raised for a malformed command line; the CLI exits with status 2."""


@dataclass
class Options:
    path: str
    filters: list = field(default_factory=list)
    output_format: str = "table"


def parse_args(argv: list) -> Options:
    filters = []
    output_format = "table"
    positional = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--filter":
            index += 1
            if index >= len(argv):
                raise UsageError("--filter requires a value")
            filters.append(argv[index])
        elif arg.startswith("--filter="):
            filters.append(arg.split("=", 1)[1])
        elif arg == "--output-format":
            index += 1
            if index >= len(argv):
                raise UsageError("--output-format requires a value")
            output_format = argv[index]
        elif arg.startswith("--output-format="):
            output_format = arg.split("=", 1)[1]
        elif arg.startswith("-"):
            raise UsageError(f"unknown option: {arg}")
        else:
            positional.append(arg)
        index += 1
    if len(positional) != 1:
        raise UsageError("exactly one input path is required")
    if output_format not in OUTPUT_FORMATS:
        raise UsageError(f"unknown output format: {output_format}")
    return Options(positional[0], filters, output_format)


def run(argv: list, emit=print) -> int:
    """Parse ``argv`` and describe the resulting invocation."""
    try:
        options = parse_args(argv)
    except UsageError as error:
        emit(f"usage error: {error}")
        return 2
    emit(f"input={options.path}")
    emit(f"output_format={options.output_format}")
    for value in options.filters:
        emit(f"filter={value}")
    return 0
