"""답변 가드(design.md 6.5, graph/guard.py): 규칙마다 통과·실패 예를 하나 이상. 그래프 안의 동작은 test_answer_llm.py."""
import pytest
from langchain_core.messages import AIMessage

from glople_agent import contract
from glople_agent.graph.guard import (MAX_ANSWER_CHARS, REASONS, AnswerFacts, answer_facts, canned_answers,
                                      check_answer, input_leaks, mentioned_names, numbers_in)
from glople_agent.llm.fake import generator_literals
from glople_agent.tools.backend import ToolContext
from glople_agent.tools.fake_backend import FakeBackend
from glople_agent.tools.registry import run_tool

from conftest import FIXED_NOW

TYPE3_SENTINEL = "TYPE3-SENTINEL-5d1c8e2a"


def _run(name: str, args: dict, call_id: str = "c1") -> tuple[str, dict]:
    content, card = run_tool(name, FakeBackend(today=FIXED_NOW.date()), args, call_id, ToolContext(now=FIXED_NOW))
    return content, {**card, "turn_id": "t1"}


@pytest.fixture(scope="module")
def keyword_facts() -> AnswerFacts:
    content, card = _run("recommend_by_keywords", {"keywords": ["바다", "맛집"]})
    return answer_facts([content], [card], "바다 맛집 추천해줘")


@pytest.fixture(scope="module")
def review_facts() -> AnswerFacts:
    content, card = _run("search_reviews", {"query": "바나힐"})
    return answer_facts([content], [card], "바나힐 후기 2박 3일")


@pytest.fixture(scope="module")
def glopler_facts() -> AnswerFacts:
    content, card = _run("search_gloplers", {"hashtag": "나고야"})
    return answer_facts([content], [card], "나고야 글로플러")


GOOD = "바다 취향으로 산토리니, 가거도, 가천 다랭이마을을 찾았어요. 맛집은 목록에 없어 반영하지 못했어요."


def test_good_answer_passes(keyword_facts):
    v = check_answer(GOOD, keyword_facts)
    assert v.ok and v.reasons == () and v.reason is None
    assert v.details["mentioned"] == ["산토리니", "가거도", "가천 다랭이마을"]
    assert check_answer(AIMessage(content=GOOD, response_metadata={"finish_reason": "stop"}), keyword_facts).ok


def test_empty(keyword_facts):
    for text in ("", "   \n"):
        v = check_answer(text, keyword_facts)
        assert not v.ok and v.reason == "empty"


def test_tool_calls_present(keyword_facts):
    msg = AIMessage(content="", tool_calls=[{"name": "search_reviews", "args": {"query": "가거도"}, "id": "x"}])
    assert check_answer(msg, keyword_facts).reasons[:2] == ("empty", "tool_calls")
    bad_args = AIMessage(content=GOOD, invalid_tool_calls=[{"name": "x", "args": "{", "id": "y", "error": "e",
                                                             "type": "invalid_tool_call"}])
    assert "tool_calls" in check_answer(bad_args, keyword_facts).reasons
    assert "tool_calls" in check_answer(GOOD, keyword_facts, finish_reason="tool_calls").reasons


@pytest.mark.parametrize("text", [
    GOOD + ' <tool_call>{"name": "search_reviews"}</tool_call>',
    GOOD + " <|im_end|>",
    "<tool_response>" + GOOD,
    GOOD + "<think>",
])
def test_special_token_text(keyword_facts, text):
    assert "tool_call_text" in check_answer(text, keyword_facts).reasons


@pytest.mark.parametrize(("text", "flagged"), [
    (GOOD + ' {"name": "가거도"}', True),
    (GOOD + ' "status": "ok"', True),
    (GOOD + " }", True),
    ('가거도는 "섬"이라고 불러요: 산토리니도 있어요.', False),   # 한글 따옴표 뒤 콜론은 JSON 조각이 아님
])
def test_json_fragment(keyword_facts, text, flagged):
    assert ("json_fragment" in check_answer(text, keyword_facts).reasons) is flagged


