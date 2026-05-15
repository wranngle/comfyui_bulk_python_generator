"""Bulk processing orchestrator — full port of bulk_processor.ps1.

Selects clips → extracts metadata → builds variant → appends CTA caption →
mixes random audio → glitch → reverse → rainbow → motion blur → LLM fill →
seed+clipname rename → cleanup.

Variant types: grid | montage | single | cta_only.
With --no-effects, runs the clean_* variant builders (dual cta/nocta outputs)
matching the legacy create_*.ps1 behavior.
"""
from __future__ import annotations
import csv, hashlib, json, math, random, re, shutil, subprocess
from datetime import datetime
from pathlib import Path

from .config import Config
from . import effects, variants, fill as fill_mod
from .checkpoint import Checkpoint
from .extract import process_file as extract_one
from .ffmpeg import (FFMPEG, FFPROBE, atempo_chain, encode_args,
                     concat_file_line, drawtext_filter, probe_duration,
                     probe_format_tag, to_posix, to_win)
from .organize import organize


_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


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


def _select_clips(source: str, variant: str, specified_file: Path | None,
                  rng: random.Random | None = None) -> list[Path]:
    if specified_file is not None and variant == "single":
        return [specified_file]
    r = rng or random
    src = Path(to_posix(source))
    pool = [p for p in src.glob("*.mp4") if p.is_file()
            and not any(s in p.name for s in ("_working", "grid_", "montage_", "final_", "assembly_"))]
    if not pool:
        raise RuntimeError(f"No source clips in {source}")
    n = {"grid": 4, "montage": r.choice([2, 3]), "single": 1, "cta_only": 1}[variant]
    if len(pool) < n:
        raise RuntimeError(f"Need {n} clips for {variant}, found {len(pool)}")
    return r.sample(pool, n)


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


def _append_cta(current: Path, ts: str, temp: Path, variant: str, cfg: Config,
                rng: random.Random | None = None) -> Path:
    captions = _load_cta_captions(cfg.paths.captions_csv)
    cta_dir = Path(to_posix(cfg.paths.cta_folder))
    if not captions or not cta_dir.is_dir():
        return current
    cta_clips = [p for p in cta_dir.glob("*.mp4") if p.is_file()]
    if not cta_clips:
        return current
    r = rng or random
    caption = r.choice(captions)
    cta_clip = r.choice(cta_clips)
    one_word = "\n".join(w for w in re.split(r"\s+", caption) if w)

    cta_with = temp / f"cta_caption_{ts}.mp4"
    drawtext = drawtext_filter(
        one_word, cfg.font.file, font_size=cfg.font.size,
        border_w=cfg.font.border_w, y="h-text_h-288",
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
    lst.write_text(
        "\n".join([concat_file_line(current), concat_file_line(cta_with)]),
        encoding="utf-8",
    )
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "concat", "-safe", "0", "-i", to_win(str(lst)),
                    "-c", "copy", "-movflags", "+faststart", to_win(str(out))], check=True)
    return out if out.exists() else current


def _mix_audio(current: Path, ts: str, temp: Path, variant: str, audio_source: str | None,
               cfg: Config, rng: random.Random | None = None) -> Path:
    vd = probe_duration(str(current))
    if audio_source and Path(to_posix(audio_source)).exists():
        audio = audio_source
    else:
        d = Path(to_posix(cfg.paths.audio_folder))
        cands = [p for p in d.iterdir() if p.suffix.lower() in (".mp3", ".flac", ".wav")]
        if not cands:
            return current
        audio = str((rng or random).choice(cands))

    ad = probe_duration(audio)
    if ad <= 0:
        return current
    ratio = vd / ad
    chain = atempo_chain(ratio)

    audio_adj = temp / f"audio_adj_{ts}.wav"
    fade_d = min(0.5, max(0.0, vd / 4.0))
    fade_out = max(0.0, vd - fade_d)
    fc = (f"{chain},aresample=48000,acompressor=threshold=-12dB:ratio=6:attack=5:release=50,"
          f"loudnorm=I=-14:TP=-1:LRA=7,afade=t=in:st=0:d={fade_d:.6f},"
          f"afade=t=out:st={fade_out:.6f}:d={fade_d:.6f},apad=whole_dur={vd:.6f}")
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


def _safe_filename_prefix(value: str, max_len: int = 80) -> str | None:
    """Sanitize metadata/LLM text before using it as a filename component."""
    s = re.sub(r"[\x00-\x1f\x7f]", "", value)
    s = re.sub(r'[<>:"/\\|?*\[\]]+', "_", s)
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"_+", "_", s).strip("._ ")
    if not s:
        return None
    s = s[:max_len].rstrip("._ ")
    if not s:
        return None
    if s.upper() in _WINDOWS_RESERVED_NAMES:
        s = f"_{s}"
    return s


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
    prefix = _safe_filename_prefix(llm)
    if not prefix:
        print(f"[RENAME] skipped unsafe metadata filename for {first_clip.name}")
        return final
    new_base = f"{prefix}_{final.stem}"
    new_name = f"{new_base[:240]}.mp4"
    new_path = finals_folder / new_name
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
    """Remove only files created for this pipeline timestamp in the temp folder."""
    if not temp.is_dir() or not ts:
        return
    temp_root = temp.resolve()
    for f in temp.glob(f"*{ts}*"):
        if not f.is_file() or f.suffix.lower() not in {".txt", ".wav", ".mp4"}:
            continue
        if f.resolve().parent != temp_root:
            continue
        f.unlink(missing_ok=True)


