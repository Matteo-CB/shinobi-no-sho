"""Phase i18n.5 (suite) : traduit les 6 system prompts LLM vers 7 langues.

Source : data/i18n/prompts/fr/*.txt (FR, source historique)
Cibles : en, es, ja, zh, ko, pt-BR, de (7 langues)
Output : data/i18n/prompts/<lang>/<name>.txt (1 fichier par paire)

6 prompts (cf docs/14_i18n.md L608-615) :
1. narrator.txt (~7 KB : cadre persona narrateur)
2. goal_pathfinder.txt (~1.2 KB : strategiste objectifs)
3. character_interpreter.txt (~0.8 KB : parsing d'intentions joueur)
4. world_resolver.txt (~0.7 KB : substitution evenements canon annules)
5. director_compactor.txt (~0.3 KB : archiviste narratif)
6. tension_analyst.txt (~1.6 KB : detection opportunites dramatiques)

Total source FR : ~12 KB = ~3000 tokens. Cible : 6 prompts x 7 langs = 42 fichiers.
Cout estime : ~3000 in tok x 7 langs x 6 prompts repetes... mais on optimise en
batchant les 6 prompts dans 1 seul appel par langue. Donc 7 appels Sonnet.

Usage :
    python scripts/i18n_translate_prompts.py --dry-run
    python scripts/i18n_translate_prompts.py --execute
    python scripts/i18n_translate_prompts.py --execute --targets en,ja --workers 1
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
from pathlib import Path

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
PROMPTS_DIR = ROOT / "data" / "i18n" / "prompts"
GLOSSARY_PATH = ROOT / "data" / "i18n" / "glossary.json"
REPORT_PATH = ROOT / "research" / "i18n-batch-report.md"

PROMPT_NAMES = [
    "narrator",
    "goal_pathfinder",
    "character_interpreter",
    "world_resolver",
    "director_compactor",
    "tension_analyst",
]

TARGET_LANGS = ["en", "es", "ja", "zh", "ko", "pt-BR", "de"]
LANG_NAMES = {
    "en": "English",
    "es": "Spanish (Spain)",
    "ja": "Japanese",
    "zh": "Mandarin Chinese (Simplified)",
    "ko": "Korean",
    "pt-BR": "Brazilian Portuguese",
    "de": "German",
}

MODEL_ID = "claude-sonnet-4-6"
PRICE_INPUT_PER_MTOK = 3.0
PRICE_OUTPUT_PER_MTOK = 15.0
MAX_OUTPUT_TOKENS = 16384

SYSTEM_PROMPT_TEMPLATE = """You are a professional video game translator specializing in the Naruto universe.

You translate LLM system prompts from French to {target_language_name}. These prompts are sent to other AI models to drive narrative game logic, so EXACT meaning and structural fidelity matter.

CRITICAL RULES:
1. Output VALID JSON: an object with the EXACT same keys as input (each key is a prompt name). Each value is the translated prompt as a STRING (not nested).
2. PRESERVE these terms in original romaji form (do NOT translate them, keep verbatim case-insensitive): {glossary_terms}
3. Keep formatting tokens INTACT: {{variable_name}} placeholders, [SECTION HEADERS], JSON schema field names (e.g. character_id, narrative, proposed_actions), enum values quoted in single-quotes, JSON keywords.
4. PRESERVE the document structure: section headers in brackets like [CADRE PERSONA], [STYLE CANON], etc. should be translated TO their natural equivalent in the target language but kept in brackets.
5. Maintain the ORIGINAL line break structure (one blank line between sections).
6. Maintain register and tone : these are TECHNICAL prompts to other LLMs. Translate faithfully without softening or paraphrasing.
7. Output ONLY the translated JSON object, no preamble, no postscript, no markdown fences.

