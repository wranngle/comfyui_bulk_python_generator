"""LLM-driven metadata fill via local Ollama.

For each row with a content_prompt: sends one Ollama request per empty field whose
prompt template exists in ai_metadata_prompts.csv. Detects existing duplicate values
across the CSV (per generated field), keeps the first occurrence, clears later
duplicates, and regenerates with explicit context about what must differ.
"""
from __future__ import annotations
import csv, os, re, shutil, subprocess, tempfile, time
from datetime import datetime
from pathlib import Path

import requests

from .config import Config
from .extract import replace_discouraged_terms
from .ffmpeg import to_posix
from .llm import LocalLLM


SHORT_FIELDS = {"cover_text", "caption"}
NEVER_FILL = {"content_prompt", "seed", "clipname"}
EXCLUDED_FROM_DUPE_SCAN = NEVER_FILL


def _load_prompts(path: str) -> dict[str, str]:
    posix = to_posix(path)
    p: dict[str, str] = {}
    with open(posix, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            field = (row.get("field") or "").strip()
            tmpl = row.get("prompt") or ""
            if field and tmpl:
                p[field] = tmpl
    return p


def _read_csv(path: str) -> tuple[list[str], list[dict]]:
    posix = to_posix(path)
    with open(posix, "r", encoding="utf-8-sig", newline="") as f:
        rdr = csv.DictReader(f)
        return list(rdr.fieldnames or []), list(rdr)


def _write_csv(path: str, fieldnames: list[str], rows: list[dict], retries: int = 3, wait: int = 20):
    target = Path(to_posix(path))
    last = None
    for i in range(retries):
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                newline="",
                dir=str(target.parent),
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                tmp_path = Path(f.name)
                w = csv.DictWriter(f, fieldnames=fieldnames, quoting=csv.QUOTE_ALL)
                w.writeheader()
                for r in rows:
                    w.writerow({k: r.get(k, "") for k in fieldnames})
            os.replace(tmp_path, target)
            return
        except (OSError, IOError) as e:
            last = e
            if tmp_path:
                tmp_path.unlink(missing_ok=True)
            if i < retries - 1:
                time.sleep(wait)
    raise RuntimeError(f"CSV write failed: {last}")


def _ollama_auto_launch_enabled(cfg: Config) -> bool:
    explicit_cfg = bool(getattr(cfg.ollama, "auto_launch", False))
    explicit_env = os.environ.get("COMFYBULK_AUTO_LAUNCH_OLLAMA", "").strip().lower() in {"1", "true", "yes", "on"}
    return explicit_cfg or explicit_env


def ensure_ollama(cfg: Config) -> None:
    """Try to reach Ollama; optionally start `ollama serve` only when explicitly enabled."""
    try:
        r = requests.get(f"{cfg.ollama.host}/api/tags", timeout=5)
        if r.ok:
            return
    except requests.RequestException:
        pass
    if not _ollama_auto_launch_enabled(cfg):
        print("[OLLAMA] Not reachable. Start Ollama manually, or opt in with cfg.ollama.auto_launch=True or COMFYBULK_AUTO_LAUNCH_OLLAMA=1.")
        return
    exe = shutil.which("ollama")
    if not exe:
        print("[OLLAMA] Auto-launch requested, but `ollama` was not found on PATH.")
        return
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.Popen([exe, "serve"], **kwargs)
    time.sleep(5)


def ensure_model(cfg: Config) -> None:
    """If model is missing locally, create it from the configured GGUF."""
    if not shutil.which("ollama"):
        return
    r = subprocess.run(["ollama", "list"], capture_output=True, text=True)
    if cfg.ollama.model in (r.stdout or ""):
        return
    gguf = to_posix(cfg.ollama.gguf_path)
    if not Path(gguf).exists():
        return
    modelfile = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", prefix="comfybulk_modelfile_", suffix=".txt", delete=False) as f:
            modelfile = Path(f.name)
            f.write(f'FROM "{gguf}"\n')
        subprocess.run(["ollama", "create", cfg.ollama.model, "-f", str(modelfile)], check=False)
    finally:
        if modelfile:
            modelfile.unlink(missing_ok=True)


def _clean_response(txt: str) -> str:
    txt = re.sub(r"<think>.*?</think>", "", txt, flags=re.DOTALL)
    txt = re.sub(r"^/no_think\s*", "", txt)
    txt = re.sub(r"\r?\n", " ", txt)
    txt = re.sub(r"\s+", " ", txt)
    txt = re.sub(r"\*\*([^*]+)\*\*", r"\1", txt)
    txt = re.sub(r"^\*+\s*", "", txt)
    txt = re.sub(r"\s*\*+$", "", txt)
    txt = re.sub(r"^\s*[\*\-•]\s*", "", txt)
    if m := re.search(r'"([^"]+)"', txt):
        txt = m.group(1)
    return txt.strip()


def ollama_generate(cfg: Config, prompt: str, *, temp: float, top_p: float, repeat_penalty: float, num_predict: int = 200) -> str:
    llm = LocalLLM(backend="ollama", host=cfg.ollama.host, model=cfg.ollama.model)
    raw = llm.generate(
        prompt,
        temperature=temp,
        top_p=top_p,
        repeat_penalty=repeat_penalty,
        num_predict=num_predict,
    )
    return _clean_response(raw)


def _canonical_generated_value(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _field_existing_values(rows: list[dict], field: str, *, skip_index: int) -> set[str]:
    return {
        _canonical_generated_value(r.get(field, ""))
        for i, r in enumerate(rows)
        if i != skip_index and _canonical_generated_value(r.get(field, ""))
    }


def _scan_duplicates(rows: list[dict], llm_fields: set[str]) -> dict[tuple[int, str], str]:
    """Mark (row_index, field) pairs whose value duplicates another row in the same field.
    Keeps the first occurrence and empties later duplicates in-place so the regen loop reruns them."""
    dupes: dict[tuple[int, str], str] = {}
    for f in llm_fields:
        if f in EXCLUDED_FROM_DUPE_SCAN or f in SHORT_FIELDS:
            continue
        seen: dict[str, int] = {}
        for i, r in enumerate(rows):
            key = _canonical_generated_value(r.get(f, ""))
            if not key:
                continue
            if key in seen:
                r[f] = ""
                dupes[(i, f)] = f"row {seen[key] + 1}"
            else:
                seen[key] = i
    return dupes


def fill(cfg: Config) -> int:
    ensure_ollama(cfg)
    ensure_model(cfg)
    prompts = _load_prompts(cfg.paths.ai_prompts_csv)
    fieldnames, rows = _read_csv(cfg.paths.metadata_csv)

    # Backup CSV
    bk = Path(to_posix(cfg.paths.metadata_csv)).with_suffix(
        f".bak.{datetime.now().strftime('%Y%m%d%H%M%S')}.csv")
    shutil.copy(to_posix(cfg.paths.metadata_csv), bk)

    # Retroactive cleanup: project vocabulary substitutions in LLM-filled fields.
    cleanable = {"filename", "title", "description", "caption", "cover_text",
                 "pinned_comment", "CTA", "tags", "content_prompt"}
    n_clean = 0
    for r in rows:
        for f in cleanable:
            v = r.get(f)
            if v:
                cleaned = replace_discouraged_terms(v)
                if cleaned != v:
                    r[f] = cleaned
                    n_clean += 1
    if n_clean:
        _write_csv(cfg.paths.metadata_csv, fieldnames, rows)

    dupes = _scan_duplicates(rows, set(prompts.keys()))
    if dupes:
        _write_csv(cfg.paths.metadata_csv, fieldnames, rows)

    filled = 0
    for i, r in enumerate(rows):
        if not r.get("content_prompt"):
            continue
        for f in fieldnames:
            if r.get(f) or f in NEVER_FILL or f not in prompts:
                continue
            duplicate_of = dupes.get((i, f))
            temp = 0.9 if duplicate_of else 0.7
            top_p = 0.9
            rp = 1.15 if duplicate_of else 1.05
            ctx_len = 500 if duplicate_of else 240
            ctx = replace_discouraged_terms(r["content_prompt"][:ctx_len])
            unique = f"Row: {i+1}" + (f" | Seed: {r['seed']}" if r.get("seed") else "")
            if duplicate_of:
                unique += f" | Regenerate because this field matched {duplicate_of}; use a different angle and wording."
            term_note = ("\n\n[PROJECT VOCABULARY: Avoid these exact terms in metadata: DMT, psychedelic, trip, ego-death. "
                         "Prefer: visionary, abstract, journey, transcendent.]")
            existing_values = _field_existing_values(rows, f, skip_index=i)
            txt = ""
            for attempt in range(1, 3):
                retry_note = ""
                if attempt > 1:
                    retry_note = "\n\n[RETRY: The previous answer matched existing metadata. Write a clearly different answer.]"
                prompt = f"/no_think\n\nContext: {ctx}...\n{unique}{term_note}{retry_note}\n\nTask: {prompts[f]}"
                try:
                    txt = replace_discouraged_terms(ollama_generate(cfg, prompt, temp=temp, top_p=top_p, repeat_penalty=rp))
                except Exception as e:
                    print(f"[LLM ERROR] row {i+1} field {f}: {e}")
                    txt = ""
                    break
                if not txt:
                    break
                if _canonical_generated_value(txt) not in existing_values:
                    break
                print(f"[LLM RETRY] row {i+1} field {f}: duplicate output on attempt {attempt}")
                txt = ""
            if not txt:
                continue
            r[f] = txt
            _write_csv(cfg.paths.metadata_csv, fieldnames, rows)
            filled += 1
    return filled
