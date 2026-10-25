"""Exact base-10 conversion. No binary floats or ambient decimal context."""

import contextlib
import csv
import io
import re
from dataclasses import dataclass
from fractions import Fraction

from ._files import InputError, integer, keys, load_config, names, publish, regular

LITERAL = re.compile(r"([+-]?)([0-9]*)(?:\.([0-9]*))?(?:[eE]([+-]?[0-9]+))?\Z")
MODES = ("reject", "half_even", "half_up", "toward_zero")


class CellError(ValueError):
    pass


class PolicyFailure(Exception):
    def __init__(self, report):
        self.report = report
        super().__init__("data or loss budget failed")


def parse_number(text):
    if not isinstance(text, str) or len(text) > 1024:
        raise CellError("invalid_number")
    match = LITERAL.fullmatch(text)
    if not match:
        raise CellError("invalid_number")
    sign, whole, fraction, exponent = match.groups()
    fraction = fraction or ""
    digits = whole + fraction
    if not digits:
        raise CellError("invalid_number")
    if len(digits) > 512:
        raise CellError("number_limit")
    exp_text = (exponent or "0").lstrip("+-").lstrip("0") or "0"
    if len(exp_text) > 3:
        raise CellError("number_limit")
    exp = int(exponent or "0")
    if abs(exp) > 512:
        raise CellError("number_limit")
    coefficient = int(digits) * (-1 if sign == "-" else 1)
    return coefficient, exp - len(fraction)


def rational(coefficient, exponent):
    if exponent >= 0:
        return Fraction(coefficient * 10**exponent)
    return Fraction(coefficient, 10**-exponent)


