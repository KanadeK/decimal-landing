import argparse
import csv
import json
import sys

from . import __version__
from ._files import InputError
from .core import PolicyFailure, process


def main(argv=None):
    parser = argparse.ArgumentParser(description="Convert CSV decimals with exact error budgets.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("input")
    parser.add_argument("--contract", required=True)
    parser.add_argument("--output", help="write converted CSV only when the entire batch passes")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = process(args.input, args.contract, args.output, args.force)
    except PolicyFailure as exc:
        print(json.dumps(exc.report, ensure_ascii=True))
        return 1
    except (InputError, OSError, UnicodeError, csv.Error) as exc:
        message = (
            str(exc)
            if isinstance(exc, InputError)
            else f"input/output error ({type(exc).__name__})"
        )
        print(json.dumps({"status": "error", "message": message}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0
