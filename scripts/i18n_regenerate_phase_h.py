"""Phase i18n.7 : regenere les 5 datasets Phase H pour les 7 langues cibles.

Source : data/canon/{deep_motivations,political_forces,divergence_points,
narrative_patterns,timeline_events_enriched}.json (FR canon)
Cibles : en, es, ja, zh, ko, pt-BR, de (7 langues)
Output : data/canon/i18n/<lang>/<dataset>.json (35 fichiers)

Strategie :
- Chaque dataset traduit en 1 (ou plusieurs chunks) appel(s) Sonnet/Haiku
- Preserve strictement les ids canon (`id`, `event_id`, `leader_id`, members,
  preconditions, etc.) — seuls les champs FR (`name_fr`, `description_fr`,
  `_fr` suffixes + listes de FR strings) sont traduits
- Les champs `_fr` sont renommes vers `_<lang>` (ex: `name_fr` -> `name_ja`)
- Validation : structure preservee, ids identiques, nombre d'entries identique

Cout estime via Haiku 4.5 (3x moins cher que Sonnet) :
- ~74K tokens FR source x 7 langs = 518K input
- ~80K tokens output x 7 langs = 560K output
- Cost : 518K x $1 + 560K x $5 / 1M = ~$3.30 (vs spec $5.50 estime via Sonnet batch)

Usage :
    python scripts/i18n_regenerate_phase_h.py --dry-run
    python scripts/i18n_regenerate_phase_h.py --execute
    python scripts/i18n_regenerate_phase_h.py --execute --langs ja,zh
    python scripts/i18n_regenerate_phase_h.py --execute --datasets deep_motivations
    python scripts/i18n_regenerate_phase_h.py --execute --model claude-sonnet-4-6 --workers 4

Configuration : .env doit contenir API_CLAUDE_KEY=sk-ant-...
"""

from __future__ import annotations

import argparse
import json
import os
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

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore[assignment]

# Qwen local backend defaults (llama.cpp OpenAI-compatible API)
QWEN_BASE_URL = "http://localhost:8080"
QWEN_MODEL = "Qwen3-4B-UD-Q4_K_XL.gguf"
QWEN_TIMEOUT_S = 180.0  # plus long pour gros chunks (294 entries timeline)


ROOT = Path(__file__).resolve().parent.parent
CANON = ROOT / "data" / "canon"
I18N_OUT = CANON / "i18n"
GLOSSARY_PATH = ROOT / "data" / "i18n" / "glossary.json"
REPORT_PATH = ROOT / "research" / "i18n-phase-h-report.md"

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

MODELS_PRICING = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
    "claude-haiku-4-5": (1.0, 5.0),
}
MODEL_ID = "claude-haiku-4-5"  # default : Haiku pour cout reduit
MAX_OUTPUT_TOKENS = 16384

# Specifications par dataset : (source_filename, container_key_or_None, id_field, chunk_entries_per_call)
# container_key = None : dict[id] -> entry
# container_key = str : dict["<key>"] -> list[entry]
# chunk_entries_per_call : nombre max d'entries dans un appel LLM (eviter de depasser max_output)
DATASET_SPECS: dict[str, dict[str, Any]] = {
    "deep_motivations": {
        "filename": "deep_motivations.json",
        "container_key": None,  # dict[id] -> entry
        "id_field": "id",
        "chunk_entries_per_call": 25,  # 50 entries / 2 chunks
    },
    "political_forces": {
        "filename": "political_forces.json",
        "container_key": "factions",
        "id_field": "id",
        "chunk_entries_per_call": 25,  # 49 entries / 2 chunks
    },
    "divergence_points": {
        "filename": "divergence_points.json",
        "container_key": "divergence_points",
        "id_field": "event_id",
        "chunk_entries_per_call": 25,  # 21 entries / 1 chunk
    },
    "narrative_patterns": {
        "filename": "narrative_patterns.json",
        "container_key": "patterns",
        "id_field": "id",
        "chunk_entries_per_call": 25,  # 14 entries / 1 chunk
    },
    "timeline_events_enriched": {
        "filename": "timeline_events_enriched.json",
        "container_key": None,  # dict[id] -> entry
        "id_field": "id",
        "chunk_entries_per_call": 50,  # 294 entries / 6 chunks
    },
}


