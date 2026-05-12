"""Phase 5 : tests unitaires complets pour le systeme d'objectifs (goals).

Couvre :
- 5.1 declaration.py : declare_goal, abandon_goal, complete_goal
- 5.2 pricing.py : base_money_price, price_for_*, negotiate_price
- 5.3 breadcrumbs.py : make_breadcrumb, mark_completed, mark_revealed
- 5.4 completion.py : check_breadcrumb_completion, check_goal_completion,
  check_goal_by_target (8 types de conditions)
- 5.5 pathfinder.py : structure PathfinderRequest/Response (LLM mocked)
- 5.6/5.7 : integration moteur (CLI wires)
- 5.9 : test e2e declaration -> indice -> sous-objectif -> completion
"""
from __future__ import annotations

import pytest

from shinobi.engine.character import Character, KnownTechnique
from shinobi.engine.stats import CoreStats, ExtendedStats
from shinobi.goals.breadcrumbs import (
    Breadcrumb,
    BreadcrumbPrice,
    CompletionCondition,
    make_breadcrumb,
    mark_completed,
    mark_revealed,
)
from shinobi.goals.completion import (
    check_breadcrumb_completion,
    check_goal_by_target,
    check_goal_completion,
)
from shinobi.goals.declaration import (
    Goal,
    GoalTargetType,
    abandon_goal,
    complete_goal,
    declare_goal,
)
from shinobi.goals.pricing import (
    base_money_price,
    negotiate_price,
    price_for_anbu,
    price_for_orochimaru,
    price_for_yakuza,
    price_in_money,
)
from shinobi.types import ActionType, GoalStatus, Gender


def _make_character(**overrides) -> Character:
    base = {
        "id": "test_id",
        "name": "Test",
        "gender": Gender.female,
        "birth_year": 5,
        "birth_date": "06-15",
        "age_years": 12,
        "village_of_origin": "konohagakure",
        "current_village": "konohagakure",
        "current_location": "konohagakure",
        "rank": "genin",
        "stats": CoreStats(),
        "extended_stats": ExtendedStats(),
    }
    base.update(overrides)
    return Character(**base)


# === 5.1 declaration ====================================================


def test_declare_goal_creates_unique_id() -> None:
    """declare_goal genere un id unique."""
    g1 = declare_goal(
        description_player="apprendre rasengan",
        interpretation_canonical="learn rasengan canonical",
        declared_at_year=12, declared_at_age=12,
    )
    g2 = declare_goal(
        description_player="apprendre rasengan",
        interpretation_canonical="learn rasengan canonical",
        declared_at_year=12, declared_at_age=12,
    )
    assert g1.id != g2.id
    assert g1.status == GoalStatus.declared


def test_declare_goal_with_target_type() -> None:
    """declare_goal accepte target_type + target_id structurés."""
    g = declare_goal(
        description_player="apprendre rasengan",
        interpretation_canonical="learn rasengan",
        declared_at_year=12, declared_at_age=12,
        target_type=GoalTargetType.learn_technique,
        target_id="rasengan",
        declared_priority=8,
    )
    assert g.target_type == "learn_technique"
    assert g.target_id == "rasengan"
    assert g.declared_priority == 8


def test_abandon_goal_sets_status_and_year() -> None:
    g = declare_goal(
        description_player="x", interpretation_canonical="y",
        declared_at_year=12, declared_at_age=12,
    )
    abandoned = abandon_goal(g, year=15)
    assert abandoned.status == GoalStatus.abandoned
    assert abandoned.abandoned_at_year == 15
    # Original immutable
    assert g.status == GoalStatus.declared


def test_complete_goal_sets_status_and_year() -> None:
    g = declare_goal(
        description_player="x", interpretation_canonical="y",
        declared_at_year=12, declared_at_age=12,
    )
    completed = complete_goal(g, year=18)
    assert completed.status == GoalStatus.completed
    assert completed.completed_at_year == 18


# === 5.2 pricing =========================================================


def test_base_money_price_scales_with_strategic_value() -> None:
    p1 = base_money_price(value_strategique=1.0)
    p2 = base_money_price(value_strategique=5.0)
    assert p2 == 5 * p1
    assert p1 == 1000


def test_base_money_price_with_rank_factor() -> None:
    p_normal = base_money_price(value_strategique=1.0, target_rank_factor=1.0)
    p_kage = base_money_price(value_strategique=1.0, target_rank_factor=3.0)
    assert p_kage == 3 * p_normal


def test_price_for_anbu_returns_favor_type() -> None:
    p = price_for_anbu(value_strategique=2.0)
    assert p.type == "favor"
    assert p.amount == 2.0


def test_price_for_anbu_minimum_one() -> None:
    """Anbu requiert toujours >=1 favor meme si value_strategique faible."""
    p = price_for_anbu(value_strategique=0.1)
    assert p.amount == 1.0


def test_price_for_yakuza_returns_political() -> None:
    p = price_for_yakuza(value_strategique=3.0)
    assert p.type == "political"
    assert p.amount == 3.0


