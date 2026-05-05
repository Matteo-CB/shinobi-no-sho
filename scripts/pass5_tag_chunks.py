"""Pass 5 : re-tagging temporel des chunks RAG (cf. pilier 5 anti-hallu v2).

Source des chunks : `shinobi.rag.chunker.chunk_all(canon)` qui produit
~16k chunks deterministes a partir de data/canonical/. Pas de scraping
ni de fichier sidecar requis.

Tagge chaque chunk RAG avec :
- arc                   : enum 30+ valeurs (cf. data/canonical/arc_temporal_anchors.json)
- year_min / year_max   : bornes annee, year 0 = naissance Naruto
- tier                  : manga | databook | anime_canon | anime_filler | movie | boruto | fan
- entities_mentioned    : liste de personnages, lieux, jutsus apparaissant dans le chunk

Output : 1 fichier JSON par chunk dans data/canonical/_pass5_output/<chunk_id>.json,
ecrit en parallele du chunk d'origine (metadata sidecar). Pas de re-embedding requis.

Usage :
    export GROQ_API_KEY=gsk_...
    python scripts/pass5_tag_chunks.py build --limit 100   # calibration
    python scripts/pass5_tag_chunks.py build                # full (~16k chunks)
    python scripts/pass5_tag_chunks.py submit
    python scripts/pass5_tag_chunks.py poll   <batch_id>
    python scripts/pass5_tag_chunks.py parse  <batch_id>
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Charge .env si present a la racine du projet (avant lecture de GROQ_API_KEY).
_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
if _ENV_PATH.exists():
    load_dotenv(_ENV_PATH)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# ---- Config corpus -----------------------------------------------------
OUTPUT_DIR = ROOT / "data" / "canonical" / "_pass5_output"
BATCH_DIR = ROOT / "data" / "canonical" / "_pass5_batches"
ARC_ANCHORS_PATH = ROOT / "data" / "canonical" / "arc_temporal_anchors.json"

# ---- Config Groq -------------------------------------------------------
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"
MODEL_PRICING = {
    "llama-3.3-70b-versatile": (0.59, 0.79),       # $/1M tokens (input, output)
    "openai/gpt-oss-120b": (0.15, 0.60),
}
TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 1500                           # tag output reste compact
REQUEST_TIMEOUT = 180.0
HARD_COST_LIMIT_USD = 15.0                          # garde-fou batch large

NET_ERRORS = (
    httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError,
    httpx.ReadTimeout, httpx.WriteError, httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)

# ---- System prompt (a ajuster apres premier test reel) -----------------
SYSTEM_PROMPT = """\
You are a Naruto-canon temporal tagger. For each chunk of wiki text given,
output a JSON object describing WHEN the chunk's events happen, WHICH tier
of canon it comes from, and WHICH canonical entities it mentions.

ABSOLUTE RULES:

1. JSON ONLY. Output starts with `{` and ends with `}`. No prose.

2. ARC enum (pick exactly one, lowercase snake_case) :
   pre_series, warring_states_period, konoha_founding,
   first_shinobi_world_war, second_shinobi_world_war,
   third_shinobi_world_war, kyuubi_attack, post_kyuubi,
   academy, wave_country, chunin_exam, sasuke_retrieval,
   pre_shippuden_timeskip, kazekage_rescue, sai_sasuke,
   immortals, hidan_kakuzu, itachi_pursuit, pain_invasion,
   five_kage_summit, fourth_shinobi_world_war, post_war,
   blank_period, boruto_academy, boruto_chunin_exam,
   boruto_kara, boruto_timeskip, unknown.

3. YEAR_MIN / YEAR_MAX. Bornes inclusive, integers. Year 0 = Naruto's
   birth = Nine-Tails attack on Konoha. Use the arc anchors as a guide ;
   if the chunk is more precise (e.g. "in year 12" cited verbatim),
   tighten the bounds. If unknown, set to null.

4. TIER (pick exactly one) :
   manga, databook, anime_canon, anime_filler, movie, boruto, fan.
   Use heuristics : official manga page numbering => manga ; databook
   entries => databook ; episode numbers from filler arcs => anime_filler.
   If ambiguous, use anime_canon as default for animated content.

5. ENTITIES_MENTIONED. List of canonical character_id, location_id,
   technique_id explicitly named in the chunk. Use lowercase snake_case
   (e.g. uzumaki_naruto, hatake_kakashi, konohagakure, rasengan).
   Skip generic terms ("ninja", "village") and unnamed background chars.

6. NEVER GUESS. If the chunk is too vague to tag, output arc="unknown",
   year_min=null, year_max=null, entities_mentioned=[].