@pytest.mark.parametrize(("finish", "ok"), [("stop", True), ("length", False), (None, False)])
def test_incomplete_stream_or_length(keyword_facts, finish, ok):
    # finish_reason None = 스트림이 [DONE]·finish_reason 없이 끊김(fake 서버 Reply.cut), length = max_tokens에서 잘림
    v = check_answer(GOOD, keyword_facts, finish_reason=finish)
    assert v.ok is ok and (ok or v.reason == "incomplete")
    msg = AIMessage(content=GOOD, response_metadata={"finish_reason": finish} if finish else {})
    assert check_answer(msg, keyword_facts, finish_reason=msg.response_metadata.get("finish_reason")).ok is ok


def test_canned_training_sentence(keyword_facts):
    greeting = generator_literals()["GREETING_ANSWER"]
    assert greeting in canned_answers()
    v = check_answer(greeting, keyword_facts)
    assert not v.ok and v.reason == "canned"
    assert "canned" in check_answer("  " + greeting.replace(" ", "\n", 1), keyword_facts).reasons   # 공백 정규화
    assert "canned" not in check_answer(greeting + " 산토리니도 있어요.", keyword_facts).reasons    # 같을 때만


@pytest.mark.needs_training_data("tool_calling_v2/train.jsonl")
def test_canned_set_is_the_30_training_texts():
    texts = contract.canned_texts()
    assert len(texts) == 30 and canned_answers() == frozenset(" ".join(t.split()) for t in texts)


def test_no_item_name(keyword_facts, review_facts, glopler_facts):
    v = check_answer("바다가 보이는 좋은 곳이 많아요. 카드를 확인해 보세요.", keyword_facts)
    assert not v.ok and v.reason == "no_item_name" and v.details["mentioned"] == []
    assert check_answer("가천다랭이마을이 좋아요.", keyword_facts).ok                     # 공백 무시
    assert check_answer("후기 '바나힐 골든브릿지 다녀왔어요'가 있어요.", review_facts).ok
    assert check_answer("바나힐 골든브릿지 다녀온 후기가 있어요.", review_facts).ok        # 제목 10자 겹침
    assert not check_answer("바나힐 후기가 있어요.", review_facts).ok                       # 검색어만으로는 부족
    assert check_answer("도쿄산책 님이 일본 글로플러예요.", glopler_facts).ok


def test_mentioned_names_for_routes():
    content, card = _run("recommend_similar_traveler_routes", {"age": 28, "gender": "남성", "mbti": "ENTP"})
    facts = answer_facts([content], [card], "28살 남자 ENTP")
    assert mentioned_names("20대 ENTP 여행자는 제주·부산·강릉을 다녀왔어요.", facts) == ["제주", "부산", "강릉"]
    assert check_answer("20대 남성 ENTP 여행자가 제주를 다녀왔어요.", facts).ok              # 20대·28 모두 근거 있음


@pytest.mark.parametrize(("text", "unknown"), [
    ("산토리니 등 5곳을 찾았어요.", []),                      # 항목 수
    ("산토리니 등 7곳을 찾았어요.", ["7"]),
    ("산토리니 등 다섯 곳을 찾았어요.", []),                   # 고유어 수 + 단위
    ("산토리니 등 세 곳을 찾았어요.", ["3"]),
    ("산토리니는 한 번쯤 가 볼 만해요. 한 가지 더, 가거도도 있어요.", []),   # '한'은 관용 표현이라 세지 않음
    ("산토리니는 세분화된 분류가 없어요.", []),                  # 붙어 쓴 한 자는 수로 보지 않음
    ("산토리니는 기온이 25도예요.", ["25"]),
    ("산토리니까지 1,000km예요.", ["1000"]),
])
def test_unknown_numbers(keyword_facts, text, unknown):
    v = check_answer(text, keyword_facts)
    assert v.details.get("unknown_numbers", []) == unknown
    assert ("unknown_number" in v.reasons) is bool(unknown)