JSON ESCAPING (mandatory):
- Newlines inside string values MUST be escaped as \\n.
- Any literal ASCII double-quote (\") inside a value MUST be escaped as \\\".
- Backslashes must be escaped as \\\\.
- Before emitting, mentally re-parse your JSON to confirm it's valid.

Source language: French.
Target language: {target_language_name}.
"""

USER_PROMPT_TEMPLATE = """Glossary (NEVER translate these — keep romaji exactly):
{glossary_block}

Source prompts to translate (French -> {target_language_name}). Each value is a multi-line string; preserve the structure, headers and formatting tokens. Output a JSON object with the same 6 keys, each mapped to the translated prompt string.

Source JSON:
{json_content}

Output ONLY the JSON object."""


@dataclass
class LangResult:
    lang: str
    success: bool
    prompts_total: int = 0
    prompts_translated: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
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


def load_source_prompts(source_lang: str) -> dict[str, str]:
    src_dir = PROMPTS_DIR / source_lang
    out: dict[str, str] = {}
    for name in PROMPT_NAMES:
        path = src_dir / f"{name}.txt"
        if not path.exists():
            raise FileNotFoundError(f"Source prompt absent : {path}")
        out[name] = path.read_text(encoding="utf-8").rstrip("\n")
    return out


def existing_target_complete(lang: str) -> bool:
    target_dir = PROMPTS_DIR / lang
    return all((target_dir / f"{n}.txt").exists() and (target_dir / f"{n}.txt").stat().st_size > 0 for n in PROMPT_NAMES)


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
        raise ValueError("Aucun objet JSON trouve dans la reponse LLM")
    parsed: dict[str, str] = json.loads(s[start:end + 1])
    return parsed


# Ratio min plausible target/source par langue (CJK condense 2-3x).
_RATIO_MIN_BY_LANG = {
    "ja": 0.20, "zh": 0.20, "ko": 0.30,
    # langues europeennes : ratio normal 0.7-1.5
    "en": 0.5, "es": 0.5, "pt-BR": 0.5, "de": 0.5, "fr": 0.5,
}
_RATIO_MAX_BY_LANG = {
    "ja": 1.5, "zh": 1.5, "ko": 1.5,
    "en": 2.5, "es": 2.5, "pt-BR": 2.5, "de": 3.0, "fr": 2.5,
}


def validate_translation(
    translated: dict[str, str],
    source: dict[str, str],
    glossary: list[str],
    glossary_categories: dict[str, list[str]],
    target_lang: str | None = None,
) -> list[str]:
    issues: list[str] = []
    src_keys = set(source.keys())
    trg_keys = set(translated.keys())
    missing = src_keys - trg_keys
    if missing:
        issues.append(f"manque {len(missing)} prompts : {sorted(missing)}")
    extra = trg_keys - src_keys
    if extra:
        issues.append(f"prompts en trop ({len(extra)}) : {sorted(extra)}")

    term_to_category: dict[str, str] = {}
    for cat, items in glossary_categories.items():
        for term in items:
            term_to_category[term.lower()] = cat

    for k in trg_keys & src_keys:
        sv = str(source[k])
        tv = str(translated[k])
        for g in glossary:
            # Termes courts (<=3) : case-sensitive (evite faux-positifs).
            # Termes longs (>=4) : case-insensitive (chakra/Chakra equivalents).
            flags = 0 if len(g) <= 3 else re.IGNORECASE
            pattern = r'(?<![A-Za-z])' + re.escape(g) + r'(?![A-Za-z])'
            if re.search(pattern, sv, flags) and not re.search(pattern, tv, flags):
                term_cat = term_to_category.get(g.lower())
                if term_cat:
                    synonyms = [s for s in glossary_categories[term_cat] if s != g]
                    if any(re.search(r'(?<![A-Za-z])' + re.escape(s) + r'(?![A-Za-z])', tv, flags) for s in synonyms):
                        continue
                issues.append(f"prompt {k} : terme glossary '{g}' perdu")
                break
        # Placeholders {var_name} preserves
        src_placeholders = set(re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", sv))
        trg_placeholders = set(re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", tv))
        if src_placeholders != trg_placeholders:
            missing_ph = src_placeholders - trg_placeholders
            if missing_ph:
                issues.append(f"prompt {k} : placeholders manquants {missing_ph}")
        # Structural sanity : ratio target/source language-aware (CJK condense)
        ratio = len(tv) / max(1, len(sv))
        ratio_min = _RATIO_MIN_BY_LANG.get(target_lang or "", 0.4)
        ratio_max = _RATIO_MAX_BY_LANG.get(target_lang or "", 3.0)
        if ratio < ratio_min:
            issues.append(f"prompt {k} : sortie suspectement courte (ratio {ratio:.2f}, min {ratio_min})")
        elif ratio > ratio_max:
            issues.append(f"prompt {k} : sortie suspectement longue (ratio {ratio:.2f}, max {ratio_max})")
    return issues


def call_anthropic(client, system: str, user: str, max_retries: int = 3) -> tuple[str, int, int]:
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
            print(f"  [retry {attempt + 1}/{max_retries}] {type(exc).__name__} -> sleep {sleep_s}s", flush=True)
            time.sleep(sleep_s)
    raise RuntimeError(f"Anthropic call failed after {max_retries} retries: {last_exc}")


def call_with_json_repair(
    client, system: str, user: str, lang: str, *, max_repair: int = 2,
) -> tuple[dict[str, str], int, int]:
    """Appel + repair-loop : si parse JSON echoue, re-prompt avec l'erreur."""
    text, in_tok, out_tok = call_anthropic(client, system, user)
    last_err = None
    for attempt in range(max_repair + 1):
        try:
            return parse_translated_json(text), in_tok, out_tok
        except (ValueError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt >= max_repair:
                raise
            print(
                f"  [{lang}] JSON parse fail ({exc}). Repair attempt {attempt + 1}/{max_repair}...",
                flush=True,
            )
            repair_user = (
                f"Your previous JSON output failed to parse with this error:\n"
                f"  {exc}\n\n"
                f"COMMON CAUSE: ASCII double-quote characters (\") inside string values must be "
                f"escaped as \\\". Newlines must be escaped as \\n. Backslashes as \\\\.\n\n"
                f"Here is the broken output. Re-emit ONLY the corrected JSON object — same keys, "
                f"same translated content, but with all string values properly JSON-escaped:\n\n"
                f"--- BROKEN OUTPUT ---\n{text}\n--- END ---"
            )
            text2, in2, out2 = call_anthropic(client, system, repair_user)
            in_tok += in2
            out_tok += out2
            text = text2
    raise RuntimeError(f"JSON repair exhausted: {last_err}")


def process_lang(
    lang: str,
    source: dict[str, str],
    glossary: list[str],
    glossary_categories: dict[str, list[str]],
    api_key: str,
    *,
    force: bool = False,
) -> LangResult:
    result = LangResult(lang=lang, success=False, prompts_total=len(source))
    t0 = time.time()
    try:
        if not force and existing_target_complete(lang):
            result.success = True
            result.prompts_translated = len(source)
            result.duration_s = time.time() - t0
            result.issues.append("skipped: already complete")
            return result

        client = anthropic.Anthropic(api_key=api_key)
        glossary_inline = ", ".join(glossary)
        glossary_block = "\n".join(f"- {g}" for g in glossary)
        system = SYSTEM_PROMPT_TEMPLATE.format(
            target_language_name=LANG_NAMES[lang], glossary_terms=glossary_inline,
        )
        user = USER_PROMPT_TEMPLATE.format(
            target_language_name=LANG_NAMES[lang],
            glossary_block=glossary_block,
            json_content=json.dumps(source, ensure_ascii=False, indent=2),
        )

        print(f"[{lang}] translating 6 prompts ({sum(len(v) for v in source.values()):,} chars source)...", flush=True)
        try:
            parsed, in_tok, out_tok = call_with_json_repair(client, system, user, lang)
        except (ValueError, json.JSONDecodeError, RuntimeError) as exc:
            result.error = f"parse fail after repair: {exc}"
            return result
        result.input_tokens = in_tok
        result.output_tokens = out_tok
        result.cost_usd = (
            (in_tok / 1_000_000) * PRICE_INPUT_PER_MTOK
            + (out_tok / 1_000_000) * PRICE_OUTPUT_PER_MTOK
        )

        # Write each prompt
        target_dir = PROMPTS_DIR / lang
        target_dir.mkdir(parents=True, exist_ok=True)
        for name in PROMPT_NAMES:
            if name in parsed:
                (target_dir / f"{name}.txt").write_text(
                    str(parsed[name]).rstrip("\n") + "\n", encoding="utf-8",
                )
                result.prompts_translated += 1

        # Validate
        result.issues = validate_translation(
            {k: v for k, v in parsed.items() if k in PROMPT_NAMES},
            source, glossary, glossary_categories,
            target_lang=lang,
        )
        result.success = True
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.duration_s = time.time() - t0
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="fr")
    parser.add_argument("--targets", default=",".join(TARGET_LANGS))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    glossary, glossary_cats = load_glossary()
    source = load_source_prompts(args.source)
    src_chars = sum(len(v) for v in source.values())
    print(f"Source {args.source}: 6 prompts, {src_chars:,} chars total ({src_chars // 4:,} tokens approx)")
    print(f"Cibles : {targets}")
    cost_per_lang = (src_chars / 4 + 600) * PRICE_INPUT_PER_MTOK / 1_000_000 + (src_chars / 4) * PRICE_OUTPUT_PER_MTOK / 1_000_000
    print(f"Estimation : ~${cost_per_lang * len(targets):.2f} pour {len(targets)} langues")

    if args.dry_run:
        print("\n[dry-run] aucune requete envoyee.")
        return 0
    if not args.execute:
        print("\n[!] passe --execute pour lancer la traduction.")
        return 1

    api_key = os.environ.get("API_CLAUDE_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[X] API_CLAUDE_KEY absente du .env / env", file=sys.stderr)
        return 3

    print(f"\nLancement {args.workers} workers...\n")
    results: list[LangResult] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {
            ex.submit(process_lang, lang, source, glossary, glossary_cats, api_key, force=args.force): lang
            for lang in targets
        }
        for fut in as_completed(futures):
            r = fut.result()
            tag = "OK" if r.success and not r.error else "FAIL"
            err_part = f" ERR={r.error}" if r.error else ""
            print(
                f"[{tag}] {r.lang} : {r.prompts_translated}/{r.prompts_total} prompts, "
                f"{r.input_tokens:,}+{r.output_tokens:,} tok, ${r.cost_usd:.3f}, "
                f"{r.duration_s:.1f}s, issues={len(r.issues)}{err_part}"
            )
            results.append(r)

    failures = [r for r in results if not r.success or r.error]
    if failures:
        print(f"\n[X] {len(failures)} langue(s) en echec.", file=sys.stderr)
        return 4
    print("\n[OK] tous les prompts traduits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
