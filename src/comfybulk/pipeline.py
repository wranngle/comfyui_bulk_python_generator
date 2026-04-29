"""Bulk processing orchestrator — full port of bulk_processor.ps1.

Selects clips → extracts metadata → builds variant → appends CTA caption →
mixes random audio → glitch → reverse → rainbow → motion blur → LLM fill →
seed+clipname rename → cleanup.

Variant types: grid | montage | single | cta_only.
With --no-effects, runs the clean_* variant builders (dual cta/nocta outputs)
matching the legacy create_*.ps1 behavior.
"""
from __future__ import annotations
import csv, json, math, random, re, shutil, subprocess
from datetime import datetime
from pathlib import Path

from .config import Config
from . import effects, variants, fill as fill_mod
from .extract import process_file as extract_one
from .ffmpeg import (FFMPEG, FFPROBE, atempo_chain, encode_args,
                     probe_duration, probe_format_tag, to_posix, to_win)
from .organize import organize


def _load_cta_captions(csv_path: str) -> list[str]:
    """captions.csv segment 3 == CTA caption pool."""
    posix = to_posix(csv_path)
    if not Path(posix).exists():
        return []
    out: list[str] = []
    with open(posix, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if (row.get("segment") or "").strip() == "3":
                t = (row.get("text") or "").strip().strip('"').strip()
                if t:
                    out.append(t)
    return out


def _select_clips(source: str, variant: str, specified_file: Path | None) -> list[Path]:
    if specified_file is not None and variant == "single":
        return [specified_file]
    src = Path(to_posix(source))
    pool = [p for p in src.glob("*.mp4") if p.is_file()
            and not any(s in p.name for s in ("_working", "grid_", "montage_", "final_", "assembly_"))]
    if not pool:
        raise RuntimeError(f"No source clips in {source}")
    n = {"grid": 4, "montage": random.choice([2, 3]), "single": 1, "cta_only": 1}[variant]
    if len(pool) < n:
        raise RuntimeError(f"Need {n} clips for {variant}, found {len(pool)}")
    return random.sample(pool, n)


def _build_base(variant: str, clips: list[Path], temp: Path, ts: str, cfg: Config) -> Path:
    """Build the raw variant (no CTA, no effects) — single output for the effects pipeline."""
    out = temp / f"{variant}_{ts}.mp4"
    if variant == "grid":
        # Stage clips into a working folder so build_grid sees exactly these 4.
        work = temp / f"working_{ts}"
        work.mkdir(parents=True, exist_ok=True)
        for i, c in enumerate(clips, start=1):
            shutil.copy(to_posix(str(c)), str(work / f"{i}_{c.name}"))
        grid_path = variants.build_grid(str(work), str(temp), fast_mode=True,
                                         crf=cfg.encode.crf, preset=cfg.encode.preset)
        Path(to_posix(grid_path)).rename(to_posix(str(out)))
        shutil.rmtree(to_posix(str(work)), ignore_errors=True)
    elif variant == "montage":
        variants.build_montage([str(c) for c in clips], str(out),
                               target_w=cfg.encode.target_w, target_h=cfg.encode.target_h,
                               fps=cfg.encode.fps, crf=cfg.encode.crf, preset=cfg.encode.preset,
                               temp_dir=str(temp))
    elif variant == "single":
        variants.build_single(str(clips[0]), str(out),
                              target_w=cfg.encode.target_w, target_h=cfg.encode.target_h,
                              fps=cfg.encode.fps, crf=cfg.encode.crf, preset=cfg.encode.preset)
    elif variant == "cta_only":
        # Pick from CTA folder; this variant has its own clip pool.
        outs = variants.clean_cta_only(cfg.paths.cta_folder, str(temp), ts,
                                       cta_caption=None, font_path=cfg.font.file,
                                       font_size=cfg.font.size,
                                       target_w=cfg.encode.target_w, target_h=cfg.encode.target_h,
                                       fps=cfg.encode.fps)
        if not outs:
            raise RuntimeError("cta_only base build failed")
        Path(to_posix(outs[0])).rename(to_posix(str(out)))
    else:
        raise ValueError(f"Unknown variant: {variant}")
    return out


def _append_cta(current: Path, ts: str, temp: Path, variant: str, cfg: Config) -> Path:
    captions = _load_cta_captions(cfg.paths.captions_csv)
    cta_dir = Path(to_posix(cfg.paths.cta_folder))
    if not captions or not cta_dir.is_dir():
        return current
    cta_clips = [p for p in cta_dir.glob("*.mp4") if p.is_file()]
    if not cta_clips:
        return current
    caption = random.choice(captions)
    cta_clip = random.choice(cta_clips)
    one_word = "\n".join(w for w in re.split(r"\s+", caption) if w)
    escaped = one_word.replace("'", "\\'")

    cta_with = temp / f"cta_caption_{ts}.mp4"
    drawtext = (
        f"drawtext=fontfile=C\\:/Windows/Fonts/arial.ttf:text='{escaped}':"
        f"fontsize=120:fontcolor=white:bordercolor=black:borderw=36:"
        f"x=(w-text_w)/2:y=h-text_h-288"
    )
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", to_win(str(cta_clip)),
                    "-vf", f"scale={cfg.encode.target_w}:{cfg.encode.target_h},fps={cfg.encode.fps},{drawtext}",
                    "-t", "5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(cfg.encode.crf),
                    "-preset", cfg.encode.preset, "-movflags", "+faststart",
                    to_win(str(cta_with))], check=True)

    out = temp / f"{variant}_cta_{ts}.mp4"
    lst = temp / f"cta_concat_{ts}.txt"
    lst.write_text(f"file '{to_win(str(current))}'\nfile '{to_win(str(cta_with))}'", encoding="ascii")
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", to_win(str(lst)),
                    "-c", "copy", "-movflags", "+faststart", to_win(str(out))], check=True)
    return out if out.exists() else current