def test_price_for_orochimaru_is_moral() -> None:
    p = price_for_orochimaru()
    assert p.type == "moral"
    assert "experience" in p.description.lower()


def test_price_in_money_uses_base_formula() -> None:
    p = price_in_money(value_strategique=2.0, target_rank_factor=1.5)
    assert p.type == "money"
    assert p.amount == 3000  # 1000 * 2.0 * 1.5


def test_negotiate_price_strong_success_halves_amount() -> None:
    base = price_in_money(value_strategique=1.0)  # 1000
    negotiated = negotiate_price(base, success_margin=15)
    assert negotiated.amount == 500  # x0.5


def test_negotiate_price_moderate_success_20pct_off() -> None:
    base = price_in_money(value_strategique=1.0)
    negotiated = negotiate_price(base, success_margin=5)
    assert negotiated.amount == 800  # x0.8


def test_negotiate_price_failure_increases_amount() -> None:
    base = price_in_money(value_strategique=1.0)
    negotiated = negotiate_price(base, success_margin=-10)
    assert negotiated.amount == 1300  # x1.3


def test_negotiate_price_no_amount_returns_unchanged() -> None:
    """Une favor sans amount ne se negocie pas."""
    base = BreadcrumbPrice(type="favor", description="x", amount=None)
    out = negotiate_price(base, success_margin=15)
    assert out.amount is None


# === 5.3 breadcrumbs =====================================================


def test_make_breadcrumb_creates_unique_id() -> None:
    bc1 = make_breadcrumb(
        parent_goal_id="g1", sequence_index=0,
        description="Aller a Konoha", canonical_basis="canon",
        completion_conditions=[],
    )
    bc2 = make_breadcrumb(
        parent_goal_id="g1", sequence_index=1,
        description="Parler a Iruka", canonical_basis="canon",
        completion_conditions=[],
    )
    assert bc1.id != bc2.id
    assert not bc1.revealed
    assert not bc1.completed


def test_mark_completed_sets_completed_and_year() -> None:
    bc = make_breadcrumb(
        parent_goal_id="g1", sequence_index=0,
        description="x", canonical_basis="y",
        completion_conditions=[],
    )
    done = mark_completed(bc, year=15)
    assert done.completed
    assert done.completed_at_year == 15
    # Original immutable
    assert not bc.completed


def test_mark_revealed_records_npc_and_price() -> None:
    bc = make_breadcrumb(
        parent_goal_id="g1", sequence_index=0,
        description="x", canonical_basis="y",
        completion_conditions=[],
    )
    revealed = mark_revealed(
        bc, year=12, revealed_by_npc_id="hatake_kakashi",
        price_paid=BreadcrumbPrice(type="money", amount=1000),
    )
    assert revealed.revealed
    assert revealed.revealed_at_year == 12
    assert revealed.revealed_by_npc_id == "hatake_kakashi"
    assert revealed.price_paid.type == "money"


# === 5.4 completion ======================================================


def _make_action_result(action_type=ActionType.wait, target_id=None,
                       outcome="full_success"):
    """Helper : build un ActionResult minimal pour les tests."""
    from shinobi.engine.actions import Action, ActionResult
    from shinobi.types import ActionOutcome

    action = Action(
        action_type=action_type,
        summary="test",
        target_id=target_id,
    )
    return ActionResult(
        action=action,
        outcome=ActionOutcome(outcome),
        summary_fr="resultat test",
    )


def test_check_breadcrumb_completion_unrevealed_returns_false() -> None:
    """Un breadcrumb non-revealed ne peut pas etre complete."""
    bc = make_breadcrumb(
        parent_goal_id="g1", sequence_index=0,
        description="x", canonical_basis="y",
        completion_conditions=[
            CompletionCondition(
                type="visit_location",
                parameters={"location_id": "konohagakure"},
            ),
        ],
    )
    char = _make_character()
    assert not check_breadcrumb_completion(
        bc, action_result=_make_action_result(), character=char,
    )


def test_check_breadcrumb_completion_already_completed_returns_true() -> None:
    """Un breadcrumb deja completed reste completed."""
    bc = mark_completed(
        make_breadcrumb(
            parent_goal_id="g1", sequence_index=0,
            description="x", canonical_basis="y",
            completion_conditions=[],
        ),
        year=10,
    )
    char = _make_character()
    assert check_breadcrumb_completion(
        bc, action_result=_make_action_result(), character=char,
    )


def test_check_breadcrumb_visit_location_condition() -> None:
    bc = mark_revealed(
        make_breadcrumb(
            parent_goal_id="g1", sequence_index=0,
            description="x", canonical_basis="y",
            completion_conditions=[
                CompletionCondition(
                    type="visit_location",
                    parameters={"location_id": "sunagakure"},
                ),
            ],
        ),
        year=10,
    )
    # Joueur PAS a Sunagakure
    char_konoha = _make_character(current_location="konohagakure")
    assert not check_breadcrumb_completion(
        bc, action_result=_make_action_result(), character=char_konoha,
    )
    # Joueur A Sunagakure
    char_suna = _make_character(current_location="sunagakure")
    assert check_breadcrumb_completion(
        bc, action_result=_make_action_result(), character=char_suna,
    )


