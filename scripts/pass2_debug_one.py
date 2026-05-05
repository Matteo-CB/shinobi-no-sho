"""Diagnostic Pass 2 sur un seul perso : capture full response.

Sert au post-mortem du batch test 50 :
- Reproduit les Type 1 (json_validate_failed) sur les persos failed
- Capture le `failed_generation` Groq complet, le status code, les headers
  (en particulier x-ratelimit-* et x-groq-region)
- Permet de tester avec differents max_tokens pour identifier la threshold
  de truncation

Usage:
    export GROQ_API_KEY=gsk_...
    python scripts/pass2_debug_one.py senju_hashirama
    python scripts/pass2_debug_one.py senju_hashirama --max-tokens 4000
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CHARACTERS_PATH = ROOT / "data" / "canonical" / "characters.json"
OUTPUT_DIR = ROOT / "data" / "canonical" / "_pass2_debug"

# Reuse the constants from the main script (kept duplicated here to avoid
# importing the asyncio scaffolding for a single sync call).
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "openai/gpt-oss-120b"

SECTIONS_PRIORITY = [
    "Background", "Personality", "Abilities",
    "Part I", "Part II", "Blank Period",
    "New Era", "New Era: Part I", "New Era: Part II",
    "Quotes", "Plot Overview", "Legacy",
]

# Same system prompt as pass2_extract_canon.py. Kept inline to keep this
# script standalone.
SYSTEM_PROMPT_PATH = ROOT / "scripts" / "pass2_extract_canon.py"


def get_system_prompt() -> str:
    """Extract SYSTEM_PROMPT from the main script (one source of truth)."""
    src = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    start = src.find('SYSTEM_PROMPT = """\\\n')
    if start < 0:
        raise RuntimeError("SYSTEM_PROMPT marker not found in pass2_extract_canon.py")
    start += len('SYSTEM_PROMPT = """\\\n')
    end = src.find('\n"""', start)
    if end < 0:
        raise RuntimeError("SYSTEM_PROMPT closing marker not found")
    return src[start:end]


def build_user_message(char: dict) -> str:
    sections = char.get("wiki_sections") or {}
    chunks = []
    for s in SECTIONS_PRIORITY:
        if s in sections and sections[s]:
            chunks.append(f"[{s}]\n{sections[s]}")
    body = "\n\n".join(chunks)
    name = char.get("name_romaji", char["id"])
    return (
        f"character_id: {char['id']}\n"
        f"name_romaji: {name}\n\n"
        f"wiki_sections:\n\n{body}\n\n"
        f"Extract facts about {name} following the schema. JSON only."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("char_id", help="character id to debug")
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--no-json-mode", action="store_true",
                        help="Don't pass response_format json_object (free generation)")
    parser.add_argument("--model", default=GROQ_DEFAULT_MODEL,
                        help="Groq model id (e.g. openai/gpt-oss-120b, llama-3.3-70b-versatile)")
    parser.add_argument("--label", default="default",
                        help="Label suffix for output file")
    args = parser.parse_args()

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("ERROR: GROQ_API_KEY not set", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    chars = json.loads(CHARACTERS_PATH.read_text(encoding="utf-8"))
    char = next((c for c in chars if c["id"] == args.char_id), None)
    if char is None:
        print(f"ERROR: char_id '{args.char_id}' not in characters.json", file=sys.stderr)
        return 1

    system_prompt = get_system_prompt()
    user_msg = build_user_message(char)

    payload = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
    }
    if not args.no_json_mode:
        payload["response_format"] = {"type": "json_object"}

    print(f"=== Debugging {args.char_id} ===")
    print(f"  model               : {args.model}")
    print(f"  user_message length : {len(user_msg)} chars")
    print(f"  max_tokens          : {args.max_tokens}")
    print(f"  json_mode           : {not args.no_json_mode}")
    print(f"  temperature         : {args.temperature}")
    print()

    with httpx.Client(base_url=GROQ_BASE_URL, timeout=180.0) as client:
        try:
            r = client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
        except httpx.HTTPError as exc:
            print(f"HTTP error type: {type(exc).__name__}")
            print(f"HTTP error str:  {exc!r}")
            return 1

    print(f"Status: {r.status_code}")
    print()
    print("=== Headers (rate limits + region) ===")
    for h, v in r.headers.items():
        if h.lower().startswith("x-") or h.lower() in ("retry-after",):
            print(f"  {h}: {v}")
    print()

    body_text = r.text
    out_path = OUTPUT_DIR / f"{args.char_id}__{args.label}.json"
    output = {
        "char_id": args.char_id,
        "request_args": vars(args),
        "status_code": r.status_code,
        "headers": dict(r.headers),
        "body": body_text,
    }
    out_path.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"Full response saved to {out_path.relative_to(ROOT)}")
    print()

    print("=== Body preview (first 2000 chars) ===")
    print(body_text[:2000])
    if len(body_text) > 2000:
        print(f"... [{len(body_text) - 2000} more chars]")
    print()

    if r.status_code == 200:
        try:
            data = r.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            print(f"=== usage ===")
            print(f"  prompt_tokens     : {usage.get('prompt_tokens', 'n/a')}")
            print(f"  completion_tokens : {usage.get('completion_tokens', 'n/a')}")
            print(f"  total_tokens      : {usage.get('total_tokens', 'n/a')}")
            print()
            print(f"=== content length : {len(content)} chars ===")
            print(content[:1500])
            if len(content) > 1500:
                print(f"... [{len(content) - 1500} more chars]")
            print()
            try:
                json.loads(content)
                print(">>> JSON parse OK")
            except json.JSONDecodeError as exc:
                print(f">>> JSON parse FAILED: {exc}")
                print(f"    last 300 chars of content: {content[-300:]!r}")
        except (KeyError, IndexError, json.JSONDecodeError) as exc:
            print(f"Cannot parse 200 response: {exc}")
    else:
        try:
            err = r.json()
            failed_gen = err.get("error", {}).get("failed_generation", "")
            print(f"=== failed_generation ({len(failed_gen)} chars) ===")
            if failed_gen:
                print(failed_gen[:2000])
                if len(failed_gen) > 2000:
                    print(f"... [{len(failed_gen) - 2000} more chars]")
            else:
                print("  (empty)")
        except json.JSONDecodeError:
            pass

    return 0


if __name__ == "__main__":
    sys.exit(main())
