"""Layout variant builders: grid (2x2 ping-pong), montage (concat 2-3), single, cta_only.

Two flavors per variant:
- `build_*` — single-output, used by the full pipeline (effects stack runs after).
- `clean_*` — dual-output (with-CTA + without-CTA), reproducing the legacy
  create_*_videos.ps1 behavior. Triggered via `--no-effects` on the CLI.
"""
from __future__ import annotations
import random, subprocess
from datetime import datetime
from pathlib import Path

from .ffmpeg import FFMPEG, encode_args, probe_duration, to_posix, to_win


# ---------- helpers ----------

def _font_for_filter(font_path: str) -> str:
    """Escape colon + backslash for ffmpeg's two-level filter parser.
    drawtext fontfile=C:/foo wants `C\\:/foo` after the filtergraph layer eats one `\\`."""
    return font_path.replace("\\", "/").replace(":", r"\\:")


def _escape_caption(text: str) -> str:
    return (text.replace("\n", " ").replace("'", "")
            .replace(":", " ").replace("|", " ").strip())


def _drawtext(text: str, font_path: str, *, font_size: int = 120, border_w: int = 36,
              y: str = "h-text_h-288") -> str:
    # ffmpeg drawtext: wrap text in single quotes so spaces don't break option parsing.
    safe = _escape_caption(text).replace("\\", "\\\\").replace("'", "")
    return (f"drawtext=fontfile={_font_for_filter(font_path)}:"
            f"text='{safe}':fontsize={font_size}:"
            f"fontcolor=white@0.9:bordercolor=black@0.8:borderw={border_w}:"
            f"x=(w-text_w)/2:y={y}")


def _write_concat_list(temp: Path, ts: str, paths: list[str]) -> Path:
    lst = temp / f"concat_{ts}.txt"
    lst.write_text("\n".join(f"file '{to_win(p)}'" for p in paths), encoding="ascii")
    return lst


# ---------- grid (2x2 ping-pong) ----------

def build_grid(input_folder: str, output_folder: str, *, fast_mode: bool = True,
               cell_w: int = 540, cell_h: int = 960, target_fps: int = 60,
               crf: int = 18, preset: str = "veryfast", stretch_to_fill: bool = True) -> str:
    """Pick 4 random MP4s, ping-pong each, sync durations, xstack 2x2. Returns final path."""
    src = Path(to_posix(input_folder))
    dest = Path(to_posix(output_folder))
    dest.mkdir(parents=True, exist_ok=True)

    clips = [p for p in src.glob("*.mp4") if p.is_file() and p.parent != dest]
    if len(clips) < 4:
        raise RuntimeError(f"Need 4 MP4s for grid, found {len(clips)}")
    selected = random.sample(clips, 4)

    durations = [probe_duration(str(c)) for c in selected]
    target = max(durations)

    normalized: list[Path] = []
    for i, (clip, dur) in enumerate(zip(selected, durations)):
        stretch = target / dur if dur else 1.0
        vf = (f"[0:v]split=2[v0][v1];[v1]reverse[vr];"
              f"[v0][vr]concat=n=2:v=1:a=0[pp];[pp]setpts={stretch:.8f}*PTS")
        if not fast_mode:
            vf += f",minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:me_mode=bidir:vsbmc=1"
        else:
            vf += f",fps={target_fps}"
        if stretch_to_fill:
            vf += f",scale={cell_w}:{cell_h},setsar=1,format=yuv420p[out]"
        else:
            vf += (f",scale={cell_w}:{cell_h}:force_original_aspect_ratio=increase,"
                   f"crop={cell_w}:{cell_h},setsar=1,format=yuv420p[out]")
        tmp = dest / f"pingpong_{i}.mp4"
        subprocess.run([FFMPEG, "-loglevel", "error", "-y", "-i", to_win(str(clip)),
                        "-filter_complex", vf, "-map", "[out]", "-an",
                        "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
                        to_win(str(tmp))], check=True)
        normalized.append(tmp)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    rid = "%06x" % random.randrange(16**6)
    out = dest / f"grid_pingpong_{ts}_{rid}.mp4"
    layout = f"0_0|{cell_w}_0|0_{cell_h}|{cell_w}_{cell_h}"
    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y"]
    for n in normalized:
        args += ["-i", to_win(str(n))]
    args += ["-filter_complex", f"xstack=inputs=4:layout={layout}[v]",
             "-map", "[v]", "-an", "-c:v", "libx264", "-crf", str(crf), "-preset", preset,
             to_win(str(out))]
    subprocess.run(args, check=True)
    for n in normalized:
        n.unlink(missing_ok=True)
    return str(out)