SYSTEM_PROMPT_TEMPLATE = """You are a professional translator of canon Naruto-universe data, specializing in narrative game datasets.

You translate JSON entries from French to {target_language_name}. The entries describe characters, factions, events, and narrative patterns from the Naruto universe.

CRITICAL RULES:
1. Output VALID JSON: a list with the EXACT same number of entries as input, in the SAME ORDER.
2. Each entry MUST preserve ALL non-string fields IDENTICALLY (numbers, ids, lists of ids, booleans, nested objects with `fact`/`value` keys, dates).
3. Translate ONLY the human-readable French text fields. Identify them by:
   - Field name suffix `_fr` (e.g. `name_fr`, `description_fr`, `why_pivotal_fr`, `title_fr`, `when_to_apply_fr`)
   - List of FR strings (e.g. `moral_red_lines`, `narrative_invariants`, `alternative_seeds`, `if_altered_consequences`, `secret_ambitions`, `what_others_dont_know`)
   - Free-form `deepest_fear`, `self_image` strings (FR text)
   - Nested dict `deep_motivations` containing `primary`/`secondary`/`tertiary` (FR snake_case strings — translate while keeping the snake_case structure: e.g. `affirmer_son_existence` -> `assert_one_s_existence` for EN)
4. RENAME `_fr`-suffixed fields to `_{target_lang_code}` (e.g. `name_fr` -> `name_{target_lang_code}`).
5. PRESERVE these terms in romaji form (do NOT translate, keep verbatim case-insensitive): {glossary_terms}
6. NEVER translate IDs (chars like `uchiha_itachi`, factions like `konoha_council`, event ids like `kyuubi_attack`). Lists like `members`, `allies`, `enemies`, `canon_examples`, `involved_canon_ids`, `leader_id` contain IDs to preserve verbatim.
7. NEVER touch numeric fields (`year`, `active_year_start`, `active_year_end`, `cascade_severity` if numeric).
8. NEVER touch nested structured data with `fact`/`value` keys (preconditions, outcomes).
9. Output ONLY the JSON array, no preamble, no markdown fences.

JSON ESCAPING (mandatory):
- Newlines inside string values MUST be escaped as \\n.
- ASCII double-quote (\") inside a value MUST be escaped as \\\".
- Backslashes as \\\\.

Source language: French. Target: {target_language_name}.
Target language code: {target_lang_code}.
"""


@dataclass
class TaskResult:
    dataset: str
    lang: str
    success: bool
    entries_total: int = 0
    entries_translated: int = 0
    chunks: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_s: float = 0.0
    issues: list[str] = field(default_factory=list)
    error: str | None = None


def _model_prices() -> tuple[float, float]:
    return MODELS_PRICING[MODEL_ID]


def load_glossary() -> tuple[str, ...]:
    if not GLOSSARY_PATH.exists():
        return ()
    data = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    terms: list[str] = []
    for category, items in data.items():
        if category.startswith("_"):
            continue
        if isinstance(items, list):
            terms.extend(str(t) for t in items if isinstance(t, str))
    return tuple(sorted(set(terms), key=lambda s: (-len(s), s.lower())))


def load_dataset(name: str) -> tuple[Any, list[dict[str, Any]]]:
    """Charge un dataset Phase H. Retourne (raw_loaded, entries_list).

    Le dict raw_loaded permet de reconstruire la meme structure (avec container_key ou dict[id]).
    """
    spec = DATASET_SPECS[name]
    raw = json.loads((CANON / spec["filename"]).read_text(encoding="utf-8"))
    container_key = spec["container_key"]
    if container_key:
        entries = list(raw[container_key])
    else:
        # dict[id] -> entry. Preserve order of insertion.
        entries = list(raw.values())
    return raw, entries