def test_check_breadcrumb_learn_technique_condition() -> None:
    bc = mark_revealed(
        make_breadcrumb(
            parent_goal_id="g1", sequence_index=0,
            description="x", canonical_basis="y",
            completion_conditions=[
                CompletionCondition(
                    type="learn_technique",
                    parameters={"technique_id": "rasengan"},
                ),
            ],
        ),
        year=10,
    )
    char_no_tech = _make_character()
    assert not check_breadcrumb_completion(
        bc, action_result=_make_action_result(), character=char_no_tech,
    )
    char_with_tech = _make_character(
        techniques_known=[KnownTechnique(technique_id="rasengan", learned_year=12)],
    )
    assert check_breadcrumb_completion(
        bc, action_result=_make_action_result(), character=char_with_tech,
    )


def test_check_breadcrumb_talk_to_npc_condition() -> None:
    bc = mark_revealed(
        make_breadcrumb(
            parent_goal_id="g1", sequence_index=0,
            description="x", canonical_basis="y",
            completion_conditions=[
                CompletionCondition(
                    type="talk_to_npc",
                    parameters={"npc_id": "jiraiya"},
                ),
            ],
        ),
        year=10,
    )
    char = _make_character()
    # Wrong target
    assert not check_breadcrumb_completion(
        bc,
        action_result=_make_action_result(target_id="iruka"),
        character=char,
    )
    # Right target
    assert check_breadcrumb_completion(
        bc,
        action_result=_make_action_result(target_id="jiraiya"),
        character=char,
    )


def test_check_breadcrumb_multiple_conditions_all_required() -> None:
    """Toutes les conditions doivent etre satisfaites (AND)."""
    bc = mark_revealed(
        make_breadcrumb(
            parent_goal_id="g1", sequence_index=0,
            description="x", canonical_basis="y",
            completion_conditions=[
                CompletionCondition(
                    type="visit_location",
                    parameters={"location_id": "konohagakure"},
                ),
                CompletionCondition(
                    type="talk_to_npc", parameters={"npc_id": "iruka"},
                ),
            ],
        ),
        year=10,
    )
    char = _make_character(current_location="konohagakure")
    # 1/2 satisfaite
    assert not check_breadcrumb_completion(
        bc,
        action_result=_make_action_result(target_id="kakashi"),
        character=char,
    )
    # 2/2 satisfaites
    assert check_breadcrumb_completion(
        bc,
        action_result=_make_action_result(target_id="iruka"),
        character=char,
    )


def test_check_goal_completion_no_revealed_breadcrumbs_returns_false() -> None:
    """Un Goal sans breadcrumb revele n'est pas considere accompli."""
    goal = declare_goal(
        description_player="x", interpretation_canonical="y",
        declared_at_year=12, declared_at_age=12,
    )
    bc = make_breadcrumb(
        parent_goal_id=goal.id, sequence_index=0,
        description="x", canonical_basis="y",
        completion_conditions=[],
    )
    assert not check_goal_completion(goal, [bc])


def test_check_goal_completion_all_required_completed() -> None:
    goal = declare_goal(
        description_player="x", interpretation_canonical="y",
        declared_at_year=12, declared_at_age=12,
    )
    bc1 = mark_completed(
        mark_revealed(
            make_breadcrumb(
                parent_goal_id=goal.id, sequence_index=0,
                description="x", canonical_basis="y",
                completion_conditions=[],
            ),
            year=12,
        ),
        year=13,
    )
    bc2 = mark_completed(
        mark_revealed(
            make_breadcrumb(
                parent_goal_id=goal.id, sequence_index=1,
                description="x", canonical_basis="y",
                completion_conditions=[],
            ),
            year=13,
        ),
        year=14,
    )
    assert check_goal_completion(goal, [bc1, bc2])


def test_check_goal_completion_optional_breadcrumbs_skipped() -> None:
    """Les breadcrumbs optional ne sont pas requis pour la completion."""
    goal = declare_goal(
        description_player="x", interpretation_canonical="y",
        declared_at_year=12, declared_at_age=12,
    )
    required = mark_completed(
        mark_revealed(
            make_breadcrumb(
                parent_goal_id=goal.id, sequence_index=0,
                description="x", canonical_basis="y",
                completion_conditions=[],
            ),
            year=12,
        ),
        year=13,
    )
    optional = mark_revealed(
        Breadcrumb(
            id="opt", parent_goal_id=goal.id, sequence_index=1,
            description="x", canonical_basis="y",
            completion_conditions=[], optional=True,
        ),
        year=12,
    )  # PAS marque completed
    assert check_goal_completion(goal, [required, optional])


