import csv
from dataclasses import asdict, replace
import io
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from bageshuo_history_import import (
    InputError, load_batch, merge, parse_batch, parse_csv,
)


ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
NATIVE = EXAMPLES / "bageshuo-native.json"
OLD_TOOL = EXAMPLES / "shandianshuo.csv"


class HistoryImportTests(unittest.TestCase):
    def setUp(self):
        self.native = load_batch(NATIVE)
        self.batch = load_batch(OLD_TOOL)

    def run_cli(self, *arguments):
        return subprocess.run(
            [sys.executable, str(ROOT / "bageshuo_history_import.py"), *arguments],
            cwd=ROOT, capture_output=True, text=True, check=False,
        )

    def test_demo_totals_and_cross_source_streak(self):
        result = merge(self.native, [self.batch], baseline=60)
        combined = result["combined"]
        self.assertEqual(combined["characters"], 2600)
        self.assertEqual(combined["sessions"], 17)
        self.assertEqual(combined["active_days"], 3)
        self.assertEqual(combined["longest_streak_days"], 3)
        self.assertEqual(combined["recording_seconds"], 260)
        self.assertEqual(combined["estimated_saved_seconds"], 2340)
        self.assertEqual(result["by_source"]["bageshuo"]["active_days"], 2)
        self.assertEqual(result["by_source"]["bageshuo"]["longest_streak_days"], 1)

    def test_cli_normalize_roundtrip_and_dedupe(self):
        normalized = self.run_cli("normalize", str(OLD_TOOL))
        self.assertEqual(normalized.returncode, 0, normalized.stderr)
        raw = json.loads(normalized.stdout)
        raw["days"].reverse()
        with TemporaryDirectory() as directory:
            alternate = Path(directory) / "same-data.json"
            alternate.write_text(json.dumps(raw), encoding="utf-8")
            merged = self.run_cli("merge", "--native", str(NATIVE), str(OLD_TOOL), str(alternate))
        self.assertEqual(merged.returncode, 0, merged.stderr)
        result = json.loads(merged.stdout)
        self.assertEqual(result["ignored_duplicate_import_batches"], 1)
        self.assertEqual(result["combined"]["characters"], 2600)

    def test_different_batches_with_same_source_overlap_are_rejected(self):
        subset = replace(self.batch, days=(self.batch.days[0],))
        with self.assertRaisesRegex(InputError, "日期重叠"):
            merge(self.native, [self.batch, subset])

    def test_disjoint_batches_with_same_source_are_accepted(self):
        next_day = replace(self.batch.days[0], date="2025-01-13")
        extra = replace(self.batch, days=(next_day,))
        result = merge(self.native, [self.batch, extra])
        self.assertEqual(result["by_source"]["shandianshuo"]["characters"], 3000)
        self.assertEqual(result["combined"]["longest_streak_days"], 4)

    def test_mismatched_time_zone_or_rules_are_rejected(self):
        for changes in (
            {"time_zone": "UTC"},
            {"character_rule": "final_text_code_points_without_whitespace"},
            {"session_rule": "recordings_started"},
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(InputError, "口径"):
                merge(self.native, [replace(self.batch, **changes)])

    def test_incomplete_recording_does_not_become_zero_or_savings(self):
        unknown = replace(self.batch.days[0], recording_seconds=None)
        result = merge(self.native, [replace(self.batch, days=(unknown, self.batch.days[1]))], baseline=60)
        combined = result["combined"]
        self.assertIsNone(combined["recording_seconds"])
        self.assertEqual(combined["known_recording_seconds"], 140)
        self.assertEqual(combined["recording_rows_covered"], 3)
        self.assertIsNone(combined["estimated_saved_seconds"])

    def test_no_baseline_does_not_produce_estimate(self):
        result = merge(self.native, [self.batch])
        self.assertEqual(result["combined"]["recording_seconds"], 260)
        self.assertIsNone(result["combined"]["estimated_saved_seconds"])

    def test_zero_session_date_does_not_bridge_streak(self):
        zero = replace(self.native.days[0], date="2025-01-11", sessions=0, characters=0, recording_seconds=None)
        native = replace(self.native, days=(*self.native.days, zero))
        result = merge(native, [])
        self.assertEqual(result["combined"]["active_days"], 2)
        self.assertEqual(result["combined"]["longest_streak_days"], 1)
        self.assertEqual(result["combined"]["recording_seconds"], 80)

    def test_invalid_native_source_and_reserved_import_are_rejected(self):
        with self.assertRaisesRegex(InputError, "source 必须"):
            merge(self.batch, [])
        with self.assertRaisesRegex(InputError, "只允许"):
            merge(self.native, [self.native])

    def test_invalid_dates_negative_values_and_boolean_counts_are_rejected(self):
        for field, value in (("date", "2025-02-29"), ("date", "2025-1-10"), ("sessions", -1), ("characters", True)):
            raw = asdict(self.batch)
            raw["days"] = list(raw["days"])
            raw["days"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(InputError):
                parse_batch(raw)

    def test_duplicate_rows_are_rejected(self):
        raw = asdict(self.batch)
        day = raw["days"][0]
        raw["days"] = [day, day]
        with self.assertRaisesRegex(InputError, "批次内日期重复"):
            parse_batch(raw)

    def test_csv_metadata_must_stay_consistent(self):
        rows = list(csv.reader(OLD_TOOL.read_text(encoding="utf-8").splitlines()))
        rows[2][1] = "other-tool"
        stream = io.StringIO()
        csv.writer(stream).writerows(rows)
        with self.assertRaisesRegex(InputError, "与首行不同"):
            parse_csv(stream.getvalue())

    def test_cli_failure_does_not_emit_partial_report(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text('{"schema_version": 1, "schema_version": 1}', encoding="utf-8")
            result = self.run_cli("merge", "--native", str(NATIVE), str(OLD_TOOL), str(path))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("重复字段", result.stderr)


if __name__ == "__main__":
    unittest.main()
