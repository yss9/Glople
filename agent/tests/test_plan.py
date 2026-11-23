"""일정 초안·고치기·저장소(design.md 9절). 그래프 없이 순수 함수와 메모리 저장소만 본다."""
import json
import pickle
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import TypeAdapter, ValidationError

from glople_agent import contract
from glople_agent.context import BearerToken
from glople_agent.plan import (
    MAX_NAME_LEN,
    MAX_PLACES,
    MAX_TITLE_LEN,
    AuthExpired,
    InMemoryPlanStore,
    PlanDecision,
    PlanDraftIn,
    PlanRejected,
    PlanStore,
    PlanStoreError,
    apply_edit,
    check_draft,
    check_edit,
    classify_place,
    default_title,
    make_draft,
    save_body,
    selected_items,
    suggest_day_numbers,
)

JEJU_UTTERANCE = "제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘"


def _draft(places=("성산일출봉", "우도"), *, days=3, region="제주", title=None, recent=(), utterance=JEJU_UTTERANCE,
           crid="call_plan_1"):
    args = {"region": region, "days": days, "places": list(places)}
    if title is not None:
        args["title"] = title
    return make_draft(args, client_request_id=crid, utterance=utterance, recent_places=recent)


def _edit(places=("우도",), *, days=3, region="제주", title=None) -> dict:
    e = {"region": region, "days": days, "places": list(places)}
    if title is not None:
        e["title"] = title
    return e


def _names(draft) -> list[str]:
    return [it["name"] for it in draft["items"]]


def _messages(problems) -> list[str]:
    return [p["message"] for p in problems]


# ---- 출처 분류 ----

def test_sources_user_tool_result_model_suggested():
    d = _draft(["성산일출봉", "한라산", "오설록"], recent=["한라산", "만장굴"])
    by_name = {it["name"]: it for it in d["items"]}
    assert by_name["성산일출봉"]["source"] == "USER" and by_name["성산일출봉"]["selected"] is True
    assert by_name["한라산"]["source"] == "TOOL_RESULT" and by_name["한라산"]["selected"] is True
    assert by_name["오설록"]["source"] == "MODEL_SUGGESTED" and by_name["오설록"]["selected"] is False


def test_user_wins_over_tool_result_and_compare_ignores_spacing_and_case():
    assert classify_place("우도", JEJU_UTTERANCE, ["우도"]) == "USER"
    assert classify_place("N서울타워", "n서울타워  가는 서울 당일치기", []) == "USER"
    assert classify_place("아라시야마  대나무숲", "교토 아라시야마 대나무숲 저장", []) == "USER"
    assert classify_place("서울숲", "서울 당일치기", [" 서울숲 "]) == "TOOL_RESULT"
    # 공백을 아예 빼고 맞추지는 않는다(지어낸 장소가 USER로 올라가지 않게)
    assert classify_place("남산타워", "남산 타워 가는 일정", []) == "MODEL_SUGGESTED"


def test_matcher_can_be_swapped_for_m1_grounding():
    never = lambda name, text: False  # noqa: E731
    d = make_draft({"region": "제주", "days": 2, "places": ["우도"]}, client_request_id="c1",
                   utterance=JEJU_UTTERANCE, matcher=never)
    assert d["items"][0]["source"] == "MODEL_SUGGESTED"