# ---------- montage / single (used by pipeline) ----------

def build_montage(clips: list[str], output: str, *, target_w: int = 1080, target_h: int = 1920,
                  fps: int = 60, t_per_clip: int = 13, crf: int = 18, preset: str = "veryfast",
                  temp_dir: str | None = None) -> str:
    if not temp_dir:
        temp_dir = str(Path(to_posix(output)).parent / "temp")
    temp = Path(temp_dir)
    temp.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
    lst = _write_concat_list(temp, ts, clips)
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", to_win(str(lst)),
                    "-vf", f"scale={target_w}:{target_h},fps={fps}",
                    "-t", str(t_per_clip * len(clips)),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
                    "-preset", preset, "-movflags", "+faststart",
                    to_win(output)], check=True)
    lst.unlink(missing_ok=True)
    return output


def build_single(clip: str, output: str, *, target_w: int = 1080, target_h: int = 1920,
                 fps: int = 60, t: int = 13, crf: int = 18, preset: str = "veryfast") -> str:
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", to_win(clip), "-vf", f"scale={target_w}:{target_h},fps={fps}",
                    "-t", str(t),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
                    "-preset", preset, "-movflags", "+faststart",
                    to_win(output)], check=True)
    return output


# ---------- clean (no-effects) variants — match create_*.ps1 dual-output behavior ----------

def _make_metadata_files(out_video: str, audio_path: str | None, ts: str, prefix: str, temp: Path):
    """Snapshot PNG + audio MP3 (+ negative MP3) — preserved from create_*.ps1."""
    snap = temp / f"{prefix}_{ts}_snapshot.png"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", "3", "-i", to_win(out_video), "-vframes", "1",
                    "-q:v", "2", to_win(str(snap))], check=False)
    if audio_path and Path(to_posix(audio_path)).exists():
        a1 = temp / f"{prefix}_{ts}_audio.mp3"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-i", to_win(audio_path), "-t", "13", "-c:a", "mp3",
                        "-b:a", "192k", to_win(str(a1))], check=False)
        a2 = temp / f"{prefix}_{ts}_audio_negative.mp3"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-i", to_win(audio_path), "-af", "areverse",
                        "-t", "13", "-c:a", "mp3", "-b:a", "192k",
                        to_win(str(a2))], check=False)


def clean_single(clip: str, output_folder: str, ts: str, *, cta_caption: str | None,
                 font_path: str, font_size: int = 120, border_w: int = 36,
                 text_y: str = "h-text_h-288", target_w: int = 1080, target_h: int = 1920,
                 fps: int = 60, t: int = 13, audio_path: str | None = None) -> list[str]:
    """Produce single_clip_cta_TS.mp4 (if caption) AND single_clip_nocta_TS.mp4. Mirrors create_single_clip_videos.ps1."""
    out_dir = Path(to_posix(output_folder))
    temp = out_dir / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []

    if cta_caption:
        out = out_dir / f"single_clip_cta_{ts}.mp4"
        vf = f"scale={target_w}:{target_h},fps={fps},{_drawtext(cta_caption, font_path, font_size=font_size, border_w=border_w, y=text_y)}"
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-i", to_win(clip), "-vf", vf, "-t", str(t),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast",
                        "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                        "-movflags", "+faststart", to_win(str(out))], check=True)
        if out.exists() and out.stat().st_size > 100_000:
            outputs.append(str(out))
            _make_metadata_files(str(out), audio_path, ts, f"single_clip_cta", temp)

    out2 = out_dir / f"single_clip_nocta_{ts}.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", to_win(clip), "-vf", f"scale={target_w}:{target_h},fps={fps}",
                    "-t", str(t),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast",
                    "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-movflags", "+faststart", to_win(str(out2))], check=True)
    if out2.exists() and out2.stat().st_size > 100_000:
        outputs.append(str(out2))
        _make_metadata_files(str(out2), audio_path, ts, f"single_clip_nocta", temp)

    return outputs


