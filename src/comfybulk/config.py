"""Config loader. Paths are Windows-style (D:\\...) — they go to ffmpeg.exe directly."""
from __future__ import annotations
import os, sys, tomllib
from dataclasses import dataclass
from importlib.resources import files as pkg_files
from pathlib import Path


@dataclass
class Paths:
    assembly_root: str
    favorites_root: str
    audio_folder: str
    neg_audio: str
    cta_folder: str
    templates: str
    metadata_csv: str
    ai_prompts_csv: str
    captions_csv: str


@dataclass
class Ollama:
    host: str
    model: str
    gguf_path: str


@dataclass
class Encode:
    crf: int
    preset: str
    target_w: int
    target_h: int
    fps: int


@dataclass
class Font:
    file: str
    size: int
    border_w: int


@dataclass
class Config:
    paths: Paths
    ollama: Ollama
    encode: Encode
    font: Font


def find_config() -> Path:
    # Search order: $COMFYBULK_CONFIG, ./config.toml, repo-root/config.toml
    if env := os.environ.get("COMFYBULK_CONFIG"):
        return Path(env)
    cwd = Path.cwd() / "config.toml"
    if cwd.exists():
        return cwd
    repo = Path(__file__).resolve().parents[2] / "config.toml"
    if repo.exists():
        return repo
    raise FileNotFoundError("config.toml not found. Copy config.example.toml to config.toml.")


def load(path: Path | None = None) -> Config:
    cfg_path = path or find_config()
    with cfg_path.open("rb") as f:
        d = tomllib.load(f)
    p = d["paths"]
    # data/*.csv ship inside the package (importlib.resources finds them whether installed,
    # editable-installed, or run from source).
    pkg_data = pkg_files("comfybulk") / "data"
    ai = p.get("ai_prompts_csv") or str(pkg_data / "ai_metadata_prompts.csv")
    cap = p.get("captions_csv") or str(pkg_data / "captions.csv")
    return Config(
        paths=Paths(
            assembly_root=p["assembly_root"],
            favorites_root=p["favorites_root"],
            audio_folder=p["audio_folder"],
            neg_audio=p["neg_audio"],
            cta_folder=p["cta_folder"],
            templates=p["templates"],
            metadata_csv=p["metadata_csv"],
            ai_prompts_csv=ai,
            captions_csv=cap,
        ),
        ollama=Ollama(**d["ollama"]),
        encode=Encode(**d["encode"]),
        font=Font(**d["font"]),
    )
