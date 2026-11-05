#!/usr/bin/env python3
"""Validate and merge local daily voice-input statistics."""

import argparse
import csv
from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import io
import json
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


SCHEMA_VERSION = 1
NATIVE_SOURCE = "bageshuo"
CHARACTER_RULES = {
    "final_text_code_points",
    "final_text_code_points_without_whitespace",
}
SESSION_RULES = {"completed_dictations", "recordings_started"}
BATCH_FIELDS = {
    "schema_version", "source", "time_zone", "character_rule", "session_rule", "days",
}
DAY_FIELDS = {"date", "characters", "sessions"}
CSV_REQUIRED = (BATCH_FIELDS - {"days"}) | DAY_FIELDS
CSV_OPTIONAL = {"recording_seconds"}


class InputError(ValueError):
    """The supplied statistics do not satisfy the import format."""


@dataclass(frozen=True)
class Day:
    date: str
    characters: int
    sessions: int
    recording_seconds: int | None = None


@dataclass(frozen=True)
class Batch:
    schema_version: int
    source: str
    time_zone: str
    character_rule: str
    session_rule: str
    days: tuple[Day, ...]

    @property
    def conventions(self):
        return (self.time_zone, self.character_rule, self.session_rule)

    @property
    def fingerprint(self):
        canonical = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def read_int(value, field):
    if type(value) is not int or value < 0:
        raise InputError(f"{field} 必须是非负整数")
    return value


