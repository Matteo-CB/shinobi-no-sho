"""Pass 2 via Groq Batch API. Soumet, poll, recupere et normalise.

Subcommands:
  submit  : genere le JSONL, upload, soumet le batch. Affiche le batch_id.
  poll    : poll un batch jusqu'a completion, telecharge l'output JSONL,
            normalise, valide les quotes, ecrit un fichier par perso.
  status  : query l'etat d'un batch sans poll continu.
  cancel  : annule un batch (safety net).

Avantage Groq Batch API vs appels en boucle :
- 50% off sur les prix
- Concurrence geree cote serveur (plus de bug json_validate_failed sous
  parallelisme client-side)
- Completion window 24h max, en pratique quelques minutes a 1h selon la file

Usage:
    export GROQ_API_KEY=gsk_...
    python scripts/pass2_batch.py submit --ids-from data/canonical/_pass2_targets_full.txt
    python scripts/pass2_batch.py status <batch_id>
    python scripts/pass2_batch.py poll <batch_id> --output-dir data/canonical/_pass2_output
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pass2_extract_canon import (
    GROQ_BASE_URL,
    GROQ_DEFAULT_MODEL,
    MODEL_PRICING,
    SYSTEM_PROMPT,
    TEMPERATURE,
    _count_fields,
    assemble_source_text,
    build_user_message,
    validate_quotes_inline,
)
from pass2_normalize import load_canon_context, normalize_extraction

CHARACTERS_PATH = ROOT / "data" / "canonical" / "characters.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "canonical" / "_pass2_output"
BATCH_DIR = ROOT / "data" / "canonical" / "_pass2_batches"

# Network-level transient errors observed on Windows during long sessions.
NET_ERRORS = (
    httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadError,
    httpx.ReadTimeout, httpx.WriteError, httpx.RemoteProtocolError,
    httpx.PoolTimeout,
)


def _http_get_with_retry(client: httpx.Client, path: str, headers: dict, *,
                         max_retries: int = 5) -> httpx.Response | None:
    """GET with exponential backoff on transient network errors."""
    last_err: str | None = None
    for attempt in range(1, max_retries + 1):
        try:
            return client.get(path, headers=headers)
        except NET_ERRORS as exc:
            last_err = f"{type(exc).__name__}: {exc!r}"
            backoff = min(60, 2 ** attempt)
            print(f"  [retry {attempt}/{max_retries}] {last_err} ; sleep {backoff}s")
            time.sleep(backoff)
    print(f"!!! GET {path} failed after {max_retries} retries: {last_err}")
    return None


def load_target_ids(ids_from: Path) -> list[str]:
    target_ids: list[str] = []
    for line in ids_from.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        target_ids.append(line)
    return target_ids


def build_jsonl(
    target_ids: list[str],
    characters: list[dict],
    model: str,
    max_tokens: int,
    out_path: Path,
) -> tuple[int, int]:
    """Returns (n_written, n_skipped_unknown)."""
    char_index = {c["id"]: c for c in characters}
    n_written = 0
    n_skipped = 0
    with out_path.open("w", encoding="utf-8") as f:
        for cid in target_ids:
            char = char_index.get(cid)
            if char is None:
                n_skipped += 1
                continue
            user_msg = build_user_message(char)
            entry = {
                "custom_id": cid,
                "method": "POST",
                "url": "/v1/chat/completions",
                "body": {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_msg},
                    ],
                    "temperature": TEMPERATURE,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                },
            }
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            n_written += 1
    return n_written, n_skipped


def cmd_submit(args: argparse.Namespace, api_key: str) -> int:
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    target_ids = load_target_ids(args.ids_from)
    print(f"Loaded {len(target_ids)} target ids from {args.ids_from}")

    characters = json.loads(CHARACTERS_PATH.read_text(encoding="utf-8"))

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    jsonl_path = BATCH_DIR / f"input_{timestamp}.jsonl"
    n_written, n_skipped = build_jsonl(
        target_ids, characters, args.model, args.max_tokens, jsonl_path,
    )
    print(f"Wrote {jsonl_path.relative_to(ROOT)}")
    print(f"  Entries: {n_written}, skipped (unknown ids): {n_skipped}")
    if n_written == 0:
        print("Nothing to submit.")
        return 1

    file_size = jsonl_path.stat().st_size
    print(f"  File size: {file_size / 1024 / 1024:.1f} MB")

    print("Uploading file to Groq...")
    upload_timeout = httpx.Timeout(600.0, connect=30.0)
    file_id: str | None = None
    with httpx.Client(base_url=GROQ_BASE_URL, timeout=upload_timeout) as client:
        last_err: str | None = None
        for attempt in range(1, 4):
            try:
                with jsonl_path.open("rb") as f:
                    r = client.post(
                        "/files",
                        headers={"Authorization": f"Bearer {api_key}"},
                        files={"file": (jsonl_path.name, f, "application/jsonl")},
                        data={"purpose": "batch"},
                    )
            except (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError,
                    httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as exc:
                last_err = f"{type(exc).__name__}: {exc!r}"
                print(f"  upload attempt {attempt}/3 failed: {last_err}")
                if attempt < 3:
                    backoff = 2 ** attempt
                    print(f"  retrying in {backoff}s...")
                    time.sleep(backoff)
                    continue
                print(f"!!! Upload failed after 3 attempts: {last_err}")
                return 1
            if r.status_code not in (200, 201):
                print(f"  upload attempt {attempt}/3 HTTP {r.status_code}: {r.text[:300]}")
                if attempt < 3 and r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(2 ** attempt)
                    continue
                return 1
            file_obj = r.json()
            file_id = file_obj["id"]
            print(f"  file_id: {file_id}")
            break

        if file_id is None:
            return 1

        print("Submitting batch...")
        r = client.post(
            "/batches",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "input_file_id": file_id,
                "endpoint": "/v1/chat/completions",
                "completion_window": args.completion_window,
            },
        )
        if r.status_code not in (200, 201):
            print(f"!!! Submit failed: {r.status_code} {r.text[:500]}")
            return 1
        batch_obj = r.json()

    batch_id = batch_obj["id"]
    print()
    print("=" * 60)
    print(f"BATCH SUBMITTED: {batch_id}")
    print(f"  Status            : {batch_obj.get('status')}")
    print(f"  Endpoint          : {batch_obj.get('endpoint')}")
    print(f"  Completion window : {batch_obj.get('completion_window')}")
    counts = batch_obj.get("request_counts", {}) or {}
    print(f"  Request counts    : total={counts.get('total', n_written)}")
    print()

    # Cost estimate
    pricing = MODEL_PRICING.get(args.model, (0.59, 0.79))
    # Approximation : taille input file en chars / 4 = tokens input
    input_tokens_est = file_size // 4
    output_tokens_est = n_written * 1500
    cost_input = input_tokens_est * pricing[0] / 1_000_000
    cost_output = output_tokens_est * pricing[1] / 1_000_000
    cost_nominal = cost_input + cost_output
    cost_batch = cost_nominal * 0.5
    print("Cost estimate (with Batch API 50% discount applied):")
    print(f"  Input  ({input_tokens_est:,} tokens × ${pricing[0]}/M)  : ${cost_input:.3f} nominal")
    print(f"  Output ({output_tokens_est:,} tokens × ${pricing[1]}/M) : ${cost_output:.3f} nominal")
    print(f"  Total nominal : ${cost_nominal:.3f}")
    print(f"  Total batch   : ${cost_batch:.3f}  <-- expected charge")
    print()

    # Persist batch metadata for later poll
    meta_path = BATCH_DIR / f"batch_{batch_id}.json"
    meta_path.write_text(
        json.dumps({
            "batch_id": batch_id,
            "submitted_at": timestamp,
            "model": args.model,
            "n_entries": n_written,
            "input_jsonl": str(jsonl_path.relative_to(ROOT)),
            "input_file_id": file_id,
            "completion_window": args.completion_window,
            "submit_response": batch_obj,
        }, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Metadata saved: {meta_path.relative_to(ROOT)}")
    print()
    print("Next step:")
    print(f"  python scripts/pass2_batch.py poll {batch_id}")
    return 0


def cmd_status(args: argparse.Namespace, api_key: str) -> int:
    with httpx.Client(base_url=GROQ_BASE_URL, timeout=60.0) as client:
        r = _http_get_with_retry(
            client, f"/batches/{args.batch_id}",
            {"Authorization": f"Bearer {api_key}"},
        )
    if r is None or r.status_code != 200:
        print(f"!!! Status failed: {r.status_code if r else 'no_response'}")
        if r is not None:
            print(r.text[:500])
        return 1
    b = r.json()
    print(f"Batch {b['id']}:")
    for k in ("status", "endpoint", "completion_window", "created_at",
              "in_progress_at", "completed_at", "failed_at", "expired_at",
              "cancelling_at", "cancelled_at", "input_file_id",
              "output_file_id", "error_file_id"):
        if b.get(k) is not None:
            print(f"  {k:20s}: {b[k]}")
    counts = b.get("request_counts", {}) or {}
    print(f"  request_counts      : total={counts.get('total', 0)} "
          f"completed={counts.get('completed', 0)} failed={counts.get('failed', 0)}")
    return 0


def cmd_parse(args: argparse.Namespace, api_key: str) -> int:
    """Parse a local output JSONL (re-do normalisation/validation without polling)."""
    args.output_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = args.jsonl
    if not jsonl_path.exists():
        print(f"!!! JSONL not found: {jsonl_path}")
        return 1

    print(f"Parsing {jsonl_path}")
    print("Loading canon context for normalization...")
    canon_ctx = load_canon_context()
    characters = json.loads(CHARACTERS_PATH.read_text(encoding="utf-8"))
    char_index = {c["id"]: c for c in characters}

    n_total = 0
    n_ok = 0
    n_failed = 0
    quotes_total = quotes_exact = quotes_near = quotes_miss = 0
    fields_filled = fields_total = 0
    norm_changes = norm_flags = 0
    failures: list[tuple[str, str]] = []

    with jsonl_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            n_total += 1
            entry = json.loads(line)
            cid = entry["custom_id"]
            if entry.get("error"):
                n_failed += 1
                failures.append((cid, str(entry["error"])[:200]))
                continue
            try:
                body = entry["response"]["body"]
                content = body["choices"][0]["message"]["content"] or ""
                parsed = json.loads(content)
            except (KeyError, IndexError, json.JSONDecodeError) as exc:
                n_failed += 1
                failures.append((cid, f"{type(exc).__name__}: {exc}"))
                continue

            normalized, report = normalize_extraction(parsed, canon_ctx)
            norm_changes += report.total_normalized
            norm_flags += len(report.flags)

            (args.output_dir / f"{cid}.json").write_text(
                json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            if report.flags:
                (args.output_dir / f"{cid}.flags.json").write_text(
                    json.dumps({"char_id": cid, "flags": report.flags},
                               indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8",
                )

            char = char_index.get(cid)
            if char is not None:
                src = assemble_source_text(char)
                t, e, ne, m = validate_quotes_inline(normalized, src)
                quotes_total += t
                quotes_exact += e
                quotes_near += ne
                quotes_miss += m
                _, n_filled = _count_fields(normalized)
                fields_filled += n_filled
                fields_total += 21
            n_ok += 1

    print()
    print("=" * 60)
    print(f"DONE. Total: {n_total}, OK: {n_ok}, Failed: {n_failed}")
    if fields_total:
        print(f"  Fields filled rate : {fields_filled}/{fields_total} "
              f"({100*fields_filled/fields_total:.1f}%)")
    if quotes_total:
        print(f"  Quotes total : {quotes_total}")
        print(f"    exact : {quotes_exact} ({100*quotes_exact/quotes_total:.1f}%)")
        print(f"    near  : {quotes_near}  ({100*quotes_near/quotes_total:.1f}%)")
        print(f"    miss  : {quotes_miss}  ({100*quotes_miss/quotes_total:.1f}%)")
    print(f"  Norm changes : {norm_changes}, non_canonical_flags : {norm_flags}")
    if failures:
        print(f"\n  First 10 failures:")
        for cid, err in failures[:10]:
            print(f"    {cid}: {err}")
    return 0


def cmd_cancel(args: argparse.Namespace, api_key: str) -> int:
    with httpx.Client(base_url=GROQ_BASE_URL, timeout=60.0) as client:
        r = client.post(
            f"/batches/{args.batch_id}/cancel",
            headers={"Authorization": f"Bearer {api_key}"},
        )
    if r.status_code not in (200, 201):
        print(f"!!! Cancel failed: {r.status_code} {r.text[:500]}")
        return 1
    print(f"Cancel requested for batch {args.batch_id}")
    return 0


def cmd_poll(args: argparse.Namespace, api_key: str) -> int:
    args.output_dir.mkdir(parents=True, exist_ok=True)

    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(base_url=GROQ_BASE_URL, timeout=300.0) as client:
        while True:
            r = _http_get_with_retry(client, f"/batches/{args.batch_id}", headers)
            if r is None:
                print("!!! Poll failed permanently after retries.")
                return 1
            if r.status_code != 200:
                print(f"!!! Poll failed: {r.status_code} {r.text[:500]}")
                return 1
            b = r.json()
            status = b.get("status")
            counts = b.get("request_counts", {}) or {}
            print(f"  [{time.strftime('%H:%M:%S')}] status={status} "
                  f"completed={counts.get('completed', 0)}/{counts.get('total', 0)} "
                  f"failed={counts.get('failed', 0)}")
            if status in ("completed", "failed", "expired", "cancelled"):
                break
            time.sleep(args.poll_interval)

        if status != "completed":
            print(f"\n!!! Batch did not complete (status={status}).")
            err_id = b.get("error_file_id")
            if err_id:
                print(f"  Downloading error file: {err_id}")
                er = _http_get_with_retry(client, f"/files/{err_id}/content", headers)
                if er is not None:
                    err_path = BATCH_DIR / f"errors_{args.batch_id}.jsonl"
                    err_path.write_text(er.text, encoding="utf-8")
                    print(f"  Saved errors to {err_path.relative_to(ROOT)}")
            return 1

        out_id = b["output_file_id"]
        print(f"Downloading output file: {out_id}")
        r = _http_get_with_retry(client, f"/files/{out_id}/content", headers)
        if r is None or r.status_code != 200:
            print(f"!!! Download failed: {r.status_code if r else 'no_response'}")
            return 1

    # Persist raw output JSONL for archive
    raw_path = BATCH_DIR / f"output_{args.batch_id}.jsonl"
    raw_path.write_text(r.text, encoding="utf-8")
    print(f"Raw output saved: {raw_path.relative_to(ROOT)}")

    # Parse + normalize + validate
    print("Loading canon context for normalization...")
    canon_ctx = load_canon_context()
    characters = json.loads(CHARACTERS_PATH.read_text(encoding="utf-8"))
    char_index = {c["id"]: c for c in characters}

    n_total = 0
    n_ok = 0
    n_failed = 0
    quotes_total = 0
    quotes_exact = 0
    quotes_near = 0
    quotes_miss = 0
    fields_filled = 0
    fields_total = 0
    norm_changes = 0
    norm_flags = 0
    failures: list[tuple[str, str]] = []

    for line in r.text.splitlines():
        if not line.strip():
            continue
        n_total += 1
        entry = json.loads(line)
        cid = entry["custom_id"]
        if entry.get("error"):
            n_failed += 1
            failures.append((cid, str(entry["error"])[:200]))
            continue
        try:
            body = entry["response"]["body"]
            content = body["choices"][0]["message"]["content"] or ""
            parsed = json.loads(content)
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            n_failed += 1
            failures.append((cid, f"{type(exc).__name__}: {exc}"))
            continue

        normalized, report = normalize_extraction(parsed, canon_ctx)
        norm_changes += report.total_normalized
        norm_flags += len(report.flags)

        out_path = args.output_dir / f"{cid}.json"
        out_path.write_text(
            json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        if report.flags:
            flag_path = args.output_dir / f"{cid}.flags.json"
            flag_path.write_text(
                json.dumps({"char_id": cid, "flags": report.flags},
                           indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )

        char = char_index.get(cid)
        if char is not None:
            src = assemble_source_text(char)
            t, e, ne, m = validate_quotes_inline(normalized, src)
            quotes_total += t
            quotes_exact += e
            quotes_near += ne
            quotes_miss += m
            _, n_filled = _count_fields(normalized)
            fields_filled += n_filled
            fields_total += 21

        n_ok += 1

    print()
    print("=" * 60)
    print(f"DONE. Total entries: {n_total}, OK: {n_ok}, Failed: {n_failed}")
    if fields_total:
        print(f"  Fields filled rate : {fields_filled}/{fields_total} "
              f"({100*fields_filled/fields_total:.1f}%)")
    if quotes_total:
        print(f"  Quotes total : {quotes_total}")
        print(f"    exact : {quotes_exact} ({100*quotes_exact/quotes_total:.1f}%)")
        print(f"    near  : {quotes_near}  ({100*quotes_near/quotes_total:.1f}%)")
        print(f"    miss  : {quotes_miss}  ({100*quotes_miss/quotes_total:.1f}%)")
    print(f"  Norm changes : {norm_changes}, non_canonical_flags : {norm_flags}")

    if failures:
        print("\n  First 10 failures:")
        for cid, err in failures[:10]:
            print(f"    {cid}: {err}")
        if len(failures) > 10:
            print(f"    ... +{len(failures) - 10} more")

    print(f"\nOutputs in {args.output_dir.relative_to(ROOT)}/")
    return 0 if n_failed == 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_submit = sub.add_parser("submit", help="Submit a batch")
    p_submit.add_argument("--ids-from", type=Path, required=True)
    p_submit.add_argument("--model", default=GROQ_DEFAULT_MODEL)
    p_submit.add_argument("--max-tokens", type=int, default=4000)
    p_submit.add_argument("--completion-window", default="24h",
                          help="Groq accepts '24h' (only). Default: 24h.")

    p_poll = sub.add_parser("poll", help="Poll a batch until completion + download")
    p_poll.add_argument("batch_id")
    p_poll.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p_poll.add_argument("--poll-interval", type=int, default=30,
                        help="Seconds between status polls. Default: 30.")

    p_status = sub.add_parser("status", help="Get a batch status (one shot)")
    p_status.add_argument("batch_id")

    p_parse = sub.add_parser("parse", help="Re-parse a local output JSONL (no API)")
    p_parse.add_argument("jsonl", type=Path, help="Path to output_*.jsonl")
    p_parse.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)

    p_cancel = sub.add_parser("cancel", help="Cancel a batch")
    p_cancel.add_argument("batch_id")

    args = parser.parse_args()

    # parse subcommand does not need API key
    api_key = os.environ.get("GROQ_API_KEY", "")
    if args.cmd != "parse" and not api_key:
        print("ERROR: GROQ_API_KEY env var is not set.", file=sys.stderr)
        return 1

    if args.cmd == "submit":
        return cmd_submit(args, api_key)
    if args.cmd == "poll":
        return cmd_poll(args, api_key)
    if args.cmd == "status":
        return cmd_status(args, api_key)
    if args.cmd == "parse":
        return cmd_parse(args, api_key)
    if args.cmd == "cancel":
        return cmd_cancel(args, api_key)
    return 1


if __name__ == "__main__":
    sys.exit(main())