def test_check_goal_by_target_learn_technique() -> None:
    """check_goal_by_target ferme un goal sans pathfinder via target match."""
    goal = declare_goal(
        description_player="learn rasengan",
        interpretation_canonical="learn rasengan",
        declared_at_year=12, declared_at_age=12,
        target_type=GoalTargetType.learn_technique,
        target_id="rasengan",
    )
    char_no = _make_character()
    assert not check_goal_by_target(goal, char_no)
    char_yes = _make_character(
        techniques_known=[KnownTechnique(technique_id="rasengan", learned_year=14)],
    )
    assert check_goal_by_target(goal, char_yes)


def test_check_goal_by_target_achieve_rank() -> None:
    goal = declare_goal(
        description_player="x", interpretation_canonical="y",
        declared_at_year=12, declared_at_age=12,
        target_type=GoalTargetType.achieve_rank,
        target_id="chunin",
    )
    assert not check_goal_by_target(goal, _make_character(rank="genin"))
    assert check_goal_by_target(goal, _make_character(rank="chunin"))


# === 5.5 pathfinder structure (LLM mocked) ==============================


def test_pathfinder_request_structure() -> None:
    """PathfinderRequest accepte les champs attendus."""
    from shinobi.goals.pathfinder import PathfinderRequest

    goal = declare_goal(
        description_player="x", interpretation_canonical="y",
        declared_at_year=12, declared_at_age=12,
    )
    req = PathfinderRequest(
        goal=goal,
        character_state_summary="Naruto, 12 ans, genin",
        current_year=12,
        sequence_index=0,
    )
    assert req.goal.id == goal.id
    assert req.sequence_index == 0


@pytest.mark.asyncio
async def test_pathfinder_find_path_with_mocked_llm() -> None:
    """5.5 : GoalPathfinder.find_path orchestre client + retriever +
    parse la reponse LLM en breadcrumbs structures.

    LLM client mocked + retriever mocked pour test offline.
    """
    from unittest.mock import AsyncMock, MagicMock

    from shinobi.goals.pathfinder import (
        GoalPathfinder,
        PathfinderRequest,
    )
    from shinobi.llm.client import LLMResponse

    # Mock LLM : retourne un payload conforme au GOAL_PATHFINDER_SCHEMA
    fake_llm_response = LLMResponse(
        content="...",
        raw_content="...",
        finish_reason="stop",
        usage_tokens={},
        parsed_json={
            "interpretation": "Apprendre rasengan via Jiraiya, canonical user",
            "sources_of_information": [
                {
                    "source_description": "Jiraiya, ermite des crapauds",
                    "indice_unlocked": {
                        "description": "Trouve Jiraiya a Tanzaku-gai",
                        "completion_conditions": [
                            {
                                "type": "talk_to_npc",
                                "parameters": {"npc_id": "jiraiya"},
                            },
                        ],
                    },
                    "price": {
                        "type": "money",
                        "description": "Paiement informateur",
                        "amount": 1500,
                    },
                },
                {
                    "source_description": "Kakashi, qui connait l'eleve de Jiraiya",
                    "indice_unlocked": {
                        "description": "Demander a Kakashi des indices sur Jiraiya",
                        "completion_conditions": [
                            {
                                "type": "talk_to_npc",
                                "parameters": {"npc_id": "hatake_kakashi"},
                            },
                        ],
                    },
                    "price": {
                        "type": "favor",
                        "description": "Mission D-rang gratuite",
                        "amount": 1.0,
                    },
                },
            ],
        },
    )
    client = MagicMock()
    client.generate = AsyncMock(return_value=fake_llm_response)

    # Mock canon + retriever (le pathfinder n'utilise canon que pour
    # construire le contexte RAG via build_turn_context).
    canon = MagicMock()
    retriever = MagicMock()
    # build_turn_context appelle retriever.query_for_turn -> mock vide
    retriever.query_for_turn.return_value = MagicMock(
        canon_facts=[], rules=[], voice_profiles=[], breadcrumbs=[],
    )

    pathfinder = GoalPathfinder(client, canon, retriever)
    goal = declare_goal(
        description_player="apprendre rasengan",
        interpretation_canonical="learn rasengan",
        declared_at_year=12, declared_at_age=12,
        target_type=GoalTargetType.learn_technique,
        target_id="rasengan",
    )
    req = PathfinderRequest(
        goal=goal,
        character_state_summary="Naruto, 12 ans, genin",
        current_year=12,
        sequence_index=0,
    )
    response = await pathfinder.find_path(req)

    # 2 sources -> 2 breadcrumbs
    assert len(response.breadcrumbs) == 2
    bc1 = response.breadcrumbs[0]
    assert bc1.description == "Trouve Jiraiya a Tanzaku-gai"
    assert bc1.canonical_basis == "Jiraiya, ermite des crapauds"
    assert bc1.parent_goal_id == goal.id
    assert bc1.sequence_index == 0
    # Conditions parsees correctement
    assert len(bc1.completion_conditions) == 1
    cc = bc1.completion_conditions[0]
    assert cc.type == "talk_to_npc"
    assert cc.parameters["npc_id"] == "jiraiya"
    # Price parse
    assert bc1.price_paid.type == "money"
    assert bc1.price_paid.amount == 1500
    # Pas encore revealed (pathfinder produit, le joueur paie ensuite)
    assert not bc1.revealed

    # 2eme source -> price favor
    bc2 = response.breadcrumbs[1]
    assert bc2.price_paid.type == "favor"
    assert "Kakashi" in bc2.canonical_basis

    # Interpretation transmise
    assert "rasengan" in response.interpretation.lower()


