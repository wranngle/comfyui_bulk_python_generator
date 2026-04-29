"""Unit tests for organize.py helpers (no I/O)."""
import pytest

from comfybulk.organize import base_pattern, model_family, prompt_key, safe_folder_name


@pytest.mark.parametrize("text,expected", [
    ("a normal prompt", "a_normal_prompt"),
    ('with "quotes" and / slashes', "with_quotes_and_slashes"),
    ("a" * 100, "a" * 60),  # truncated to 60
    ("", "unknown_prompt"),
    ("   spaces   ", "spaces"),
])
def test_safe_folder_name(text, expected):
    assert safe_folder_name(text) == expected


def test_prompt_key_lowercase_truncated():
    s = "X" * 200
    assert prompt_key(s) == "x" * 100


def test_prompt_key_collapses_whitespace():
    assert prompt_key("Hello   World\n\nfoo") == "hello world foo"


@pytest.mark.parametrize("name,expected", [
    ("wan22_wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors", "wan22"),
    ("WVI2V_CC_24FPS_1108887061710056_00001", "WVI2V"),
    ("Wan2.2-T2V-A14B-Q4_K_M", "Wan2.2"),
    ("AnimateDiff_00006", "AnimateDiff"),
    ("ezgif-something", "ezgif"),
    ("foo_bar", "foo"),
    ("plain", "plain"),
])
def test_model_family(name, expected):
    assert model_family(name) == expected


def test_base_pattern_strips_trailing_index():
    """The legacy regex strips trailing _NNNNN... indices but keeps mid-name `_seed518...` substrings.
    We faithfully port that behavior — improving it would break grouping equivalence with PowerShell runs."""
    n = "wan22_t2v_low_noise_14B_Q3_K_L_272x512_6steps_seed518898413366690_euler_raw_interpolated_00004"
    bp = base_pattern(n)
    assert not bp.endswith("00004")
    # 'seed518...' is preserved by design — no `_\d{4,}` boundary precedes it.
    assert "seed518898413366690" in bp