def _mix_audio(current: Path, ts: str, temp: Path, variant: str, audio_source: str | None, cfg: Config) -> Path:
    vd = probe_duration(str(current))
    if audio_source and Path(to_posix(audio_source)).exists():
        audio = audio_source
    else:
        d = Path(to_posix(cfg.paths.audio_folder))
        cands = [p for p in d.iterdir() if p.suffix.lower() in (".mp3", ".flac", ".wav")]
        if not cands:
            return current
        audio = str(random.choice(cands))

    ad = probe_duration(audio)
    if ad <= 0:
        return current
    ratio = vd / ad
    chain = atempo_chain(ratio)

    audio_adj = temp / f"audio_adj_{ts}.wav"
    fc = (f"{chain},aresample=48000,acompressor=threshold=-12dB:ratio=6:attack=5:release=50,"
          f"loudnorm=I=-14:TP=-1:LRA=7,afade=t=in:st=0:d=0.5,"
          f"afade=t=out:st={vd-0.5:.6f}:d=0.5,apad=whole_dur={vd:.6f}")
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", to_win(audio), "-filter:a", fc,
                    "-t", f"{vd:.6f}", to_win(str(audio_adj))], check=True)

    out = temp / f"{variant}_cta_audio_{ts}.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", to_win(str(current)), "-i", to_win(str(audio_adj)),
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                    "-c:a", "aac", "-b:a", "192k", "-t", f"{vd:.6f}",
                    "-movflags", "+faststart", to_win(str(out))], check=True)
    return out if out.exists() else current