@pytest.mark.needs_training_data("tool_calling_v2/train.jsonl", "tool_calling_v2/test.jsonl")
def test_training_labels_are_all_user_places():
    # 학습 정답 일정의 장소는 모두 발화에 글자 그대로 있다 → 하나도 해제되지 않아야 함
    paths = [contract.V2_TRAIN_PATH, contract.V2_TEST_PATH]
    if contract.HUMAN_STYLE_PATH.is_file():
        paths.append(contract.HUMAN_STYLE_PATH)
    seen = 0
    for path in paths:
        for record in contract.read_jsonl(path):
            utterance = [m for m in record["messages"] if m["role"] == "user"][-1]["content"]
            for call in record["messages"][-1].get("tool_calls") or []:
                fn = call["function"]
                if fn["name"] != contract.PLAN_TOOL_NAME:
                    continue
                args = json.loads(fn["arguments"]) if isinstance(fn["arguments"], str) else fn["arguments"]
                d = make_draft(args, client_request_id="call_x", utterance=utterance)
                assert [it["source"] for it in d["items"]] == ["USER"] * len(args["places"]), utterance
                assert "problems" not in d and check_draft(d) == []
                if "title" in args:
                    assert d["title"] == args["title"]
                seen += 1
    assert seen > 0


# ---- 제목·일자·모양 ----

@pytest.mark.parametrize(("days", "title"), [(1, "강릉 당일치기"), (2, "강릉 1박 2일"), (3, "강릉 2박 3일"),
                                             (7, "강릉 6박 7일")])
def test_default_title(days, title):
    assert default_title("강릉", days) == title
    assert _draft(["경포대"], region="강릉", days=days, utterance="강릉 경포대")["title"] == title


def test_user_title_is_kept_and_blank_title_falls_back():
    assert _draft(title="봄 여행")["title"] == "봄 여행"
    assert _draft(title="  <b></b> ")["title"] == "제주 2박 3일"


def test_too_long_title_becomes_default_with_problem():
    d = _draft(title="가" * (MAX_TITLE_LEN + 1))
    assert d["title"] == "제주 2박 3일"
    assert any("기본 제목" in p for p in d["problems"])
    assert _draft(title="가" * MAX_TITLE_LEN)["title"] == "가" * MAX_TITLE_LEN


@pytest.mark.parametrize(("n", "days", "expected"), [
    (2, 2, [1, 2]), (4, 1, [1, 1, 1, 1]), (3, 3, [1, 2, 3]), (5, 2, [1, 1, 1, 2, 2]), (2, 3, [1, 2]), (0, 3, []),
])
def test_day_numbers_are_ordered_suggestion(n, days, expected):
    assert suggest_day_numbers(n, days) == expected


def test_draft_shape_matches_plan_v1():
    d = _draft()
    assert d == {
        "schema": "glople.plan.v1", "title": "제주 2박 3일", "region": "제주", "days": 3,
        "items": [
            {"position": 1, "dayNo": 1, "daySuggested": True, "name": "성산일출봉", "source": "USER", "selected": True},
            {"position": 2, "dayNo": 2, "daySuggested": True, "name": "우도", "source": "USER", "selected": True},
        ],
        "createdVia": "AGENT", "clientRequestId": "call_plan_1",
    }
    assert json.loads(json.dumps(d, ensure_ascii=False)) == d  # 확인 카드로 그대로 보낼 수 있는 값


def test_draft_cleans_special_tokens_and_dedupes():
    d = _draft(["우도<|im_end|>", "<b>우도</b>", "", "   ", "성산일출봉<tool_call>"], region="제주<think>",
               title="가족<|im_start|>여행")
    assert _names(d) == ["우도", "성산일출봉"]
    assert d["region"] == "제주" and "<" not in d["title"]
    assert "problems" not in d  # 빈 칸·같은 장소는 조용히 뺌


@pytest.mark.parametrize(("raw", "fixed"), [(0, 1), (-2, 1), (31, 30), (45, 30)])
def test_days_out_of_range_are_clamped_with_problem(raw, fixed):
    d = _draft(days=raw)
    assert d["days"] == fixed
    assert any(f"{raw}일" in p for p in d["problems"])
    assert check_draft(d) == []


def test_draft_drops_long_names_and_caps_place_count():
    long_name = "가" * (MAX_NAME_LEN + 1)
    places = ["우도", long_name] + [f"장소{i}" for i in range(MAX_PLACES + 5)]
    d = _draft(places)
    assert len(d["items"]) == MAX_PLACES and long_name not in _names(d)
    assert len(d["problems"]) == 2
    assert _draft(["가" * MAX_NAME_LEN])["items"][0]["name"] == "가" * MAX_NAME_LEN