@pytest.mark.asyncio
async def test_pathfinder_raises_on_empty_llm_response() -> None:
    """5.5 : si le LLM retourne parsed_json=None, pathfinder leve LLMSchemaError."""
    from unittest.mock import AsyncMock, MagicMock

    from shinobi.errors import LLMSchemaError
    from shinobi.goals.pathfinder import (
        GoalPathfinder,
        PathfinderRequest,
    )
    from shinobi.llm.client import LLMResponse

    fake = LLMResponse(
        content="invalid", raw_content="invalid",
        finish_reason="stop", usage_tokens={}, parsed_json=None,
    )
    client = MagicMock()
    client.generate = AsyncMock(return_value=fake)
    canon = MagicMock()
    retriever = MagicMock()
    retriever.query_for_turn.return_value = MagicMock(
        canon_facts=[], rules=[], voice_profiles=[], breadcrumbs=[],
    )

    pathfinder = GoalPathfinder(client, canon, retriever)
    goal = declare_goal(
        description_player="x", interpretation_canonical="y",
        declared_at_year=12, declared_at_age=12,
    )
    req = PathfinderRequest(
        goal=goal, character_state_summary="x",
        current_year=12, sequence_index=0,
    )
    with pytest.raises(LLMSchemaError):
        await pathfinder.find_path(req)


# === 5.9 integration : declaration -> indice -> sous-objectif -> completion ==


def test_phase_5_e2e_goal_lifecycle() -> None:
    """Spec 5.9 : declarer goal, recevoir un breadcrumb, payer, executer,
    completer le breadcrumb, completer le goal.

    Simule le flow LLM-mocke (le pathfinder est testable independamment
    via le mock de son client en tests d'integration LLM).
    """
    # 1. Joueur declare un goal "apprendre rasengan"
    goal = declare_goal(
        description_player="je veux apprendre rasengan",
        interpretation_canonical="learn rasengan via canonical_user training",
        declared_at_year=12, declared_at_age=12,
        target_type=GoalTargetType.learn_technique,
        target_id="rasengan",
    )
    assert goal.status == GoalStatus.declared

    # 2. Le pathfinder LLM (mocked ici) retourne 1 breadcrumb : trouver Jiraiya
    bc = make_breadcrumb(
        parent_goal_id=goal.id, sequence_index=0,
        description="Trouver Jiraiya et lui demander un entrainement",
        canonical_basis="Jiraiya is canonical_users of rasengan",
        completion_conditions=[
            CompletionCondition(
                type="talk_to_npc", parameters={"npc_id": "jiraiya"},
            ),
        ],
    )

    # 3. Joueur paie le prix (1000 ryos) -> breadcrumb revealed
    price = price_in_money(value_strategique=1.0)
    bc_revealed = mark_revealed(
        bc, year=12, revealed_by_npc_id="informer_npc",
        price_paid=price.model_copy(update={"paid": True, "paid_at_year": 12}),
    )
    assert bc_revealed.revealed
    assert bc_revealed.price_paid.amount == 1000

    # 4. Joueur execute le sous-objectif (parle a Jiraiya)
    char = _make_character()
    action_result = _make_action_result(
        action_type=ActionType.talk, target_id="jiraiya",
    )
    assert check_breadcrumb_completion(
        bc_revealed, action_result=action_result, character=char,
    )

    # 5. Mark completed
    bc_done = mark_completed(bc_revealed, year=13)
    assert bc_done.completed

    # 6. Le goal n'est PAS encore complete : le breadcrumb dit juste "trouver
    # Jiraiya". Mais une fois rasengan appris, check_goal_by_target ferme.
    char_with_rasengan = _make_character(
        techniques_known=[KnownTechnique(technique_id="rasengan", learned_year=15)],
    )
    assert check_goal_by_target(goal, char_with_rasengan)

    # 7. Mark goal completed
    goal_done = complete_goal(goal, year=15)
    assert goal_done.status == GoalStatus.completed
    assert goal_done.completed_at_year == 15