def _execution_seed(seed: int | None) -> int:
    if seed is not None:
        return int(seed)
    return random.SystemRandom().randrange(0, 2**63)


def _derive_iteration_seed(seed: int, variant: str, iteration: int) -> int:
    raw = f"{seed}:{variant}:{iteration}".encode("utf-8")
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "big")


def _write_manifest(finals: Path, entry: dict, enabled: bool = True) -> None:
    line = json.dumps(entry, sort_keys=True)
    print(f"[MANIFEST] {line}")
    if not enabled:
        return
    with open(finals / "pipeline_manifest.jsonl", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_one(variant: str, source: str, cfg: Config, *, audio_source: str | None = None,
            reversal_speed: float = 4.0, no_effects: bool = False,
            organize_favorites: bool = False, seed: int | None = None,
            write_manifest: bool = False) -> list[str]:
    """Run one pipeline iteration. Returns list of final output paths."""
    run_seed = _execution_seed(seed)
    rng = random.Random(run_seed)
    src = Path(to_posix(source))
    specified = src if src.is_file() else None
    actual = src.parent if specified else src
    finals = actual / "finals"
    temp = finals / "temp"
    finals.mkdir(parents=True, exist_ok=True)
    temp.mkdir(parents=True, exist_ok=True)

    if organize_favorites:
        try:
            result = organize(cfg.paths.favorites_root)
            print(f"[ORGANIZE] {result}")
        except Exception as e:
            print(f"[ORGANIZE] skipped: {e}")

    clips = _select_clips(str(actual), variant, specified, rng=rng)
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
        caption = rng.choice(captions) if captions else None
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
        _write_manifest(finals, {
            "event": "pipeline_run",
            "timestamp": ts,
            "completed_at": datetime.now().isoformat(timespec="seconds"),
            "seed": run_seed,
            "variant": variant,
            "source": str(actual),
            "specified_file": str(specified) if specified else None,
            "clips": [str(c) for c in clips],
            "outputs": outs,
            "no_effects": True,
            "organize_favorites": organize_favorites,
        }, enabled=write_manifest)
        return outs

    # Full effects pipeline
    base = _build_base(variant, clips, temp, ts, cfg)
    cur = _append_cta(base, ts, temp, variant, cfg, rng=rng)
    cur = _mix_audio(cur, ts, temp, variant, audio_source, cfg, rng=rng)

    print("[GLITCH] applying...")
    glitch_out = effects.apply_glitch_negative(
        str(cur), str(temp), neg_audio_dir=cfg.paths.neg_audio,
        output_filename=f"{variant}_cta_audio_glitch_{ts}.mp4", rng=rng)
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
    _write_manifest(finals, {
        "event": "pipeline_run",
        "timestamp": ts,
        "completed_at": datetime.now().isoformat(timespec="seconds"),
        "seed": run_seed,
        "variant": variant,
        "source": str(actual),
        "specified_file": str(specified) if specified else None,
        "clips": [str(c) for c in clips],
        "outputs": [str(final_out)],
        "no_effects": False,
        "organize_favorites": organize_favorites,
        "audio_source": audio_source,
        "reversal_speed": reversal_speed,
    }, enabled=write_manifest)
    return [str(final_out)]


def _checkpoint_base(source: str) -> Path:
    src = Path(to_posix(source))
    return src.parent if src.is_file() else src


def run(variants_list: list[str], source: str, *, quantity: int = 1, cfg: Config | None = None,
        audio_source: str | None = None, reversal_speed: float = 4.0,
        no_effects: bool = False, organize_favorites: bool = False,
        seed: int | None = None, write_manifest: bool = False,
        resume: bool = False, checkpoint_dir: str | None = None) -> list[str]:
    if cfg is None:
        from .config import load
        cfg = load()
    ckpt_root = Path(checkpoint_dir) if checkpoint_dir else _checkpoint_base(source)
    ckpt = Checkpoint.for_dir(ckpt_root).start_run(
        source=source, variants=variants_list, quantity=quantity, resume=resume,
    )
    if resume and ckpt.processed:
        print(f"[RESUME] skipping {len(ckpt.processed)} already-processed iteration(s) "
              f"(ledger: {ckpt.path})")
    all_outputs: list[str] = []
    for v in variants_list:
        for i in range(quantity):
            if ckpt.is_processed(v, i):
                print(f"[RESUME] skip {v} {i+1}/{quantity} (already done)")
                continue
            print(f"\n=== {v} {i+1}/{quantity} ===")
            run_seed = _derive_iteration_seed(int(seed), v, i) if seed is not None else None
            outs = run_one(v, source, cfg, audio_source=audio_source,
                           reversal_speed=reversal_speed, no_effects=no_effects,
                           organize_favorites=organize_favorites, seed=run_seed,
                           write_manifest=write_manifest)
            ckpt.mark_processed(v, i, outputs=outs)
            all_outputs.extend(outs)
    return all_outputs
