"""Named recipe library: JSON + thumbnail starters that dispatch through
the images subcommand (round-1 #2) via the local-LLM router (round-1 #3)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True)
class Recipe:
    name: str
    path: Path
    thumb_path: Path
    body: dict[str, Any]


def _data_dir() -> Path:
    return _DATA_DIR


def list_recipes() -> list[Recipe]:
    out: list[Recipe] = []
    for p in sorted(_data_dir().glob("*.json")):
        body = json.loads(p.read_text())
        thumb = p.with_suffix(".thumb.png")
        if thumb.suffix == ".png" and not thumb.exists():
            thumb = p.parent / f"{p.stem}.thumb.png"
        out.append(Recipe(name=body.get("name", p.stem), path=p, thumb_path=thumb, body=body))
    return out


def load_recipe(name: str) -> Recipe:
    for r in list_recipes():
        if r.name == name:
            return r
    raise KeyError(f"recipe not found: {name!r}")


def run_recipe(name: str, input_dir: Path, output_dir: Path,
               dispatch=None) -> list[Path]:
    """Apply a recipe to every file in input_dir, writing results to output_dir.

    `dispatch` is an injection point: a callable
    `(recipe: Recipe, src: Path, dst: Path) -> Path` that performs the heavy
    image graph step. Defaults to a passthrough copy so unit tests stay fast
    and run without GPU/model dependencies."""
    recipe = load_recipe(name)
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if dispatch is None:
        dispatch = _passthrough_dispatch
    outputs: list[Path] = []
    for src in sorted(input_dir.iterdir()):
        if not src.is_file():
            continue
        suffix = recipe.body.get("output_suffix", f"_{recipe.name}")
        dst = output_dir / f"{src.stem}{suffix}{src.suffix}"
        result = dispatch(recipe, src, dst)
        outputs.append(Path(result))
    return outputs


def _passthrough_dispatch(recipe: Recipe, src: Path, dst: Path) -> Path:
    dst.write_bytes(src.read_bytes())
    return dst