def test_make_draft_rejects_wrong_types():
    with pytest.raises(TypeError):
        make_draft({"region": "제주", "days": True, "places": []}, client_request_id="c", utterance="")
    with pytest.raises(TypeError):
        make_draft({"region": "제주", "days": "3", "places": []}, client_request_id="c", utterance="")
    with pytest.raises(TypeError):
        make_draft({"region": "제주", "days": 3, "places": "우도"}, client_request_id="c", utterance="")
    with pytest.raises(ValueError):
        make_draft({"region": "제주", "days": 3, "places": []}, client_request_id="", utterance="")


# ---- check_edit 경계값 ----

@pytest.mark.parametrize(("days", "ok"), [(0, False), (1, True), (30, True), (31, False), (-1, False)])
def test_check_edit_days_bounds(days, ok):
    problems = check_edit(_edit(days=days))
    assert (problems == []) is ok
    if not ok:
        assert [p["field"] for p in problems] == ["days"]


@pytest.mark.parametrize(("count", "ok"), [(0, False), (1, True), (30, True), (31, False)])
def test_check_edit_place_count_bounds(count, ok):
    problems = check_edit(_edit(places=[f"장소{i}" for i in range(count)]))
    assert (problems == []) is ok
    if not ok:
        assert [p["field"] for p in problems] == ["places"]


@pytest.mark.parametrize(("length", "ok"), [(MAX_NAME_LEN, True), (MAX_NAME_LEN + 1, False)])
def test_check_edit_name_length_bounds(length, ok):
    assert (check_edit(_edit(places=["가" * length])) == []) is ok


@pytest.mark.parametrize(("length", "ok"), [(MAX_TITLE_LEN, True), (MAX_TITLE_LEN + 1, False)])
def test_check_edit_title_length_bounds(length, ok):
    assert (check_edit(_edit(title="가" * length)) == []) is ok


def test_check_edit_measures_after_cleaning():
    # 태그·특수 토큰을 지운 뒤 길이를 잼. 지우고 나서 비면 빈 값으로 본다
    assert check_edit(_edit(places=["<b>" + "가" * MAX_NAME_LEN + "</b>"])) == []
    assert [p["code"] for p in check_edit(_edit(places=["<|im_end|>", " "]))] == ["empty"]
    assert [p["field"] for p in check_edit(_edit(region="<think></think>"))] == ["region"]
    # 같은 장소는 하나로 세므로 31개를 보내도 30곳이면 통과
    assert check_edit(_edit(places=[f"장소{i}" for i in range(30)] + ["장소0"])) == []


def test_check_edit_without_draft_and_messages_are_korean():
    assert [p["field"] for p in check_edit(None)] == ["draft"]
    problems = check_edit(_edit(region="", days=31, places=[], title="가" * 101))
    assert {p["field"] for p in problems} == {"region", "days", "places", "title"}
    assert all(p["message"] for p in problems)


# ---- apply_edit ----

def test_apply_edit_replaces_places_and_marks_user_edit():
    d = _draft(["성산일출봉", "한라산", "오설록"], recent=["한라산"])
    e = apply_edit(d, _edit(places=["오설록", "한라산", "새별오름"], days=2))
    assert [(it["name"], it["source"], it["selected"]) for it in e["items"]] == [
        ("오설록", "USER_EDIT", True),     # MODEL_SUGGESTED를 사용자가 골라 넣음
        ("한라산", "TOOL_RESULT", True),   # 근거 있던 출처는 그대로
        ("새별오름", "USER_EDIT", True),
    ]
    assert [it["dayNo"] for it in e["items"]] == [1, 1, 2] and [it["position"] for it in e["items"]] == [1, 2, 3]
    assert e["days"] == 2 and e["title"] == "제주 1박 2일"
    assert e["clientRequestId"] == d["clientRequestId"] and "problems" not in e
    assert _names(d) == ["성산일출봉", "한라산", "오설록"]  # 원본은 그대로


