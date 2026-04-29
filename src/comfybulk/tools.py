"""Standalone CLI tools: fastpingpong, timestretch, convert (webp/webm → mp4)."""
from __future__ import annotations
import math, shutil, subprocess
from datetime import datetime
from pathlib import Path

from .ffmpeg import FFMPEG, atempo_chain, probe_dims, probe_duration, probe_fps, probe_has_audio, to_posix, to_win


# ---------- Fast ping-pong ----------

def fastpingpong(input_video: str, *, speed_percent: int = 1000,
                 exponential: bool = False, exponential_base: float = 1.2,
                 output: str | None = None) -> str:
    if speed_percent <= 100:
        raise ValueError("speed_percent must be > 100")
    in_path = Path(to_posix(input_video))
    base = in_path.stem
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    work = in_path.parent
    duration = probe_duration(input_video)
    speed = speed_percent / 100.0
    accel_dur = duration / speed

    speedup = work / f"{base}_speedup_{speed_percent}pct_{ts}.mp4"
    subprocess.run([FFMPEG, "-y", "-i", to_win(input_video),
                    "-filter:v", f"setpts=PTS/{speed}", "-an",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    to_win(str(speedup))], check=True)

    cycles = math.ceil(duration / (accel_dur * 2))
    segments: list[Path] = []
    for cycle in range(cycles):
        f = work / f"{base}_forward_{cycle}_{ts}.mp4"
        shutil.copyfile(to_posix(str(speedup)), to_posix(str(f)))
        segments.append(f)
        r = work / f"{base}_reverse_{cycle}_{ts}.mp4"
        if exponential:
            mult = exponential_base ** (cycle + 1)
            vf = f"reverse,setpts=PTS/{mult}"
        else:
            vf = "reverse"
        subprocess.run([FFMPEG, "-y", "-i", to_win(str(speedup)),
                        "-filter:v", vf, "-an",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                        to_win(str(r))], check=True)
        segments.append(r)

    lst = work / f"{base}_segments_{ts}.txt"
    lst.write_text("\n".join(f"file '{to_win(str(s))}'" for s in segments), encoding="ascii")

    concat = work / f"{base}_concatenated_{ts}.mp4"
    subprocess.run([FFMPEG, "-y", "-f", "concat", "-safe", "0",
                    "-i", to_win(str(lst)), "-c", "copy",
                    to_win(str(concat))], check=True)

    out = Path(to_posix(output)) if output else (work / f"{base}_fastpingpong_{speed_percent}pct_{ts}.mp4")
    subprocess.run([FFMPEG, "-y", "-i", to_win(str(concat)),
                    "-t", f"{duration}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-movflags", "+faststart", to_win(str(out))], check=True)

    for f in [speedup, lst, concat] + segments:
        try:
            Path(to_posix(str(f))).unlink(missing_ok=True)
        except OSError:
            pass
    return str(out)


# ---------- Time stretch ----------

def timestretch(input_video: str, *, stretch: float = 2.0, no_pingpong: bool = False,
                target_fps: int = 60, output: str | None = None) -> str:
    in_path = Path(to_posix(input_video))
    if not in_path.exists():
        raise FileNotFoundError(input_video)
    if not output:
        suffix = f"_s{stretch}_{target_fps}fps" + ("" if no_pingpong else "_pp")
        output = str(in_path.with_name(f"{in_path.stem}{suffix}{in_path.suffix}"))

    vd = probe_duration(input_video)
    has_audio = probe_has_audio(input_video)
    audio_speed = 1.0 / stretch
    af = atempo_chain(audio_speed)

    if no_pingpong:
        fc = (f"[0:v]trim=0:{vd},setpts={stretch}*PTS,"
              f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1[outv]")
        if has_audio:
            fc += f";[0:a]atrim=0:{vd},{af}[outa]"
    else:
        fc = (f"[0:v]trim=0:{vd},setpts={stretch}*PTS,"
              f"minterpolate=fps={target_fps}:mi_mode=mci:mc_mode=aobmc:vsbmc=1,"
              "split=2[v1][v2];[v2]reverse[vr];[v1][vr]concat=n=2:v=1:a=0[outv]")
        if has_audio:
            fc += (f";[0:a]atrim=0:{vd},{af},asplit=2[a1][a2];"
                   "[a2]areverse[ar];[a1][ar]concat=n=2:v=0:a=1[outa]")

    args = [FFMPEG, "-loglevel", "warning", "-stats", "-i", to_win(input_video),
            "-filter_complex", fc, "-map", "[outv]"]
    if has_audio:
        args += ["-map", "[outa]"]
    args += ["-c:v", "libx264", "-preset", "medium", "-crf", "21"]
    if has_audio:
        args += ["-c:a", "aac", "-b:a", "192k"]
    if not no_pingpong:
        args += ["-shortest"]
    args += ["-y", to_win(output)]
    subprocess.run(args, check=True)
    return output


# ---------- WebP/WebM → MP4 ----------

def convert_to_mp4(input_file: str, *, output_file: str | None = None,
                   duration: float = 5.0, fps: int = 30,
                   timeout: int | None = None) -> str:
    in_path = Path(to_posix(input_file))
    if not in_path.exists():
        raise FileNotFoundError(input_file)
    ext = in_path.suffix.lower()
    if ext not in (".webp", ".webm"):
        raise ValueError(f"Unsupported format: {ext} (expected .webp or .webm)")

    if not output_file:
        output_file = str(in_path.with_suffix(".mp4"))

    if ext == ".webp":
        args = [FFMPEG, "-loop", "1", "-i", to_win(input_file),
                "-c:v", "libx264", "-t", str(duration), "-pix_fmt", "yuv420p",
                "-vf", f"fps={fps}", "-preset", "medium", "-crf", "23", "-y",
                to_win(output_file)]
        timeout = timeout or 300
    else:
        args = [FFMPEG, "-i", to_win(input_file), "-c:v", "libx264", "-c:a", "aac",
                "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "23",
                "-movflags", "+faststart", "-y", to_win(output_file)]
        timeout = timeout or 600

    subprocess.run(args, check=True, timeout=timeout)
    return output_file