def _apply_reversal(current: Path, ts: str, temp: Path, variant: str, reversal_speed: float, cfg: Config) -> Path:
    speed_factor = 1.0 / reversal_speed
    a_chain = (f"atempo=2,atempo={(reversal_speed/2.0):.6f}"
               if reversal_speed > 2.0 else f"atempo={reversal_speed:.6f}")
    reversed_path = temp / f"reversed_{ts}.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", to_win(str(current)),
                    "-vf", f"reverse,setpts={speed_factor:.6f}*PTS",
                    "-af", f"areverse,{a_chain}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(cfg.encode.crf),
                    "-preset", "fast", "-c:a", "aac", "-b:a", "192k",
                    "-movflags", "+faststart", to_win(str(reversed_path))], check=True)
    if not reversed_path.exists():
        return current
    out = temp / f"{variant}_cta_audio_glitch_reversal_{ts}.mp4"
    lst = temp / f"reversal_concat_{ts}.txt"
    lst.write_text(f"file '{to_win(str(current))}'\nfile '{to_win(str(reversed_path))}'", encoding="ascii")
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", to_win(str(lst)),
                    "-c", "copy", "-movflags", "+faststart", to_win(str(out))], check=True)
    return out if out.exists() else current


def _extract_seed_for_rename(clip_path: Path) -> str | None:
    name = clip_path.name
    comment = probe_format_tag(str(clip_path), "comment")
    if comment:
        for pat in (r'seed\\+":\s*(\d{10,})', r'"seed"[:\s]*"?(\d{10,})"?'):
            m = re.search(pat, comment)
            if m:
                return m.group(1)
    for pat in (r'seed(\d{10,})', r'_(\d{10,})_'):
        m = re.search(pat, name)
        if m:
            return m.group(1)
    return None


def _rename_via_metadata(final: Path, first_clip: Path, finals_folder: Path, cfg: Config) -> Path:
    """Match seed+clipname against metadata.csv, take LAST (most recent) row, prepend its filename."""
    seed = _extract_seed_for_rename(first_clip)
    if not seed:
        return final
    csv_posix = to_posix(cfg.paths.metadata_csv)
    if not Path(csv_posix).exists():
        return final
    with open(csv_posix, "r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    matches = [r for r in rows if r.get("seed") == seed and r.get("clipname") == first_clip.name]
    if not matches:
        matches = [r for r in rows if r.get("clipname") == first_clip.name]
    if not matches:
        return final
    row = matches[-1]
    llm = (row.get("filename") or "").strip()
    if not llm:
        return final
    new_base = f"{llm}_{final.stem}"
    new_path = finals_folder / f"{new_base}.mp4"
    if new_path.exists():
        return final
    final.rename(to_posix(str(new_path)))
    # Update CSV with new filename
    old_name = final.name
    for r in rows:
        if r.get("filename") == old_name:
            r["filename"] = new_path.name
    with open(csv_posix, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), quoting=csv.QUOTE_ALL)
        w.writeheader()
        w.writerows(rows)
    return new_path


def _cleanup(temp: Path, ts: str):
    for pat in ("*.txt", "*.wav"):
        for f in temp.glob(pat):
            f.unlink(missing_ok=True)
    for f in temp.glob(f"*_{ts}.mp4"):
        f.unlink(missing_ok=True)