OUTPUT SCHEMA :
{
  "chunk_id": "<echo of input chunk_id>",
  "arc": "<one of the enum>",
  "year_min": <int|null>,
  "year_max": <int|null>,
  "tier": "<one of the tier enum>",
  "entities_mentioned": ["<id1>", "<id2>", ...],
  "confidence": "high|medium|low",
  "source_quote": "<short verbatim excerpt that justifies the tagging>"
}
"""


# ---- Loaders -----------------------------------------------------------

def load_chunks() -> list[dict]:
    """Charge les ~16k chunks via chunk_all(canon).

    Retourne une liste de dicts {chunk_id, text, type, source_id,
    canonicity, metadata} dans l'ordre deterministe de chunk_all.
    """
    from shinobi.canon.loader import load_canon
    from shinobi.rag.chunker import chunk_all
    canon = load_canon()
    chunks = chunk_all(canon)
    out: list[dict] = []
    for c in chunks:
        out.append({
            "chunk_id": c.id,
            "text": c.text,
            "type": c.type.value,
            "source_id": c.source_id,
            "canonicity": c.canonicity,
            "section": (c.metadata or {}).get("section", ""),
        })
    return out


# ---- JSONL building ----------------------------------------------------

def build_user_message(chunk: dict) -> str:
    return (
        f"chunk_id: {chunk['chunk_id']}\n"
        f"chunk_type: {chunk.get('type', '')}\n"
        f"source_id: {chunk.get('source_id', '')}\n"
        f"section: {chunk.get('section', '')}\n"
        f"canonicity: {chunk.get('canonicity', '')}\n\n"
        f"--- CHUNK TEXT ---\n{chunk['text']}\n--- END CHUNK ---"
    )


def build_jsonl(chunks: list[dict], model: str, max_tokens: int,
                out_path: Path, *, limit: int | None = None,
                offset: int = 0) -> int:
    """Build le JSONL Groq Batch. Optionnel : --limit pour calibration."""
    n_written = 0
    selected = chunks[offset:]
    if limit is not None:
        selected = selected[:limit]
    with out_path.open("w", encoding="utf-8") as f:
        for chunk in selected:
            cid = chunk["chunk_id"]
            request = {
                "custom_id": cid,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": model,
                    "temperature": TEMPERATURE,
                    "max_completion_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_user_message(chunk)},
                    ],
                    "response_format": {"type": "json_object"},
                },
            }
            f.write(json.dumps(request, ensure_ascii=False) + "\n")
            n_written += 1
    return n_written


# ---- Groq batch helpers (minimal, copies du pattern pass2_batch) -------

def _http_with_retry(fn, *, max_retries: int = 5):
    for attempt in range(1, max_retries + 1):
        try:
            return fn()
        except NET_ERRORS as exc:
            backoff = min(60, 2 ** attempt)
            print(f"  [retry {attempt}/{max_retries}] {type(exc).__name__} ; sleep {backoff}s")
            time.sleep(backoff)
    return None


def cmd_build(args: argparse.Namespace) -> int:
    print("Loading chunks via chunk_all(canon)...")
    chunks = load_chunks()
    print(f"  {len(chunks)} chunks total")
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    suffix = f"_limit{args.limit}" if args.limit else ""
    if args.offset:
        suffix += f"_offset{args.offset}"
    out_path = BATCH_DIR / f"input_{int(time.time())}{suffix}.jsonl"
    n = build_jsonl(
        chunks, args.model, args.max_tokens, out_path,
        limit=args.limit, offset=args.offset,
    )
    # Estimation cout
    pricing = MODEL_PRICING.get(args.model, (0.5, 0.5))
    # Heuristique : ~1200 input tokens + 400 output tokens / chunk
    est_input_tokens = n * 1200
    est_output_tokens = n * 400
    est_cost = (
        est_input_tokens * pricing[0] / 1e6 * 0.5  # batch = 50% off
        + est_output_tokens * pricing[1] / 1e6 * 0.5
    )
    print(f"Built {out_path}")
    print(f"  {n} chunks queued")
    print(f"  estimated cost (Batch API 50% off) : ~${est_cost:.2f}")
    print(f"  next : python scripts/pass5_tag_chunks.py submit")
    return 0


def _upload_with_retry(client: httpx.Client, headers: dict, jsonl_path: Path,
                       *, max_retries: int = 5) -> str:
    """Upload du JSONL avec retry exponentiel sur 10053 / ReadError /
    ConnectError. Re-ouvre le fichier a chaque tentative.

    Returns:
        file_id Groq.
    """
    last_err: str | None = None
    for attempt in range(1, max_retries + 1):
        try:
            with jsonl_path.open("rb") as f:
                files = {"file": (jsonl_path.name, f, "application/jsonl")}
                data = {"purpose": "batch"}
                # Timeout long pour les gros uploads (~50 MB)
                r = client.post(
                    "/files", headers=headers, files=files, data=data,
                    timeout=httpx.Timeout(300.0, connect=30.0),
                )
                r.raise_for_status()
                return r.json()["id"]
        except NET_ERRORS as exc:
            last_err = f"{type(exc).__name__}: {exc!r}"
            backoff = min(120, 2 ** attempt + 5)
            print(f"  [retry {attempt}/{max_retries}] upload {last_err}; "
                  f"sleep {backoff}s")
            time.sleep(backoff)
    raise RuntimeError(f"Upload failed after {max_retries} retries: {last_err}")


def cmd_submit(args: argparse.Namespace) -> int:
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("!!! GROQ_API_KEY not set"); return 2
    headers = {"Authorization": f"Bearer {api_key}"}
    inputs = sorted(BATCH_DIR.glob("input_*.jsonl"))
    if not inputs:
        print("!!! No input JSONL found. Run `build` first."); return 2
    jsonl_path = inputs[-1]
    size_mb = jsonl_path.stat().st_size / 1_048_576
    print(f"Uploading {jsonl_path.name} ({size_mb:.1f} MB)")
    with httpx.Client(base_url=GROQ_BASE_URL, timeout=REQUEST_TIMEOUT) as client:
        file_id = _upload_with_retry(client, headers, jsonl_path)
        print(f"  file_id={file_id}")
        r = client.post("/batches", headers=headers, json={
            "input_file_id": file_id,
            "endpoint": "/v1/chat/completions",
            "completion_window": "24h",
        })
        r.raise_for_status()
        batch_id = r.json()["id"]
    print(f"Batch submitted : {batch_id}")
    print(f"Next : python scripts/pass5_tag_chunks.py poll {batch_id}")
    return 0


def cmd_poll(args: argparse.Namespace) -> int:
    api_key = os.environ.get("GROQ_API_KEY")
    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(base_url=GROQ_BASE_URL, timeout=REQUEST_TIMEOUT) as client:
        while True:
            r = _http_with_retry(lambda: client.get(f"/batches/{args.batch_id}", headers=headers))
            if r is None:
                return 2
            r.raise_for_status()
            data = r.json()
            status = data["status"]
            counts = data.get("request_counts", {})
            print(f"[{time.strftime('%H:%M:%S')}] status={status} counts={counts}")
            if status in {"completed", "failed", "expired", "cancelled"}:
                break
            time.sleep(30)
        if status != "completed":
            print(f"!!! Batch ended with status={status}"); return 3
        out_file_id = data["output_file_id"]
        r = _http_with_retry(lambda: client.get(f"/files/{out_file_id}/content", headers=headers))
        if r is None:
            return 2
        r.raise_for_status()
        out_path = BATCH_DIR / f"output_{args.batch_id}.jsonl"
        out_path.write_bytes(r.content)
        print(f"Wrote {out_path}")
    return cmd_parse(args)


def _safe_filename(cid: str) -> str:
    """Convertit un chunk_id (avec ':') en filename Windows-safe."""
    return cid.replace(":", "__").replace("/", "_").replace("\\", "_")


def cmd_parse(args: argparse.Namespace) -> int:
    out_path = BATCH_DIR / f"output_{args.batch_id}.jsonl"
    if not out_path.exists():
        print(f"!!! {out_path} not found"); return 2
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    n_ok = n_err = 0
    for line in out_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        cid = rec.get("custom_id")
        body = (rec.get("response") or {}).get("body") or {}
        choices = body.get("choices") or []
        if not choices:
            n_err += 1
            continue
        content = choices[0]["message"]["content"]
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            n_err += 1
            continue
        # Garde le chunk_id original dans le contenu, mais sanitize le filename
        if cid and "chunk_id" not in data:
            data["chunk_id"] = cid
        (OUTPUT_DIR / f"{_safe_filename(cid)}.json").write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        n_ok += 1
    print(f"Parsed : {n_ok} OK, {n_err} errors")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pb = sub.add_parser("build", help="Build batch JSONL")
    pb.add_argument("--limit", type=int, default=None,
                    help="Limite le nombre de chunks (calibration). None = tous.")
    pb.add_argument("--offset", type=int, default=0,
                    help="Skip les N premiers chunks (utile post-calibration).")
    pb.add_argument("--model", default=GROQ_DEFAULT_MODEL)
    pb.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    pb.set_defaults(fn=cmd_build)

    ps = sub.add_parser("submit", help="Upload JSONL + create batch")
    ps.set_defaults(fn=cmd_submit)

    pp = sub.add_parser("poll", help="Poll batch + parse output")
    pp.add_argument("batch_id")
    pp.set_defaults(fn=cmd_poll)

    pp2 = sub.add_parser("parse", help="Parse local output JSONL only")
    pp2.add_argument("batch_id")
    pp2.set_defaults(fn=cmd_parse)

    args = p.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