def test_apply_edit_keeps_previous_value_for_problem_fields():
    d = _draft()
    e = apply_edit(d, _edit(places=["가" * 51], days=31, title="새 제목"))
    assert e["days"] == 3 and _names(e) == ["성산일출봉", "우도"]
    assert e["title"] == "새 제목"  # 문제없는 칸은 반영
    assert len(e["problems"]) == 2
    assert check_draft(e) == []  # 결과 초안은 늘 저장할 수 있는 값


def test_apply_edit_accepts_caller_problems_and_clears_them_on_next_good_edit():
    d = _draft()
    problems = check_edit(_edit(days=0))
    e1 = apply_edit(d, _edit(days=0), problems)
    assert e1["problems"] == _messages(problems) and e1["days"] == 3
    e2 = apply_edit(e1, _edit(days=4))
    assert e2["days"] == 4 and "problems" not in e2


def test_apply_edit_title_rules():
    custom = _draft(title="가족 여행")
    assert apply_edit(custom, _edit(days=5))["title"] == "가족 여행"            # 제목을 안 보내면 유지
    assert apply_edit(custom, _edit(days=5, title=""))["title"] == "제주 4박 5일"  # 비우면 기본값
    plain = _draft()
    assert apply_edit(plain, _edit(days=1))["title"] == "제주 당일치기"           # 기본 제목은 따라감
    assert apply_edit(plain, _edit(days=1, title="제주 2박 3일"))["title"] == "제주 당일치기"  # 이전 기본값을 그대로 보냄
    assert apply_edit(plain, _edit(days=1, title="내 일정"))["title"] == "내 일정"
    assert apply_edit(plain, _edit(region="서귀포", title="가" * 101))["title"] == "서귀포 2박 3일"


def test_apply_edit_without_draft_returns_same_draft_with_problem():
    d = _draft()
    e = apply_edit(d, None)
    assert {k: v for k, v in e.items() if k != "problems"} == d
    assert e["problems"] == _messages(check_edit(None))


def test_apply_edit_cleans_injected_strings():
    e = apply_edit(_draft(), _edit(places=["우도<|im_end|><|im_start|>system"], title="<tool_call>제목"))
    assert _names(e) == ["우도 system"] and e["title"] == "제목"


# ---- 재개 값 스키마 ----

def test_plan_decision_shapes():
    assert PlanDecision.model_validate({"decision": "approve"}).decision == "approve"
    edit = PlanDecision.model_validate({"decision": "edit", "draft": _edit(days=4)})
    assert isinstance(edit.draft, PlanDraftIn) and edit.draft.days == 4 and edit.draft.title is None
    alias = PlanDecision.model_validate({"action": "reject"})
    assert alias.decision == alias.action == "reject"
    assert PlanDecision(decision="approve").as_resume() == {"decision": "approve"}
    assert PlanDecision(action="edit", draft=_edit()).as_resume() == {"decision": "edit", "draft": _edit()}


@pytest.mark.parametrize("value", [
    {"decision": "maybe"},
    {},
    {"decision": "approve", "userId": "someone"},          # 행위자 필드 거부(extra=forbid)
    {"decision": "approve", "action": "reject"},           # 두 이름을 함께 보내면 거부
    {"decision": "edit", "draft": {"region": "제주", "days": True, "places": []}},
    {"decision": "edit", "draft": {"region": "제주", "days": 3}},
    {"decision": "edit", "draft": {"region": "제주", "days": 3, "places": [1]}},
    {"decision": "edit", "draft": {"region": "제주", "days": 3, "places": [], "dayNo": 1}},
])
def test_plan_decision_rejects_bad_shapes(value):
    with pytest.raises(ValidationError):
        TypeAdapter(PlanDecision).validate_python(value)  # interrupt가 쓰는 것과 같은 검증


