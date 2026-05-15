"""Unit tests for images.py: bulk image → metadata.csv mapping."""
import csv
from pathlib import Path

import pytest

from comfybulk.images import IMAGE_EXTS, extract_image, is_image, process_directory


# Minimal 1x1 transparent PNG (no ComfyUI metadata). Used as a stand-in for arbitrary images.
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63000100000005000100"
    "0d0a2db40000000049454e44ae426082"
)


def _write_tiny_png(path: Path, name: str) -> Path:
    p = path / name
    p.write_bytes(TINY_PNG)
    return p


# ---- is_image / IMAGE_EXTS ----

@pytest.mark.parametrize("name,expected", [
    ("a.png", True), ("b.PNG", True),
    ("c.jpg", True), ("d.JPEG", True), ("e.jpeg", True),
    ("f.mp4", False), ("g.txt", False), ("h", False), ("i.webp", False),
])
def test_is_image_extensions(tmp_path, name, expected):
    f = tmp_path / name
    f.write_bytes(b"x")
    assert is_image(f) is expected


def test_image_exts_is_lowercase():
    assert IMAGE_EXTS == {".png", ".jpg", ".jpeg"}


# ---- extract_image ----

def test_extract_image_jpg_returns_row_with_empty_prompt(tmp_path):
    f = tmp_path / "plain.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0not-a-real-jpeg")
    row = extract_image(str(f))
    assert row["clipname"] == "plain.jpg"
    assert row["content_prompt"] == ""
    assert row["seed"] == ""


def test_extract_image_png_no_metadata_returns_row(tmp_path):
    f = _write_tiny_png(tmp_path, "tiny.png")
    row = extract_image(str(f))
    assert row["clipname"] == "tiny.png"
    assert row["content_prompt"] == ""
    assert row["seed"] == ""


def test_extract_image_seed_from_filename(tmp_path):
    # Filename-embedded seed gets picked up even if PNG has no embedded JSON.
    f = _write_tiny_png(tmp_path, "WVI2V_seed1108887061710056.png")
    row = extract_image(str(f))
    assert row["seed"] == "1108887061710056"


# ---- process_directory: happy path ----

def test_process_directory_writes_one_row_per_image(tmp_path):
    src = tmp_path / "imgs"
    src.mkdir()
    for i in range(10):
        _write_tiny_png(src, f"img_{i:02d}.png")
    csv_path = str(tmp_path / "metadata.csv")
    ok, fail = process_directory(str(src), csv_path)
    assert ok == 10
    assert fail == 0
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 10
    assert {r["clipname"] for r in rows} == {f"img_{i:02d}.png" for i in range(10)}


# ---- process_directory: non-image skip ----

def test_process_directory_skips_non_image_files(tmp_path):
    src = tmp_path / "mixed"
    src.mkdir()
    _write_tiny_png(src, "a.png")
    _write_tiny_png(src, "b.jpg")  # not a real JPEG but accepted by extension
    (src / "notes.txt").write_text("not an image")
    (src / "movie.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    (src / "config.toml").write_text("[x]\n")
    csv_path = str(tmp_path / "metadata.csv")
    ok, fail = process_directory(str(src), csv_path)
    assert ok == 2
    assert fail == 0
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    clipnames = sorted(r["clipname"] for r in rows)
    assert clipnames == ["a.png", "b.jpg"]


# ---- process_directory: idempotent re-run de-dups ----

def test_process_directory_dedupes_on_rerun(tmp_path):
    src = tmp_path / "imgs"
    src.mkdir()
    for i in range(3):
        _write_tiny_png(src, f"x_{i}.png")
    csv_path = str(tmp_path / "metadata.csv")
    ok1, _ = process_directory(str(src), csv_path)
    ok2, _ = process_directory(str(src), csv_path)
    assert ok1 == 3
    assert ok2 == 0  # all duplicates
    with open(csv_path, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 3


# ---- process_directory: missing directory ----

def test_process_directory_raises_for_missing_dir(tmp_path):
    with pytest.raises(FileNotFoundError):
        process_directory(str(tmp_path / "does-not-exist"), str(tmp_path / "out.csv"))
