import unittest
from unittest.mock import patch

from decimal_landing._files import InputError, strict_json


class JsonLimitTests(unittest.TestCase):
    def test_integer_limit_is_controlled(self):
        # Independent of an embedding application's sys.set_int_max_str_digits.
        with patch("decimal_landing._files.json.loads", side_effect=ValueError("integer limit")):
            with self.assertRaisesRegex(InputError, "invalid JSON"):
                strict_json('{"value":1}')

    def test_duplicate_member_diagnostic_survives(self):
        with self.assertRaisesRegex(InputError, "duplicate JSON"):
            strict_json('{"value":1,"value":2}')

    def test_deep_nesting_is_controlled(self):
        with self.assertRaises(InputError):
            strict_json("[" * 2000 + "]" * 2000)