def chunk_entries(entries: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [entries[i:i + chunk_size] for i in range(0, len(entries), chunk_size)]


def parse_translated_json(raw: str) -> list[dict[str, Any]]:
    s = raw.strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    # Find first [ and last ]
    start = s.find("[")
    end = s.rfind("]")
    if start < 0 or end < 0:
        raise ValueError("aucun JSON array trouve dans la sortie LLM")
    parsed: list[dict[str, Any]] = json.loads(s[start:end + 1])
    return parsed


def _rename_fr_fields(entry: dict[str, Any], lang: str) -> dict[str, Any]:
    """Renomme `_fr` -> `_<lang>` recursivement. Preserve les autres cles."""
    if not isinstance(entry, dict):
        return entry
    out: dict[str, Any] = {}
    for k, v in entry.items():
        new_key = k
        if k.endswith("_fr"):
            new_key = k[:-3] + f"_{lang}"
        if isinstance(v, dict):
            out[new_key] = _rename_fr_fields(v, lang)
        elif isinstance(v, list):
            out[new_key] = [_rename_fr_fields(x, lang) if isinstance(x, dict) else x for x in v]
        else:
            out[new_key] = v
    return out


def validate_chunk(
    translated: list[dict[str, Any]],
    source: list[dict[str, Any]],
    id_field: str,
    lang: str,
) -> list[str]:
    """Validation post-traduction : meme nombre d'entries + meme ids + dans le meme ordre."""
    issues: list[str] = []
    if len(translated) != len(source):
        issues.append(f"chunk size mismatch: src={len(source)} trg={len(translated)}")
        return issues
    for i, (src, trg) in enumerate(zip(source, translated, strict=True)):
        src_id = src.get(id_field)
        trg_id = trg.get(id_field)
        if src_id != trg_id:
            issues.append(f"entry {i}: id mismatch src={src_id!r} trg={trg_id!r}")
        # Verify any _fr field has been renamed to _<lang>
        for k in src:
            if k.endswith("_fr"):
                renamed = k[:-3] + f"_{lang}"
                if k in trg:
                    issues.append(f"entry {i}: field {k!r} not renamed to {renamed!r}")
                # Note : si renamed est absent c'est aussi un probleme mais on tolere
                # (le LLM peut produire le format dans la nouvelle convention)
    return issues


def call_qwen_http(
    system: str, user: str, *, max_retries: int = 3,
) -> tuple[str, int, int]:
    """Appel le serveur llama.cpp local (OpenAI-compatible /v1/chat/completions).

    Couts : $0 (compute local). Retourne (text, in_tokens, out_tokens).
    Leve RuntimeError si le serveur ne repond pas.
    """
    if httpx is None:
        raise RuntimeError("httpx non installe (requis pour Qwen backend)")
    payload = {
        "model": QWEN_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0.2,
    }
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            with httpx.Client(timeout=QWEN_TIMEOUT_S) as client:
                resp = client.post(f"{QWEN_BASE_URL}/v1/chat/completions", json=payload)
                resp.raise_for_status()
                data = resp.json()
            choices = data.get("choices") or []
            if not choices:
                raise RuntimeError("Qwen response missing 'choices'")
            text = (choices[0].get("message") or {}).get("content", "")
            usage = data.get("usage", {}) or {}
            in_tok = int(usage.get("prompt_tokens", 0))
            out_tok = int(usage.get("completion_tokens", 0))
            return (text, in_tok, out_tok)
        except (httpx.HTTPError, json.JSONDecodeError, RuntimeError) as exc:
            last_exc = exc
            sleep_s = 5 * (2 ** attempt)
            print(f"  retry qwen {attempt + 1}: {type(exc).__name__} sleep {sleep_s}s", flush=True)
            time.sleep(sleep_s)
    raise RuntimeError(f"Qwen call failed after {max_retries} retries: {last_exc}")


def call_anthropic(
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
                # Faible temperature : translation = output deterministe, pas creatif
                temperature=0.2,
            )
            text_parts = [b.text for b in resp.content if hasattr(b, "text")]
            return ("".join(text_parts), resp.usage.input_tokens, resp.usage.output_tokens)
        except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.APITimeoutError) as exc:
            last_exc = exc
            sleep_s = 5 * (2 ** attempt)
            print(f"  retry {attempt + 1}: {type(exc).__name__} sleep {sleep_s}s", flush=True)
            time.sleep(sleep_s)
    raise RuntimeError(f"sync call failed: {last_exc}")


