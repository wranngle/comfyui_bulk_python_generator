"""Tests for path conversion, atempo chaining, encode args."""
import sys

import pytest

from comfybulk.ffmpeg import atempo_chain, encode_args, to_posix, to_win


@pytest.mark.skipif(sys.platform == "win32", reason="WSL-specific path conversion")
class TestPathsLinux:
    def test_to_win_mnt(self):
        assert to_win("/mnt/d/foo/bar.mp4") == r"D:\foo\bar.mp4"

    def test_to_win_drive_root(self):
        assert to_win("/mnt/c") == "C:\\"

    def test_to_win_already_windows(self):
        assert to_win(r"D:\foo\bar") == r"D:\foo\bar"

    def test_to_posix_drive(self):
        assert to_posix(r"D:\foo\bar.mp4") == "/mnt/d/foo/bar.mp4"

    def test_to_posix_already_posix(self):
        assert to_posix("/home/user/file") == "/home/user/file"

    def test_roundtrip(self):
        win = to_win("/mnt/d/ComfyUI/output/favorites/clip.mp4")
        assert to_posix(win) == "/mnt/d/ComfyUI/output/favorites/clip.mp4"


@pytest.mark.parametrize("ratio,min_count", [
    (1.0, 1),
    (0.7, 1),
    (1.5, 1),
    (3.0, 2),       # 2 stages
    (0.25, 2),
    (10.0, 2),
    (0.158, 3),     # the bug we caught: 32s audio / 5s video — sqrt isn't enough
    (50.0, 3),
])
def test_atempo_chain_count(ratio, min_count):
    chain = atempo_chain(ratio)
    assert chain.count("atempo=") >= min_count


def test_atempo_chain_each_step_in_range():
    """Every atempo= value must be in ffmpeg's [0.5, 2.0] range."""
    import re
    for r in (0.1, 0.158, 0.25, 0.5, 1.0, 2.0, 3.0, 10.0, 50.0):
        chain = atempo_chain(r)
        for v in re.findall(r"atempo=([\d.]+)", chain):
            f = float(v)
            assert 0.5 <= f <= 2.0, f"ratio={r} produced atempo={f} (out of range)"


def test_atempo_chain_zero_raises():
    with pytest.raises(ValueError):
        atempo_chain(0)


def test_encode_args_default():
    a = encode_args()
    assert "-c:v" in a and "libx264" in a
    assert "-crf" in a and "18" in a
    assert "-movflags" in a and "+faststart" in a


def test_encode_args_no_faststart():
    a = encode_args(faststart=False)
    assert "+faststart" not in a
