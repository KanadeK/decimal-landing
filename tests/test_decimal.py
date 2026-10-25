import contextlib
import csv
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, localcontext
from fractions import Fraction
from pathlib import Path

from decimal_landing._files import InputError
from decimal_landing.cli import main
from decimal_landing.core import (
    CellError,
    PolicyFailure,
    contract,
    decimal_text,
    parse_number,
    process,
    rational,
)


def spec(**updates):
    column = {"name": "amount", "precision": 6, "scale": 2}
    column.update(updates)
    return {"version": 1, "columns": [column]}


class NumericTests(unittest.TestCase):
    def test_exact_and_no_float(self):
        column = contract(spec(precision=30))[0]
        self.assertEqual(column.cast("9007199254740993.01")[0], "9007199254740993.01")
        self.assertEqual(column.cast("1.2300")[0], "1.23")

    def test_inexact_default(self):
        with self.assertRaisesRegex(CellError, "inexact"):
            contract(spec())[0].cast("1.001")

    def test_half_even_ties_and_signs(self):
        column = contract(spec(rounding="half_even"))[0]
        for before, after in [
            ("1.005", "1.00"),
            ("1.015", "1.02"),
            ("-1.005", "-1.00"),
            ("-1.015", "-1.02"),
        ]:
            self.assertEqual(column.cast(before)[0], after)

    def test_half_up_ties(self):
        column = contract(spec(rounding="half_up"))[0]
        self.assertEqual(column.cast("1.005")[0], "1.01")
        self.assertEqual(column.cast("-1.005")[0], "-1.01")

    def test_toward_zero(self):
        column = contract(spec(rounding="toward_zero"))[0]
        self.assertEqual(column.cast("-1.999")[0], "-1.99")

    def test_carry_overflow(self):
        column = contract(spec(rounding="half_up"))[0]
        for value in ["9999.995", "-9999.995", "10000", "1e512"]:
            with self.subTest(value=value), self.assertRaisesRegex(CellError, "overflow"):
                column.cast(value)
        self.assertEqual(column.cast("9999.994")[0], "9999.99")

    def test_precision_equals_scale(self):
        column = contract(spec(precision=2, scale=2))[0]
        self.assertEqual(column.cast(".99")[0], "0.99")
        with self.assertRaisesRegex(CellError, "overflow"):
            column.cast("1")

    def test_scale_zero_and_negative_zero(self):
        column = contract(spec(scale=0, rounding="half_even"))[0]
        self.assertEqual(column.cast("-0.1")[0], "0")
        self.assertEqual(column.cast("12.5")[0], "12")
        self.assertEqual(column.cast("13.5")[0], "14")

    def test_exponents_and_grammar(self):
        for text, expected in [
            ("+1e2", 100),
            (".1", Fraction(1, 10)),
            ("1.", 1),
            ("0001E-2", Fraction(1, 100)),
            ("-0", 0),
        ]:
            self.assertEqual(rational(*parse_number(text)), expected)
        for text in [
            "",
            ".",
            "NaN",
            "Infinity",
            "1_000",
            "１",
            " 1",
            "1 ",
            "0x10",
            "1,2",
            "1e",
            "+",
            "\n1",
            "1\n",
        ]:
            with self.subTest(text=text), self.assertRaises(CellError):
                parse_number(text)

    def test_resource_bounds(self):
        for text in ["1e513", "1e-513", "1e9999999", "1" * 513, "0" * 1025]:
            with self.assertRaises(CellError):
                parse_number(text)
        self.assertEqual(rational(*parse_number("1e-512")), Fraction(1, 10**512))

    def test_decimal_rendering(self):
        for number, expected in [
            (Fraction(), "0"),
            (Fraction(-1, 8), "-0.125"),
            (Fraction(1, 5), "0.2"),
            (Fraction(100), "100"),
        ]:
            self.assertEqual(decimal_text(number), expected)
        with self.assertRaises(ValueError):
            decimal_text(Fraction(1, 3))

    def test_ambient_precision_does_not_affect_results(self):
        with localcontext() as ctx:
            ctx.prec = 2
            self.assertEqual(
                contract(spec(precision=30))[0].cast("123456789.12")[0], "123456789.12"
            )

    def test_seeded_decimal_oracle(self):
        rng = random.Random(3921)
        for mode, oracle in [
            ("half_even", ROUND_HALF_EVEN),
            ("half_up", ROUND_HALF_UP),
            ("toward_zero", ROUND_DOWN),
        ]:
            for _ in range(1000):
                scale = rng.randrange(7)
                value = f"{rng.randrange(-999999, 999999)}e-{rng.randrange(9)}"
                column = contract(spec(precision=30, scale=scale, rounding=mode))[0]
                with localcontext() as ctx:
                    ctx.prec = 50
                    expected = Decimal(value).quantize(Decimal(1).scaleb(-scale), rounding=oracle)
                actual = column.cast(value)[0]
                self.assertEqual(Decimal(actual), expected)

    def test_bad_contract(self):
        for updates in [
            {"scale": -1},
            {"precision": 129},
            {"scale": 7},
            {"precision": True},
            {"rounding": "nearest"},
            {"null_values": [None]},
            {"null_values": ["", ""]},
            {"max_total_abs_error": 0.01},
            {"max_cell_abs_error": "-0.1"},
            {"max_changed": True},
            {"typo": 1},
        ]:
            with self.subTest(updates=updates), self.assertRaises(InputError):
                contract(spec(**updates))


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "input.csv"
        self.schema = self.root / "contract.json"
        self.output = self.root / "out.csv"
        self.schema.write_text(json.dumps(spec(rounding="half_even")), encoding="utf-8")
        self.source.write_text("id,amount\n001,1.005\n002,1.015\n", encoding="utf-8")

    def configure(self, **updates):
        self.schema.write_text(json.dumps(spec(rounding="half_even", **updates)), encoding="utf-8")

    def test_cancellation_still_has_absolute_loss(self):
        report = process(self.source, self.schema)
        stat = report["columns"][0]
        self.assertEqual(stat["signed_error"], "0")
        self.assertEqual(stat["total_abs_error"], "0.01")
        self.assertEqual(stat["changed"], 2)
        self.assertFalse(report["published"])

    def test_total_budget_blocks_output(self):
        self.configure(max_total_abs_error="0.009")
        with self.assertRaises(PolicyFailure) as caught:
            process(self.source, self.schema, self.output)
        self.assertEqual(caught.exception.report["issue_counts"], {"total_error_budget": 1})
        self.assertFalse(self.output.exists())

    def test_inclusive_budget_and_csv_output(self):
        self.configure(max_cell_abs_error="0.005", max_total_abs_error="0.01", max_changed=2)
        report = process(self.source, self.schema, self.output)
        self.assertTrue(report["published"])
        with self.output.open(encoding="utf-8", newline="") as f:
            self.assertEqual(
                list(csv.reader(f)), [["id", "amount"], ["001", "1.00"], ["002", "1.02"]]
            )

    def test_cell_and_count_budgets(self):
        self.configure(max_cell_abs_error="0.004", max_changed=1)
        with self.assertRaises(PolicyFailure) as caught:
            process(self.source, self.schema)
        self.assertEqual(
            caught.exception.report["issue_counts"], {"cell_error_budget": 2, "changed_budget": 1}
        )

    def test_rejects_reported_without_values(self):
        self.source.write_text("id,amount\n001,NaN\n002,9999.995\n003,1\n", encoding="utf-8")
        with self.assertRaises(PolicyFailure) as caught:
            process(self.source, self.schema, self.output)
        report = caught.exception.report
        self.assertEqual(report["issue_counts"], {"invalid_number": 1, "overflow": 1})
        self.assertEqual(report["columns"][0]["sum_before"], "1")
        self.assertNotIn("9999", json.dumps(report))

    def test_null_explicit_and_nonselected_unchanged(self):
        self.configure(null_values=["NULL", ""])
        self.source.write_text(
            'id,amount,note\n001,NULL,"a,b"\n002,,"x\ny"\n', encoding="utf-8", newline=""
        )
        report = process(self.source, self.schema, self.output)
        self.assertEqual(report["columns"][0]["nulls"], 2)
        self.assertEqual(report["columns"][0]["accepted"], 0)
        with self.output.open(encoding="utf-8", newline="") as f:
            self.assertEqual(list(csv.reader(f))[2], ["002", "", "x\ny"])

    def test_embedded_newline_variants_preserved(self):
        for newline in ("\r", "\n", "\r\n"):
            data = f'id,amount,note\n001,1,"a{newline}b"\n'
            self.source.write_bytes(data.encode())
            process(self.source, self.schema, self.output, True)
            with self.output.open(encoding="utf-8", newline="") as f:
                self.assertEqual(list(csv.reader(f))[1], ["001", "1.00", f"a{newline}b"])

    def test_failure_preserves_existing_and_cleans_stage(self):
        self.output.write_text("prior", encoding="utf-8")
        self.configure(max_changed=0)
        with self.assertRaises(PolicyFailure):
            process(self.source, self.schema, self.output, True)
        self.assertEqual(self.output.read_text(), "prior")
        self.assertEqual(list(self.root.glob(".transit-*")), [])

    def test_no_overwrite_and_alias(self):
        with self.assertRaises(InputError):
            process(self.source, self.schema, self.source, True)
        os.link(self.source, self.output)
        with self.assertRaises(InputError):
            process(self.source, self.schema, self.output, True)

    def test_headers_and_ragged(self):
        for data in ["", "id,id\n", "x,y\n", "id,amount\n1\n", "id,amount\n1,2,3\n"]:
            self.source.write_text(data, encoding="utf-8")
            with self.assertRaises(InputError):
                process(self.source, self.schema, self.output)

    def test_empty_table_has_zero_totals(self):
        self.source.write_text("id,amount\n", encoding="utf-8")
        report = process(self.source, self.schema)
        self.assertEqual(report["records"], 0)
        self.assertEqual(report["columns"][0]["sum_after"], "0")

    def test_bounded_issue_examples(self):
        self.source.write_text("id,amount\n" + "x,bad\n" * 100, encoding="utf-8")
        with self.assertRaises(PolicyFailure) as caught:
            process(self.source, self.schema)
        self.assertEqual(len(caught.exception.report["issues"]), 20)
        self.assertEqual(caught.exception.report["issues_omitted"], 80)

    def test_cli_exit_codes(self):
        args = [str(self.source), "--contract", str(self.schema)]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 0)
        self.configure(max_changed=0)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 1)
        self.schema.write_text("{", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(main(args), 2)

    def test_module_entry(self):
        result = subprocess.run(
            [sys.executable, "-m", "decimal_landing", "--version"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "0.1.0")


if __name__ == "__main__":
    unittest.main()