def _call_backend(
    backend: str, client: anthropic.Anthropic | None, system: str, user: str,
) -> tuple[str, int, int]:
    """Dispatch sur le backend choisi : 'anthropic' ou 'qwen'."""
    if backend == "qwen":
        return call_qwen_http(system, user)
    # default : anthropic
    if client is None:
        raise RuntimeError("Anthropic client requis pour backend=anthropic")
    return call_anthropic(client, system, user)


def call_with_repair(
    client: anthropic.Anthropic | None, system: str, user: str, dataset: str, lang: str,
    *, max_repair: int = 2, backend: str = "anthropic",
) -> tuple[list[dict[str, Any]], int, int]:
    """Call + JSON repair loop : si parse echoue, re-prompt avec l'erreur."""
    text, in_tok, out_tok = _call_backend(backend, client, system, user)
    last_err: Exception | None = None
    for attempt in range(max_repair + 1):
        try:
            return parse_translated_json(text), in_tok, out_tok
        except (ValueError, json.JSONDecodeError) as exc:
            last_err = exc
            if attempt >= max_repair:
                raise
            print(
                f"  [{dataset}/{lang}] JSON parse fail ({exc}). Repair {attempt + 1}/{max_repair}",
                flush=True,
            )
            repair_user = (
                f"Your previous output failed JSON parse:\n  {exc}\n\n"
                f"COMMON CAUSES: ASCII double-quotes inside strings unescaped (use \\\"), "
                f"or wrong outer wrapper (must be a JSON array, not object).\n\n"
                f"Re-emit ONLY the corrected JSON array, same entries, same order, "
                f"properly escaped:\n\n--- BROKEN ---\n{text}\n--- END ---"
            )
            text2, in2, out2 = _call_backend(backend, client, system, repair_user)
            in_tok += in2
            out_tok += out2
            text = text2
    raise RuntimeError(f"JSON repair exhausted: {last_err}")


def existing_target_complete(dataset: str, lang: str, expected_count: int) -> bool:
    """Verifie si le fichier cible existe et a le bon nombre d'entries."""
    path = I18N_OUT / lang / DATASET_SPECS[dataset]["filename"]
    if not path.exists():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    container_key = DATASET_SPECS[dataset]["container_key"]
    if container_key:
        items = raw.get(container_key, [])
    else:
        items = list(raw.values()) if isinstance(raw, dict) else []
    return len(items) == expected_count


