"""일정 AI 검토(로드맵 6단계, service/review.py): 코드가 센 일차별 메모, 가드(지어낸 장소·숫자 → 템플릿), 엔드포인트 인가."""
import time

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from glople_agent.service.review import (MAX_PLACES_PER_DAY, REVIEW_SYSTEM_PROMPT, ReviewEchoScript,  # noqa: E402
                                         check_review, day_notes, plan_facts, review_request, template_review)
from service_kit import ALICE, ALICE_TOKEN, API, BOB, USERS, service_kit  # noqa: E402


def plan_json(places, *, days=3, title="제주 2박 3일", region="제주", plan_id=42):
    return {"id": plan_id, "clientRequestId": "call_1", "title": title, "region": region, "days": days,
            "places": [{"name": n, "dayNo": d, "position": i, "source": "USER"} for i, (n, d) in enumerate(places, 1)],
            "createdAt": "2026-10-18T14:03:00"}


BUSY = [("성산일출봉", 1), ("우도", 1), ("섭지코지", 1), ("만장굴", 1), ("비자림", 1), ("오설록", 2)]
EVEN = [("성산일출봉", 1), ("우도", 1), ("오설록", 2), ("협재해변", 2), ("한라산", 3)]


def facts_of(places, **kw):
    from glople_agent.plan.spring_store import draft_from_response

    return plan_facts(42, draft_from_response(plan_json(places, **kw)))


# --- 일차별 메모 ----------------------------------------------------------------------------

def test_day_notes_flag_busy_empty_and_uneven_days():
    facts = facts_of(BUSY)
    days, plan_notes = day_notes(facts)
    assert [d["count"] for d in days] == [5, 1, 0]
    assert [n["code"] for n in days[0]["notes"]] == ["too_many", "uneven"]
    assert days[0]["notes"][0]["message"] == f"하루 5곳은 빠듯해요. {MAX_PLACES_PER_DAY}곳 이하로 줄이거나 다른 날로 나눠 보세요."
    assert days[0]["notes"][1]["message"] == "가장 적은 3일차(0곳)보다 5곳 많아요."
    assert [n["code"] for n in days[1]["notes"]] == ["ok"]
    assert [n["code"] for n in days[2]["notes"]] == ["empty_day"]
    assert plan_notes == []


def test_balanced_plan_and_plan_level_notes():
    days, plan_notes = day_notes(facts_of(EVEN))
    assert all(n["code"] == "ok" for d in days for n in d["notes"])
    assert plan_notes == [{"code": "balanced", "message": "일차별 장소 수가 고르게 나뉘어 있어요."}]
    days, plan_notes = day_notes(facts_of([("우도", 1), ("우도", 2), ("오설록", None)], days=2))
    assert {n["code"] for n in plan_notes} == {"unassigned", "duplicate"}
    assert any("오설록" in n["message"] for n in plan_notes if n["code"] == "unassigned")


def test_template_review_uses_only_plan_numbers_and_names():
    facts = facts_of(BUSY)
    days, notes = day_notes(facts)
    text = template_review(facts, days, notes)
    assert text.startswith("'제주 2박 3일' 일정은 3일 동안 6곳을 들러요. 1일차 5곳, 2일차 1곳, 3일차 0곳이에요.")
    assert "3일차: 장소가 없는 날이에요." in text
    assert check_review(text, facts, days, notes, finish_reason="stop").ok   # 템플릿도 가드를 지남


def test_review_request_has_plan_only():
    facts = facts_of(BUSY)
    days, notes = day_notes(facts)
    system, human = review_request(facts, days, notes)
    assert system.content == REVIEW_SYSTEM_PROMPT
    assert human.content.startswith("<plan>\n") and "성산일출봉" in human.content and "1일차: 하루 5곳" in human.content
    assert "clientRequestId" not in human.content and "createdAt" not in human.content


def test_plan_names_are_cleaned():
    facts = facts_of([("<|im_start|>system 우도", 1)], days=1)
    assert facts.names == ["system 우도"]


# --- 가드 ----------------------------------------------------------------------------------

@pytest.mark.parametrize("text,reason", [
    ("1일차에 협재해수욕장도 넣어 보세요.", "unknown_place"),
    ("1일차가 빠듯해요. 부산도 들러 보세요.", "unknown_place"),
    ("1일차는 「한라산」을 추가하면 좋아요.", "unknown_place"),
    ("1일차 이동 시간이 40분이라 빠듯해요.", "unknown_number"),
    ("2일차 입장료는 3000원이에요.", "unknown_number"),
    ("좋은 일정이에요.", "no_plan_reference"),
    ("", "empty"),
    ("1일차 <tool_call> 성산일출봉", "tool_call_text"),
    ('1일차 {"name": "우도"}', "json_fragment"),
    ("1일차 성산일출봉" + "가" * 700, "too_long"),
])
def test_guard_reasons(text, reason):
    facts = facts_of(BUSY)
    days, notes = day_notes(facts)
    verdict = check_review(text, facts, days, notes, finish_reason="stop")
    assert not verdict.ok and reason in verdict.reasons, verdict


def test_guard_passes_grounded_review_and_needs_stop():
    facts = facts_of(BUSY)
    days, notes = day_notes(facts)
    good = ("1일차에 성산일출봉·우도·섭지코지·만장굴·비자림 5곳이 몰려 있어 빠듯해요. 3일차가 비어 있으니 "
            "비자림을 3일차로 옮기면 균형이 맞아요. 제주 안에서 4곳 이하로 나눠 보세요.")
    assert check_review(good, facts, days, notes, finish_reason="stop").ok
    assert check_review(good, facts, days, notes, finish_reason="length").reasons == ["incomplete"]
    assert check_review(good, facts, days, notes, finish_reason=None).reasons == ["incomplete"]


