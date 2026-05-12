"""Phase i18n.5 : traduction des catalogs i18n vers les 6 langues cibles via Anthropic Sonnet 4.6.

Source : data/i18n/en.json (catalog canonique, le plus complet a date)
Cibles : es, ja, zh, ko, pt-BR, de (6 langues, EN reste source)
Glossary : data/i18n/glossary.json (termes Naruto preserves en romaji)

Strategie :
- 1 appel sync par langue avec le catalog ENTIER (~30K tokens) en JSON
- Glossary injecte dans le system prompt (preservation des termes canon)
- Validation post-traduction : couverture cles 100% + glossary intact
- Concurrent : 6 langues en parallele via ThreadPoolExecutor
- Resumable : skip une langue si son catalog est deja complet

Usage :
    python scripts/i18n_batch_translate.py --dry-run         # estime cout
    python scripts/i18n_batch_translate.py --execute         # lance les 6 langues
    python scripts/i18n_batch_translate.py --execute --targets ja,zh
    python scripts/i18n_batch_translate.py --execute --force # re-traduit meme si complet

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

# Load .env for API_CLAUDE_KEY
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    print("[!] python-dotenv non installe. .env ne sera pas charge.", file=sys.stderr)

try:
    import anthropic
except ImportError:
    print("[X] anthropic SDK manquant. Installe avec: pip install anthropic", file=sys.stderr)
    sys.exit(2)


ROOT = Path(__file__).resolve().parent.parent
I18N_DIR = ROOT / "data" / "i18n"
GLOSSARY_PATH = I18N_DIR / "glossary.json"
REPORT_PATH = ROOT / "research" / "i18n-batch-report.md"

TARGET_LANGS = ["es", "ja", "zh", "ko", "pt-BR", "de"]
LANG_NAMES = {
    "es": "Spanish (Spain)",
    "ja": "Japanese",
    "zh": "Mandarin Chinese (Simplified)",
    "ko": "Korean",
    "pt-BR": "Brazilian Portuguese",
    "de": "German",
}
LANG_NATIVE = {
    "en": "English",
    "fr": "Français",
    "es": "Español",
    "ja": "日本語",
    "zh": "中文",
    "ko": "한국어",
    "pt-BR": "Português (Brasil)",
    "de": "Deutsch",
}

MODEL_ID = "claude-sonnet-4-6"
# Sync pricing (Sonnet 4.6) : $3/M input, $15/M output. Batch API serait
# ~50% off mais necessite polling async. On reste sync pour simplicite.
PRICE_INPUT_PER_MTOK = 3.0
PRICE_OUTPUT_PER_MTOK = 15.0

# Cap conservatif sur les chunks pour eviter rate-limits + sortie tronquee.
# 749 cles ~30K tokens : split en chunks de 250 cles ~10K tokens chacun.
CHUNK_KEY_COUNT = 250
MAX_OUTPUT_TOKENS = 16384  # Sonnet 4.6 supporte jusqu'a 64K

SYSTEM_PROMPT_TEMPLATE = """You are a professional video game translator specializing in the Naruto universe.

You translate JSON i18n catalogs from English to {target_language_name}.

CRITICAL RULES:
1. Output VALID JSON with the EXACT same keys as input. Only translate values.
2. PRESERVE these terms in original romaji form (do NOT translate them, keep verbatim case-insensitive): {glossary_terms}
3. Keep formatting tokens INTACT: {{variable_name}} placeholders, [bold]...[/bold] / [color]...[/color] / [dim]...[/dim] / [yellow]...[/yellow] markup, \\n newlines.
4. Maintain register and tone consistent with a serious narrative video game (no slang, no emoji, no em-dashes).
5. Output ONLY the translated JSON object, no preamble, no postscript, no markdown fences.
6. Translate the META keys "_schema" and "_native_name" to the target's native value, but keep "_language" as the target lang code.

