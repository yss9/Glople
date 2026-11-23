"""후속 발화 메모리(M1): memory.pending_clarify 이어 붙이기, 지시어(거기·그곳·그쪽) → memory.last_region.

라우터에는 여전히 [System, Human(한 마디)]만 간다. 이어 붙이기·지시어 풀이는 router_input에만 반영되고 HumanMessage 원문은 그대로.
"""
import pytest
from langchain_core.messages import HumanMessage

from glople_agent.answer import templates as tpl
from glople_agent.graph.followup import compose_router_input, looks_like_new_request, replace_demonstratives
from glople_agent.llm.fake import QuirkScript, RuleScript, text_message, tool_call_message

PENDING = {"pending_clarify": {"utterance": "나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임", "reason": "clarify:age",
                               "tool": "recommend_similar_traveler_routes"}}


@pytest.mark.parametrize(("text", "region", "expected"), [
    ("거기 날씨 어때?", "제주", "제주 날씨 어때?"),
    ("거기서 맛집 찾아줘", "강릉", "강릉에서 맛집 찾아줘"),
    ("그곳은 어때", "부산", "부산은 어때"),
    ("그곳은 어때", "제주", "제주는 어때"),
    ("그쪽으로 가는 가이드", "서울", "서울로 가는 가이드"),
    ("그쪽으로 가는 가이드", "부산", "부산으로 가는 가이드"),
    ("거기 후기랑 거기 날씨", "속초", "속초 후기랑 속초 날씨"),
    ("여기 날씨", "제주", "여기 날씨"),                 # 여기(지금 있는 곳)는 모름 → 그대로
    ("거기요?", "제주", "거기요?"),                     # 모르는 꼴은 건드리지 않음
    ("거기 날씨", None, "거기 날씨"),                   # last_region 없음
])
def test_replace_demonstratives(text, region, expected):
    assert replace_demonstratives(text, region)[0] == expected


@pytest.mark.parametrize(("text", "new"), [
    ("28살이요", False), ("부산", False), ("2박 3일, 성산일출봉이랑 우도", False), ("엔프피 여자", False),
    ("안녕", True), ("고마워요", True), ("부산 날씨 알려줘", True), ("제주 맛집 추천해줘", True),
    ("가" * 41, True),
])
def test_looks_like_new_request(text, new):
    assert looks_like_new_request(text) is new


def test_compose_joins_answer_and_skips_new_request():
    joined, info = compose_router_input("28살이요", PENDING)
    assert joined == "나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임 28살이요"
    assert info["joined"] == {"reason": "clarify:age", "tool": "recommend_similar_traveler_routes"}
    same, info = compose_router_input("부산 날씨 알려줘", PENDING)
    assert same == "부산 날씨 알려줘" and info["joined"]["skipped"] == "new_request"
    out, info = compose_router_input("거기 날씨", {"last_region": "제주"})
    assert out == "제주 날씨" and info == {"demonstratives": 1}
    assert compose_router_input("안녕", {}) == ("안녕", {})


def test_clarify_then_answer_runs_tool_with_joined_utterance(make_graph, agent_context):
    kit = make_graph(RuleScript())
    first = kit.turn("나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임", agent_context)
    assert first.reply_kind == "clarify"
    memory = kit.values()["memory"]
    assert memory["pending_clarify"]["utterance"] == "나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임"
    second = kit.turn("28살이요", agent_context)
    assert second.reply_kind == "template" and "비슷한 여행자 3명" in second.final_text
    v = kit.values()
    assert v["router_input"] == "나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임 28살이요"
    assert v["user_text"] == "28살이요" and v["messages"][-4].content == "28살이요"   # 원문 HumanMessage 그대로
    assert v["memory"]["pending_clarify"] is None                                  # 한 턴만 씀
    messages, _ = kit.model.calls[-1]
    assert [type(m).__name__ for m in messages] == ["SystemMessage", "HumanMessage"]   # 여전히 한 마디
    assert messages[1].content == v["router_input"]
    assert kit.backend.calls[-1] == ("recommend_similar_traveler_routes", {"age": 28, "gender": "남성", "mbti": "ENTP"})
    assert kit.sink[-1]["followup"] == {"joined": True, "demonstratives": 0}


def test_invented_age_then_answer(make_graph, agent_context):
    # 설계 5.5의 2번: LoRA가 age 30을 지어냄 → 근거 검사가 되물음 → "28살이요"를 이어 붙여 다시
    kit = make_graph(RuleScript())
    r = kit.turn("(지어냄) 나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임", agent_context)
    assert r.reply_kind == "clarify" and kit.backend.calls == []
    assert kit.values()["memory"]["pending_clarify"]["reason"] == "clarify:age"
    kit.turn("28살이요", agent_context)
    assert kit.backend.calls == [("recommend_similar_traveler_routes", {"age": 28, "gender": "남성", "mbti": "ENTP"})]


