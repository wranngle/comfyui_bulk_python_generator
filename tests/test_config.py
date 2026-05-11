"""Config loading roundtrip."""

from comfybulk.config import load


def test_load_config_from_explicit_file(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        r"""
[paths]
assembly_root  = 'D:\media\assembly'
favorites_root = 'D:\media\favorites'
audio_folder   = 'D:\media\audio'
neg_audio      = 'D:\media\audio\negatives'
cta_folder     = 'D:\media\CTA'
templates      = 'D:\media\templates'
metadata_csv   = 'D:\media\metadata.csv'

[ollama]
host = 'http://localhost:11434'
model = 'local-model:latest'
gguf_path = 'D:\models\local-model.gguf'
auto_launch = false

[encode]
crf = 18
preset = 'veryfast'
target_w = 1080
target_h = 1920
fps = 60

[font]
file = 'C:/Windows/Fonts/arial.ttf'
size = 120
border_w = 36
""",
        encoding="utf-8",
    )

    cfg = load(cfg_path)
    assert cfg.paths.assembly_root == r"D:\media\assembly"
    assert cfg.ollama.host.startswith("http")
    assert cfg.ollama.auto_launch is False
    assert cfg.encode.target_w == 1080
    assert cfg.encode.target_h == 1920
    assert cfg.font.size == 120
    # Defaults point to packaged data unless explicitly overridden.
    assert "ai_metadata_prompts.csv" in cfg.paths.ai_prompts_csv
    assert "captions.csv" in cfg.paths.captions_csv
