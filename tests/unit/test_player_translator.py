"""Phase i18n.8 : tests pour PlayerTranslator + helper process_player_input.

Couvre :
1. Heuristique de detection (CJK + latin accents + sets de marqueurs)
2. PlayerTranslator.detect avec mock LLM
3. PlayerTranslator.translate avec mock LLM
4. process(...) : verbatim si source == target
5. process(...) : detection + traduction succes
6. process(...) : backend down -> pending True, source brut sous cle target
7. process(...) : detection echouee -> fallback_source utilise
8. declare_goal + describe_goal_for_lang : roundtrip schema Phase 8
"""

from __future__ import annotations

from typing import Any

from shinobi.goals.declaration import (
    Goal,
    declare_goal,
    describe_goal_for_lang,
)
from shinobi.i18n.player_translator import (
    PlayerTranslator,
    detect_language_heuristic,
    process_player_input,
)

# === Fake HTTP client utilities =======================================


class _FakeResponse:
    def __init__(self, status_code: int, json_payload: Any) -> None:
        self.status_code = status_code
        self._payload = json_payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                "boom", request=None, response=None,  # type: ignore[arg-type]
            )

    def json(self) -> Any:
        return self._payload


class FakeClient:
    """Mock minimal compatible PlayerTranslator._http_client."""

    def __init__(self, responses: list[Any]) -> None:
        # `responses` est une liste d'elements, chaque element est soit :
        # - un dict (sera renvoye par .json())
        # - une str (sera enroule en {"choices":[{"message":{"content":str}}]})
        # - une exception (sera levee)
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def post(self, url: str, *, json: dict[str, Any], **_: Any) -> Any:
        self.calls.append({"url": url, "json": json})
        if not self._responses:
            raise AssertionError("FakeClient out of responses")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            payload = {"choices": [{"message": {"content": item}}]}
            return _FakeResponse(200, payload)
        return _FakeResponse(200, item)


# === 1. Heuristique ====================================================


def test_detect_heuristic_cjk_and_latin() -> None:
    # Hiragana -> ja
    assert detect_language_heuristic("私はRasengan を学びたい") == "ja"
    # Hangul -> ko
    assert detect_language_heuristic("나는 라센건을 배우고 싶다") == "ko"
    # Han pur -> zh
    assert detect_language_heuristic("我想学习螺旋丸") == "zh"
    # Umlaut allemand
    assert detect_language_heuristic("Ich möchte den Rasengan lernen") == "de"
    # Tilde portugaise
    assert detect_language_heuristic("Eu não quero aprender") == "pt-BR"
    # FR : marqueurs + diacritique
    assert detect_language_heuristic("Je veux apprendre le Rasengan") == "fr"
    # ES : marqueurs sans tilde portugaise
    assert (
        detect_language_heuristic("Yo quiero aprender el Rasengan muy rapido") == "es"
    )
    # EN : marqueurs anglais
    assert (
        detect_language_heuristic("I want to learn the Rasengan with my sensei") == "en"
    )
    # Vide / non discriminant -> None
    assert detect_language_heuristic("") is None
    assert detect_language_heuristic("Rasengan") is None


# === 2. detect via LLM mock ===========================================


def test_detect_llm_normalizes_codes() -> None:
    # LLM peut repondre "fr-FR", "FR", "pt", "zh-CN" : on les normalise.
    fake = FakeClient(["fr-FR"])
    pt = PlayerTranslator(http_client=fake)
    assert pt.detect("Je veux apprendre") == "fr"

    fake = FakeClient(["pt"])  # raw "pt" -> "pt-BR"
    pt = PlayerTranslator(http_client=fake)
    assert pt.detect("Eu quero aprender") == "pt-BR"

    fake = FakeClient(["unknown"])  # LLM dit unknown -> on tombe sur heuristique
    pt = PlayerTranslator(http_client=fake)
    # "I want to learn the Rasengan" contient marqueurs EN -> heuristique resout "en"
    assert pt.detect("I want to learn the Rasengan") == "en"


# === 3. translate via LLM mock ========================================


def test_translate_llm_success_and_quote_stripping() -> None:
    # Le LLM renvoie une chaine entouree de guillemets : on les strip.
    fake = FakeClient(['"I want to learn the Rasengan"'])
    pt = PlayerTranslator(http_client=fake)
    out = pt.translate(
        "Je veux apprendre le Rasengan", source="fr", target="en",
    )
    assert out == "I want to learn the Rasengan"

    # source == target : court-circuit, retourne None
    pt2 = PlayerTranslator(http_client=FakeClient([]))
    assert pt2.translate("hi", source="en", target="en") is None

    # langue non supportee : None
    pt3 = PlayerTranslator(http_client=FakeClient([]))
    assert pt3.translate("x", source="xx", target="en") is None


# === 4. process : source == target -> verbatim ========================


def test_process_source_equals_target_skips_translation() -> None:
    fake = FakeClient(["en"])  # 1 call : detection
    pt = PlayerTranslator(http_client=fake)
    src, translated, pending = pt.process(
        "I want to learn the Rasengan with my sensei",
        target_lang="en",
    )
    assert src == "en"
    assert translated == {}
    assert pending is False
    # Une seule call HTTP : detection. Pas de traduction.
    assert len(fake.calls) == 1