def write_dataset_translated(
    dataset: str, lang: str, raw_source: Any, translated_entries: list[dict[str, Any]],
) -> Path:
    """Reconstruit la structure originale avec les entries traduites."""
    spec = DATASET_SPECS[dataset]
    container_key = spec["container_key"]
    id_field: str = spec["id_field"]
    out_dir = I18N_OUT / lang
    out_dir.mkdir(parents=True, exist_ok=True)
    filename: str = spec["filename"]
    out_path: Path = out_dir / filename

    if container_key:
        # dict avec list interne
        out_data = dict(raw_source)
        out_data[container_key] = translated_entries
    else:
        # dict[id] -> entry
        out_data = {entry.get(id_field, f"_unknown_{i}"): entry for i, entry in enumerate(translated_entries)}

    out_path.write_text(json.dumps(out_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def process_dataset_lang(
    dataset: str, lang: str, glossary: tuple[str, ...], api_key: str | None,
    *, force: bool = False, backend: str = "anthropic", chunk_override: int = 0,
) -> TaskResult:
    result = TaskResult(dataset=dataset, lang=lang, success=False)
    t0 = time.time()
    try:
        raw_source, entries = load_dataset(dataset)
        result.entries_total = len(entries)

        if not force and existing_target_complete(dataset, lang, len(entries)):
            result.success = True
            result.entries_translated = len(entries)
            result.duration_s = time.time() - t0
            result.issues.append("skipped: already complete")
            return result

        client = anthropic.Anthropic(api_key=api_key) if backend == "anthropic" else None
        spec = DATASET_SPECS[dataset]
        chunk_size = chunk_override if chunk_override > 0 else spec["chunk_entries_per_call"]
        chunks = chunk_entries(entries, chunk_size)
        result.chunks = len(chunks)

        translated_all: list[dict[str, Any]] = []
        for i, chunk in enumerate(chunks, 1):
            print(f"[{dataset}/{lang}] chunk {i}/{len(chunks)} ({len(chunk)} entries) [{backend}]...", flush=True)
            system = SYSTEM_PROMPT_TEMPLATE.format(
                target_language_name=LANG_NAMES[lang],
                target_lang_code=lang,
                glossary_terms=", ".join(glossary),
            )
            user = (
                f"Translate the following {len(chunk)} entries from FR to {LANG_NAMES[lang]}. "
                f"Output a JSON array of {len(chunk)} entries in the same order, with `_fr` "
                f"fields renamed to `_{lang}` and FR text translated. Preserve ALL ids, numbers, "
                f"and structured fields IDENTICALLY.\n\n"
                f"Source JSON:\n{json.dumps(chunk, ensure_ascii=False, indent=2)}\n\n"
                f"Output ONLY the JSON array."
            )
            try:
                parsed, in_tok, out_tok = call_with_repair(
                    client, system, user, dataset, lang, backend=backend,
                )
            except (ValueError, json.JSONDecodeError, RuntimeError) as exc:
                # Tolerant : si chunk fail, fallback FR + marker pending pour les
                # entries de ce chunk. Le fichier final est partiel mais utilisable.
                print(f"  [{dataset}/{lang}] chunk {i} fallback FR ({exc})", flush=True)
                result.issues.append(f"chunk {i} fallback FR after parse fail: {exc}")
                # Apply rename + add pending marker per entry
                fallback = []
                for src_entry in chunk:
                    renamed = _rename_fr_fields(src_entry, lang)
                    if isinstance(renamed, dict):
                        renamed["_translation_pending"] = True
                    fallback.append(renamed)
                parsed = fallback
                in_tok, out_tok = 0, 0
            result.input_tokens += in_tok
            result.output_tokens += out_tok

            # Validate chunk + pad with FR fallback si count mismatch
            chunk_issues = validate_chunk(parsed, chunk, spec["id_field"], lang)
            if chunk_issues:
                result.issues.extend([f"chunk {i}: {iss}" for iss in chunk_issues])
            if len(parsed) < len(chunk):
                # LLM a retourne moins d'entries : pad avec FR fallback marker
                missing = chunk[len(parsed):]
                print(f"  [{dataset}/{lang}] chunk {i} pad {len(missing)} FR fallback", flush=True)
                for src_entry in missing:
                    renamed = _rename_fr_fields(src_entry, lang)
                    if isinstance(renamed, dict):
                        renamed["_translation_pending"] = True
                    parsed.append(renamed)
            elif len(parsed) > len(chunk):
                # LLM a hallucine des entries en trop : tronque au size attendu
                parsed = parsed[:len(chunk)]

            # Rename any leftover _fr fields (defensive : si LLM les laisse)
            parsed = [_rename_fr_fields(e, lang) for e in parsed]
            translated_all.extend(parsed)

        if len(translated_all) != len(entries):
            result.error = f"final count mismatch: src={len(entries)} translated={len(translated_all)}"
            return result

        # Write output
        write_dataset_translated(dataset, lang, raw_source, translated_all)
        result.entries_translated = len(translated_all)

        # Cost
        p_in, p_out = _model_prices()
        result.cost_usd = (result.input_tokens / 1e6) * p_in + (result.output_tokens / 1e6) * p_out
        result.success = True
    except Exception as exc:
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.duration_s = time.time() - t0
    return result


def estimate_cost(datasets: list[str], langs: list[str]) -> tuple[int, int, float]:
    total_chars = 0
    for ds in datasets:
        path = CANON / DATASET_SPECS[ds]["filename"]
        total_chars += path.stat().st_size
    src_tokens = total_chars // 4
    sys_overhead = 800
    chunks_per_lang = max(1, sum(
        (sum(1 for _ in chunk_entries(load_dataset(ds)[1], DATASET_SPECS[ds]["chunk_entries_per_call"])))
        for ds in datasets
    ))
    in_per_lang = src_tokens + sys_overhead * chunks_per_lang
    out_per_lang = src_tokens
    in_total = in_per_lang * len(langs)
    out_total = out_per_lang * len(langs)
    p_in, p_out = _model_prices()
    cost = (in_total / 1e6) * p_in + (out_total / 1e6) * p_out
    return (in_total, out_total, cost)


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
    lines.append("# Phase i18n.7 — Rapport regeneration Phase H datasets")
    lines.append("")
    lines.append(f"Date : {datetime.now(UTC).isoformat()}")
    lines.append(f"Modele : `{MODEL_ID}`")
    lines.append("")
    lines.append("## Resume")
    lines.append(f"- Tasks : {len(results)} ({successes} succes)")
    lines.append(f"- Tokens input : {total_in:,}")
    lines.append(f"- Tokens output : {total_out:,}")
    lines.append(f"- **Cost total : ${total_cost:.2f}**")
    lines.append("")
    lines.append("## Par langue")
    lines.append("| Lang | Tasks OK | In tok | Out tok | Cost |")
    lines.append("|------|----------|--------|---------|------|")
    for lang in sorted(by_lang):
        rs = by_lang[lang]
        ok = sum(1 for r in rs if r.success and not r.error)
        in_t = sum(r.input_tokens for r in rs)
        out_t = sum(r.output_tokens for r in rs)
        cost = sum(r.cost_usd for r in rs)
        lines.append(f"| {lang} | {ok}/{len(rs)} | {in_t:,} | {out_t:,} | ${cost:.2f} |")
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    global MODEL_ID, QWEN_BASE_URL, QWEN_MODEL
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--langs", default=",".join(TARGET_LANGS))
    parser.add_argument(
        "--datasets",
        default=",".join(DATASET_SPECS),
        help=f"Comma-separated dataset names (default: all {len(DATASET_SPECS)})",
    )
    parser.add_argument(
        "--model",
        default="claude-haiku-4-5",
        choices=list(MODELS_PRICING),
        help="Anthropic model id (default: haiku-4-5 for ~3x cheaper than sonnet)",
    )
    parser.add_argument(
        "--backend",
        default="anthropic",
        choices=["anthropic", "qwen"],
        help="Backend LLM : 'anthropic' (paid API, fast, high quality) ou "
             "'qwen' (local llama.cpp on http://localhost:8080, free, slower). "
             "Qwen requires a llama.cpp server already running with Qwen3-4B model.",
    )
    parser.add_argument(
        "--qwen-url",
        default=QWEN_BASE_URL,
        help=f"Qwen server URL (default {QWEN_BASE_URL}). Ignore si --backend=anthropic.",
    )
    parser.add_argument(
        "--qwen-model",
        default=QWEN_MODEL,
        help=f"Qwen model id (default {QWEN_MODEL}). Ignore si --backend=anthropic.",
    )
    parser.add_argument(
        "--chunk-override",
        type=int,
        default=0,
        help="Override entries-per-chunk pour TOUS les datasets (defaut 0 = utilise la valeur par dataset). "
             "Recommande pour Qwen 4B : --chunk-override 5 (Qwen 4B a du mal avec les gros JSON).",
    )
    args = parser.parse_args()

    MODEL_ID = args.model
    QWEN_BASE_URL = args.qwen_url
    QWEN_MODEL = args.qwen_model
    langs = [s.strip() for s in args.langs.split(",") if s.strip()]
    datasets = [s.strip() for s in args.datasets.split(",") if s.strip()]
    for d in datasets:
        if d not in DATASET_SPECS:
            print(f"[X] dataset inconnu : {d}. Supportes : {list(DATASET_SPECS)}", file=sys.stderr)
            return 2
    for lg in langs:
        if lg not in LANG_NAMES:
            print(f"[X] lang inconnue : {lg}. Supportees : {list(LANG_NAMES)}", file=sys.stderr)
            return 2

    in_est, out_est, cost_est = estimate_cost(datasets, langs)
    print(f"Datasets : {datasets}")
    print(f"Langues : {langs}")
    print(f"Tasks total : {len(datasets) * len(langs)}")
    print(f"Backend : {args.backend}")
    if args.backend == "anthropic":
        print(f"Modele : {MODEL_ID}")
        print(f"Estimation : ~{in_est:,} in tok + ~{out_est:,} out tok = ~${cost_est:.2f}")
    else:
        print(f"Qwen URL : {QWEN_BASE_URL}, modele : {QWEN_MODEL}")
        print(f"Cost : $0 (local). Estimation tokens : ~{in_est:,} in + ~{out_est:,} out")

    if args.dry_run:
        print("\n[dry-run] aucune requete envoyee.")
        return 0
    if not args.execute:
        print("\n[!] passe --execute pour lancer.")
        return 1

    api_key: str | None = None
    if args.backend == "anthropic":
        api_key = os.environ.get("API_CLAUDE_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("[X] API_CLAUDE_KEY absente du .env", file=sys.stderr)
            return 3
    else:
        # Qwen : verifie que le serveur repond
        if httpx is None:
            print("[X] httpx requis pour --backend qwen", file=sys.stderr)
            return 3
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{QWEN_BASE_URL}/v1/models")
                resp.raise_for_status()
            print(f"[OK] Qwen server reachable at {QWEN_BASE_URL}")
        except Exception as exc:
            print(f"[X] Qwen server non joignable a {QWEN_BASE_URL}: {exc}", file=sys.stderr)
            print("[!] Demarre llama-server (ex: llama-server -m Qwen3-4B-UD-Q4_K_XL.gguf --port 8080)", file=sys.stderr)
            return 3

    glossary = load_glossary()
    print(f"Glossary : {len(glossary)} termes preserves\n")

    tasks = [(d, lg) for d in datasets for lg in langs]
    print(f"Lancement {args.workers} workers, {len(tasks)} tasks...\n")
    results: list[TaskResult] = []
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {
            ex.submit(
                process_dataset_lang, ds, lang, glossary, api_key,
                force=args.force, backend=args.backend, chunk_override=args.chunk_override,
            ): (ds, lang)
            for ds, lang in tasks
        }
        for fut in as_completed(futures):
            r = fut.result()
            tag = "OK" if r.success and not r.error else "FAIL"
            err_part = f" ERR={r.error[:80]}" if r.error else ""
            print(
                f"[{tag}] {r.dataset}/{r.lang}: "
                f"{r.entries_translated}/{r.entries_total} entries, {r.chunks} chunks, "
                f"{r.input_tokens:,}+{r.output_tokens:,} tok, ${r.cost_usd:.3f}, "
                f"{r.duration_s:.1f}s, {len(r.issues)} issues{err_part}",
                flush=True,
            )
            results.append(r)

    write_report(results)
    failures = [r for r in results if not r.success or r.error]
    if failures:
        print(f"\n[X] {len(failures)} task(s) en echec.", file=sys.stderr)
        return 4
    print("\n[OK] Phase 7 complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
