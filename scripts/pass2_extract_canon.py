"""Pass 2 : extraction LLM ciblee par perso, via Groq gpt-oss-120b.

Contrat (cf. research/pass2-extraction-spec.md) :
- 1 appel API par perso
- System prompt strict avec regles "possede vs mentionne avec"
- Source_quote obligatoire pour chaque fact extrait
- Output JSON only
- Validation post-extraction par grep NFKD + edit_distance <= 5
- Hard cost limit : $5 cumule (le script s'arrete et demande confirmation)
- max_concurrency : 10 requetes en parallele (rate limit Groq dev plan)
- output cap : 2000 tokens

Usage :
    export GROQ_API_KEY=gsk_...
    python scripts/pass2_extract_canon.py --ids-from data/canonical/_pass2_targets_50.txt
    python scripts/pass2_extract_canon.py --ids-from <file> --resume

Le fichier --ids-from contient un char_id par ligne (commentaires #
ignores). Les outputs sont ecrits dans data/canonical/_pass2_output/<id>.json.

Avec --resume, les ids ayant deja un output sont skip (utile apres crash).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

import httpx

# Local imports
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pass2_normalize import (
    load_canon_context,
    normalize_extraction,
)

ROOT = Path(__file__).resolve().parents[1]
CHARACTERS_PATH = ROOT / "data" / "canonical" / "characters.json"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "canonical" / "_pass2_output"

# Groq config (defaults, overridable via CLI)
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Pricing tables (per 1M tokens). Add models as needed.
MODEL_PRICING = {
    "openai/gpt-oss-120b": (0.15, 0.60),
    "llama-3.3-70b-versatile": (0.59, 0.79),
    "qwen/qwen3-32b": (0.29, 0.59),
}

HARD_COST_LIMIT_USD = 5.0
DEFAULT_MAX_CONCURRENCY = 1   # sequential by default to avoid Groq json_object concurrency bug
DEFAULT_MAX_TOKENS = 4000     # raised from 2000 (Hiruzen needs ~3200 to complete)
TEMPERATURE = 0.0
REQUEST_TIMEOUT = 180.0
LOG_EVERY_N = 5

# Retry policy
MAX_RETRIES_TRANSIENT = 3      # ReadTimeout / ConnectError / ConnectTimeout
MAX_RETRIES_VALIDATION = 2     # HTTP 400 json_validate_failed

# Sections de wiki a inclure dans le user prompt, par ordre de priorite
SECTIONS_PRIORITY = [
    "Background",
    "Personality",
    "Abilities",
    "Part I",
    "Part II",
    "Blank Period",
    "New Era",
    "New Era: Part I",
    "New Era: Part II",
    "Quotes",
    "Plot Overview",
    "Legacy",
]

# Soft cap : si le user prompt depasse cette taille (chars), on tronque
# en supprimant les sections les moins prioritaires en premier.
USER_PROMPT_SOFT_CAP_CHARS = 26000  # ~6500 tokens, laisse de la marge sur 8k

# System prompt - DOIT correspondre au bloc section 2 de
# research/pass2-extraction-spec.md. ~1100 tokens cl100k_base.
SYSTEM_PROMPT = """\
You are a canon-fact extractor for the Naruto universe. Your sole task
is to extract structured facts about a single character from the
character's wiki_sections (provided in the user message).

ABSOLUTE RULES (violations cause rejection):

1. JSON ONLY. Your output starts with `{` and ends with `}`. No prose,
   no markdown code fences, no explanation, no greeting. Just the JSON
   object matching the schema.

2. SOURCE_QUOTE REQUIRED. Every extracted fact MUST cite a verbatim
   source_quote from the wiki_sections of THIS character. The quote
   must be a contiguous substring of the source text. If you cannot
   cite, set the field to null and confidence to null.

3. NEVER GUESS. If the wiki does not state a fact, the field is null.
   No inference from prior knowledge of Naruto. No completion from
   what a Naruto fan "would know". The wiki_sections of this character
   are your ONLY source of truth.

