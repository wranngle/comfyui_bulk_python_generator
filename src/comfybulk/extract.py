"""ComfyUI metadata extraction (PNG/MP4 → metadata.csv).

Ports Extract-ComfyUIPrompt.ps1 behavior:
- Strict seed validation (10+ digits, 3+ unique, ≤60% repetition).
- Recursive seed search across nested JSON.
- Multiple prompt-extraction strategies (workflow.nodes, prompt nodes, escaped JSON, tEXt chunk).
- Project vocabulary substitutions applied before save.
- MP4→PNG sibling fallback.
- File priority MP4 > AVI > WEBM > PNG for same base name.
- Append to metadata.csv with prompt+seed+clipname duplicate check + 3x retry on lock.
"""
from __future__ import annotations
import csv, json, os, re, tempfile, time
from collections import Counter
from pathlib import Path

from .ffmpeg import probe_format_tag, to_posix

CSV_HEADER = ["filename", "seed", "content_prompt", "clipname", "caption",
              "title", "description", "tags", "cover_text", "pinned_comment", "CTA"]

DISCOURAGED_TERM_REPLACEMENTS = [
    (re.compile(r"\bDMT\b", re.I), "visionary"),
    (re.compile(r"\bpsychedelic\b", re.I), "abstract"),
    (re.compile(r"\btrip\b", re.I), "journey"),
    (re.compile(r"\bego-death\b", re.I), "transcendent"),
]
BANNED = DISCOURAGED_TERM_REPLACEMENTS


def replace_discouraged_terms(text: str) -> str:
    """Apply project-specific vocabulary substitutions.

    This is metadata style cleanup, not safety moderation.
    """
    if not text:
        return text
    for pat, repl in DISCOURAGED_TERM_REPLACEMENTS:
        text = pat.sub(repl, text)
    return text


def remove_banned(text: str) -> str:
    """Compatibility wrapper for older callers."""
    return replace_discouraged_terms(text)


def validate_seed(s: str) -> bool:
    if not s or not s.isdigit() or len(s) < 10:
        return False
    if len(set(s)) < 3:
        return False
    if re.fullmatch(r"0+|1+|9+", s):
        return False
    counts = Counter(s)
    if max(counts.values()) / len(s) > 0.6:
        return False
    try:
        if float(s) > 1e307:
            return False
    except ValueError:
        return False
    return True


def find_seed_recursive(obj, path: str = "root", depth: int = 0, max_depth: int = 10):
    if obj is None or depth > max_depth:
        return None
    if "seed" in path.lower():
        s = str(obj)
        if validate_seed(s):
            return s
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            r = find_seed_recursive(v, f"{path}[{i}]", depth + 1, max_depth)
            if r:
                return r
    elif isinstance(obj, dict):
        for k, v in obj.items():
            sub = f"{path}.{k}"
            if isinstance(v, (str, int)):
                s = str(v)
                if validate_seed(s):
                    return s
            r = find_seed_recursive(v, sub, depth + 1, max_depth)
            if r:
                return r
    return None


def _seed_from_filename(name: str) -> str | None:
    for pat in (r"seed(\d{10,})", r"_(\d{10,})_"):
        m = re.search(pat, name)
        if m and validate_seed(m.group(1)):
            return m.group(1)
    return None


def _extract_prompt_from_workflow(wf) -> str | None:
    nodes = (wf or {}).get("workflow", {}).get("nodes")
    if isinstance(nodes, list):
        for n in nodes:
            ct = n.get("class_type", "")
            wv = n.get("widgets_values") or []
            if (re.search(r"Text|Prompt|String", ct) or n.get("type") == "STRING") and wv and isinstance(wv[0], str) and len(wv[0]) > 30:
                return wv[0]
    pd = (wf or {}).get("prompt")
    if isinstance(pd, str):
        try:
            pd = json.loads(pd)
        except json.JSONDecodeError:
            pd = None
    if isinstance(pd, dict):
        for nid, nd in pd.items():
            if not isinstance(nd, dict):
                continue
            inputs = nd.get("inputs") or {}
            ct = nd.get("class_type", "")
            t = inputs.get("text_0") if ct == "ShowText|pysssss" else None
            if isinstance(t, str) and len(t) > 30:
                return t.strip()
            t = inputs.get("text") if ct == "TextInputBasic" else None
            if isinstance(t, str) and len(t) > 30:
                return t.strip()
            t = inputs.get("text")
            if isinstance(t, str) and len(t) > 30:
                return t.strip()
    return None


def _seed_from_jsonstring(js: str) -> str | None:
    for pat in (r'seed\\+":\s*(\d{10,})', r'"seed"[:\s]*"?(\d{10,})"?'):
        m = re.search(pat, js)
        if m and validate_seed(m.group(1)):
            return m.group(1)
    return None


def _prompt_from_escaped_json(js: str) -> str | None:
    for pat in (r'text_0\\+":\s*\\+"([^"]+(?:\\\\.[^"]*)*)"', r'"text_0":\s*"([^"]+(?:\\\\.[^"]*)*)"'):
        m = re.search(pat, js)
        if m:
            v = m.group(1).replace("\\\\n", "\n").replace('\\\\"', '"').replace("\\\\\\\\", "\\")
            return v
    return None


def extract_from_mp4(path: str) -> dict | None:
    """Returns {prompt, seed, source_file} or None if either is missing."""
    comment = probe_format_tag(path, "comment")
    prompt = seed = None
    if comment:
        try:
            wf = json.loads(comment)
            prompt = _extract_prompt_from_workflow(wf)
            seed = _seed_from_jsonstring(comment) or find_seed_recursive(wf, "wf", max_depth=5)
            if not prompt:
                prompt = _prompt_from_escaped_json(comment)
        except json.JSONDecodeError:
            prompt = _prompt_from_escaped_json(comment)
            seed = _seed_from_jsonstring(comment)
    if not seed:
        seed = _seed_from_filename(Path(path).stem)
    if seed and prompt:
        return {"prompt": prompt.strip(), "seed": seed, "source_file": Path(path).name}
    return None