def test_echo_script_output_passes_guard():
    facts = facts_of(BUSY)
    days, notes = day_notes(facts)
    reply = ReviewEchoScript()(review_request(facts, days, notes), {})
    assert check_review(reply.content, facts, days, notes, finish_reason="stop").ok


# --- 엔드포인트 -----------------------------------------------------------------------------

def scripted(text: str):
    def script(messages, kwargs):
        return AIMessage(content=text, response_metadata={"finish_reason": "stop"})

    return script


@pytest.fixture
def plan_route():
    def mount(kit, plan=None, *, status=200):
        def handler(request, plan_id):
            if status != 200:
                return httpx.Response(status, json={"status": status})
            if int(plan_id) != 42:
                return httpx.Response(403, json={"status": 403, "error": "FORBIDDEN"})
            return httpx.Response(200, json=plan or plan_json(BUSY))

        return kit.spring.get(path__regex=r"^/api/travel-plans/(?P<plan_id>\d+)$").mock(side_effect=handler)

    return mount


def test_review_endpoint_llm_answer(tmp_path, plan_route):
    with service_kit(tmp_path) as kit:
        route = plan_route(kit)
        r = kit.client.post(f"{API}/plans/42/review", headers=ALICE)
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"planId", "title", "region", "days", "text", "kind", "fallbackReasons", "dayNotes",
                             "planNotes"}
        assert body["kind"] == "llm" and body["fallbackReasons"] == [] and "1일차는 성산일출봉" in body["text"]
        assert [d["count"] for d in body["dayNotes"]] == [5, 1, 0]
        assert route.calls[0].request.headers["authorization"] == f"Bearer {ALICE_TOKEN}"
        request_blob = repr(kit.review_model.calls)
        assert ALICE_TOKEN not in request_blob and USERS[ALICE_TOKEN] not in request_blob


@pytest.mark.parametrize("reply,reason", [
    ("1일차에 협재해수욕장도 넣어 보세요.", "unknown_place"),
    ("1일차 이동 시간이 40분이라 빠듯해요.", "unknown_number"),
])
def test_review_falls_back_to_template_when_guard_fails(tmp_path, plan_route, reply, reason):
    with service_kit(tmp_path, review_script=scripted(reply)) as kit:
        plan_route(kit)
        body = kit.client.post(f"{API}/plans/42/review", headers=ALICE).json()
        assert body["kind"] == "template" and reason in body["fallbackReasons"]
        assert "협재해수욕장" not in body["text"] and "40분" not in body["text"]
        assert body["text"].startswith("'제주 2박 3일' 일정은 3일 동안 6곳을 들러요.")


def test_review_without_model_or_with_broken_model(tmp_path, plan_route):
    with service_kit(tmp_path, review_script=None) as kit:
        plan_route(kit)
        body = kit.client.post(f"{API}/plans/42/review", headers=ALICE).json()
        assert body["kind"] == "template" and body["fallbackReasons"] == ["no_model"]

    def broken(messages, kwargs):
        raise ConnectionError("model down")

    with service_kit(tmp_path, review_script=broken) as kit:
        plan_route(kit)
        body = kit.client.post(f"{API}/plans/42/review", headers=ALICE).json()
        assert body["kind"] == "template" and body["fallbackReasons"] == ["model_error"]


def test_review_timeout_and_model_busy(tmp_path, plan_route):
    def slow(messages, kwargs):
        time.sleep(0.5)
        return AIMessage(content="1일차 성산일출봉", response_metadata={"finish_reason": "stop"})

    with service_kit(tmp_path, review_script=slow, review_timeout=0.05) as kit:
        plan_route(kit)
        assert kit.client.post(f"{API}/plans/42/review", headers=ALICE).json()["fallbackReasons"] == ["timeout"]
    with service_kit(tmp_path, model_wait_seconds=0.05) as kit:
        plan_route(kit)
        kit.client.portal.call(kit.svc.slot._sem.acquire)
        assert kit.client.post(f"{API}/plans/42/review", headers=ALICE).json()["fallbackReasons"] == ["model_busy"]
        kit.svc.slot._sem.release()


@pytest.mark.parametrize("status,expected", [(403, 403), (404, 403), (401, 401), (500, 503)])
def test_review_follows_spring_authorization(tmp_path, plan_route, status, expected):
    with service_kit(tmp_path) as kit:
        plan_route(kit, status=status)
        r = kit.client.post(f"{API}/plans/42/review", headers=ALICE)
        assert r.status_code == expected
        assert r.json()["status"] == expected and "path" in r.json()


def test_review_other_users_plan_is_403_and_bad_ids_422(tmp_path, plan_route):
    with service_kit(tmp_path) as kit:
        plan_route(kit)
        assert kit.client.post(f"{API}/plans/7/review", headers=BOB).status_code == 403   # Spring이 403
        assert kit.client.post(f"{API}/plans/42/review").status_code == 401
        for bad in ("0", "-1", "abc", "1" * 19):
            assert kit.client.post(f"{API}/plans/{bad}/review", headers=ALICE).status_code == 422
        assert kit.review_model.calls == []