def parse_day(raw):
    if not isinstance(raw, dict) or not DAY_FIELDS <= raw.keys() <= DAY_FIELDS | CSV_OPTIONAL:
        raise InputError("每日记录需要 date、characters、sessions，可选 recording_seconds")
    day = raw["date"]
    if not isinstance(day, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", day):
        raise InputError("日期必须为 YYYY-MM-DD")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise InputError(f"{day} 不是有效日期") from None
    characters = read_int(raw["characters"], f"{day} characters")
    sessions = read_int(raw["sessions"], f"{day} sessions")
    seconds = raw.get("recording_seconds")
    if seconds is not None:
        seconds = read_int(seconds, f"{day} recording_seconds")
    if sessions == 0 and (characters > 0 or seconds):
        raise InputError(f"{day} 有字符或录音时长时，sessions 必须大于 0")
    return Day(day, characters, sessions, seconds)


def parse_batch(raw):
    if not isinstance(raw, dict) or set(raw) != BATCH_FIELDS:
        raise InputError("JSON 顶层字段必须符合 v1 格式，见 README")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != SCHEMA_VERSION:
        raise InputError("schema_version 必须为整数 1")
    source = raw["source"]
    if not isinstance(source, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", source):
        raise InputError("source 必须以小写英文字母开头，只用小写字母、数字或连字符")
    time_zone = raw["time_zone"]
    try:
        if not isinstance(time_zone, str) or time_zone.startswith("."):
            raise ValueError
        ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        raise InputError("time_zone 必须为可用的 IANA 时区，例如 Asia/Shanghai") from None
    character_rule = raw["character_rule"]
    session_rule = raw["session_rule"]
    if not isinstance(character_rule, str) or character_rule not in CHARACTER_RULES:
        raise InputError("character_rule 不受支持，见 README")
    if not isinstance(session_rule, str) or session_rule not in SESSION_RULES:
        raise InputError("session_rule 不受支持，见 README")
    if not isinstance(raw["days"], list):
        raise InputError("days 必须为数组")
    days = tuple(sorted(map(parse_day, raw["days"]), key=lambda day: day.date))
    for left, right in zip(days, days[1:]):
        if left.date == right.date:
            raise InputError(f"{source} 批次内日期重复：{left.date}")
    return Batch(SCHEMA_VERSION, source, time_zone, character_rule, session_rule, days)


def unique_json_keys(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise InputError("JSON 包含重复字段")
    return result


def parse_csv(text):
    reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
    fields = reader.fieldnames or []
    if len(fields) != len(set(fields)) or not CSV_REQUIRED <= set(fields) <= CSV_REQUIRED | CSV_OPTIONAL:
        raise InputError("CSV 列名缺失、重复或超出 v1 格式，见 README")
    metadata = None
    days = []
    for row in reader:
        if None in row or None in row.values():
            raise InputError(f"CSV 第 {reader.line_num} 行列数不正确")
        current = {key: row[key] for key in BATCH_FIELDS - {"days"}}
        if metadata is None:
            metadata = current
        if current != metadata:
            raise InputError(f"CSV 第 {reader.line_num} 行的来源或口径与首行不同")
        day = {"date": row["date"]}
        for key in ("characters", "sessions", "recording_seconds"):
            value = row.get(key, "")
            if key == "recording_seconds" and value == "":
                continue
            if not re.fullmatch(r"[0-9]+", value):
                raise InputError(f"CSV 第 {reader.line_num} 行 {key} 必须为非负整数")
            day[key] = int(value)
        days.append(day)
    if metadata is None:
        raise InputError("CSV 至少需要一条每日记录；空样例请使用 JSON 的 days: []")
    if metadata["schema_version"] != "1":
        raise InputError("CSV schema_version 必须为 1")
    return parse_batch({**metadata, "schema_version": SCHEMA_VERSION, "days": days})


def load_batch(path):
    try:
        text = path.read_text(encoding="utf-8-sig")
        suffix = path.suffix.lower()
        if suffix == ".csv":
            return parse_csv(text)
        if suffix == ".json":
            return parse_batch(json.loads(text, object_pairs_hook=unique_json_keys))
        raise InputError("文件扩展名必须为 .csv 或 .json")
    except (OSError, ValueError, csv.Error) as error:
        raise InputError(f"{path.name}：{error}") from None


def summarize(days, baseline):
    characters = sum(day.characters for day in days)
    sessions = sum(day.sessions for day in days)
    active = [day for day in days if day.sessions > 0]
    ordinals = sorted({date.fromisoformat(day.date).toordinal() for day in active})
    longest = streak = 0
    previous = None
    for ordinal in ordinals:
        streak = streak + 1 if previous == ordinal - 1 else 1
        longest = max(longest, streak)
        previous = ordinal
    recorded = [day.recording_seconds for day in active if day.recording_seconds is not None]
    known_seconds = sum(recorded)
    complete = len(recorded) == len(active)
    saved = max(0, characters * 60 // baseline - known_seconds) if baseline is not None and complete else None
    return {
        "characters": characters,
        "sessions": sessions,
        "active_days": len(ordinals),
        "longest_streak_days": longest,
        "recording_seconds": known_seconds if complete else None,
        "known_recording_seconds": known_seconds,
        "recording_rows_covered": len(recorded),
        "active_source_date_rows": len(active),
        "estimated_saved_seconds": saved,
    }


def merge(native, imports, baseline=None):
    if native.source != NATIVE_SOURCE:
        raise InputError("--native 的 source 必须是 bageshuo")
    if baseline is not None and read_int(baseline, "每分钟键盘字符数") == 0:
        raise InputError("每分钟键盘字符数必须大于 0")
    source_days = {NATIVE_SOURCE: {day.date: day for day in native.days}}
    fingerprints = set()
    duplicate_count = 0
    for batch in imports:
        if batch.source == NATIVE_SOURCE:
            raise InputError("bageshuo 来源只允许通过 --native 提供")
        if batch.conventions != native.conventions:
            raise InputError(f"{batch.source} 的时区或统计口径与原生样例不一致")
        fingerprint = batch.fingerprint
        if fingerprint in fingerprints:
            duplicate_count += 1
            continue
        days = source_days.setdefault(batch.source, {})
        overlap = days.keys() & {day.date for day in batch.days}
        if overlap:
            raise InputError(f"{batch.source} 日期重叠：{min(overlap)}（共 {len(overlap)} 天）")
        days.update((day.date, day) for day in batch.days)
        fingerprints.add(fingerprint)

    by_source = {
        source: summarize(list(days.values()), baseline)
        for source, days in sorted(source_days.items())
    }
    combined_days = [day for days in source_days.values() for day in days.values()]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "local_statistics_preview",
        "time_zone": native.time_zone,
        "character_rule": native.character_rule,
        "session_rule": native.session_rule,
        "ignored_duplicate_import_batches": duplicate_count,
        "accepted_import_fingerprints": sorted(fingerprints),
        "typing_baseline_chars_per_minute": baseline,
        "by_source": by_source,
        "combined": summarize(combined_days, baseline),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线校验并合并每日语音输入统计")
    commands = parser.add_subparsers(dest="command", required=True)
    normalize = commands.add_parser("normalize", help="校验 CSV/JSON 并输出规范 JSON")
    normalize.add_argument("input", type=Path)
    combine = commands.add_parser("merge", help="预览原生和导入统计的合计")
    combine.add_argument("--native", required=True, type=Path)
    combine.add_argument("--typing-chars-per-minute", type=int)
    combine.add_argument("imports", nargs="*", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "normalize":
            result = asdict(load_batch(args.input))
        else:
            result = merge(
                load_batch(args.native),
                [load_batch(path) for path in args.imports],
                args.typing_chars_per_minute,
            )
    except InputError as error:
        print(f"无法处理：{error}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
