"""Round-2 proof: Shopify CSV exporter.

Heavy ffmpeg/pipeline graph is mocked via a fixture manifest written directly
to disk (the real pipeline emits the same JSONL via
``comfybulk.pipeline._write_manifest``).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from comfybulk.export.shopify import (
    SHOPIFY_HEADERS,
    ShopifyDefaults,
    asset_count,
    export_shopify_csv,
    manifest_to_rows,
)


# Header spec: Shopify products.csv import template positional contract.
SHOPIFY_SPEC_REQUIRED = (
    "Handle",
    "Title",
    "Body (HTML)",
    "Vendor",
    "Type",
    "Tags",
    "Published",
    "Variant SKU",
    "Variant Price",
    "Image Src",
    "Image Position",
    "Image Alt Text",
    "SEO Title",
    "SEO Description",
    "Status",
)


@pytest.fixture
def fixture_manifest(tmp_path: Path) -> tuple[Path, int]:
    """Mock a bulk run's pipeline_manifest.jsonl. Returns (path, asset_count)."""
    finals = tmp_path / "finals"
    finals.mkdir()
    manifest = finals / "pipeline_manifest.jsonl"
    entries = [
        {
            "event": "pipeline_run",
            "timestamp": "20260514_120000",
            "completed_at": "2026-05-14T12:00:30",
            "seed": 42,
            "variant": "grid",
            "source": str(tmp_path),
            "specified_file": None,
            "clips": [str(tmp_path / "clip_a.mp4")],
            "outputs": [str(finals / "grid_20260514_120000.mp4")],
            "no_effects": True,
            "organize_favorites": False,
        },
        {
            "event": "pipeline_run",
            "timestamp": "20260514_120130",
            "completed_at": "2026-05-14T12:01:45",
            "seed": 43,
            "variant": "single",
            "source": str(tmp_path),
            "specified_file": str(tmp_path / "clip_b.mp4"),
            "clips": [str(tmp_path / "clip_b.mp4")],
            "outputs": [
                str(finals / "single_cta_20260514_120130.mp4"),
                str(finals / "single_nocta_20260514_120130.mp4"),
            ],
            "no_effects": True,
            "organize_favorites": False,
        },
    ]
    with manifest.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, sort_keys=True) + "\n")
    expected_assets = sum(len(e["outputs"]) for e in entries)
    return manifest, expected_assets


# --- contract: header order matches Shopify spec ---------------------------

def test_header_order_starts_with_handle_title_body():
    assert SHOPIFY_HEADERS[0] == "Handle"
    assert SHOPIFY_HEADERS[1] == "Title"
    assert SHOPIFY_HEADERS[2] == "Body (HTML)"


def test_header_includes_all_required_shopify_columns():
    missing = [col for col in SHOPIFY_SPEC_REQUIRED if col not in SHOPIFY_HEADERS]
    assert not missing, f"missing Shopify columns: {missing}"


# --- contract: rows = manifest asset count ---------------------------------

def test_rows_equal_asset_count(fixture_manifest):
    manifest, expected = fixture_manifest
    rows = manifest_to_rows(manifest)
    assert len(rows) == expected == 3


def test_export_writes_csv_with_correct_shape(tmp_path: Path, fixture_manifest):
    manifest, expected = fixture_manifest
    out = tmp_path / "products.csv"
    written = export_shopify_csv(manifest, out)
    assert written == expected

    with out.open(newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        body = list(reader)

    assert header == list(SHOPIFY_HEADERS)
    assert len(body) == expected


# --- behavior: handle uniqueness, image src wiring, sane defaults ----------

def test_handles_are_unique_across_rows(fixture_manifest):
    manifest, _ = fixture_manifest
    rows = manifest_to_rows(manifest)
    handles = [r["Handle"] for r in rows]
    assert len(set(handles)) == len(handles)


def test_image_src_points_at_output_path(fixture_manifest):
    manifest, _ = fixture_manifest
    rows = manifest_to_rows(manifest)
    for row in rows:
        assert row["Image Src"].endswith(".mp4")
        assert Path(row["Image Src"]).name in row["Image Src"]


def test_defaults_propagate_into_rows(fixture_manifest):
    manifest, _ = fixture_manifest
    rows = manifest_to_rows(
        manifest,
        defaults=ShopifyDefaults(vendor="Acme", product_type="Demo", price="19.99"),
    )
    assert all(r["Vendor"] == "Acme" for r in rows)
    assert all(r["Type"] == "Demo" for r in rows)
    assert all(r["Variant Price"] == "19.99" for r in rows)


def test_status_defaults_to_draft(fixture_manifest):
    manifest, _ = fixture_manifest
    rows = manifest_to_rows(manifest)
    assert all(r["Status"] == "draft" for r in rows)
    assert all(r["Published"] == "FALSE" for r in rows)


# --- dry-run / no-side-effect path -----------------------------------------

def test_manifest_to_rows_does_not_create_files(tmp_path: Path, fixture_manifest):
    manifest, _ = fixture_manifest
    before = {p.name for p in tmp_path.rglob("*")}
    manifest_to_rows(manifest)
    after = {p.name for p in tmp_path.rglob("*")}
    assert before == after


def test_missing_manifest_raises(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        export_shopify_csv(tmp_path / "nope.jsonl", tmp_path / "out.csv")


def test_asset_count_matches_manifest_outputs(fixture_manifest):
    manifest, expected = fixture_manifest
    assert asset_count(manifest) == expected


# --- malformed input resilience --------------------------------------------

def test_malformed_lines_are_skipped(tmp_path: Path):
    manifest = tmp_path / "noisy.jsonl"
    finals = tmp_path / "finals"
    finals.mkdir()
    manifest.write_text(
        "\n".join([
            "not-json-at-all",
            json.dumps({"event": "other_event", "outputs": ["ignored.mp4"]}),
            json.dumps({
                "event": "pipeline_run",
                "timestamp": "t",
                "variant": "grid",
                "outputs": [str(finals / "real.mp4")],
            }),
            "",
        ]) + "\n",
        encoding="utf-8",
    )
    rows = manifest_to_rows(manifest)
    assert len(rows) == 1
    assert rows[0]["Image Src"].endswith("real.mp4")