JSON ESCAPING (mandatory, common pitfall):
- Any literal ASCII double-quote (\") inside a translated value MUST be escaped as \\\" — exactly like in the English source.
- Newlines inside values must be escaped as \\n.
- Backslashes must be escaped as \\\\.
- For Chinese / Japanese / Korean : prefer language-native quote marks (e.g. 「 」, 『 』, " " or « ») when possible — they read more naturally and avoid escaping issues. ASCII double-quotes are allowed but MUST be escaped with backslash.
- Before emitting, mentally re-parse your JSON to confirm it's valid.

Source language: English.
Target language: {target_language_name}.
"""

USER_PROMPT_TEMPLATE = """Glossary (NEVER translate these — keep romaji exactly):
{glossary_block}

Source JSON to translate (English -> {target_language_name}):
{json_content}

Output ONLY the JSON object."""


@dataclass
class LangResult:
    lang: str
    success: bool
    keys_total: int = 0
    keys_translated: int = 0
    chunks: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    issues: list[str] = field(default_factory=list)
    error: str | None = None


def load_glossary() -> list[str]:
    data = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    terms: list[str] = []
    for category, items in data.items():
        if category.startswith("_"):
            continue
        if isinstance(items, list):
            terms.extend(items)
    # Dedup, sort by length desc (longer first for proper regex matching)
    return sorted(set(terms), key=lambda s: (-len(s), s.lower()))


def load_glossary_categories() -> dict[str, list[str]]:
    """Return categorie -> liste de termes (utile pour synonymes/substitutions)."""
    data = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    return {
        cat: items
        for cat, items in data.items()
        if not cat.startswith("_") and isinstance(items, list)
    }


def load_source_catalog(source_lang: str) -> dict[str, str]:
    path = I18N_DIR / f"{source_lang}.json"
    if not path.exists():
        raise FileNotFoundError(f"Source catalog absent : {path}")
    data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    return data


def load_existing_target(lang: str) -> dict[str, str]:
    path = I18N_DIR / f"{lang}.json"
    if not path.exists():
        return {}
    try:
        data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
        return data
    except json.JSONDecodeError:
        return {}


def filter_meta(catalog: dict) -> tuple[dict, dict]:
    """Split (meta_keys, content_keys)."""
    meta = {k: v for k, v in catalog.items() if k.startswith("_")}
    content = {k: v for k, v in catalog.items() if not k.startswith("_")}
    return meta, content


def chunk_keys(content: dict[str, str], chunk_size: int) -> list[dict[str, str]]:
    items = list(content.items())
    return [dict(items[i:i + chunk_size]) for i in range(0, len(items), chunk_size)]


def build_prompts(target_lang: str, glossary: list[str], chunk: dict[str, str]) -> tuple[str, str]:
    glossary_inline = ", ".join(glossary)
    glossary_block = "\n".join(f"- {g}" for g in glossary)
    system = SYSTEM_PROMPT_TEMPLATE.format(
        target_language_name=LANG_NAMES[target_lang],
        glossary_terms=glossary_inline,
    )
    user = USER_PROMPT_TEMPLATE.format(
        target_language_name=LANG_NAMES[target_lang],
        glossary_block=glossary_block,
        json_content=json.dumps(chunk, ensure_ascii=False, indent=2),
    )
    return system, user


def parse_translated_json(raw: str) -> dict[str, str]:
    """Extract JSON object from LLM output (tolerant to fences, preamble)."""
    # Strip markdown fences if present
    s = raw.strip()
    if s.startswith("```"):
        # remove first fence line + last fence
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    # Find first { and last }
    start = s.find("{")
    end = s.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("Aucun objet JSON trouve dans la reponse LLM")
    parsed: dict[str, str] = json.loads(s[start:end + 1])
    return parsed


def validate_translation(
    translated: dict[str, str],
    source: dict[str, str],
    glossary: list[str],
    glossary_categories: dict[str, list[str]] | None = None,
) -> list[str]:
    """Returns list of issues (empty = OK).

    Glossary preservation is satisfied when EITHER the exact term appears in
    target, OR a synonym from the same glossary category does (e.g. nukenin /
    missing-nin are both rank synonyms).
    """
    issues: list[str] = []
    src_keys = set(source.keys())
    trg_keys = set(translated.keys())
    missing = src_keys - trg_keys
    if missing:
        issues.append(f"manque {len(missing)} cles : {sorted(missing)[:5]}")
    extra = trg_keys - src_keys
    if extra:
        issues.append(f"cles en trop ({len(extra)}) : {sorted(extra)[:5]}")

    # Map term -> categorie pour permettre synonymes
    term_to_category: dict[str, str] = {}
    if glossary_categories:
        for cat, items in glossary_categories.items():
            for term in items:
                term_to_category[term.lower()] = cat

    for k in trg_keys & src_keys:
        sv = str(source[k])
        tv = str(translated[k])
        for g in glossary:
            # Termes courts (<=3) : case-sensitive (evite les faux-positifs
            # comme 'Ne' org vs 'ne' negation FR).
            # Termes longs (>=4) : case-insensitive (chakra/Chakra acceptes).
            flags = 0 if len(g) <= 3 else re.IGNORECASE
            pattern = r'(?<![A-Za-z])' + re.escape(g) + r'(?![A-Za-z])'
            if re.search(pattern, sv, flags) and not re.search(pattern, tv, flags):
                term_cat = term_to_category.get(g.lower())
                if term_cat and glossary_categories is not None:
                    synonyms = [s for s in glossary_categories[term_cat] if s != g]
                    found_synonym = any(
                        re.search(r'(?<![A-Za-z])' + re.escape(s) + r'(?![A-Za-z])', tv, flags)
                        for s in synonyms
                    )
                    if found_synonym:
                        continue
                issues.append(f"cle {k} : terme glossary '{g}' perdu")
                break
        # Placeholders {var_name} preserves
        src_placeholders = set(re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", sv))
        trg_placeholders = set(re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", tv))
        if src_placeholders != trg_placeholders:
            missing_ph = src_placeholders - trg_placeholders
            if missing_ph:
                issues.append(f"cle {k} : placeholders manquants {missing_ph}")
    return issues


def call_anthropic(
    client: anthropic.Anthropic,
    system: str,
    user: str,
    *,
    max_retries: int = 3,
) -> tuple[str, int, int]:
    """Sync call avec retry sur erreurs transitoires. Returns (text, in_tok, out_tok)."""
    last_exc: Exception | None = None
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


def estimate_cost(
    source: dict[str, str],
    targets: list[str],
    glossary: list[str],
) -> tuple[int, int, float]:
    """Returns (estimated_input_tokens, estimated_output_tokens, cost_usd)."""
    json_text = json.dumps(source, ensure_ascii=False, indent=2)
    # Rough chars-per-token estimate: 4
    src_tokens = len(json_text) // 4
    sys_tokens = (len(", ".join(glossary)) + 600) // 4  # system prompt + glossary inline
    chunks_per_lang = max(1, (len(source) + CHUNK_KEY_COUNT - 1) // CHUNK_KEY_COUNT)
    in_per_lang = src_tokens + sys_tokens * chunks_per_lang
    out_per_lang = src_tokens  # output ~ same size as input
    in_total = in_per_lang * len(targets)
    out_total = out_per_lang * len(targets)
    cost = (in_total / 1_000_000) * PRICE_INPUT_PER_MTOK + (out_total / 1_000_000) * PRICE_OUTPUT_PER_MTOK
    return (in_total, out_total, cost)


def process_lang(
    lang: str,
    source: dict[str, str],
    glossary: list[str],
    api_key: str,
    *,
    force: bool = False,
    glossary_categories: dict[str, list[str]] | None = None,
) -> LangResult:
    result = LangResult(lang=lang, success=False)
    t0 = time.time()
    try:
        client = anthropic.Anthropic(api_key=api_key)
        _meta_src, content_src = filter_meta(source)
        existing = load_existing_target(lang)
        _meta_existing, content_existing = filter_meta(existing)
        result.keys_total = len(content_src)

        if not force and len(content_existing) >= len(content_src):
            # Already complete (skip)
            result.success = True
            result.keys_translated = len(content_existing)
            result.duration_s = time.time() - t0
            result.issues.append("skipped: already complete")
            return result

        chunks = chunk_keys(content_src, CHUNK_KEY_COUNT)
        result.chunks = len(chunks)
        translated_all: dict[str, str] = {}

        def _update_cost() -> None:
            result.cost_usd = (
                (result.input_tokens / 1_000_000) * PRICE_INPUT_PER_MTOK
                + (result.output_tokens / 1_000_000) * PRICE_OUTPUT_PER_MTOK
            )

        for i, chunk in enumerate(chunks, 1):
            print(f"[{lang}] chunk {i}/{len(chunks)} ({len(chunk)} keys)...", flush=True)
            system, user = build_prompts(lang, glossary, chunk)
            text, in_tok, out_tok = call_anthropic(client, system, user)
            result.input_tokens += in_tok
            result.output_tokens += out_tok
            _update_cost()
            try:
                parsed = parse_translated_json(text)
            except (ValueError, json.JSONDecodeError) as exc:
                debug_path = I18N_DIR / f"_debug_{lang}_chunk{i}.txt"
                debug_path.write_text(text, encoding="utf-8")
                result.error = f"chunk {i} parse fail: {exc} (raw saved to {debug_path.name})"
                return result
            # Drop meta keys from per-chunk parsed (we set meta separately)
            for k, v in parsed.items():
                if not k.startswith("_"):
                    translated_all[k] = v

        # Build final catalog : meta override + sorted by source order
        out_catalog: dict[str, str] = {
            "_schema": "i18n_v1",
            "_language": lang,
            "_native_name": LANG_NATIVE[lang],
        }
        for k in content_src:
            if k in translated_all:
                out_catalog[k] = translated_all[k]
            else:
                # missing : fallback sur source EN pour ne pas crasher l'app
                out_catalog[k] = content_src[k]

        # Validate
        result.issues = validate_translation(
            {k: v for k, v in out_catalog.items() if not k.startswith("_")},
            content_src,
            glossary,
            glossary_categories=glossary_categories,
        )
        result.keys_translated = sum(1 for k in out_catalog if not k.startswith("_") and k in translated_all)
        _update_cost()

        # Write file
        path = I18N_DIR / f"{lang}.json"
        path.write_text(
            json.dumps(out_catalog, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result.success = True
    except Exception as exc:  # pragma: no cover - network error path
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.duration_s = time.time() - t0
    return result


def write_report(results: list[LangResult], source_lang: str) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    total_in = sum(r.input_tokens for r in results)
    total_out = sum(r.output_tokens for r in results)
    total_cost = sum(r.cost_usd for r in results)
    total_dur = sum(r.duration_s for r in results)
    successes = sum(1 for r in results if r.success and not r.error)
    lines: list[str] = []
    lines.append("# Phase i18n.5 — Rapport de traduction batch")
    lines.append("")
    lines.append(f"Date : {datetime.now(UTC).isoformat()}")
    lines.append(f"Source : `data/i18n/{source_lang}.json`")
    lines.append(f"Modele : `{MODEL_ID}` (sync, no batch discount)")
    lines.append("")
    lines.append("## Resume")
    lines.append("")
    lines.append(f"- Langues traitees : {len(results)} ({successes} succes)")
    lines.append(f"- Tokens input total : {total_in:,}")
    lines.append(f"- Tokens output total : {total_out:,}")
    lines.append(f"- Cout total : **${total_cost:.2f}**")
    lines.append(f"- Duree totale : {total_dur:.1f}s")
    lines.append("")
    lines.append("## Detail par langue")
    lines.append("")
    lines.append("| Lang | Status | Cles | Chunks | In tok | Out tok | Cost USD | Duration | Issues |")
    lines.append("|------|--------|------|--------|--------|---------|----------|----------|--------|")
    for r in results:
        status = "OK" if r.success and not r.error else "FAIL"
        issues = "; ".join(r.issues[:2]) if r.issues else "—"
        if r.error:
            issues = r.error[:80]
        lines.append(
            f"| {r.lang} | {status} | {r.keys_translated}/{r.keys_total} | {r.chunks} | "
            f"{r.input_tokens:,} | {r.output_tokens:,} | ${r.cost_usd:.3f} | "
            f"{r.duration_s:.1f}s | {issues} |"
        )
    lines.append("")
    lines.append("## Validation")
    lines.append("")
    any_issues = False
    for r in results:
        if r.issues and r.issues != ["skipped: already complete"]:
            any_issues = True
            lines.append(f"### {r.lang}")
            lines.append("")
            for issue in r.issues:
                lines.append(f"- {issue}")
            lines.append("")
    if not any_issues:
        lines.append("Aucune anomalie detectee. Glossary preserve a 100%, toutes les cles couvertes.")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n[OK] rapport ecrit dans {REPORT_PATH.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--source", default="en", help="Source lang (default: en)")
    parser.add_argument(
        "--targets",
        default=",".join(TARGET_LANGS),
        help="Comma-separated target langs (default: %(default)s)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Estimate cost only, no API call")
    parser.add_argument("--execute", action="store_true", help="Submit translation requests")
    parser.add_argument("--force", action="store_true", help="Re-translate even if target file complete")
    parser.add_argument("--workers", type=int, default=3, help="Concurrent target langs (default 3)")
    args = parser.parse_args()

    targets = [t.strip() for t in args.targets.split(",") if t.strip()]
    for t in targets:
        if t not in LANG_NAMES:
            print(f"[X] cible inconnue : {t}. Supportees : {list(LANG_NAMES)}", file=sys.stderr)
            return 2

    glossary = load_glossary()
    glossary_categories = load_glossary_categories()
    source = load_source_catalog(args.source)
    _, content = filter_meta(source)
    print(f"Source {args.source}: {len(content)} cles + {len(glossary)} termes glossary")
    print(f"Cibles : {targets}")

    in_est, out_est, cost_est = estimate_cost(content, targets, glossary)
    print(f"Estimation : ~{in_est:,} in tok + ~{out_est:,} out tok = ~${cost_est:.2f}")

    if args.dry_run:
        print("\n[dry-run] aucune requete envoyee.")
        return 0

    if not args.execute:
        print("\n[!] passe --execute pour lancer la traduction (ou --dry-run pour estimer).")
        return 1

    api_key = os.environ.get("API_CLAUDE_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("[X] API_CLAUDE_KEY (ou ANTHROPIC_API_KEY) absente du .env / environnement.", file=sys.stderr)
        return 3

    print(f"\nLancement avec {args.workers} workers concurrents...\n")
    results: list[LangResult] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {
            ex.submit(
                process_lang,
                lang,
                source,
                glossary,
                api_key,
                force=args.force,
                glossary_categories=glossary_categories,
            ): lang
            for lang in targets
        }
        for fut in as_completed(futures):
            r = fut.result()
            tag = "OK" if r.success and not r.error else "FAIL"
            err_part = f" ERR={r.error}" if r.error else ""
            print(
                f"[{tag}] {r.lang} : {r.keys_translated}/{r.keys_total} keys, "
                f"{r.input_tokens:,}+{r.output_tokens:,} tok, ${r.cost_usd:.3f}, "
                f"{r.duration_s:.1f}s, issues={len(r.issues)}{err_part}"
            )
            results.append(r)

    # Sort by lang code for deterministic report
    results.sort(key=lambda r: r.lang)
    write_report(results, args.source)

    failures = [r for r in results if not r.success or r.error]
    if failures:
        print(f"\n[X] {len(failures)} langue(s) en echec.", file=sys.stderr)
        return 4
    return 0


if __name__ == "__main__":
    sys.exit(main())
