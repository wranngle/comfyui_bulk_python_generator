"""Random audio mix. Ports add_random_audio_mix.ps1.

Picks a random audio file from a folder, time-stretches it to match the video
duration (atempo with sqrt-decomposition for extreme ratios), runs the
platform-ready chain (SoXR resample, artifact reduction, fades, compression,
EQ, loudnorm to -14 LUFS), and either mixes with original audio or replaces it.
"""
from __future__ import annotations
import random, subprocess
from pathlib import Path

from .ffmpeg import FFMPEG, atempo_chain, probe_duration, probe_has_audio, to_posix, to_win


AUDIO_EXTS = (".mp3", ".flac", ".wav")


def _pick_audio(folder: str) -> str:
    posix = to_posix(folder)
    if not Path(posix).is_dir():
        raise FileNotFoundError(folder)
    cands = [p for p in Path(posix).iterdir() if p.suffix.lower() in AUDIO_EXTS]
    if not cands:
        raise RuntimeError(f"No audio (.mp3/.flac/.wav) in {folder}")
    return str(cands[random.randrange(len(cands))])


def mix_random_audio(input_video: str, audio_folder: str, *, output_video: str | None = None,
                     audio_volume: float = 0.6, original_audio_volume: float = 0.4) -> str:
    audio = _pick_audio(audio_folder)
    vd = probe_duration(input_video)
    ad = probe_duration(audio)
    if vd <= 0 or ad <= 0:
        raise RuntimeError("Could not probe video or audio duration")
    ratio = vd / ad
    has_orig = probe_has_audio(input_video)

    if not output_video:
        in_path = Path(to_posix(input_video))
        output_video = str(in_path.with_name(f"{in_path.stem}_audiomix{in_path.suffix}"))

    chain = ",".join([
        atempo_chain(ratio),
        "aresample=48000:resampler=soxr:precision=28:dither_method=triangular",
        "highshelf=f=8000:g=-2:width_type=h:width=500,lowpass=f=18000:poles=2",
        "afade=t=in:st=0:d=0.3",
        f"afade=t=out:st={vd-0.3:.6f}:d=0.3",
        "acompressor=threshold=-16dB:ratio=8:attack=3:release=100:makeup=3",
        "equalizer=f=100:width_type=h:width=200:g=2,equalizer=f=3000:width_type=h:width=1000:g=-1.5,equalizer=f=10000:width_type=h:width=2000:g=-2",
        "loudnorm=I=-14:TP=-1.5:LRA=7",
        f"atrim=0:{vd:.6f}",
    ])

    if has_orig:
        new_a = f"[1:a]{chain},volume={audio_volume:.6f}[newaudio]"
        orig_a = (f"[0:a]atrim=0:{vd:.6f},aresample=48000,volume={original_audio_volume:.6f},"
                  "acompressor=threshold=-18dB:ratio=4:attack=5:release=80[origaudio]")
        mixed = "[origaudio][newaudio]amix=inputs=2:duration=first:dropout_transition=0.3[aout]"
        fc = f"{new_a};{orig_a};{mixed}"
    else:
        fc = f"[1:a]{chain},volume={audio_volume:.6f}[aout]"

    subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-stats", "-y",
         "-i", to_win(input_video), "-i", to_win(audio),
         "-filter_complex", fc, "-map", "0:v:0", "-map", "[aout]",
         "-c:v", "copy", "-c:a", "aac", "-b:a", "256k", "-ar", "48000",
         "-t", f"{vd:.6f}", "-movflags", "+faststart", to_win(output_video)],
        check=True,
    )
    return output_video