def test_phase_5_multi_step_breadcrumb_chain() -> None:
    """Spec 5.9 critere de sortie : 'obtenir l'etape suivante'.

    Simule le flow complet sur 2 etapes :
    - bc1 (sequence 0) : trouver Jiraiya -> revealed -> completed
    - bc2 (sequence 1) : entrainement avec Jiraiya -> revealed -> completed
    Verifie que sequence_index s'incremente correctement et que le goal
    n'est complet qu'apres les 2.
    """
    goal = declare_goal(
        description_player="apprendre rasengan",
        interpretation_canonical="learn rasengan via Jiraiya",
        declared_at_year=12, declared_at_age=12,
        target_type=GoalTargetType.learn_technique,
        target_id="rasengan",
    )

    # Etape 1 : trouver Jiraiya
    bc1 = mark_completed(
        mark_revealed(
            make_breadcrumb(
                parent_goal_id=goal.id, sequence_index=0,
                description="Trouver Jiraiya a Tanzaku-gai",
                canonical_basis="Jiraiya canonical user rasengan",
                completion_conditions=[
                    CompletionCondition(
                        type="talk_to_npc", parameters={"npc_id": "jiraiya"},
                    ),
                ],
            ),
            year=12,
            price_paid=BreadcrumbPrice(type="money", amount=1000, paid=True),
        ),
        year=12,
    )
    # Apres bc1 completed, le goal n'est pas encore valide via target
    char_no_rasengan = _make_character()
    assert not check_goal_by_target(goal, char_no_rasengan)

    # Etape 2 : entrainement (sequence_index=1, suit bc1)
    bc2 = mark_completed(
        mark_revealed(
            make_breadcrumb(
                parent_goal_id=goal.id, sequence_index=1,
                description="Suivre l'entrainement de Jiraiya 6 mois",
                canonical_basis="canonical training arc",
                completion_conditions=[
                    CompletionCondition(
                        type="learn_technique",
                        parameters={"technique_id": "rasengan"},
                    ),
                ],
            ),
            year=13,
        ),
        year=14,
    )

    # Sequence_index incremental verifie
    assert bc1.sequence_index == 0
    assert bc2.sequence_index == 1

    # Goal valide via target apres apprentissage rasengan
    char_with_rasengan = _make_character(
        techniques_known=[
            KnownTechnique(technique_id="rasengan", learned_year=14),
        ],
    )
    assert check_goal_by_target(goal, char_with_rasengan)
    # Et via les 2 breadcrumbs (toutes les conditions completes)
    assert check_goal_completion(goal, [bc1, bc2])


def test_phase_5_abandon_goal_lifecycle() -> None:
    """Spec 5 : un joueur peut abandonner un goal a tout moment."""
    goal = declare_goal(
        description_player="je veux devenir hokage",
        interpretation_canonical="achieve_rank kage",
        declared_at_year=12, declared_at_age=12,
        target_type=GoalTargetType.achieve_rank,
        target_id="kage",
    )
    # Joueur change d'avis a 18 ans
    abandoned = abandon_goal(goal, year=18)
    assert abandoned.status == GoalStatus.abandoned
    assert abandoned.abandoned_at_year == 18
    # Original immutable
    assert goal.status == GoalStatus.declared


def test_fail_goal_transitions_to_failed() -> None:
    """Phase 5 : fail_goal transitionne vers failed avec abandoned_at_year set."""
    from shinobi.goals.declaration import fail_goal

    goal = declare_goal(
        description_player="x", interpretation_canonical="y",
        declared_at_year=12, declared_at_age=12,
    )
    failed = fail_goal(goal, year=15, reason="target dead")
    assert failed.status == GoalStatus.failed
    assert failed.abandoned_at_year == 15
    # Original immutable
    assert goal.status == GoalStatus.declared


def test_fail_goal_idempotent_on_completed_goal() -> None:
    """Phase 5 : fail_goal sur completed -> no-op."""
    from shinobi.goals.declaration import fail_goal

    completed = complete_goal(
        declare_goal(
            description_player="x", interpretation_canonical="y",
            declared_at_year=12, declared_at_age=12,
        ),
        year=15,
    )
    out = fail_goal(completed, year=20)
    assert out.status == GoalStatus.completed
    assert out.completed_at_year == 15  # preserve
    assert out.abandoned_at_year is None  # pas mute


def test_detect_goal_failure_player_dead() -> None:
    """Phase 5 : si joueur mort, tous les goals actifs sont failed."""
    from shinobi.goals.declaration import detect_goal_failure

    goal = declare_goal(
        description_player="x", interpretation_canonical="y",
        declared_at_year=12, declared_at_age=12,
    )
    reason = detect_goal_failure(
        goal, canon_characters={}, current_year=15, player_is_dead=True,
    )
    assert reason is not None
    assert "joueur" in reason.lower()


