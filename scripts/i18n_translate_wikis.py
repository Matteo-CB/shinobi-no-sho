"""Phase i18n.6.A : traduit les 3 sections wiki des 100 chars top vers 7 langues.

Source : `data/canonical/characters.json` (sections wiki en EN, scraped from EN wiki)
Cibles : fr, es, ja, zh, ko, pt-BR, de (7 langues, EN reste source)
Selection : `data/i18n/wiki/_top100.json` produit par i18n_select_top100.py
Output : `data/i18n/wiki/<lang>/<canon_id>.json` x 100 chars x 7 langs = 700 fichiers

Strategie :
- Anthropic Batch API : 50% de discount vs sync, async ~1h turnaround
- 1 requete par (char, lang) = 700 requetes au total
- Chaque requete traduit les 3 sections (Background, Personality, Abilities)
  d'un char vers une langue, en JSON

Cost estime : ~$13 via Batch API ($26 via sync). Budget Phase 5+6 ~ $16 / $25.

Usage :
    python scripts/i18n_translate_wikis.py --dry-run                 # estime cout
    python scripts/i18n_translate_wikis.py --submit                  # cree batch + sauve batch_ids
    python scripts/i18n_translate_wikis.py --poll                    # check status
    python scripts/i18n_translate_wikis.py --collect                 # download + write files
    python scripts/i18n_translate_wikis.py --execute-sync --workers 4  # sync mode plus rapide

Configuration : .env doit contenir API_CLAUDE_KEY=sk-ant-...
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[!] python-dotenv non installe", file=sys.stderr)

try:
    import anthropic
except ImportError:
    print("[X] anthropic SDK manquant", file=sys.stderr)
    sys.exit(2)


ROOT = Path(__file__).resolve().parent.parent
CANONICAL = ROOT / "data" / "canonical"
WIKI_DIR = ROOT / "data" / "i18n" / "wiki"
TOP100_PATH = WIKI_DIR / "_top100.json"
GLOSSARY_PATH = ROOT / "data" / "i18n" / "glossary.json"
BATCH_STATE_PATH = WIKI_DIR / "_batch_state.json"
REPORT_PATH = ROOT / "research" / "i18n-wiki-translation-report.md"

WIKI_SECTIONS = ["Background", "Personality", "Abilities"]
# Cap par defaut. Surchargeable via --section-cap.
SECTION_CHAR_CAP = 2500
TARGET_LANGS = ["fr", "es", "ja", "zh", "ko", "pt-BR", "de"]
LANG_NAMES = {
    "fr": "French",
    "es": "Spanish (Spain)",
    "ja": "Japanese",
    "zh": "Mandarin Chinese (Simplified)",
    "ko": "Korean",
    "pt-BR": "Brazilian Portuguese",
    "de": "German",
}

MODELS_PRICING = {
    # (input $/M, output $/M)
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-4-5": (1.0, 5.0),  # alias
}
MODEL_ID = "claude-sonnet-4-6"  # default, overridable via --model
PRICE_BATCH_DISCOUNT = 0.5
MAX_OUTPUT_TOKENS = 8192

SYSTEM_PROMPT_TEMPLATE = """You are a professional translator specializing in the Naruto universe wiki content.

Translate the wiki sections (Background, Personality, Abilities) of a Naruto character from English to {target_language_name}.

CRITICAL RULES:
1. Output VALID JSON: an object with exactly these 3 keys: "Background", "Personality", "Abilities". Each value is the translated section as a STRING.
2. PRESERVE these terms in original romaji form (do NOT translate them, keep verbatim case-insensitive): {glossary_terms}
3. Wikitext markup like `[[link|alias]]`, `<ref>...</ref>`, `{{template}}`, `''italic''`, `'''bold'''`, image markup `[[File:...]]`, line breaks: keep INTACT.
4. Character / clan / village / technique proper nouns stay in romaji (Sasuke, Uchiha, Konohagakure, Rasengan).
5. Maintain narrative tone consistent with a serious encyclopedia entry.
6. Output ONLY the translated JSON object, no preamble, no postscript, no markdown fences.