def decimal_text(number):
    """Render an exact fraction with a terminating decimal expansion."""
    denominator = number.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator != 1:
        raise ValueError("non-terminating decimal")
    scale = max(twos, fives)
    integer_value = number.numerator * (10**scale // number.denominator)
    text = scaled_text(integer_value, scale)
    return text.rstrip("0").rstrip(".") if "." in text else text


def scaled_text(value, scale):
    sign = "-" if value < 0 else ""
    digits = str(abs(value)).zfill(scale + 1)
    return sign + (digits[:-scale] + "." + digits[-scale:] if scale else digits)


@dataclass(frozen=True)
class Column:
    name: str
    precision: int
    scale: int
    rounding: str
    nulls: tuple[str, ...]
    max_cell: Fraction | None
    max_total: Fraction | None
    max_changed: int | None

    def cast(self, text):
        coefficient, exponent = parse_number(text)
        shift = exponent + self.scale
        if shift >= 0:
            result = coefficient * 10**shift
        else:
            divisor = 10**-shift
            result, remainder = divmod(abs(coefficient), divisor)
            if remainder:
                if self.rounding == "reject":
                    raise CellError("inexact")
                if self.rounding == "half_up" and remainder * 2 >= divisor:
                    result += 1
                elif self.rounding == "half_even" and (
                    remainder * 2 > divisor or (remainder * 2 == divisor and result % 2)
                ):
                    result += 1
            if coefficient < 0:
                result = -result
        if abs(result) >= 10**self.precision:
            raise CellError("overflow")
        before = rational(coefficient, exponent)
        after = Fraction(result, 10**self.scale)
        return scaled_text(result, self.scale), before, after


def contract(data):
    keys(data, {"version", "columns"}, {"version", "columns"})
    integer(data["version"], 1, 1)
    if not isinstance(data["columns"], list) or not data["columns"]:
        raise InputError("columns must be a nonempty list")
    columns = []
    for item in data["columns"]:
        keys(
            item,
            {
                "name",
                "precision",
                "scale",
                "rounding",
                "null_values",
                "max_cell_abs_error",
                "max_total_abs_error",
                "max_changed",
            },
            {"name", "precision", "scale"},
        )
        precision = integer(item["precision"], 1, 128)
        scale = integer(item["scale"], 0, precision)
        rounding = item.get("rounding", "reject")
        if rounding not in MODES:
            raise InputError("unsupported rounding policy")
        nulls = item.get("null_values", [])
        if not isinstance(nulls, list) or any(not isinstance(x, str) for x in nulls):
            raise InputError("null_values must be an array of strings")
        if len(set(nulls)) != len(nulls):
            raise InputError("duplicate null token")
        budgets = []
        for key in ("max_cell_abs_error", "max_total_abs_error"):
            value = None
            if key in item:
                try:
                    value = rational(*parse_number(item[key]))
                except CellError as exc:
                    raise InputError("error budgets must be finite decimal strings") from exc
                if value < 0:
                    raise InputError("error budgets must be nonnegative")
            budgets.append(value)
        changed = integer(item["max_changed"], 0, 10**12) if "max_changed" in item else None
        columns.append(
            Column(
                item["name"],
                precision,
                scale,
                rounding,
                tuple(nulls),
                budgets[0],
                budgets[1],
                changed,
            )
        )
    names([c.name for c in columns])
    return columns


def process(input_path, contract_path, output_path=None, force=False):
    columns = contract(load_config(contract_path))
    source = regular(input_path)
    stats = [
        {
            "accepted": 0,
            "nulls": 0,
            "rejected": 0,
            "changed": 0,
            "sum_before": Fraction(),
            "sum_after": Fraction(),
            "signed_error": Fraction(),
            "total_abs_error": Fraction(),
            "max_abs_error": Fraction(),
        }
        for _ in columns
    ]
    issues, counts = [], {}
    records = 0

    def issue(code, row, column):
        counts[code] = counts.get(code, 0) + 1
        if len(issues) < 20:
            issues.append({"code": code, "record": row, "column": column})

    transaction = (
        publish(output_path, [source, contract_path], force)
        if output_path
        else contextlib.nullcontext(None)
    )
    with transaction as target, source.open(encoding="utf-8-sig", newline="") as stream:
        previous = csv.field_size_limit(1048576)
        try:
            reader = csv.reader(stream, strict=True)
            header = names(next(reader, None))
            if any(c.name not in header for c in columns):
                raise InputError("configured column missing from CSV header")
            positions = [header.index(c.name) for c in columns]

            def write(row):
                if target is not None:
                    buffer = io.StringIO(newline="")
                    csv.writer(buffer, lineterminator="\n").writerow(row)
                    target.write(buffer.getvalue().encode("utf-8"))

            write(header)
            for records, row in enumerate(reader, 1):
                if len(row) != len(header):
                    raise InputError(f"record {records}: column count differs from header")
                for index, (column, position) in enumerate(zip(columns, positions, strict=True)):
                    stat = stats[index]
                    text = row[position]
                    if text in column.nulls:
                        stat["nulls"] += 1
                        continue
                    try:
                        converted, before, after = column.cast(text)
                    except CellError as exc:
                        stat["rejected"] += 1
                        issue(str(exc), records, column.name)
                        continue
                    error = after - before
                    stat["accepted"] += 1
                    stat["changed"] += bool(error)
                    stat["sum_before"] += before
                    stat["sum_after"] += after
                    stat["signed_error"] += error
                    stat["total_abs_error"] += abs(error)
                    stat["max_abs_error"] = max(stat["max_abs_error"], abs(error))
                    if column.max_cell is not None and abs(error) > column.max_cell:
                        issue("cell_error_budget", records, column.name)
                    row[position] = converted
                write(row)
        finally:
            csv.field_size_limit(previous)
        for column, stat in zip(columns, stats, strict=True):
            if column.max_total is not None and stat["total_abs_error"] > column.max_total:
                issue("total_error_budget", None, column.name)
            if column.max_changed is not None and stat["changed"] > column.max_changed:
                issue("changed_budget", None, column.name)
        rendered = []
        for column, stat in zip(columns, stats, strict=True):
            rendered.append(
                {
                    "name": column.name,
                    **{
                        key: decimal_text(value) if isinstance(value, Fraction) else value
                        for key, value in stat.items()
                    },
                }
            )
        report = {
            "status": "failed" if counts else "ok",
            "records": records,
            "published": bool(output_path) and not counts,
            "totals_scope": "accepted numeric cells; null and rejected cells excluded",
            "columns": rendered,
            "issue_counts": counts,
            "issues": issues,
            "issues_omitted": max(0, sum(counts.values()) - len(issues)),
        }
        if counts:
            raise PolicyFailure(report)
    return report
