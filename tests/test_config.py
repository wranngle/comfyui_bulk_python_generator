"""Config loading roundtrip."""
import pytest
from pathlib import Path

from comfybulk.config import load


def test_load_config_default_paths(repo_root):
    cfg = load(repo_root / "config.toml")
    assert cfg.paths.assembly_root.startswith("D:")
    assert cfg.ollama.host.startswith("http")
    assert cfg.encode.target_w == 1080
    assert cfg.encode.target_h == 1920
    assert cfg.font.size == 120
    # Default ai_prompts/captions point to repo data/
    assert "ai_metadata_prompts.csv" in cfg.paths.ai_prompts_csv
    assert "captions.csv" in cfg.paths.captions_csv
