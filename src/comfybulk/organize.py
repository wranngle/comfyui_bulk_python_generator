"""Organize media into per-prompt subfolders.

Ports Organize-MediaByPrompt.ps1: extracts the content prompt from each MP4 via
ffprobe metadata, groups files by the first-100-chars-of-prompt key, and moves
each group into a folder named after the (sanitized) prompt. PNG companions
are matched into existing folders by seed → model family → base pattern. If no
prompts are extracted, files stay put (filename-fallback is intentionally OFF).
"""
from __future__ import annotations
import json, re, shutil
from pathlib import Path

from .extract import _seed_from_filename, _extract_prompt_from_workflow, _seed_from_jsonstring, _prompt_from_escaped_json
from .ffmpeg import probe_format_tag, to_posix


def safe_folder_name(text: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\[\]]', "_", text)
    s = re.sub(r"[–—]", "-", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if len(s) > 60:
        s = s[:60].strip("_")
    return s or "unknown_prompt"


def prompt_key(prompt: str) -> str:
    k = re.sub(r"\s+", " ", prompt.lower().strip())
    return k[:100]


def _strip_known_ext(name: str) -> str:
    """Drop only known media extensions — pathlib.stem trips on multi-dot names like 'Wan2.2-…'."""
    for ext in (".mp4", ".png", ".avi", ".webm", ".mov", ".webp", ".jpg", ".jpeg",
                ".gif", ".safetensors", ".gguf"):
        if name.lower().endswith(ext):
            return name[: -len(ext)]
    return name


def base_pattern(filename: str) -> str:
    base = _strip_known_ext(filename)
    for pat in (r"_\d{4,}.*$", r"_\d+$", r"_%.*", r"_audio$", r"_caption$",
                r"_nocaption.*$", r"_noaudio.*$", r"_upscaled.*$", r"_simple_interpolated.*$"):
        base = re.sub(pat, "", base)
    return base


def model_family(filename: str) -> str:
    base = _strip_known_ext(filename)
    for prefix, fam in (("wan22_", "wan22"), ("WVI2V_", "WVI2V"),
                        ("Wan2.2-", "Wan2.2"), ("AnimateDiff_", "AnimateDiff"),
                        ("ezgif-", "ezgif")):
        if base.startswith(prefix):
            return fam
    parts = base.split("_")
    return parts[0] if len(parts) > 1 else base


def extract_prompt_for_organize(mp4_path: str) -> dict | None:
    comment = probe_format_tag(mp4_path, "comment")
    if not comment:
        return None
    prompt = seed = None
    try:
        wf = json.loads(comment)
        prompt = _extract_prompt_from_workflow(wf)
        seed = _seed_from_jsonstring(comment)
    except json.JSONDecodeError:
        prompt = _prompt_from_escaped_json(comment)
        seed = _seed_from_jsonstring(comment)
    if not seed:
        seed = _seed_from_filename(Path(mp4_path).stem)
    if not prompt:
        return None
    return {"prompt": prompt.strip(), "seed": seed}


def match_png_to_existing_folder(png_path: str, favorites_path: str) -> str | None:
    fav = Path(to_posix(favorites_path))
    pf = Path(png_path)
    seed = _seed_from_filename(pf.stem)
    fam = model_family(pf.name)
    base = base_pattern(pf.name)
    for folder in (d for d in fav.iterdir() if d.is_dir()):
        for f in folder.iterdir():
            if not f.is_file():
                continue
            if fam == model_family(f.name) and fam:
                return str(folder)
            if base and base == base_pattern(f.name) and len(base) > 8:
                return str(folder)
            if seed and seed == _seed_from_filename(f.stem):
                return str(folder)
    return None


def organize(favorites_path: str, test_mode: bool = False) -> dict:
    root = Path(to_posix(favorites_path))
    if not root.exists():
        raise FileNotFoundError(favorites_path)

    mp4s = [p for p in root.glob("*.mp4") if p.is_file()]
    pngs = [p for p in root.glob("*.png") if p.is_file()]

    groups: dict[str, list[dict]] = {}
    for mp4 in mp4s:
        e = extract_prompt_for_organize(str(mp4))
        if e:
            k = prompt_key(e["prompt"])
            groups.setdefault(k, []).append({"path": str(mp4), "name": mp4.name,
                                             "prompt": e["prompt"], "seed": e["seed"], "type": "MP4"})

    # Match PNGs to existing prompt groups by seed.
    for png in pngs:
        seed = _seed_from_filename(png.stem)
        if not seed:
            continue
        for k, lst in groups.items():
            if any(item["seed"] == seed for item in lst):
                lst.append({"path": str(png), "name": png.name,
                            "prompt": lst[0]["prompt"], "seed": seed, "type": "PNG"})
                break

    moved = 0
    created: list[str] = []
    for k, lst in groups.items():
        if not lst:
            continue
        folder = root / safe_folder_name(lst[0]["prompt"])
        if test_mode:
            continue
        if not folder.exists():
            folder.mkdir(parents=True, exist_ok=True)
            created.append(folder.name)
        for item in lst:
            src = Path(item["path"])
            if src.exists():
                shutil.move(str(src), str(folder / src.name))
                moved += 1

    if not test_mode:
        # Second pass: try to match remaining PNGs to existing folders.
        for png in (p for p in root.glob("*.png") if p.is_file()):
            target = match_png_to_existing_folder(str(png), str(root))
            if target:
                shutil.move(str(png), str(Path(target) / png.name))
                moved += 1

    return {"groups": len(groups), "created": created, "moved": moved}