def run_one(variant: str, source: str, cfg: Config, *, audio_source: str | None = None,
            reversal_speed: float = 4.0, no_effects: bool = False) -> list[str]:
    """Run one pipeline iteration. Returns list of final output paths."""
    src = Path(to_posix(source))
    specified = src if src.is_file() else None
    actual = src.parent if specified else src
    finals = actual / "finals"
    temp = finals / "temp"
    finals.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)

    # Optional pre-step: organize root favorites by prompt (best-effort).
    try:
        organize(cfg.paths.favorites_root)
    except Exception as e:
        print(f"[ORGANIZE] skipped: {e}")

    clips = _select_clips(str(actual), variant, specified)
    print(f"[SELECT] {variant}: {[c.name for c in clips]}")

    # Extract metadata for each source clip into metadata.csv
    for c in clips:
        try:
            extract_one(str(c), cfg.paths.metadata_csv)
        except Exception as e:
            print(f"[META] {c.name}: {e}")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    if no_effects:
        # Clean dual-output (cta + nocta) — matches legacy create_*.ps1
        captions = _load_cta_captions(cfg.paths.captions_csv)
        caption = random.choice(captions) if captions else None
        if variant == "single":
            outs = variants.clean_single(str(clips[0]), str(finals), ts, cta_caption=caption,
                                          font_path=cfg.font.file, font_size=cfg.font.size,
                                          border_w=cfg.font.border_w,
                                          target_w=cfg.encode.target_w, target_h=cfg.encode.target_h,
                                          fps=cfg.encode.fps)
        elif variant == "montage":
            outs = variants.clean_montage([str(c) for c in clips], str(finals), ts, cta_caption=caption,
                                           font_path=cfg.font.file, font_size=cfg.font.size,
                                           border_w=cfg.font.border_w,
                                           target_w=cfg.encode.target_w, target_h=cfg.encode.target_h,
                                           fps=cfg.encode.fps)
        elif variant == "cta_only":
            outs = variants.clean_cta_only(cfg.paths.cta_folder, str(finals), ts, cta_caption=caption,
                                            font_path=cfg.font.file, font_size=cfg.font.size,
                                            target_w=cfg.encode.target_w, target_h=cfg.encode.target_h,
                                            fps=cfg.encode.fps)
        elif variant == "grid":
            # Grid has no CTA twin in legacy code; --no-effects = just the raw grid in finals/.
            base = _build_base(variant, clips, finals, ts, cfg)
            outs = [str(base)]
        else:
            raise ValueError(variant)
        return outs

    # Full effects pipeline
    base = _build_base(variant, clips, temp, ts, cfg)
    cur = _append_cta(base, ts, temp, variant, cfg)
    cur = _mix_audio(cur, ts, temp, variant, audio_source, cfg)

    print("[GLITCH] applying...")
    glitch_out = effects.apply_glitch_negative(
        str(cur), str(temp), neg_audio_dir=cfg.paths.neg_audio,
        output_filename=f"{variant}_cta_audio_glitch_{ts}.mp4")
    cur = Path(to_posix(glitch_out))

    print("[REVERSAL] applying...")
    cur = _apply_reversal(cur, ts, temp, variant, reversal_speed, cfg)

    print("[RAINBOW] applying...")
    rainbow_out = temp / f"{variant}_cta_audio_glitch_reversal_rainbow_{ts}.mp4"
    effects.rainbow_apply(str(cur), str(rainbow_out), cfg.paths.templates, crf=cfg.encode.crf)
    cur = rainbow_out

    print("[MOTIONBLUR] tblend @ 60fps -> finals/")
    final_out = finals / f"{variant}_cta_audio_glitch_reversal_rainbow_motionblur_{ts}.mp4"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", to_win(str(cur)), "-filter:v", "tblend", "-r", "60",
                    "-c:v", "libx264", "-crf", str(cfg.encode.crf), "-preset", cfg.encode.preset,
                    "-c:a", "copy", "-movflags", "+faststart",
                    to_win(str(final_out))], check=True)

    # LLM fill (best-effort) then seed+clipname rename
    try:
        fill_mod.fill(cfg)
    except Exception as e:
        print(f"[FILL] skipped: {e}")
    final_out = _rename_via_metadata(final_out, clips[0], finals, cfg)

    _cleanup(temp, ts)
    return [str(final_out)]


def run(variants_list: list[str], source: str, *, quantity: int = 1, cfg: Config | None = None,
        audio_source: str | None = None, reversal_speed: float = 4.0,
        no_effects: bool = False) -> list[str]:
    if cfg is None:
        from .config import load
        cfg = load()
    all_outputs: list[str] = []
    for v in variants_list:
        for i in range(quantity):
            print(f"\n=== {v} {i+1}/{quantity} ===")
            outs = run_one(v, source, cfg, audio_source=audio_source,
                           reversal_speed=reversal_speed, no_effects=no_effects)
            all_outputs.extend(outs)
    return all_outputs
