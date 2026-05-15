"""Recipe library contract.

Asserts behavior the user can actually see:
  - `recipes list` returns at least 3 starter recipes
  - every shipped recipe has a thumbnail companion PNG
  - `recipes show <name>` round-trips parsed JSON
  - `recipes run <name>` produces one output per input file
"""
from __future__ import annotations

import io
import json
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from comfybulk.recipes import list_recipes, load_recipe, run_recipe
from comfybulk.cli.recipes import _cmd_list, _cmd_show, _cmd_run


REQUIRED_STARTERS = {"etsy-product", "realestate-listing", "social-square"}


# ---- contract: list ----

def test_list_recipes_returns_three_starters():
    names = {r.name for r in list_recipes()}
    assert REQUIRED_STARTERS.issubset(names), f"missing starters: {REQUIRED_STARTERS - names}"


def test_every_recipe_has_thumb_companion():
    for r in list_recipes():
        assert r.thumb_path.exists(), f"missing thumb for {r.name}: {r.thumb_path}"
        assert r.thumb_path.stat().st_size > 0


def test_cli_list_prints_one_line_per_recipe(capsys):
    rc = _cmd_list()
    assert rc == 0
    out = capsys.readouterr().out.splitlines()
    names_in_output = {line.split("\t", 1)[0] for line in out}
    assert REQUIRED_STARTERS.issubset(names_in_output)


# ---- contract: show ----

def test_show_etsy_product_returns_parsed_json(capsys):
    rc = _cmd_show("etsy-product")
    assert rc == 0
    body = json.loads(capsys.readouterr().out)
    assert body["name"] == "etsy-product"
    assert isinstance(body.get("ops"), list) and len(body["ops"]) > 0


def test_load_recipe_unknown_raises():
    with pytest.raises(KeyError):
        load_recipe("does-not-exist")


# ---- contract: run ----

def _make_fixture_dir(tmp_path: Path, names=("a.png", "b.png", "c.png")) -> Path:
    src = tmp_path / "in"
    src.mkdir()
    for n in names:
        (src / n).write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    return src


def test_run_recipe_produces_one_output_per_input(tmp_path: Path):
    src = _make_fixture_dir(tmp_path)
    dst = tmp_path / "out"
    outs = run_recipe("etsy-product", src, dst)
    assert len(outs) == 3
    assert all(o.exists() for o in outs)
    assert all("_etsy" in o.name for o in outs)


def test_run_recipe_dispatch_injection_is_called(tmp_path: Path):
    src = _make_fixture_dir(tmp_path, names=("only.png",))
    dst = tmp_path / "out"
    calls: list[tuple] = []

    def fake(recipe, s, d):
        calls.append((recipe.name, s.name, d.name))
        d.write_bytes(b"fake-dispatch-output")
        return d

    outs = run_recipe("realestate-listing", src, dst, dispatch=fake)
    assert len(outs) == 1
    assert calls == [("realestate-listing", "only.png", "only_listing.png")]
    assert outs[0].read_bytes() == b"fake-dispatch-output"


def test_cli_run_writes_processed_files(tmp_path: Path, capsys):
    src = _make_fixture_dir(tmp_path, names=("x.png", "y.png"))
    dst = tmp_path / "out"
    rc = _cmd_run("social-square", src, dst)
    assert rc == 0
    assert sorted(p.name for p in dst.iterdir()) == ["x_social.png", "y_social.png"]
    assert "processed=2" in capsys.readouterr().out


# ---- contract: CLI surface end-to-end ----

def test_top_level_comfybulk_recipes_list_invokable():
    """Smoke: `python -m comfybulk recipes list` returns 0 and lists starters."""
    result = subprocess.run(
        [sys.executable, "-m", "comfybulk", "recipes", "list"],
        cwd=Path(__file__).resolve().parent.parent,
        env={"PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")},
        capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    for name in REQUIRED_STARTERS:
        assert name in result.stdout
