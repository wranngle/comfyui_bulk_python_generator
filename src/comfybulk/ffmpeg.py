"""ffmpeg/ffprobe subprocess wrappers + WSL↔Windows path conversion."""
from __future__ import annotations
import math, os, shutil, subprocess, sys
from pathlib import Path

FFMPEG = "ffmpeg.exe" if shutil.which("ffmpeg.exe") else "ffmpeg"
FFPROBE = "ffprobe.exe" if shutil.which("ffprobe.exe") else "ffprobe"


def to_win(p: str | Path) -> str:
    """Path string suitable for ffmpeg.exe (Windows form)."""
    s = str(p)
    if sys.platform == "win32":
        return s.replace("/", "\\")
    if s.startswith("/mnt/") and len(s) >= 7 and s[5].isalpha() and s[6] == "/":
        return f"{s[5].upper()}:" + s[6:].replace("/", "\\")
    if len(s) == 6 and s.startswith("/mnt/") and s[5].isalpha():
        return f"{s[5].upper()}:\\"
    return s


def to_posix(p: str | Path) -> str:
    """Path string suitable for Python open()/exists() in WSL."""
    s = str(p)
    if sys.platform == "win32":
        return s
    if len(s) >= 2 and s[1] == ":":
        rest = s[2:].replace("\\", "/")
        if rest and not rest.startswith("/"):
            rest = "/" + rest
        return f"/mnt/{s[0].lower()}{rest}"
    return s


def filter_path(p: str | Path) -> str:
    """Escape a path for use inside an ffmpeg filter option value."""
    return str(p).replace("\\", "/").replace(":", r"\\:")


def filter_text(text: str) -> str:
    """Escape text for single-quoted ffmpeg filter option values."""
    s = " ".join(str(text).splitlines()).strip()
    return (s.replace("\\", r"\\")
             .replace("'", r"\'")
             .replace(":", r"\:")
             .replace(",", r"\,")
             .replace("[", r"\[")
             .replace("]", r"\]")
             .replace(";", r"\;"))


def drawtext_filter(text: str, font_path: str | Path, *, font_size: int = 120,
                    border_w: int = 36, y: str = "h-text_h-288",
                    fontcolor: str = "white@0.9",
                    bordercolor: str = "black@0.8",
                    x: str = "(w-text_w)/2") -> str:
    """Build a drawtext filter with centralized font/text escaping."""
    return (f"drawtext=fontfile={filter_path(font_path)}:"
            f"text='{filter_text(text)}':fontsize={font_size}:"
            f"fontcolor={fontcolor}:bordercolor={bordercolor}:borderw={border_w}:"
            f"x={x}:y={y}")


def concat_file_line(p: str | Path) -> str:
    """Build a concat-demuxer file line using slash paths and escaped quotes."""
    s = to_win(p).replace("\\", "/").replace("'", r"'\''")
    return f"file '{s}'"


def exists(p: str | Path) -> bool:
    return os.path.exists(to_posix(p))


def listdir(p: str | Path):
    return os.listdir(to_posix(p))


def file_size(p: str | Path) -> int:
    return os.path.getsize(to_posix(p))


def run(args: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    """Run ffmpeg/ffprobe. All path args should already be in Windows form (use to_win)."""
    return subprocess.run(args, check=check, capture_output=capture, text=True)


def probe_duration(path: str) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", to_win(path)],
        capture_output=True, text=True,
    )
    return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0.0


def probe_dims(path: str) -> tuple[int, int]:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", to_win(path)],
        capture_output=True, text=True,
    )
    w, h = r.stdout.strip().split(",")
    return int(w), int(h)


def probe_fps(path: str) -> float:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1",
         to_win(path)],
        capture_output=True, text=True,
    )
    s = r.stdout.strip()
    if "/" in s:
        n, d = s.split("/")
        return float(n) / float(d) if float(d) else 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def probe_has_audio(path: str) -> bool:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=index", "-of", "csv=p=0", to_win(path)],
        capture_output=True, text=True,
    )
    return bool(r.stdout.strip())


def probe_format_tag(path: str, tag: str) -> str:
    """Read a format-level metadata tag (e.g. 'comment')."""
    r = subprocess.run(
        [FFPROBE, "-v", "quiet", "-print_format", "default=noprint_wrappers=1:nokey=1",
         "-show_entries", f"format_tags={tag}", to_win(path)],
        capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


def encode_args(crf: int = 18, preset: str = "veryfast",
                pix_fmt: str = "yuv420p", faststart: bool = True) -> list[str]:
    a = ["-c:v", "libx264", "-pix_fmt", pix_fmt, "-crf", str(crf), "-preset", preset]
    if faststart:
        a += ["-movflags", "+faststart"]
    return a


def atempo_chain(ratio: float) -> str:
    """ffmpeg atempo only supports 0.5-2.0; chain N copies of ratio**(1/N) for extreme ratios.
    The legacy PowerShell version used sqrt only, which silently broke for ratios <0.25 or >4.0."""
    if ratio <= 0:
        raise ValueError(f"atempo ratio must be > 0, got {ratio}")
    if 0.5 <= ratio <= 2.0:
        return f"atempo={ratio:.6f}"
    bound_log = math.log(2.0) if ratio > 1 else math.log(0.5)
    n = max(2, math.ceil(math.log(ratio) / bound_log))
    step = ratio ** (1.0 / n)
    return ",".join(f"atempo={step:.6f}" for _ in range(n))