JSON ESCAPING (mandatory):
- Newlines inside string values MUST be escaped as \\n.
- ASCII double-quote (\") inside a value MUST be escaped as \\\".
- Backslashes must be escaped as \\\\.

If a section is empty in source, return an empty string for that key.

Source language: English. Target: {target_language_name}.
"""


@dataclass
class TaskResult:
    char_id: str
    lang: str
    success: bool
    sections_done: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    issues: list[str] = field(default_factory=list)
    error: str | None = None


def load_glossary() -> tuple[list[str], dict[str, list[str]]]:
    data = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    terms: list[str] = []
    cats: dict[str, list[str]] = {}
    for category, items in data.items():
        if category.startswith("_"):
            continue
        if isinstance(items, list):
            terms.extend(items)
            cats[category] = items
    return sorted(set(terms), key=lambda s: (-len(s), s.lower())), cats


def load_top100() -> dict[str, dict[str, Any]]:
    if not TOP100_PATH.exists():
        raise FileNotFoundError(
            f"Selection top-100 absente : {TOP100_PATH}. "
            f"Execute scripts/i18n_select_top100.py d'abord."
        )
    data: dict[str, dict[str, Any]] = json.loads(TOP100_PATH.read_text(encoding="utf-8"))
    return data


def _truncate_at_sentence(text: str, cap: int) -> str:
    """Tronque a `cap` chars en preferant une frontiere de phrase."""
    if len(text) <= cap:
        return text
    # Cherche le dernier '. ' avant cap
    cut = text[:cap]
    last_period = cut.rfind(". ")
    if last_period >= cap * 0.7:  # acceptable si > 70% du cap
        return cut[:last_period + 1]
    return cut.rstrip() + "..."


def load_char_wiki(char_id: str, all_chars: dict[str, dict[str, Any]]) -> dict[str, str]:
    char = all_chars.get(char_id)
    if char is None:
        raise KeyError(f"Char {char_id} introuvable")
    ws = char.get("wiki_sections", {}) or {}
    return {
        section: _truncate_at_sentence(str(ws.get(section, "") or ""), SECTION_CHAR_CAP)
        for section in WIKI_SECTIONS
    }


def load_all_characters() -> dict[str, dict[str, Any]]:
    data = json.loads((CANONICAL / "characters.json").read_text(encoding="utf-8"))
    return {c["id"]: c for c in data}


def existing_target_complete(char_id: str, lang: str) -> bool:
    path = WIKI_DIR / lang / f"{char_id}.json"
    if not path.exists():
        return False
    try:
        data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return all(s in data for s in WIKI_SECTIONS)
    except json.JSONDecodeError:
        return False


def build_user_prompt(sections: dict[str, str], target_lang: str, glossary_block: str) -> str:
    return (
        f"Glossary (NEVER translate these — keep romaji exactly):\n{glossary_block}\n\n"
        f"Source character wiki sections to translate (English -> {LANG_NAMES[target_lang]}). "
        f"Output a JSON object with the 3 keys 'Background', 'Personality', 'Abilities', each "
        f"mapped to the translated section as a string.\n\n"
        f"Source JSON:\n{json.dumps(sections, ensure_ascii=False, indent=2)}\n\n"
        f"Output ONLY the JSON object."
    )


def parse_translated_json(raw: str) -> dict[str, str]:
    s = raw.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("Aucun objet JSON trouve")
    parsed: dict[str, str] = json.loads(s[start:end + 1])
    return parsed


def validate_translation(translated: dict[str, str], source: dict[str, str], glossary: list[str]) -> list[str]:
    issues: list[str] = []
    for section in WIKI_SECTIONS:
        if section not in translated:
            issues.append(f"section manquante : {section}")
        elif source[section] and not translated[section]:
            issues.append(f"section {section} : vide en target alors que source non-vide")
    # Glossary preservation: if a glossary term appears in any source section, check target
    src_blob = " ".join(source.values()).lower()
    tgt_blob = " ".join(translated.values()).lower()
    for g in glossary[:30]:  # check top-30 glossary terms (most important : techniques + ranks)
        if len(g) <= 3:
            continue  # skip short terms (false-positive risk)
        pattern = r'(?<![a-z])' + re.escape(g.lower()) + r'(?![a-z])'
        if re.search(pattern, src_blob) and not re.search(pattern, tgt_blob):
            issues.append(f"glossary term '{g}' perdu")
    return issues


def _model_prices() -> tuple[float, float]:
    return MODELS_PRICING[MODEL_ID]


def estimate_cost_sync(top100: dict[str, dict[str, Any]], all_chars: dict[str, dict[str, Any]]) -> tuple[int, int, float, float]:
    """Returns (in_tok, out_tok, sync_cost, batch_cost) estimates."""
    total_chars = 0
    for cid in top100:
        sections = load_char_wiki(cid, all_chars)
        total_chars += sum(len(v) for v in sections.values())
    src_tokens = total_chars // 4
    sys_tokens_per_call = 600
    n_calls_per_lang = len(top100)
    in_per_lang = src_tokens + sys_tokens_per_call * n_calls_per_lang
    out_per_lang = src_tokens
    in_total = in_per_lang * len(TARGET_LANGS)
    out_total = out_per_lang * len(TARGET_LANGS)
    p_in, p_out = _model_prices()
    sync = (in_total / 1e6) * p_in + (out_total / 1e6) * p_out
    batch = sync * PRICE_BATCH_DISCOUNT
    return (in_total, out_total, sync, batch)


def call_anthropic_sync(
    client: anthropic.Anthropic, system: str, user: str, *, max_retries: int = 3,
) -> tuple[str, int, int]:
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = client.messages.create(
                model=MODEL_ID,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            text_parts = [b.text for b in resp.content if hasattr(b, "text")]
            return ("".join(text_parts), resp.usage.input_tokens, resp.usage.output_tokens)
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            last_exc = exc
            sleep_s = 5 * (2 ** attempt)
            print(f"  retry {attempt + 1}: {type(exc).__name__} sleep {sleep_s}s", flush=True)
            time.sleep(sleep_s)
    raise RuntimeError(f"sync call failed: {last_exc}")


def call_with_repair(
    client: anthropic.Anthropic, system: str, user: str, char_id: str, lang: str,
    *, max_repair: int = 2,
) -> tuple[dict[str, str], int, int]:
    """Call + JSON repair loop : si parse fail, re-prompt avec l'erreur."""
    text, in_tok, out_tok = call_anthropic_sync(client, system, user)
    last_err: Exception | None = None
    for attempt in range(max_repair + 1):
        try:
            return parse_translated_json(text), in_tok, out_tok
        except (ValueError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt >= max_repair:
                raise
            print(
                f"  [{char_id}/{lang}] JSON parse fail ({exc}). Repair {attempt + 1}/{max_repair}",
                flush=True,
            )
            repair_user = (
                f"Your previous JSON output failed to parse:\n  {exc}\n\n"
                f"COMMON CAUSE: ASCII double-quote characters (\") inside string values must be "
                f"escaped as \\\". Newlines as \\n. Backslashes as \\\\.\n"
                f"For Chinese: PREFER native quotes 「 」 『 』 instead of \" \" — they don't need escaping.\n\n"
                f"Re-emit ONLY the corrected JSON, same 3 keys, same translated content but properly escaped:\n\n"
                f"--- BROKEN OUTPUT ---\n{text}\n--- END ---"
            )
            text2, in2, out2 = call_anthropic_sync(client, system, repair_user)
            in_tok += in2
            out_tok += out2
            text = text2
    raise RuntimeError(f"JSON repair exhausted: {last_err}")


def process_one_sync(
    char_id: str,
    lang: str,
    sections: dict[str, str],
    glossary: list[str],
    glossary_block: str,
    api_key: str,
    *,
    force: bool = False,
) -> TaskResult:
    result = TaskResult(char_id=char_id, lang=lang, success=False)
    try:
        if not force and existing_target_complete(char_id, lang):
            result.success = True
            result.sections_done = 3
            result.issues.append("skipped: already complete")
            return result
        client = anthropic.Anthropic(api_key=api_key)
        system = SYSTEM_PROMPT_TEMPLATE.format(
            target_language_name=LANG_NAMES[lang], glossary_terms=", ".join(glossary),
        )
        user = build_user_prompt(sections, lang, glossary_block)
        # Skip if all sections empty
        if not any(v for v in sections.values()):
            result.issues.append("skipped: source sections all empty")
            result.success = True
            return result
        try:
            parsed, in_tok, out_tok = call_with_repair(client, system, user, char_id, lang)
        except (ValueError, json.JSONDecodeError, RuntimeError) as exc:
            result.error = f"parse fail after repair: {exc}"
            return result
        result.input_tokens = in_tok
        result.output_tokens = out_tok
        p_in, p_out = _model_prices()
        result.cost_usd = (in_tok / 1e6) * p_in + (out_tok / 1e6) * p_out

        # Write file
        out_dir = WIKI_DIR / lang
        out_dir.mkdir(parents=True, exist_ok=True)
        out_data = {
            "_schema": "i18n_wiki_v1",
            "_language": lang,
            "_char_id": char_id,
            "_translated_at": datetime.now(UTC).isoformat(),
            **{s: parsed.get(s, sections.get(s, "")) for s in WIKI_SECTIONS},
        }
        (out_dir / f"{char_id}.json").write_text(
            json.dumps(out_data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result.sections_done = sum(1 for s in WIKI_SECTIONS if parsed.get(s))
        result.issues = validate_translation(
            {s: parsed.get(s, "") for s in WIKI_SECTIONS}, sections, glossary,
        )
        result.success = True
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    return result


def cmd_dry_run(top100: dict, all_chars: dict) -> int:
    in_tok, out_tok, sync, batch = estimate_cost_sync(top100, all_chars)
    print(f"Top-100 chars : {len(top100)}")
    print(f"Cibles : {TARGET_LANGS}")
    print(f"Tokens estime : {in_tok:,} in + {out_tok:,} out")
    print(f"Cost SYNC : ${sync:.2f}")
    print(f"Cost BATCH (50% off) : ${batch:.2f}")
    return 0


def cmd_execute_sync(
    top100: dict, all_chars: dict, glossary: list[str], glossary_cats: dict[str, list[str]],
    api_key: str, *, workers: int = 3, force: bool = False, langs: list[str] | None = None,
) -> int:
    glossary_block = "\n".join(f"- {g}" for g in glossary)
    targets = langs or TARGET_LANGS
    tasks: list[tuple[str, str, dict[str, str]]] = []
    for cid in top100:
        sections = load_char_wiki(cid, all_chars)
        for lang in targets:
            tasks.append((cid, lang, sections))
    print(f"Tasks total : {len(tasks)} ({len(top100)} chars × {len(targets)} langs)")

    results: list[TaskResult] = []
    completed = 0
    started_at = time.time()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(
                process_one_sync, cid, lang, sections, glossary, glossary_block, api_key, force=force,
            ): (cid, lang)
            for cid, lang, sections in tasks
        }
        for fut in as_completed(futures):
            r = fut.result()
            completed += 1
            tag = "OK" if r.success and not r.error else "FAIL"
            err_part = f" ERR={r.error[:60]}" if r.error else ""
            elapsed = time.time() - started_at
            eta = elapsed / completed * (len(tasks) - completed)
            print(
                f"[{completed}/{len(tasks)}] [{tag}] {r.char_id}/{r.lang}: "
                f"{r.input_tokens:,}+{r.output_tokens:,} tok, ${r.cost_usd:.3f}, "
                f"{len(r.issues)} issues, ETA {int(eta)}s{err_part}",
                flush=True,
            )
            results.append(r)

    write_report(results)
    failures = [r for r in results if not r.success or r.error]
    return 0 if not failures else 4


def write_report(results: list[TaskResult]) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    by_lang: dict[str, list[TaskResult]] = {}
    for r in results:
        by_lang.setdefault(r.lang, []).append(r)

    total_in = sum(r.input_tokens for r in results)
    total_out = sum(r.output_tokens for r in results)
    total_cost = sum(r.cost_usd for r in results)
    successes = sum(1 for r in results if r.success and not r.error)

    lines: list[str] = []
    lines.append("# Phase i18n.6.A — Rapport de traduction wiki sections (top-100)")
    lines.append("")
    lines.append(f"Date : {datetime.now(UTC).isoformat()}")
    lines.append(f"Modele : `{MODEL_ID}` (sync)")
    lines.append("")
    lines.append("## Resume")
    lines.append("")
    lines.append(f"- Tasks : {len(results)} ({successes} succes)")
    lines.append(f"- Tokens input : {total_in:,}")
    lines.append(f"- Tokens output : {total_out:,}")
    lines.append(f"- **Cout total : ${total_cost:.2f}**")
    lines.append("")
    lines.append("## Detail par langue")
    lines.append("")
    lines.append("| Lang | Tasks | OK | In tok | Out tok | Cost |")
    lines.append("|------|-------|----|---------|---------|------|")
    for lang in sorted(by_lang):
        rs = by_lang[lang]
        ok = sum(1 for r in rs if r.success and not r.error)
        in_t = sum(r.input_tokens for r in rs)
        out_t = sum(r.output_tokens for r in rs)
        cost = sum(r.cost_usd for r in rs)
        lines.append(f"| {lang} | {len(rs)} | {ok} | {in_t:,} | {out_t:,} | ${cost:.2f} |")
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    global MODEL_ID, SECTION_CHAR_CAP
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute-sync", action="store_true", help="Use sync API (faster, 2x cost)")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--langs", default=",".join(TARGET_LANGS), help="Comma-separated target langs")
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        choices=list(MODELS_PRICING.keys()),
        help="Anthropic model id (default: sonnet-4-6, alternative: haiku-4-5 for ~3x cheaper)",
    )
    parser.add_argument(
        "--section-cap",
        type=int,
        default=SECTION_CHAR_CAP,
        help=f"Max chars per wiki section (default: {SECTION_CHAR_CAP})",
    )
    args = parser.parse_args()

    MODEL_ID = args.model
    SECTION_CHAR_CAP = args.section_cap

    top100 = load_top100()
    all_chars = load_all_characters()
    glossary, glossary_cats = load_glossary()

    if args.dry_run:
        return cmd_dry_run(top100, all_chars)
    if args.execute_sync:
        api_key = os.environ.get("API_CLAUDE_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("[X] API_CLAUDE_KEY absente", file=sys.stderr)
            return 3
        langs = [s.strip() for s in args.langs.split(",") if s.strip()]
        return cmd_execute_sync(
            top100, all_chars, glossary, glossary_cats, api_key,
            workers=args.workers, force=args.force, langs=langs,
        )
    print("[!] passe --dry-run ou --execute-sync", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