@pytest.mark.parametrize(("text", "ok"), [
    ("'바나힐 골든브릿지 다녀왔어요' 후기는 좋아요 12개예요.", True),
    ("'바나힐 골든브릿지 다녀왔어요'는 2024년 6월 2일 글이에요.", True),        # 날짜 구성 숫자
    ("'바나힐 골든브릿지 다녀왔어요'는 2024.06.02에 올라왔어요.", True),
    ("'바나힐 골든브릿지 다녀왔어요'는 2024년 5월 글이에요.", False),
    ("'다낭 3박 4일: 바나힐, 미케비치, 호이안'은 2박 3일 일정과 달라요.", True),   # 제목·사용자 발화의 숫자
    ("'바나힐 골든브릿지 다녀왔어요' 후기는 좋아요 13개예요.", False),
])
def test_numbers_from_results_dates_and_user(review_facts, text, ok):
    assert check_answer(text, review_facts).ok is ok


def test_decimal_numbers(glopler_facts):
    assert check_answer("도쿄산책 님은 별점 4.8점이에요.", glopler_facts).ok
    v = check_answer("도쿄산책 님은 별점 4.9점이에요.", glopler_facts)
    assert v.details["unknown_numbers"] == ["4.9"]


def test_numbers_in_table():
    assert numbers_in("2박 3일, 20대, 4.8점, 2024-08-01, 2024.08.01, 열두 곳, 스무 명, 1,234원") == {
        "2", "3", "20", "4.8", "2024", "8", "1", "12", "1234"}
    assert numbers_in("한 번 가 보세요. 네거리, 세분화, 두개") == set()


def test_card_overlap_with_type3_text(keyword_facts):
    # 모델은 원문을 보지 못하지만 우연·기억으로 옮겨 쓰면 막는다(공백 정규화 뒤 20자)
    copied = f"가거도는 {TYPE3_SENTINEL} 가거도 설명문 자리   표시자입니다 라고 해요."
    v = check_answer(copied, keyword_facts)
    assert not v.ok and "card_overlap" in v.reasons and v.details["card_overlap"] == [
        "recommend_by_keywords.items[1].text"]   # (sentinel의 16진 숫자는 unknown_number로도 걸림)
    # FREE_FACTS 틀 문장(Glople가 만든 분류 문장)은 LLM에도 가는 글이라 겹쳐도 괜찮음
    free = "경포해변은 강원특별자치도 강릉시의 관광지입니다. 분류: 자연관광 > 자연경관이에요."
    assert "card_overlap" not in check_answer(free, keyword_facts).reasons


def test_card_overlap_with_review_excerpt_and_glopler_intro(review_facts, glopler_facts):
    excerpt = "바나힐 골든브릿지 다녀왔어요: 케이블카 줄이 길었지만 골든브릿지 풍경이 정말 좋았어요."
    assert check_answer(excerpt, review_facts).reason == "card_overlap"
    intro = "도쿄산책 님은 도쿄에서 10년째 살고 있어요. 골목 맛집과 카페를 안내해요."
    assert "card_overlap" in check_answer(intro, glopler_facts).reasons


def test_too_long(keyword_facts):
    text = ("산토리니가 좋아요. " * 80)[:MAX_ANSWER_CHARS + 1]
    assert check_answer(text, keyword_facts).reasons == ("too_long",)
    assert check_answer(text[:MAX_ANSWER_CHARS], keyword_facts).ok


def test_reasons_are_all_listed_in_fixed_order(keyword_facts):
    v = check_answer('안녕! {"tool": 1} 7곳 ' + "가" * 600, keyword_facts)
    assert v.reasons == tuple(r for r in REASONS if r in {"json_fragment", "no_item_name", "unknown_number",
                                                          "too_long"})


def test_input_leaks_catch_card_text_in_contents(keyword_facts):
    content, _ = _run("recommend_by_keywords", {"keywords": ["바다"]})
    assert input_leaks([content], keyword_facts) == []
    assert input_leaks([content + f" {TYPE3_SENTINEL} 가거도 설명문 자리 표시자입니다."], keyword_facts) == [
        "recommend_by_keywords.items[1].text"]
