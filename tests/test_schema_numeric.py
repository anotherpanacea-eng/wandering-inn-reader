"""Synthetic timing-boundary regressions; no audio, model or browser needed."""
import copy
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from schema import SchemaError, validate_doc


BASE = {
    "title": "Synthetic timing fixture",
    "audio": "synthetic.wav",
    "segments": [{"id": 0, "start": 0, "end": 2, "text": "Synthetic sentence.",
                  "words": [{"w": "Synthetic", "s": 0, "e": 1}]}],
    "chapters": [{"title": "Synthetic chapter", "start": 0, "seg": 0}],
}
FIELDS = (
    ("segments", 0, "start"),
    ("segments", 0, "end"),
    ("segments", 0, "words", 0, "s"),
    ("segments", 0, "words", 0, "e"),
    ("chapters", 0, "start"),
)


def with_timing(path, value):
    doc = copy.deepcopy(BASE)
    cursor = doc
    for part in path[:-1]:
        cursor = cursor[part]
    cursor[path[-1]] = value
    return doc


class NumericTimingTests(unittest.TestCase):
    def test_overflowing_json_integer_has_schema_error_at_every_boundary(self):
        for path in FIELDS:
            for value in (10 ** 400, -(10 ** 400)):
                with self.subTest(path=path, negative=value < 0):
                    doc = json.loads(json.dumps(with_timing(path, value)))
                    with self.assertRaisesRegex(SchemaError, "schema error"):
                        validate_doc(doc, source="synthetic")

    def test_overflowing_integer_returns_diagnostics_when_not_strict(self):
        for path in FIELDS:
            with self.subTest(path=path):
                issues = validate_doc(with_timing(path, 10 ** 400), strict=False)
                self.assertTrue(any(level == "ERROR" and "invalid" in message
                                    for level, message in issues))

    def test_existing_nonfinite_and_nonnumeric_refusals(self):
        for path in FIELDS:
            for value in (float("nan"), float("inf"), -float("inf"), True, "1", None):
                with self.subTest(path=path, value=value):
                    with self.assertRaises(SchemaError):
                        validate_doc(with_timing(path, value))

    def test_ordinary_and_large_finite_timings_remain_valid(self):
        self.assertEqual(validate_doc(BASE), [])
        for value in (2.5, 10 ** 300):
            with self.subTest(value=value):
                self.assertEqual(validate_doc(with_timing(FIELDS[1], value)), [])

    def test_nonmonotonic_start_remains_only_a_warning(self):
        doc = copy.deepcopy(BASE)
        doc["segments"].append({"id": 1, "start": 0, "end": 1, "text": "Second."})
        doc["segments"][0]["start"] = 1
        issues = validate_doc(doc)
        self.assertEqual([level for level, _ in issues], ["WARN"])


if __name__ == "__main__":
    unittest.main()
