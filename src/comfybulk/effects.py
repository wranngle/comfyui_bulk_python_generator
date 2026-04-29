"""Visual + audio effect modules. Ports apply_glitch_negative.ps1, apply_rainbow_vignette_overlay.ps1, apply_reverse_ending.ps1."""
from __future__ import annotations
import math, random, subprocess
from datetime import datetime
from pathlib import Path

from .ffmpeg import (FFMPEG, FFPROBE, encode_args, atempo_chain,
                     probe_dims, probe_duration, probe_has_audio, to_posix, to_win)


# ---------- Glitch negative ----------

def _glitch_events(duration: float) -> list[dict]:
    """4-phase exponential buildup matching the PowerShell version."""
    events: list[dict] = []
    t = 2.0
    p1 = duration * 0.3
    while t < p1:
        events.append({"start": t, "duration": 0.05, "intensity": 0.3})
        t += random.uniform(2.0, 4.0)
    p2 = duration * 0.6
    while t < p2:
        events.append({"start": t, "duration": random.uniform(0.05, 0.15), "intensity": 0.5})
        t += random.uniform(1.0, 2.5)
    p3 = duration * 0.85
    while t < p3:
        events.append({"start": t, "duration": random.uniform(0.1, 0.3), "intensity": 0.7})
        t += random.uniform(0.3, 1.0)
    while t < (duration - 0.5):
        events.append({"start": t, "duration": random.uniform(0.2, 0.5), "intensity": 1.0})
        t += random.uniform(0.1, 0.3)
    return events


def apply_glitch_negative(input_video: str, dest_folder: str, *, neg_audio: str | None = None,
                          neg_audio_dir: str | None = None, output_filename: str | None = None) -> str:
    if not neg_audio and neg_audio_dir:
        d = Path(to_posix(neg_audio_dir))
        if d.is_dir():
            cands = [p for p in d.iterdir() if p.suffix.lower() in (".mp3", ".flac", ".wav")]
            if cands:
                neg_audio = str(cands[random.randrange(len(cands))])
    if not neg_audio:
        raise RuntimeError(f"No negative audio found (folder={neg_audio_dir})")

    duration = probe_duration(input_video)
    has_audio = probe_has_audio(input_video)
    events = _glitch_events(duration)

    # Build temp dir + output path
    dest = Path(to_posix(dest_folder))
    temp = dest if dest.name == "temp" else dest / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    if not output_filename:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:-3]
        output_filename = f"glitch_negative_output_{ts}.mp4"
    out = temp / output_filename

    enable_expr = "+".join(f"between(t,{e['start']:.3f},{e['start']+e['duration']:.3f})" for e in events) or "0"

    vf_complex = (
        "[0:v]split=2[normal][glitch];"
        "[glitch]negate,hflip,rgbashift=rh=-5:gh=5:bv=-5[glitched];"
        f"[normal][glitched]overlay=enable='{enable_expr}'[out]"
    )
    if has_audio:
        af = (
            f"[0:a]volume=enable='not({enable_expr})':volume=1.0:enable='{enable_expr}':volume=0.2[orig];"
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=end={duration},"
            f"volume=enable='{enable_expr}':volume=1.5:enable='not({enable_expr})':volume=0[ga];"
            "[orig][ga]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
    else:
        af = (
            f"[1:a]aloop=loop=-1:size=2e+09,atrim=end={duration},"
            f"volume=enable='{enable_expr}':volume=1.0:enable='not({enable_expr})':volume=0[aout]"
        )
    args = ([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
             "-i", to_win(input_video), "-i", to_win(neg_audio),
             "-filter_complex", f"{vf_complex};{af}",
             "-map", "[out]", "-map", "[aout]"]
            + encode_args(crf=18, preset="fast")
            + ["-c:a", "aac", "-b:a", "192k", to_win(str(out))])
    subprocess.run(args, check=True)
    return str(out)


# ---------- Rainbow vignette overlay ----------

def _template_path(templates_dir: str) -> Path:
    return Path(to_posix(templates_dir)) / "rainbow_border_1080x1920_10s.mov"


def rainbow_generate(templates_dir: str, *, width: int = 1080, height: int = 1920,
                     duration: int = 10, border: int = 80) -> str:
    """One-time bake of the rainbow-border overlay template (alpha PNG-in-MOV)."""
    out = _template_path(templates_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    cx, cy = width / 2, height / 2
    geq = (
        f"[0:v]geq="
        f"'r=127.5*(1+sin(2*PI*(mod(atan2(Y-{cy},X-{cx})/(2*PI)+T/4,1)+0))):"
        f"g=127.5*(1+sin(2*PI*(mod(atan2(Y-{cy},X-{cx})/(2*PI)+T/4,1)+0.333))):"
        f"b=127.5*(1+sin(2*PI*(mod(atan2(Y-{cy},X-{cx})/(2*PI)+T/4,1)+0.667))):"
        f"a=if(lt(min(min(X,{width}-X),min(Y,{height}-Y)),{border}),"
        f"255*pow(1-min(min(X,{width}-X),min(Y,{height}-Y))/{border},3),0)'"
    )
    color = f"color=black:s={width}x{height}:d={duration},format=rgba"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", color, "-filter_complex", geq,
                    "-c:v", "png", "-pix_fmt", "rgba", to_win(str(out))], check=True)
    return str(out)


def rainbow_apply(input_video: str, output_video: str, templates_dir: str, *, crf: int = 18) -> str:
    tpath = _template_path(templates_dir)
    if not tpath.exists():
        rainbow_generate(templates_dir)
    w, h = probe_dims(input_video)
    fc = f"[1:v]scale={w}:{h}[scaled];[0:v][scaled]overlay=format=auto:shortest=1"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", to_win(input_video), "-stream_loop", "-1", "-i", to_win(str(tpath)),
                    "-filter_complex", fc, "-map", "0:a?",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf), "-preset", "fast",
                    "-c:a", "copy", to_win(output_video)], check=True)
    return output_video


# ---------- Reverse ending ----------

def reverse_ending(input_video: str, dest_folder: str, *, reverse_duration: float = 0.0,
                   output_name: str | None = None) -> str:
    duration = probe_duration(input_video)
    rd = reverse_duration if reverse_duration > 0 else round(duration * 0.20, 3)
    rd = min(rd, duration)
    start = max(0.0, duration - rd)
    has_audio = probe_has_audio(input_video)

    dest = Path(to_posix(dest_folder))
    dest.mkdir(parents=True, exist_ok=True)
    if not output_name:
        output_name = f"reverse_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    out = dest / output_name

    args = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-ss", f"{start:.3f}", "-i", to_win(input_video), "-t", f"{rd:.3f}",
            "-vf", "reverse"]
    if has_audio:
        args += ["-af", "areverse",
                 "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "fast",
                 "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
                 "-movflags", "+faststart", to_win(str(out))]
    else:
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-preset", "fast",
                 "-an", "-movflags", "+faststart", to_win(str(out))]
    subprocess.run(args, check=True)
    return str(out)