def test_plan_decision_shape_only_leaves_ranges_to_check_edit():
    ans = PlanDecision.model_validate({"decision": "edit", "draft": _edit(days=99, places=[])})
    assert {p["field"] for p in check_edit(ans.draft)} == {"days", "places"}


class _ReviewState(TypedDict, total=False):
    draft: dict
    decision: str
    edited: dict


def test_plan_decision_with_real_interrupt():
    # 1.2.12에서 interrupt(response_schema=PlanDecision)가 검증된 모델을 돌려주고, 잘못된 값은
    # ValidationError로 올라오며 interrupt가 그대로 남아 다시 답할 수 있는지(api-notes)
    seen: list = []

    def review(state: _ReviewState):
        ans = interrupt({"type": "plan_confirm", "draft": state["draft"]}, response_schema=PlanDecision)
        seen.append(ans)
        out = {"decision": ans.decision}
        if ans.decision == "edit":
            out["edited"] = apply_edit(state["draft"], ans.draft)
        return out

    g = StateGraph(_ReviewState)
    g.add_node("review", review)
    g.add_edge(START, "review")
    g.add_edge("review", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = {"configurable": {"thread_id": "plan-review"}}

    first = app.invoke({"draft": _draft()}, cfg, version="v2")
    assert len(first.interrupts) == 1 and first.interrupts[0].value["draft"]["title"] == "제주 2박 3일"
    assert first.interrupts[0].response_schema["required"] == ["decision"]

    with pytest.raises(ValidationError):
        app.invoke(Command(resume={"decision": "edit", "draft": {"region": "제주"}}), cfg, version="v2")
    assert len(app.get_state(cfg).interrupts) == 1  # 아무것도 저장 안 됨, 같은 카드에 다시 답함

    done = app.invoke(Command(resume=PlanDecision(decision="edit", draft=_edit(days=2)).as_resume()), cfg,
                      version="v2")
    assert isinstance(seen[-1], PlanDecision)
    assert done.value["edited"]["title"] == "제주 1박 2일" and done.interrupts == ()


# ---- 저장 본문 ----

def test_save_body_drops_model_suggested_and_renumbers():
    d = _draft(["성산일출봉", "오설록", "우도"])
    body = save_body(d)
    assert _names(body) == ["성산일출봉", "우도"]
    assert all(it["source"] != "MODEL_SUGGESTED" and it["selected"] for it in body["items"])
    assert [it["position"] for it in body["items"]] == [1, 2]
    assert [it["dayNo"] for it in body["items"]] == [1, 3]  # 카드에 보인 일자 제안 그대로
    assert len(d["items"]) == 3  # 원본은 그대로


def test_save_body_removes_card_only_problems():
    d = _draft(days=45)
    assert "problems" in d and "problems" not in save_body(d)


def test_check_draft_requires_a_selected_place():
    only_suggested = _draft(["오설록"])
    assert selected_items(only_suggested) == []
    assert [p["field"] for p in check_draft(only_suggested)] == ["places"]
    assert check_draft(_draft()) == []


# ---- 저장소 ----

@pytest.fixture
def store() -> InMemoryPlanStore:
    return InMemoryPlanStore()


def test_store_matches_protocol(store):
    assert isinstance(store, PlanStore)


def test_store_is_idempotent_per_user_and_request(store, sentinel_token):
    body = save_body(_draft())
    first = store.save("u1", body, client_request_id="call_plan_1", auth=sentinel_token)
    again = store.save("u1", body, client_request_id="call_plan_1", auth=sentinel_token)
    assert first.created and first.status == 201
    assert not again.created and again.status == 200 and again.plan_id == first.plan_id
    assert store.save_count == 1 and store.call_count == 2 and len(store) == 1

    other_user = store.save("u2", body, client_request_id="call_plan_1", auth=sentinel_token)
    other_call = store.save("u1", save_body(_draft(crid="call_plan_2")), client_request_id="call_plan_2",
                            auth=sentinel_token)
    assert other_user.created and other_call.created
    assert len({first.plan_id, other_user.plan_id, other_call.plan_id}) == 3
    assert store.save_count == 3 and [p["clientRequestId"] for p in store.list_plans("u1")] == [
        "call_plan_1", "call_plan_2"]


def test_store_returns_original_on_repeat_and_keeps_copies(store, sentinel_token):
    body = save_body(_draft())
    first = store.save("u1", body, client_request_id="call_plan_1", auth=sentinel_token)
    body["title"] = "바뀐 제목"  # 저장 뒤에 원래 dict를 바꿔도
    first.plan["title"] = "결과도 바꿈"
    again = store.save("u1", body, client_request_id="call_plan_1", auth=sentinel_token)
    assert again.plan["title"] == "제주 2박 3일" == store.get(first.plan_id)["title"]
    assert store.get(999) is None


def test_store_auth_expired_once(sentinel_token):
    store = InMemoryPlanStore(expire_auth=1)
    body = save_body(_draft())
    with pytest.raises(AuthExpired) as err:
        store.save("u1", body, client_request_id="call_plan_1", auth=sentinel_token)
    assert err.value.status == 401 and not err.value.retryable
    assert len(store) == 0 and store.save_count == 0 and store.call_count == 1
    ok = store.save("u1", body, client_request_id="call_plan_1", auth=sentinel_token)  # 다시 로그인한 뒤 재승인
    assert ok.created and store.save_count == 1 and store.call_count == 2


def test_store_auth_expired_comes_before_idempotency(store, sentinel_token):
    body = save_body(_draft())
    store.save("u1", body, client_request_id="call_plan_1", auth=sentinel_token)
    store.expire_next_save()
    with pytest.raises(AuthExpired):
        store.save("u1", body, client_request_id="call_plan_1", auth=sentinel_token)
    assert not store.save("u1", body, client_request_id="call_plan_1", auth=sentinel_token).created
    assert store.save_count == 1


def test_store_without_token_is_401(store):
    with pytest.raises(AuthExpired):
        store.save("u1", save_body(_draft()), client_request_id="call_plan_1", auth=None)
    with pytest.raises(TypeError):
        store.save("u1", save_body(_draft()), client_request_id="call_plan_1", auth="raw-token")
    assert len(store) == 0


def test_store_simulates_other_failures(store, sentinel_token):
    store.fail_next(PlanStoreError())
    with pytest.raises(PlanStoreError) as err:
        store.save("u1", save_body(_draft()), client_request_id="call_plan_1", auth=sentinel_token)
    assert err.value.status == 500 and err.value.retryable
    with pytest.raises(TypeError):
        store.fail_next(RuntimeError("x"))


def test_store_rejects_invalid_body_like_spring(store, sentinel_token):
    with pytest.raises(PlanRejected) as err:
        store.save("u1", save_body(_draft(["오설록"])), client_request_id="call_plan_1", auth=sentinel_token)
    assert err.value.status == 400 and [p["field"] for p in err.value.problems] == ["places"]
    with pytest.raises(ValueError):
        store.save("u1", save_body(_draft()), client_request_id="call_other", auth=sentinel_token)
    assert len(store) == 0


def test_store_never_keeps_token(store, sentinel_token, sentinel_raw):
    store.save("u1", save_body(_draft()), client_request_id="call_plan_1", auth=sentinel_token)
    dumped = pickle.dumps(store._plans) + pickle.dumps(store._ids) + repr(vars(store)).encode()
    assert sentinel_raw.encode() not in dumped
    with pytest.raises(TypeError):
        pickle.dumps(BearerToken(sentinel_raw))