PNG_PROMPT_PATTERNS = [
    re.compile(r'"text"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'),
    re.compile(r'"prompt"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'),
    re.compile(r'"inputs"\s*:\s*\{[^}]*"text"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"'),
    re.compile(r'"widgets_values"\s*:\s*\[\s*"([^"\\]*(?:\\.[^"\\]*)*)"'),
]
PNG_SEED_PATTERNS = [re.compile(r'"seed"\s*:\s*(\d+)'),
                     re.compile(r'"inputs"\s*:\s*\{[^}]*"seed"\s*:\s*(\d+)')]


def extract_from_png(path: str) -> dict | None:
    data = open(to_posix(path), "rb").read()
    text = data.decode("utf-8", errors="replace")
    prompt = seed = None
    for p in PNG_PROMPT_PATTERNS:
        if m := p.search(text):
            prompt = m.group(1).replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
            break
    for p in PNG_SEED_PATTERNS:
        if m := p.search(text):
            cand = m.group(1)
            if validate_seed(cand):
                seed = cand
                break
    if not prompt:
        marker = b"tEXtprompt"
        i = data.find(marker)
        if i >= 0:
            start = i + len(marker) + 1
            end = data.find(b"\x00", start)
            if end == -1:
                end = data.find(b"IDAT", start)
            if end > start:
                prompt = data[start:end].decode("utf-8", errors="replace")
    if not seed:
        seed = _seed_from_filename(Path(path).stem)
    if seed and prompt:
        return {"prompt": prompt.strip(), "seed": seed, "source_file": Path(path).name}
    return None


def _csv_read(csv_path: str, retries: int = 3, wait: int = 20):
    last = None
    for i in range(retries):
        try:
            with open(to_posix(csv_path), "r", encoding="utf-8-sig", newline="") as f:
                return list(csv.DictReader(f))
        except (OSError, IOError) as e:
            last = e
            if i < retries - 1:
                time.sleep(wait)
    raise RuntimeError(f"Failed to read CSV after {retries} attempts: {last}")


def _csv_write(csv_path: str, rows: list[dict], retries: int = 3, wait: int = 20):
    last = None
    target = Path(to_posix(csv_path))
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
                w = csv.DictWriter(f, fieldnames=CSV_HEADER, quoting=csv.QUOTE_ALL)
                w.writeheader()
                for r in rows:
                    w.writerow({k: r.get(k, "") for k in CSV_HEADER})
            os.replace(tmp_path, target)
            return
        except (OSError, IOError) as e:
            last = e
            if tmp_path:
                tmp_path.unlink(missing_ok=True)
            if i < retries - 1:
                time.sleep(wait)
    raise RuntimeError(f"Failed to write CSV after {retries} attempts: {last}")


def append_to_csv(prompt: str, seed: str, source_file: str, csv_path: str) -> bool:
    """Append a row. Returns False if exact (prompt, seed, clipname) duplicate exists."""
    posix = to_posix(csv_path)
    if not Path(posix).exists():
        _csv_write(csv_path, [])
    rows = _csv_read(csv_path)
    cleaned = replace_discouraged_terms(prompt)
    fmt_seed = ""
    try:
        fmt_seed = str(int(float(seed)))
    except (ValueError, TypeError):
        fmt_seed = seed
    for r in rows:
        if r.get("content_prompt") == cleaned and r.get("seed") == fmt_seed and r.get("clipname") == source_file:
            return False
    rows.append({"filename": "", "seed": fmt_seed, "content_prompt": cleaned,
                 "clipname": source_file, "caption": "", "title": "", "description": "",
                 "tags": "", "cover_text": "", "pinned_comment": "", "CTA": ""})
    _csv_write(csv_path, rows)
    return True


def _extract_for_path(path: str) -> dict | None:
    ext = Path(path).suffix.lower()
    if ext == ".png":
        return extract_from_png(path)
    if ext in (".mp4", ".avi", ".webm"):
        r = extract_from_mp4(path)
        if r:
            return r
        png = Path(to_posix(path)).with_suffix(".png")
        if png.exists():
            r = extract_from_png(str(png))
            if r:
                r["source_file"] = f"{Path(path).name} (from PNG)"
                return r
    return None


def process_file(path: str, csv_path: str, test_mode: bool = False) -> bool:
    r = _extract_for_path(path)
    if not r:
        return False
    r["prompt"] = re.sub(r"\s+", " ", r["prompt"]).strip()
    if test_mode:
        return True
    return append_to_csv(r["prompt"], r["seed"], r["source_file"], csv_path)


def process_directory(directory: str, csv_path: str, test_mode: bool = False) -> tuple[int, int]:
    """Recursively process all media; prefer mp4>avi>webm>png per base name; skip assemblymaker subdirs."""
    root = Path(to_posix(directory))
    pri = {".mp4": 0, ".avi": 1, ".webm": 2, ".png": 3}
    by_base: dict[str, Path] = {}
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        ext = f.suffix.lower()
        if ext not in pri:
            continue
        if "assemblymaker" in f.parts or (f.parent.name == "favorites" and f.parent != root):
            continue
        cur = by_base.get(f.stem)
        if cur is None or pri[ext] < pri[cur.suffix.lower()]:
            by_base[f.stem] = f
    ok = fail = 0
    for f in by_base.values():
        try:
            if process_file(str(f), csv_path, test_mode):
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1
    return ok, fail
