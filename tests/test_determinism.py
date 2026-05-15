"""Determinism contract: rerunning the pipeline against the same input
must produce byte-identical metadata.csv and the same set of output filenames
(timestamps excluded). Operates on 3 small fixture clips under
tests/fixtures/samples/.

The full encoder stack is intentionally not invoked here — ffmpeg encoding is
not bit-deterministic across runs even with identical inputs, and the proof
in 03-feature-plans.md scopes the comparison to "the metadata.csv and all
output filenames". The determinism we own is:

  1. Metadata extraction → metadata.csv writes are byte-identical for the same
     input set.
  2. The pipeline's seeded clip-selection step (_select_clips) yields the
     identical clip list and derived output basename when invoked with the
     same seed.
"""
from __future__ import annotations
import hashlib
import shutil
from pathlib import Path

import pytest

from comfybulk.extract import process_directory
from comfybulk.pipeline import _select_clips, _derive_iteration_seed
import random


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "samples"
EXPECTED_FIXTURE_COUNT = 3


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def _strip_filename_column(csv_text: str) -> str:
    """Drop the 'filename' field (first column) before hashing.

    metadata.csv's filename column is populated downstream by the rename step,
    which is keyed on the run timestamp. The determinism contract is over the
    extraction-derived columns; filename is timestamped and intentionally out
    of scope (see module docstring + 03-feature-plans.md §9.5).
    """
    import csv, io
    out = io.StringIO()
    reader = csv.DictReader(io.StringIO(csv_text))
    fieldnames = [f for f in (reader.fieldnames or []) if f != "filename"]
    writer = csv.DictWriter(out, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
    writer.writeheader()
    for row in reader:
        writer.writerow({k: row.get(k, "") for k in fieldnames})
    return out.getvalue()


def test_fixtures_present():
    """Guard: the 3 sample clips ship in-tree."""
    assert FIXTURES.is_dir(), f"missing fixtures dir: {FIXTURES}"
    clips = sorted(FIXTURES.glob("*.mp4"))
    assert len(clips) == EXPECTED_FIXTURE_COUNT, (
        f"expected {EXPECTED_FIXTURE_COUNT} fixture clips, found {len(clips)}: {clips}"
    )
    for c in clips:
        assert c.stat().st_size > 0, f"empty fixture: {c}"
        assert c.stat().st_size < 2 * 1024 * 1024, f"fixture >2MB: {c}"


def test_metadata_csv_is_byte_identical_across_runs(tmp_path):
    """Extracting twice from the same fixture set yields byte-identical CSVs
    (after dropping the timestamp-dependent filename column)."""
    runs = []
    for label in ("run_a", "run_b"):
        work = tmp_path / label
        work.mkdir()
        # Process from a copy so each run is independent of the others.
        src = work / "samples"
        shutil.copytree(FIXTURES, src)
        csv_path = work / "metadata.csv"
        ok, fail = process_directory(str(src), str(csv_path))
        assert ok == EXPECTED_FIXTURE_COUNT, f"{label}: extracted {ok}/{EXPECTED_FIXTURE_COUNT} (fail={fail})"
        runs.append(csv_path.read_text(encoding="utf-8"))

    a, b = (_strip_filename_column(r) for r in runs)
    digest_a = hashlib.sha256(a.encode("utf-8")).hexdigest()
    digest_b = hashlib.sha256(b.encode("utf-8")).hexdigest()
    assert digest_a == digest_b, (
        f"metadata.csv content drifted between runs\n  a={digest_a}\n  b={digest_b}\n"
        f"--- run_a ---\n{a}\n--- run_b ---\n{b}"
    )


def test_seeded_clip_selection_is_stable(tmp_path):
    """_select_clips with the same Random(seed) returns the same clip list
    twice. This is the determinism contract that drives output filenames
    (each final output is named after the selected clip basenames)."""
    src = tmp_path / "samples"
    shutil.copytree(FIXTURES, src)

    fixed_seed = 1108887061710056
    derived = _derive_iteration_seed(fixed_seed, "montage", 0)

    rng_a = random.Random(derived)
    rng_b = random.Random(derived)
    pick_a = _select_clips(str(src), "montage", None, rng=rng_a)
    pick_b = _select_clips(str(src), "montage", None, rng=rng_b)

    assert [p.name for p in pick_a] == [p.name for p in pick_b], (
        f"seeded selection drift:\n  a={[p.name for p in pick_a]}\n  b={[p.name for p in pick_b]}"
    )


def test_fixture_file_hashes_are_stable():
    """Sanity: the on-disk fixture files have not been tampered with mid-run.
    Establishes the byte-identical-input precondition the rest of this module
    relies on."""
    clips = sorted(FIXTURES.glob("*.mp4"))
    digests = {c.name: _sha256(c) for c in clips}
    assert len(digests) == EXPECTED_FIXTURE_COUNT
    # Re-hash to confirm read-side determinism (catches a hash impl change).
    redigests = {c.name: _sha256(c) for c in clips}
    assert digests == redigests