def test_pending_is_dropped_for_new_request(make_graph, agent_context):
    kit = make_graph(RuleScript())
    kit.turn("나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임", agent_context)
    r = kit.turn("안녕", agent_context)
    assert r.reply_kind == "direct" and kit.values()["router_input"] == "안녕"
    assert kit.values()["memory"]["pending_clarify"] is None


def test_router_clarify_text_also_sets_pending(make_graph, agent_context):
    weather = tool_call_message([("get_weather", {"region": "부산", "when": "내일"})])
    kit = make_graph(QuirkScript([text_message(tpl.CLARIFY["weather_region"]), weather]))
    r = kit.turn("내일 날씨 어때?", agent_context)
    assert r.reply_kind == "direct" and kit.values()["memory"]["pending_clarify"]["reason"] == "router:weather_region"
    kit.turn("부산", agent_context)
    assert kit.values()["router_input"] == "내일 날씨 어때? 부산"
    assert kit.backend.calls == [("get_weather", {"region": "부산", "when": "내일"})]


def test_greeting_does_not_set_pending(make_graph, agent_context):
    kit = make_graph(RuleScript())
    kit.turn("안녕", agent_context)
    assert not (kit.values().get("memory") or {}).get("pending_clarify")


def test_demonstrative_uses_last_region_from_previous_turn(make_graph, agent_context):
    kit = make_graph(RuleScript())
    kit.turn("부산 이번 주말 날씨 어때?", agent_context)
    assert kit.values()["memory"]["last_region"] == "부산"
    r = kit.turn("거기 후기 좀 찾아줘", agent_context)
    v = kit.values()
    assert v["router_input"] == "부산 후기 좀 찾아줘" and v["messages"][-4].content == "거기 후기 좀 찾아줘"
    assert kit.backend.calls[-1] == ("search_reviews", {"query": "부산"}) and r.reply_kind == "template"
    assert kit.sink[-1]["followup"]["demonstratives"] == 1


def test_demonstrative_without_memory_still_clarifies(make_graph, agent_context):
    kit = make_graph(RuleScript())
    r = kit.turn("거기 후기 좀 찾아줘", agent_context)
    assert r.final_text == tpl.CLARIFY["reviews_target"]   # 학습한 되묻기 그대로(지시어 불허)


def test_last_region_from_glopler_hashtag_and_plan(make_graph, agent_context):
    kit = make_graph(RuleScript())
    kit.turn("도쿄 글로플러 찾아줘", agent_context)
    assert kit.values()["memory"]["last_region"] == "도쿄"   # 해시태그가 아는 지역이면 last_region
    kit.turn("제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘", agent_context)
    assert kit.values()["memory"]["last_region"] == "제주"
    kit.resume({"decision": "reject"}, agent_context)
    kit.turn("맛집투어 글로플러 찾아줘", agent_context)
    assert kit.values()["memory"]["last_region"] == "제주"   # 지역이 아닌 해시태그는 바꾸지 않음


def test_original_message_untouched_in_history(make_graph, agent_context):
    kit = make_graph(RuleScript())
    kit.turn("부산 이번 주말 날씨 어때?", agent_context)
    kit.turn("그곳은 후기 어때", agent_context)
    humans = [m.content for m in kit.values()["messages"] if isinstance(m, HumanMessage)]
    assert humans == ["부산 이번 주말 날씨 어때?", "그곳은 후기 어때"]


@pytest.mark.parametrize("key", list(tpl.CLARIFY))
def test_clarify_kind_knows_every_trained_clarify(key):
    assert tpl.clarify_kind(tpl.CLARIFY[key]) == key


def test_clarify_kind_frames_and_non_clarify():
    assert tpl.clarify_kind(tpl.clarify_search_region("맛집")) == "search_region"
    assert tpl.clarify_kind(tpl.clarify_routes(["age", "gender"])) == "routes"
    assert tpl.clarify_kind(tpl.clarify_region_choice(["광주광역시", "경기 광주시"])) == "region_choice"
    assert tpl.clarify_kind("안녕하세요! Glople 여행 도우미예요.") is None
    assert tpl.clarify_kind(None) is None
    with pytest.raises(ValueError):
        tpl.clarify_region_choice(["광주광역시"])