def clean_montage(clips: list[str], output_folder: str, ts: str, *, cta_caption: str | None,
                  font_path: str, font_size: int = 120, border_w: int = 36,
                  text_y: str = "h-text_h-288", target_w: int = 1080, target_h: int = 1920,
                  fps: int = 60, t_per_clip: int = 13, audio_path: str | None = None) -> list[str]:
    """Mirrors create_montage_videos.ps1: numbered copies, dual cta/nocta concat outputs."""
    if len(clips) < 2:
        raise RuntimeError(f"Need ≥2 clips for montage, got {len(clips)}")
    out_dir = Path(to_posix(output_folder))
    temp = out_dir / "temp"
    temp.mkdir(parents=True, exist_ok=True)

    numbered: list[str] = []
    for i, c in enumerate(clips, start=1):
        n = temp / f"{i:02d}_{Path(to_posix(c)).name}"
        n.write_bytes(Path(to_posix(c)).read_bytes())
        numbered.append(str(n))

    n_clips = len(numbered)
    outputs: list[str] = []

    def _build(out_path: Path, vf_extra: str = ""):
        lst = _write_concat_list(temp, ts, numbered)
        vf = f"scale={target_w}:{target_h},fps={fps}" + (f",{vf_extra}" if vf_extra else "")
        subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                        "-f", "concat", "-safe", "0", "-i", to_win(str(lst)),
                        "-vf", vf, "-t", str(t_per_clip * n_clips),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast",
                        "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                        "-movflags", "+faststart", to_win(str(out_path))], check=True)
        lst.unlink(missing_ok=True)

    if cta_caption:
        out = out_dir / f"montage_{n_clips}clips_cta_{ts}.mp4"
        _build(out, _drawtext(cta_caption, font_path, font_size=font_size, border_w=border_w, y=text_y))
        if out.exists() and out.stat().st_size > 100_000:
            outputs.append(str(out))
            _make_metadata_files(str(out), audio_path, ts, f"montage_{n_clips}clips_cta", temp)

    out2 = out_dir / f"montage_{n_clips}clips_nocta_{ts}.mp4"
    _build(out2)
    if out2.exists() and out2.stat().st_size > 100_000:
        outputs.append(str(out2))
        _make_metadata_files(str(out2), audio_path, ts, f"montage_{n_clips}clips_nocta", temp)

    for n in numbered:
        Path(to_posix(n)).unlink(missing_ok=True)
    return outputs


def clean_cta_only(cta_folder: str, output_folder: str, ts: str, *, cta_caption: str | None,
                   font_path: str, font_size: int = 120, target_w: int = 1080, target_h: int = 1920,
                   fps: int = 60, t: int = 5, text_y: str = "h-text_h-288",
                   audio_path: str | None = None) -> list[str]:
    """Mirrors create_cta_only_clips.ps1: random CTA pick + optional caption, single output."""
    cta = Path(to_posix(cta_folder))
    if not cta.is_dir():
        raise FileNotFoundError(f"CTA folder not found: {cta_folder}")
    files = sorted(p for p in cta.glob("*.mp4") if p.is_file())
    if not files:
        raise RuntimeError(f"No MP4 in CTA folder: {cta_folder}")
    pick = random.choice(files)

    out_dir = Path(to_posix(output_folder))
    temp = out_dir / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    out = temp / f"cta_only_{ts}.mp4"

    if cta_caption:
        safe = _escape_caption(cta_caption).replace("'", "")
        dt = (f"drawtext=fontfile={_font_for_filter(font_path)}:text='{safe}':"
              f"fontsize={font_size}:fontcolor=white:x=(w-text_w)/2:y={text_y}")
        vf = f"scale={target_w}:{target_h},fps={fps},{dt}"
    else:
        vf = f"scale={target_w}:{target_h},fps={fps}"

    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", to_win(str(pick)), "-vf", vf, "-t", str(t),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "veryfast",
                    "-r", str(fps), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-movflags", "+faststart", to_win(str(out))], check=True)

    outputs: list[str] = []
    if out.exists() and out.stat().st_size > 100_000:
        outputs.append(str(out))
        _make_metadata_files(str(out), audio_path, ts, "cta_only", temp)
    return outputs