def test_detect_goal_failure_target_dead_befriend() -> None:
    """Phase 5 : befriend_character avec target mort -> failed."""
    from unittest.mock import MagicMock

    from shinobi.goals.declaration import detect_goal_failure

    goal = declare_goal(
        description_player="devenir ami avec Itachi",
        interpretation_canonical="befriend uchiha_itachi",
        declared_at_year=10, declared_at_age=10,
        target_type=GoalTargetType.befriend_character,
        target_id="uchiha_itachi",
    )
    # Itachi mort year=15
    fake_char = MagicMock()
    fake_char.death_year = 15
    canon_characters = {"uchiha_itachi": fake_char}

    # Avant year=15 : pas de raison
    assert detect_goal_failure(
        goal, canon_characters=canon_characters, current_year=14,
    ) is None
    # A year=15 : failed
    reason = detect_goal_failure(
        goal, canon_characters=canon_characters, current_year=15,
    )
    assert reason is not None
    assert "uchiha_itachi" in reason
    # Apres year=15 : toujours failed
    assert detect_goal_failure(
        goal, canon_characters=canon_characters, current_year=20,
    ) is not None


def test_detect_goal_failure_kill_character_already_dead() -> None:
    """Phase 5 : kill_character mais target deja mort par autre cause -> failed."""
    from unittest.mock import MagicMock

    from shinobi.goals.declaration import detect_goal_failure

    goal = declare_goal(
        description_player="tuer Madara",
        interpretation_canonical="kill uchiha_madara",
        declared_at_year=10, declared_at_age=10,
        target_type=GoalTargetType.kill_character,
        target_id="uchiha_madara",
    )
    fake_char = MagicMock()
    fake_char.death_year = 12
    reason = detect_goal_failure(
        goal,
        canon_characters={"uchiha_madara": fake_char},
        current_year=15,
    )
    assert reason is not None
    assert "deja mort" in reason


def test_detect_goal_failure_returns_none_when_safe() -> None:
    """Phase 5 : si goal toujours atteignable, detect retourne None."""
    from shinobi.goals.declaration import detect_goal_failure

    goal = declare_goal(
        description_player="apprendre rasengan",
        interpretation_canonical="learn rasengan",
        declared_at_year=12, declared_at_age=12,
        target_type=GoalTargetType.learn_technique,
        target_id="rasengan",  # technique != character, pas de death_year
    )
    assert detect_goal_failure(
        goal, canon_characters={}, current_year=12, player_is_dead=False,
    ) is None


def test_detect_goal_failure_skips_terminal_states() -> None:
    """Phase 5 : un goal completed/abandoned/failed n'est plus detecte."""
    from shinobi.goals.declaration import detect_goal_failure

    g_completed = complete_goal(
        declare_goal(
            description_player="x", interpretation_canonical="y",
            declared_at_year=12, declared_at_age=12,
        ),
        year=15,
    )
    assert detect_goal_failure(
        g_completed, canon_characters={}, current_year=20,
        player_is_dead=True,  # meme si player dead, completed reste completed
    ) is None


def test_mark_goal_in_progress_transitions_declared() -> None:
    """Phase 5 : declared -> in_progress quand 1er breadcrumb revele."""
    from shinobi.goals.declaration import mark_goal_in_progress

    goal = declare_goal(
        description_player="x", interpretation_canonical="y",
        declared_at_year=12, declared_at_age=12,
    )
    assert goal.status == GoalStatus.declared
    g_active = mark_goal_in_progress(goal)
    assert g_active.status == GoalStatus.in_progress


def test_mark_goal_in_progress_idempotent() -> None:
    """Phase 5 : si deja in_progress / completed / abandoned, no-op."""
    from shinobi.goals.declaration import mark_goal_in_progress

    completed = complete_goal(
        declare_goal(
            description_player="x", interpretation_canonical="y",
            declared_at_year=12, declared_at_age=12,
        ),
        year=15,
    )
    assert mark_goal_in_progress(completed).status == GoalStatus.completed

    abandoned = abandon_goal(
        declare_goal(
            description_player="x", interpretation_canonical="y",
            declared_at_year=12, declared_at_age=12,
        ),
        year=15,
    )
    assert mark_goal_in_progress(abandoned).status == GoalStatus.abandoned


def test_phase_5_failed_goal_survives_save_reload(tmp_path, monkeypatch) -> None:
    """Phase 4 <-> 5 : un goal status=failed avec abandoned_at_year set
    survit a save/reload via SQLite Goal table.
    """
    from shinobi.canon.profiles import CanonicityProfile
    from shinobi.config import settings
    from shinobi.engine.world import create_default_world
    from shinobi.goals.declaration import fail_goal
    from shinobi.persistence import saves as save_module

    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir",
        property(lambda self: tmp_path),
    )

    char = _make_character()
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=12,
    )
    sid = save_module.create_save(char, world)

    # Declare puis fail
    goal = declare_goal(
        description_player="devenir ami avec Itachi",
        interpretation_canonical="befriend uchiha_itachi",
        declared_at_year=10, declared_at_age=10,
        target_type=GoalTargetType.befriend_character,
        target_id="uchiha_itachi",
    )
    save_module.save_goal(sid, goal)

    failed_goal = fail_goal(goal, year=15, reason="Itachi dead")
    save_module.save_goal(sid, failed_goal)

    # Reload
    goals_loaded = save_module.load_goals(sid)
    assert len(goals_loaded) == 1
    g = goals_loaded[0]
    assert g.id == goal.id
    assert g.status == GoalStatus.failed
    assert g.abandoned_at_year == 15  # reused as failure-time field
    assert g.target_id == "uchiha_itachi"
    assert g.target_type == "befriend_character"

    save_module.delete_save(sid)