4. POSSESSION vs MENTION. A fact is extracted only if the wiki states
   the character POSSESSES the attribute, not merely that the
   attribute appears nearby:
   - "Hiruzen battled Orochimaru who used the Sharingan"
     => Sharingan is NOT a kekkei_genkai of Hiruzen.
   - "Naruto witnessed Itachi's Mangekyo Sharingan"
     => Mangekyo Sharingan is NOT a kekkei_genkai of Naruto.
   - "Kakashi explained that the Yondaime had a son named Naruto"
     => In Kakashi's wiki, this does NOT make Naruto Kakashi's son.

5. THIRD-PARTY FACTS STAY OUT. The wiki of character X may contain
   facts about character Y. Do NOT attribute Y's facts to X. Only
   facts where the subject is THIS character (X) are extracted.

6. CONFIDENCE LEVELS:
   - "high" : the value is explicitly stated in the source_quote
     (e.g. "Itachi was born in year -7" -> birth_year=-7, high).
   - "medium" : the value is computed by simple arithmetic from a
     fact in the quote (e.g. "Itachi was 13 when X happened in
     year 6" -> birth_year=-7, medium).
   - "low" : the quote is hedged ("some say...", "it is rumored
     that..."), or requires multi-step inference. Use sparingly.

7. AGE AND TIME ANCHORS. Year 0 is canonically the birth of Naruto
   Uzumaki and the Nine-Tails attack on Konoha. All years are
   relative to that anchor. If the wiki gives an age at a known canon
   event, capture both an age_at_event entry and (if computable) a
   birth_year value derived from it.

8. RELATIVE AGES. If the wiki states X is N years older/younger than
   Y, capture this in relative_age_to (positive = older, negative =
   younger).

9. Output schema is strict. Do not add fields. Do not omit fields.
   Use null for absent values. Lists are empty (not null) when no
   facts apply.

10. Ranks vocabulary (rank_progression.rank): one of
    "academy_student", "genin", "chunin", "tokubetsu_jonin", "jonin",
    "anbu", "sannin", "kage", "missing_nin", "civilian". Other ranks
    => use "civilian" with the description in source_quote.

11. CHARACTER ID FORMAT. Output character ids in lowercase snake_case.
    Format depends on the character:
    - Characters with a clan in their full name : "clan_lastname_firstname"
      (e.g. "Naruto Uzumaki" -> "uzumaki_naruto", "Butsuma Senju" ->
      "senju_butsuma", "Mikoto Uchiha" -> "uchiha_mikoto",
      "Kakashi Hatake" -> "hatake_kakashi"). Clan goes FIRST.
    - Characters without a clan : just the lowercase romaji
      (e.g. "Konan" -> "konan", "Deidara" -> "deidara",
      "Tenten" -> "tenten", "Gaara" -> "gaara", "Nagato" -> "nagato").
      Do NOT invent clan prefixes for these characters.
    - DO NOT confuse roles (Hokage, Kazekage) with clans. "Kazekage Kankuro"
      is NOT a valid id ; use "kankuro".
    - NEVER output the English-form name like "Butsuma Senju" or
      "Kushina Uzumaki" as a value. Always translate to the canonical
      lowercase snake_case slug.
    - This applies to character_id at the root and to all character
      ids inside parents, children, siblings, team_members, spouse,
      sensei_id, relative_age_to.other_char.

12. CANONICAL SLUGS for kekkei_genkai_possessed and natures_possessed.

    NATURES (lowercase romaji slug, go in natures_possessed):
    - "Fire Release" -> "katon"
    - "Water Release" -> "suiton"
    - "Earth Release" -> "doton"
    - "Wind Release" -> "fuuton"
    - "Lightning Release" -> "raiton"
    - "Yin Release" -> "inton"
    - "Yang Release" -> "youton_yang"

    COMBINATORY KEKKEI GENKAI (lowercase English snake_case slug, go in
    kekkei_genkai_possessed) :
    - "Wood Release" -> "wood_release"
    - "Ice Release" -> "ice_release"
    - "Lava Release" / "Scorch Release" -> "lava_release"
    - "Magnet Release" -> "magnet_release"
    - "Explosion Release" -> "explosion_release"
    - "Crystal Release" -> "crystal_release"
    - "Storm Release" -> "storm_release"
    - "Boil Release" -> "boil_release"
    - "Swift Release" / "Dust Release" -> "swift_release"
    - "Dark Release" -> "dark_release"
    - "Mud Release" -> "mud_release"
    - "Steel Release" -> "steel_release"

    DOJUTSU (kekkei_genkai_possessed):
    - "Sharingan" -> "sharingan"
    - "Mangekyo Sharingan" -> "mangekyo_sharingan"
    - "Rinnegan" -> "rinnegan"
    - "Byakugan" -> "byakugan"
    - "Tenseigan" -> "tenseigan"
    - "Jogan" -> "jogan"
    - "Ketsuryugan" -> "ketsuryugan"

    OTHER KG:
    - "Shikotsumyaku" / "Dead Bone Pulse" -> "shikotsumyaku"

    NEVER output the title-case English name ("Wood Release",
    "Sharingan") as a value. Always the lowercase snake_case slug.

13. SCHEMA COMPLETENESS. EVERY field listed in the OUTPUT SCHEMA below
    MUST appear in your output, in the same order, with value=null when
    you have no fact. Empty lists ([]) when no list items apply.
    DO NOT skip fields. The validator rejects outputs missing fields.

14. VERBATIM SOURCE_QUOTE. The source_quote MUST be a CHARACTER-FOR-
    CHARACTER substring of the source text. Do NOT paraphrase. Do NOT
    combine two sentences. Do NOT skip words for brevity. Copy special
    characters EXACTLY (e.g. "Fū" stays "Fū", not "Fu" ; typographic
    apostrophes stay typographic). A grep validator will check every
    quote.

OUTPUT SCHEMA (use exactly these field names, all required):
{
  "character_id": "<char_id>",
  "extraction_metadata": {
    "wiki_sections_used": [...],
    "extractor_notes": "string or null"
  },
  "fields": {
    "birth_year": {"value": int|null, "source_quote": str|null, "confidence": "high|medium|low"|null, "derivation_method": "explicit|computed_from_event"|null},
    "death_year": {"value": int|null, "source_quote": str|null, "confidence": "high|medium|low"|null, "derivation_method": "explicit|computed_from_event"|null},
    "death_arc": {"value": str|null, "source_quote": str|null, "confidence": "high|medium|low"|null},
    "village_of_origin": {"value": str|null, "source_quote": str|null, "confidence": "high|medium|low"|null},
    "clan": {"value": str|null, "source_quote": str|null, "confidence": "high|medium|low"|null},
    "kekkei_genkai_possessed": [{"value": str, "source_quote": str, "confidence": "high|medium|low"}, ...],
    "natures_possessed": [{"value": str, "source_quote": str, "confidence": "high|medium|low"}, ...],
    "team_name": {"value": str|null, "source_quote": str|null, "confidence": "high|medium|low"|null},
    "team_members": [{"value": str, "source_quote": str, "confidence": "high|medium|low"}, ...],
    "sensei_id": {"value": str|null, "source_quote": str|null, "confidence": "high|medium|low"|null},
    "parents": [{"value": str, "source_quote": str, "confidence": "high|medium|low"}, ...],
    "children": [{"value": str, "source_quote": str, "confidence": "high|medium|low"}, ...],
    "siblings": [{"value": str, "source_quote": str, "confidence": "high|medium|low"}, ...],
    "spouse": {"value": str|null, "source_quote": str|null, "confidence": "high|medium|low"|null},
    "rank_progression": [{"rank": str, "year_approx": int|null, "source_quote": str, "confidence": "high|medium|low"}, ...],
    "first_appearance_arc": {"value": str|null, "source_quote": str|null, "confidence": "high|medium|low"|null},
    "key_techniques": [{"value": str, "source_quote": str, "confidence": "high|medium|low"}, ...],
    "age_at_event": [{"arc": str, "age": int, "source_quote": str}, ...],
    "relative_age_to": [{"other_char": str, "delta_years": int, "source_quote": str}, ...],
    "is_jinchuuriki": {"value": bool|null, "source_quote": str|null, "confidence": "high|medium|low"|null},
    "tailed_beast": {"value": str|null, "source_quote": str|null, "confidence": "high|medium|low"|null}
  }
}
"""

# Punctuation map for source-quote validation (typographic -> ASCII).
_PUNCT_MAP = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-",
    "…": "...",
    " ": " ",
})


@dataclass
class CostTracker:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def cost_usd(self) -> float:
        prices = MODEL_PRICING.get(self.model, (0.15, 0.60))
        return (
            self.input_tokens * prices[0] / 1_000_000
            + self.output_tokens * prices[1] / 1_000_000
        )


@dataclass
class ExtractionStats:
    completed: int = 0
    succeeded: int = 0
    failed: int = 0
    quotes_total: int = 0
    quotes_exact: int = 0
    quotes_near: int = 0
    quotes_miss: int = 0
    fields_filled: int = 0  # somme des fields avec value non-null sur tous les outputs
    fields_total: int = 0   # 21 * nb_outputs (theorique)
    norm_changes: int = 0
    norm_non_canonical: int = 0
    failures: list[tuple[str, str]] = field(default_factory=list)
    retries_transient: int = 0
    retries_validation: int = 0


def normalize(text: str) -> str:
    if not text:
        return ""
    n = text.translate(_PUNCT_MAP)
    n = unicodedata.normalize("NFKD", n)
    n = n.lower()
    n = " ".join(n.split())
    return n


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i] + [0] * len(b)
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = cur
    return prev[len(b)]


def best_window_distance(quote: str, source: str) -> int:
    if not quote or not source:
        return max(len(quote), len(source))
    n, m = len(source), len(quote)
    if m > n:
        return levenshtein(quote, source)
    best = m
    step = max(1, m // 4)
    candidates = list(range(0, n - m + 1, step))
    if (n - m) not in candidates:
        candidates.append(n - m)
    for start in candidates:
        d = levenshtein(quote, source[start:start + m])
        if d < best:
            best = d
            if d == 0:
                return 0
    return best


def collect_quotes_from_extraction(extraction: dict) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    fields = extraction.get("fields", {}) or {}
    for fname, val in fields.items():
        if isinstance(val, dict) and "source_quote" in val:
            q = val.get("source_quote")
            if q:
                out.append((fname, q))
        elif isinstance(val, list):
            for i, item in enumerate(val):
                if isinstance(item, dict) and item.get("source_quote"):
                    label = item.get("value", item.get("rank", "?"))
                    out.append((f"{fname}[{i}].{label}", item["source_quote"]))
    return out


def validate_quotes_inline(extraction: dict, source_text: str) -> tuple[int, int, int, int]:
    """Validate every source_quote of an extraction. Returns (total, exact, near, miss)."""
    norm_source = normalize(source_text)
    quotes = collect_quotes_from_extraction(extraction)
    exact = near = miss = 0
    for _, q in quotes:
        nq = normalize(q)
        if nq in norm_source:
            exact += 1
        else:
            d = best_window_distance(nq, norm_source)
            if d <= 5:
                near += 1
            else:
                miss += 1
    return len(quotes), exact, near, miss


def build_user_message(char: dict) -> str:
    sections = char.get("wiki_sections") or {}
    chunks: list[tuple[str, str]] = []
    for s in SECTIONS_PRIORITY:
        if sections.get(s):
            chunks.append((s, f"[{s}]\n{sections[s]}"))
    if not chunks:
        # Fallback : take whatever sections exist.
        for s, txt in sections.items():
            if txt:
                chunks.append((s, f"[{s}]\n{txt}"))

    body = "\n\n".join(c[1] for c in chunks)
    # Soft cap : si trop long, drop sections les moins prioritaires.
    while len(body) > USER_PROMPT_SOFT_CAP_CHARS and len(chunks) > 1:
        # drop la derniere (moins prioritaire)
        chunks.pop()
        body = "\n\n".join(c[1] for c in chunks)

    return (
        f"character_id: {char['id']}\n"
        f"name_romaji: {char.get('name_romaji', char['id'])}\n\n"
        f"wiki_sections:\n\n{body}\n\n"
        f"Extract facts about {char.get('name_romaji', char['id'])} following the schema. JSON only."
    )


async def call_groq_one(
    client: httpx.AsyncClient,
    char: dict,
    api_key: str,
    model: str,
    max_tokens: int,
    stats: ExtractionStats,
) -> tuple[bool, dict | None, str | None, int, int]:
    """Returns (success, parsed_json, error_msg, input_tokens, output_tokens).

    Implements retry logic :
    - ReadTimeout / ConnectError / ConnectTimeout : up to MAX_RETRIES_TRANSIENT
      with exponential backoff (1s, 2s, 4s).
    - HTTP 400 json_validate_failed : up to MAX_RETRIES_VALIDATION with backoff.
    """
    user_msg = build_user_message(char)
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        "temperature": TEMPERATURE,
        "max_tokens": max_tokens,
        "response_format": {"type": "json_object"},
    }

    transient_retries = 0
    validation_retries = 0

    while True:
        try:
            r = await client.post(
                "/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError) as exc:
            if transient_retries < MAX_RETRIES_TRANSIENT:
                transient_retries += 1
                stats.retries_transient += 1
                backoff = 2 ** (transient_retries - 1)  # 1s, 2s, 4s
                await asyncio.sleep(backoff)
                continue
            return (
                False, None,
                f"{type(exc).__name__} after {transient_retries} retries: {exc!r}",
                0, 0,
            )
        except httpx.HTTPError as exc:
            return (False, None, f"{type(exc).__name__}: {exc!r}", 0, 0)

        # 400 json_validate_failed -> retry once
        if r.status_code == 400 and "json_validate_failed" in r.text:
            if validation_retries < MAX_RETRIES_VALIDATION:
                validation_retries += 1
                stats.retries_validation += 1
                await asyncio.sleep(2 * validation_retries)
                continue
            return (
                False, None,
                f"json_validate_failed after {validation_retries} retries; body: {r.text[:300]}",
                0, 0,
            )

        if r.status_code != 200:
            return (False, None, f"HTTP {r.status_code}: {r.text[:300]}", 0, 0)

        try:
            data = r.json()
        except json.JSONDecodeError as exc:
            return (False, None, f"non-JSON response: {exc}", 0, 0)

        usage = data.get("usage", {}) or {}
        in_tok = int(usage.get("prompt_tokens", 0))
        out_tok = int(usage.get("completion_tokens", 0))

        try:
            content = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError) as exc:
            return (False, None, f"malformed response: {exc}", in_tok, out_tok)

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            return (
                False, None,
                f"JSONDecodeError: {exc}; last 200 chars: {content[-200:]!r}",
                in_tok, out_tok,
            )

        return (True, parsed, None, in_tok, out_tok)


def assemble_source_text(char: dict) -> str:
    sections = char.get("wiki_sections") or {}
    return "\n\n".join(sections.values())


async def process_one(
    sem: asyncio.Semaphore,
    client: httpx.AsyncClient,
    char: dict,
    api_key: str,
    model: str,
    max_tokens: int,
    output_dir: Path,
    canon_ctx,  # CanonContext for normalization
    cost: CostTracker,
    stats: ExtractionStats,
    log_fp,
    abort_flag: list[bool],
    n_total: int,
) -> None:
    async with sem:
        if abort_flag[0]:
            return

        ok, parsed, err, in_tok, out_tok = await call_groq_one(
            client, char, api_key, model, max_tokens, stats
        )
        cost.input_tokens += in_tok
        cost.output_tokens += out_tok
        stats.completed += 1

        log_fp.write(
            f"{char['id']} | success={ok} | in={in_tok} | out={out_tok} | "
            f"cumul=${cost.cost_usd:.4f} | err={err or '-'}\n"
        )
        log_fp.flush()

        if ok and parsed is not None:
            # Normalize (deterministic, local, no LLM)
            normalized, norm_report = normalize_extraction(parsed, canon_ctx)
            stats.norm_changes += norm_report.total_normalized
            stats.norm_non_canonical += len(norm_report.flags)

            # Count fields filled
            _, n_filled = _count_fields(normalized)
            stats.fields_total += 21  # full schema
            stats.fields_filled += n_filled

            # Persist
            out_path = output_dir / f"{char['id']}.json"
            out_path.write_text(
                json.dumps(normalized, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            # Persist normalization report (per-char)
            if norm_report.flags:
                flag_path = output_dir / f"{char['id']}.flags.json"
                flag_path.write_text(
                    json.dumps(
                        {"char_id": char["id"], "flags": norm_report.flags},
                        indent=2, ensure_ascii=False,
                    ) + "\n",
                    encoding="utf-8",
                )

            # Validate quotes inline (after normalization)
            src = assemble_source_text(char)
            total, exact, near, miss = validate_quotes_inline(normalized, src)
            stats.quotes_total += total
            stats.quotes_exact += exact
            stats.quotes_near += near
            stats.quotes_miss += miss
            stats.succeeded += 1
        else:
            stats.failed += 1
            stats.failures.append((char["id"], err or "unknown"))

        if stats.completed % LOG_EVERY_N == 0 or stats.completed == n_total:
            print(
                f"  [{stats.completed}/{n_total}] cumulative_cost=${cost.cost_usd:.4f} "
                f"ok={stats.succeeded} fail={stats.failed} "
                f"quotes={stats.quotes_total} (exact={stats.quotes_exact}, "
                f"near={stats.quotes_near}, miss={stats.quotes_miss}) "
                f"norm_flags={stats.norm_non_canonical} "
                f"retries(t={stats.retries_transient},v={stats.retries_validation})"
            )

        if cost.cost_usd > HARD_COST_LIMIT_USD:
            abort_flag[0] = True
            print(
                f"\n!!! HARD COST LIMIT ${HARD_COST_LIMIT_USD} REACHED. Aborting. "
                f"Remaining tasks will be skipped. Current cost: ${cost.cost_usd:.4f}.\n"
                f"Re-run with --resume to continue (after manual confirmation if needed)."
            )


def _count_fields(extraction: dict) -> tuple[int, int]:
    """Returns (n_present, n_filled) over the 21 schema fields."""
    fields = extraction.get("fields", {}) or {}
    schema_fields = (
        "birth_year", "death_year", "death_arc", "village_of_origin", "clan",
        "kekkei_genkai_possessed", "natures_possessed", "team_name", "team_members",
        "sensei_id", "parents", "children", "siblings", "spouse",
        "rank_progression", "first_appearance_arc", "key_techniques",
        "age_at_event", "relative_age_to", "is_jinchuuriki", "tailed_beast",
    )
    n_present = sum(1 for f in schema_fields if f in fields)
    n_filled = 0
    for f in schema_fields:
        v = fields.get(f)
        if (isinstance(v, dict) and v.get("value") not in (None, "")) or (isinstance(v, list) and len(v) > 0):
            n_filled += 1
    return n_present, n_filled


async def main_async(
    target_ids: list[str],
    characters: list[dict],
    api_key: str,
    model: str,
    max_tokens: int,
    max_concurrency: int,
    output_dir: Path,
    resume: bool,
) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "_pass2_run.log"
    char_index = {c["id"]: c for c in characters}

    todo: list[dict] = []
    skipped_resume = 0
    skipped_unknown = 0
    for cid in target_ids:
        if cid not in char_index:
            print(f"  WARN: '{cid}' not in characters.json, skipping")
            skipped_unknown += 1
            continue
        if resume and (output_dir / f"{cid}.json").exists():
            skipped_resume += 1
            continue
        todo.append(char_index[cid])

    print(f"Targets: {len(target_ids)}, todo: {len(todo)}, "
          f"skipped_resume: {skipped_resume}, skipped_unknown: {skipped_unknown}")
    if not todo:
        print("Nothing to do.")
        return 0

    print("Loading canon context for normalization...")
    canon_ctx = load_canon_context()
    print(f"  KG={len(canon_ctx.kg_ids)} natures={len(canon_ctx.nature_ids)} "
          f"chars={len(canon_ctx.char_ids)} clans={len(canon_ctx.clan_ids)} "
          f"villages={len(canon_ctx.village_ids)}")

    cost = CostTracker(model=model)
    stats = ExtractionStats()
    abort_flag = [False]
    sem = asyncio.Semaphore(max_concurrency)

    pricing = MODEL_PRICING.get(model, (0.15, 0.60))
    print(f"Starting extraction. Model={model}, max_concurrency={max_concurrency}, "
          f"max_tokens={max_tokens}, hard_cost_limit=${HARD_COST_LIMIT_USD}")
    print(f"Pricing: ${pricing[0]}/M input, ${pricing[1]}/M output")
    print(f"Output dir: {output_dir.relative_to(ROOT)}")

    with log_path.open("a", encoding="utf-8") as log_fp:
        async with httpx.AsyncClient(base_url=GROQ_BASE_URL, timeout=REQUEST_TIMEOUT) as client:
            await asyncio.gather(*(
                process_one(
                    sem, client, char, api_key, model, max_tokens, output_dir,
                    canon_ctx, cost, stats, log_fp, abort_flag, len(todo),
                )
                for char in todo
            ))

    print()
    print("=" * 60)
    print(f"DONE. Final cost: ${cost.cost_usd:.4f}  (input={cost.input_tokens:,} tokens, "
          f"output={cost.output_tokens:,} tokens)")
    print(f"  Completed : {stats.completed}/{len(todo)}")
    print(f"  Succeeded : {stats.succeeded}")
    print(f"  Failed    : {stats.failed}")
    if stats.fields_total:
        print(f"  Fields filled rate : {stats.fields_filled}/{stats.fields_total} "
              f"({100*stats.fields_filled/stats.fields_total:.1f}%)")
    print(f"  Quotes total : {stats.quotes_total}")
    if stats.quotes_total:
        print(f"    exact : {stats.quotes_exact} ({100*stats.quotes_exact/stats.quotes_total:.1f}%)")
        print(f"    near  : {stats.quotes_near}  ({100*stats.quotes_near/stats.quotes_total:.1f}%)")
        print(f"    miss  : {stats.quotes_miss}  ({100*stats.quotes_miss/stats.quotes_total:.1f}%)")
    print(f"  Norm changes : {stats.norm_changes}, non_canonical_flags : {stats.norm_non_canonical}")
    print(f"  Retries : transient={stats.retries_transient}, validation={stats.retries_validation}")

    if stats.failures:
        print("\n  Failures:")
        for cid, err in stats.failures:
            print(f"    {cid}: {err[:200]}")

    print(f"\nOutputs in {output_dir.relative_to(ROOT)}/")
    print(f"Run log: {log_path.relative_to(ROOT)}")

    return 0 if stats.failed == 0 and not abort_flag[0] else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids-from", type=Path, required=True,
                        help="Path to a text file with one char_id per line ('#' lines ignored)")
    parser.add_argument("--resume", action="store_true",
                        help="Skip ids that already have an output file")
    parser.add_argument("--model", default=GROQ_DEFAULT_MODEL,
                        help=f"Groq model id (default: {GROQ_DEFAULT_MODEL})")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                        help=f"Output cap per call (default: {DEFAULT_MAX_TOKENS})")
    parser.add_argument("--max-concurrency", type=int, default=DEFAULT_MAX_CONCURRENCY,
                        help=f"Parallel calls (default: {DEFAULT_MAX_CONCURRENCY}, "
                             "sequential to avoid Groq json_object concurrency bug)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help=f"Where outputs go (default: {DEFAULT_OUTPUT_DIR.relative_to(ROOT)})")
    args = parser.parse_args()

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("ERROR: GROQ_API_KEY env var is not set.", file=sys.stderr)
        return 1

    if not args.ids_from.exists():
        print(f"ERROR: --ids-from file does not exist: {args.ids_from}", file=sys.stderr)
        return 1

    target_ids: list[str] = []
    for line in args.ids_from.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        target_ids.append(line)
    print(f"Loaded {len(target_ids)} target ids from {args.ids_from}")

    characters = json.loads(CHARACTERS_PATH.read_text(encoding="utf-8"))

    return asyncio.run(main_async(
        target_ids, characters, api_key,
        model=args.model,
        max_tokens=args.max_tokens,
        max_concurrency=args.max_concurrency,
        output_dir=args.output_dir,
        resume=args.resume,
    ))


if __name__ == "__main__":
    sys.exit(main())
