"""Unit tests for extract.py — seed validation, banned filter, regex, CSV roundtrip."""
import csv
from pathlib import Path

import pytest

from comfybulk.extract import (
    BANNED, CSV_HEADER, append_to_csv, find_seed_recursive, remove_banned,
    validate_seed, _seed_from_filename, _prompt_from_escaped_json, _seed_from_jsonstring,
)


# ---- validate_seed ----

@pytest.mark.parametrize("seed", [
    "1108887061710056", "518898413366690", "26598041383814",
    "503586485798689", "82305535705460",
])
def test_validate_seed_real(seed):
    assert validate_seed(seed)


@pytest.mark.parametrize("seed", [
    "",                  # empty
    "abc",               # non-numeric
    "12345",             # too short
    "1111111111",        # all same digit
    "0000000000",        # all zeros (matches 0+ pattern)
    "9999999999",        # all 9s
    "111111111122",      # >60% repetition
])
def test_validate_seed_bad(seed):
    assert not validate_seed(seed)


# ---- remove_banned ----

@pytest.mark.parametrize("inp,expect", [
    ("a DMT trip with psychedelic ego-death visuals",
     "a visionary journey with abstract transcendent visuals"),
    ("Dmt and Psychedelic", "visionary and abstract"),  # case-insensitive
    ("nothing to clean here", "nothing to clean here"),
    ("", ""),
])
def test_remove_banned(inp, expect):
    assert remove_banned(inp) == expect


def test_banned_table_complete():
    # Sanity: 4 rules at minimum.
    assert len(BANNED) >= 4


# ---- find_seed_recursive ----

def test_find_seed_in_nested_dict():
    obj = {"workflow": {"nodes": [{"inputs": {"seed": 1108887061710056, "other": 1}}]}}
    assert find_seed_recursive(obj) == "1108887061710056"


def test_find_seed_path_match_overrides():
    obj = {"seed_value": "503586485798689"}
    assert find_seed_recursive(obj) == "503586485798689"


def test_find_seed_returns_none_for_invalid():
    obj = {"seed": 12345}  # too short
    assert find_seed_recursive(obj) is None


# ---- filename seed extraction ----

@pytest.mark.parametrize("name,expect", [
    ("WVI2V_CC_24FPS_1108887061710056_00001", "1108887061710056"),
    ("WVI2V_seed1108887061710056", "1108887061710056"),
    ("file_short_seed.mp4", None),
])
def test_seed_from_filename(name, expect):
    assert _seed_from_filename(name) == expect


# ---- escaped JSON regexes ----

def test_seed_from_jsonstring_escaped():
    js = r'something seed\\": 503586485798689 etc'
    assert _seed_from_jsonstring(js) == "503586485798689"


def test_seed_from_jsonstring_normal():
    js = '{"seed": 1108887061710056}'
    assert _seed_from_jsonstring(js) == "1108887061710056"


def test_prompt_from_escaped_json():
    js = r'foo "text_0": "hello world this prompt is long enough" bar'
    p = _prompt_from_escaped_json(js)
    assert p == "hello world this prompt is long enough"


# ---- CSV roundtrip ----

def test_append_to_csv_creates_and_dedupes(tmp_path):
    csv_path = str(tmp_path / "metadata.csv")
    assert append_to_csv("a long enough prompt for testing", "1108887061710056",
                         "clip1.mp4", csv_path) is True
    assert append_to_csv("a long enough prompt for testing", "1108887061710056",
                         "clip1.mp4", csv_path) is False  # exact duplicate
    assert append_to_csv("a long enough prompt for testing", "1108887061710056",
                         "clip2.mp4", csv_path) is True   # different clipname OK
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 2
    assert all(set(r.keys()) == set(CSV_HEADER) for r in rows)


def test_append_to_csv_applies_banned_filter(tmp_path):
    csv_path = str(tmp_path / "metadata.csv")
    append_to_csv("DMT trip with psychedelic ego-death", "1108887061710056",
                  "x.mp4", csv_path)
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert "DMT" not in rows[0]["content_prompt"]
    assert "visionary" in rows[0]["content_prompt"]