# === 5. process : detection + traduction succes =======================


def test_process_detects_and_translates() -> None:
    # Sequence: 1) detection -> "fr"  2) translate -> EN
    fake = FakeClient(["fr", "I want to learn the Rasengan"])
    pt = PlayerTranslator(http_client=fake)
    src, translated, pending = pt.process(
        "Je veux apprendre le Rasengan",
        target_lang="en",
    )
    assert src == "fr"
    assert translated == {"en": "I want to learn the Rasengan"}
    assert pending is False
    assert len(fake.calls) == 2


# === 6. process : backend down -> pending True ========================


def test_process_backend_down_marks_pending() -> None:
    import httpx

    # Detection echoue (HTTPError) -> tombe sur heuristique FR (diacritique + marqueurs)
    # Traduction echoue ensuite (HTTPError) -> pending=True avec source brut.
    fake = FakeClient([
        httpx.HTTPError("down"),
        httpx.HTTPError("down"),
    ])
    pt = PlayerTranslator(http_client=fake)
    src, translated, pending = pt.process(
        "Je veux apprendre le Rasengan",
        target_lang="en",
        fallback_source="fr",
    )
    assert src == "fr"  # via heuristique
    assert translated == {"en": "Je veux apprendre le Rasengan"}  # source brut
    assert pending is True


# === 7. process : detection echouee + fallback_source =================


def test_process_uses_fallback_source_when_detection_fails() -> None:
    # Texte purement neutre (Rasengan seul) : LLM dit "unknown", heuristique None.
    # On doit utiliser fallback_source="ja".
    fake = FakeClient(["unknown", "ラセンガンを学びたい"])
    pt = PlayerTranslator(http_client=fake)
    src, translated, pending = pt.process(
        "Rasengan",
        target_lang="ja",
        fallback_source="ja",
    )
    # fallback == target -> verbatim, pas de traduction
    assert src == "ja"
    assert translated == {}
    assert pending is False
    # Pas d'appel translate puisque source == target
    assert len(fake.calls) == 1

    # Cas symetrique : fallback != target -> on traduit
    fake2 = FakeClient(["unknown", "I want to learn the Rasengan"])
    pt2 = PlayerTranslator(http_client=fake2)
    src2, translated2, pending2 = pt2.process(
        "Rasengan",
        target_lang="en",
        fallback_source="fr",
    )
    assert src2 == "fr"
    assert translated2 == {"en": "I want to learn the Rasengan"}
    assert pending2 is False
    assert len(fake2.calls) == 2


# === 8. Goal schema + describe_goal_for_lang ==========================


def test_goal_schema_phase8_fields_roundtrip() -> None:
    # declare_goal accepte les nouveaux champs Phase 8
    g = declare_goal(
        description_player="Je veux apprendre le Rasengan",
        interpretation_canonical="apprendre rasengan",
        declared_at_year=8,
        declared_at_age=5,
        description_player_original_language="fr",
        description_player_translated={"en": "I want to learn the Rasengan"},
    )
    assert g.description_player_original_language == "fr"
    assert g.description_player_translated == {"en": "I want to learn the Rasengan"}

    # Roundtrip JSON (Pydantic serialise/parse)
    payload = g.model_dump_json()
    g2 = Goal.model_validate_json(payload)
    assert g2.description_player_original_language == "fr"
    assert g2.description_player_translated == {"en": "I want to learn the Rasengan"}

    # describe_goal_for_lang : lang == source -> verbatim
    assert (
        describe_goal_for_lang(g, "fr")
        == "Je veux apprendre le Rasengan"
    )
    # lang in translated -> traduction
    assert (
        describe_goal_for_lang(g, "en")
        == "I want to learn the Rasengan"
    )
    # lang inconnu -> fallback verbatim
    assert (
        describe_goal_for_lang(g, "ja")
        == "Je veux apprendre le Rasengan"
    )

    # Retrocompat : goal sans les nouveaux champs (defaults) -> verbatim
    g_old = declare_goal(
        description_player="hello",
        interpretation_canonical="hello",
        declared_at_year=8,
        declared_at_age=5,
    )
    assert g_old.description_player_original_language is None
    assert g_old.description_player_translated == {}
    assert describe_goal_for_lang(g_old, "fr") == "hello"


# === Bonus : helper module-level passe par defaut translator =========


def test_process_player_input_helper_with_explicit_translator() -> None:
    fake = FakeClient(["en"])  # detection only
    pt = PlayerTranslator(http_client=fake)
    src, translated, pending = process_player_input(
        "I want to learn the Rasengan",
        target_lang="en",
        translator=pt,
    )
    assert src == "en"
    assert translated == {}
    assert pending is False


def test_process_player_input_empty_text() -> None:
    fake = FakeClient([])  # 0 call attendue
    pt = PlayerTranslator(http_client=fake)
    src, translated, pending = pt.process("", target_lang="en")
    assert src is None
    assert translated == {}
    assert pending is False
    assert fake.calls == []
