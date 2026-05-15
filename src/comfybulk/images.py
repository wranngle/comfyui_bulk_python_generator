"""Image-bulk mode: process a directory of PNG/JPG files into metadata.csv.

Unlike `extract.process_directory` (which is video-first and prefers MP4>AVI>WEBM>PNG
per base name, then drops anything missing a prompt+seed), `images.process_directory`
treats each image as a first-class row even when ComfyUI metadata is absent. This
exists so the image-pipeline path doesn't silently swallow JPGs (which never carry
ComfyUI metadata) and so the row count matches the input file count.
"""
from __future__ import annotations
import re
from pathlib import Path

from .extract import (
    _csv_read,
    _csv_write,
    _seed_from_filename,
    extract_from_png,
    replace_discouraged_terms,
)
from .ffmpeg import to_posix

IMAGE_EXTS = {".png", ".jpg", ".jpeg"}


def is_image(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in IMAGE_EXTS


def extract_image(path: str) -> dict:
    """Return a row dict for one image. Missing fields default to "".

    PNGs: try ComfyUI tEXt/JSON extraction; fall back to filename-derived seed.
    JPGs: only filename-derived seed (no embedded ComfyUI metadata expected).
    """
    p = Path(path)
    ext = p.suffix.lower()
    prompt = ""
    seed = ""
    if ext == ".png":
        r = extract_from_png(path)
        if r:
            prompt = re.sub(r"\s+", " ", r["prompt"]).strip()
            seed = r["seed"]
    if not seed:
        seed = _seed_from_filename(p.stem) or ""
    if seed:
        try:
            seed = str(int(float(seed)))
        except (ValueError, TypeError):
            pass
    return {
        "filename": "",
        "seed": seed,
        "content_prompt": replace_discouraged_terms(prompt),
        "clipname": p.name,
        "caption": "",
        "title": "",
        "description": "",
        "tags": "",
        "cover_text": "",
        "pinned_comment": "",
        "CTA": "",
    }


def process_directory(directory: str, csv_path: str, test_mode: bool = False) -> tuple[int, int]:
    """Walk `directory` recursively; one CSV row per image, non-images skipped.

    Returns (ok, fail). Non-image files do not count toward `fail` — they are skipped.
    `fail` only counts images that raised during extraction.
    """
    root = Path(to_posix(directory))
    if not root.exists():
        raise FileNotFoundError(directory)
    images = sorted([p for p in root.rglob("*") if is_image(p)])
    rows: list[dict] = []
    fail = 0
    for img in images:
        try:
            rows.append(extract_image(str(img)))
        except Exception:
            fail += 1
    if test_mode:
        return len(rows), fail
    if not Path(to_posix(csv_path)).exists():
        _csv_write(csv_path, [])
    existing = _csv_read(csv_path)
    seen = {(r.get("content_prompt", ""), r.get("seed", ""), r.get("clipname", "")) for r in existing}
    appended = 0
    for r in rows:
        key = (r["content_prompt"], r["seed"], r["clipname"])
        if key in seen:
            continue
        existing.append(r)
        seen.add(key)
        appended += 1
    _csv_write(csv_path, existing)
    return appended, fail