def test_phase_5_breadcrumb_persistence_roundtrip(tmp_path, monkeypatch) -> None:
    """Phase 4 <-> 5 integration : Breadcrumb avec price_paid + completion
    survit a un save/reload roundtrip via persistence.saves.
    """
    from shinobi.canon.profiles import CanonicityProfile
    from shinobi.config import settings
    from shinobi.engine.world import create_default_world
    from shinobi.persistence import saves as save_module

    # Isolate saves dir
    monkeypatch.setattr(settings, "saves_path", str(tmp_path))
    monkeypatch.setattr(
        type(settings), "saves_dir",
        property(lambda self: tmp_path),
    )

    # Setup save
    char = _make_character()
    world = create_default_world(
        profile=CanonicityProfile.default(), starting_year=12,
    )
    sid = save_module.create_save(char, world)

    # Cree un goal + breadcrumb riche (revealed + completed + price paid)
    goal = declare_goal(
        description_player="apprendre rasengan",
        interpretation_canonical="learn rasengan",
        declared_at_year=12, declared_at_age=12,
        target_type=GoalTargetType.learn_technique,
        target_id="rasengan",
    )
    save_module.save_goal(sid, goal)

    bc_full = mark_completed(
        mark_revealed(
            make_breadcrumb(
                parent_goal_id=goal.id, sequence_index=0,
                description="Trouver Jiraiya",
                canonical_basis="Jiraiya canonical user rasengan",
                completion_conditions=[
                    CompletionCondition(
                        type="talk_to_npc",
                        parameters={"npc_id": "jiraiya"},
                    ),
                    CompletionCondition(
                        type="visit_location",
                        parameters={"location_id": "tanzaku_gai"},
                    ),
                ],
            ),
            year=12,
            revealed_by_npc_id="iruka_umino",
            price_paid=BreadcrumbPrice(
                type="money",
                description="Pot-de-vin pour info confidentielle",
                amount=2500.0,
                paid=True,
                paid_at_year=12,
            ),
        ),
        year=14,
    )
    save_module.save_breadcrumb(sid, bc_full)

    # Reload + verifie integrite
    goals_loaded = save_module.load_goals(sid)
    assert len(goals_loaded) == 1
    g_reloaded = goals_loaded[0]
    assert g_reloaded.id == goal.id
    assert g_reloaded.target_type == "learn_technique"
    assert g_reloaded.target_id == "rasengan"

    bcs_loaded = save_module.load_breadcrumbs(sid, parent_goal_id=goal.id)
    assert len(bcs_loaded) == 1
    bc_reloaded = bcs_loaded[0]
    # Identite preservee
    assert bc_reloaded.id == bc_full.id
    assert bc_reloaded.sequence_index == 0
    # Etat preserve : revealed + completed + price
    assert bc_reloaded.revealed
    assert bc_reloaded.revealed_at_year == 12
    assert bc_reloaded.revealed_by_npc_id == "iruka_umino"
    assert bc_reloaded.completed
    assert bc_reloaded.completed_at_year == 14
    # Price details preserves
    assert bc_reloaded.price_paid is not None
    assert bc_reloaded.price_paid.type == "money"
    assert bc_reloaded.price_paid.amount == 2500.0
    assert bc_reloaded.price_paid.paid
    assert bc_reloaded.price_paid.paid_at_year == 12
    # Conditions preservees (2 conditions multi-types)
    assert len(bc_reloaded.completion_conditions) == 2
    cc_types = {c.type for c in bc_reloaded.completion_conditions}
    assert cc_types == {"talk_to_npc", "visit_location"}
    # Cleanup
    save_module.delete_save(sid)


def test_phase_5_pricing_negotiation_in_full_flow() -> None:
    """Spec 5.2 : negociation reduit reellement le cout effectif paye."""
    # Prix de base info anbu (faveur)
    base_anbu = price_for_anbu(value_strategique=2.0)
    assert base_anbu.amount == 2.0

    # Prix de base info money
    base_money = price_in_money(
        value_strategique=2.0, target_rank_factor=1.5,
    )  # 1000 * 2.0 * 1.5 = 3000

    # Negociation reussie -50% sur money
    negotiated = negotiate_price(base_money, success_margin=15)
    assert negotiated.amount == 1500

    # Mais favor anbu non-negociable (amount = 2.0 quand meme apres x0.5)
    negotiated_anbu = negotiate_price(base_anbu, success_margin=15)
    assert negotiated_anbu.amount == 1.0  # 2.0 * 0.5
    # Type prix preserve
    assert negotiated_anbu.type == "favor"
