"""템플릿 답: 되묻기 문장 = labeling_rules.md 6절, 조사 규칙, 병렬 결과 이어 붙이기, 출처 문구."""
import itertools
import json
import re
from datetime import date

import pytest
from langchain_core.messages import ToolMessage

from glople_agent import contract
from glople_agent.answer import templates as T
from glople_agent.tools.envelope import ToolBackendError, bind_card, card_from_tool_message, tool_error_to_json
from glople_agent.tools.fake_backend import FakeBackend
from glople_agent.tools.registry import build_read_tools

RULES = contract.LABELING_RULES_PATH.read_text(encoding="utf-8")
SECTION6 = RULES.split("## 6.", 1)[1].split("## 7.", 1)[0]
SEARCH_RE = re.compile(r"^어느 지역의 (.+)[을를] 찾아드릴까요\?$")


@pytest.fixture
def tools() -> dict:
    return {t.name: t for t in build_read_tools(FakeBackend(today=date(2026, 10, 17)))}


def _card(tool, args: dict, call_id: str = "c1") -> dict:
    msg = tool.invoke({"type": "tool_call", "id": call_id, "name": tool.name, "args": args})
    return bind_card(msg.artifact, turn_id="t1")


# --- 되묻기 문장 = labeling_rules.md 6절 ----------------------------------------------

@pytest.mark.parametrize("key", list(T.CLARIFY))
def test_clarify_sentences_are_in_labeling_rules_section6(key):
    assert T.CLARIFY[key] in SECTION6


def test_clarify_templates_are_in_labeling_rules():
    assert T.CLARIFY_SEARCH_REGION.replace("{category_eul}", "{카테고리 말}을/를") in SECTION6
    assert T.CLARIFY_ROUTES.replace("{missing_i}", "{빠진 것}이/가") in SECTION6
    assert T.clarify_search_region("맛집") == "어느 지역의 맛집을 찾아드릴까요?"
    assert "(예: 어느 지역의 맛집을 찾아드릴까요?)" in SECTION6


def test_clarify_extra_is_marked_outside_rules():
    # 학습 정답에 없는 문장은 CLARIFY와 섞지 않는다
    assert not set(T.CLARIFY_EXTRA.values()) & set(T.CLARIFY.values())
    for text in T.CLARIFY_EXTRA.values():
        assert text not in RULES


def test_josa_examples_from_section6():
    # 6절: 나이가, 성별이, MBTI가(엠비티아이), 나이와 성별이, 성별과 MBTI가, 나이, 성별, MBTI가
    assert [T.josa(T.join_words(ws), "이/가") for ws in (
        ["나이"], ["성별"], ["MBTI"], ["나이", "성별"], ["성별", "MBTI"], ["나이", "성별", "MBTI"])] == [
        "나이가", "성별이", "MBTI가", "나이와 성별이", "성별과 MBTI가", "나이, 성별, MBTI가"]
    for word in ("나이가", "성별이", "MBTI**가**", "나이와 성별**이**", "성별**과** MBTI**가**"):
        assert word.replace("**", "") in SECTION6.replace("**", "")


@pytest.mark.parametrize(("word", "particle", "expected"), [
    ("맛집", "을/를", "맛집을"), ("숙소", "을/를", "숙소를"), ("잘 곳", "을/를", "잘 곳을"),
    ("레포츠", "을/를", "레포츠를"), ("호텔", "를/을", "호텔을"),
    ("제주", "은/는", "제주는"), ("부산", "은/는", "부산은"), ("성별", "와/과", "성별과"),
    ("ENFP", "이/가", "ENFP가"), ("ENTJ", "이/가", "ENTJ가"), ("Seoul", "이/가", "Seoul이"),
    ("BTS", "이/가", "BTS가"), ("3", "이/가", "3이"), ("2", "이/가", "2가"), ("10", "이/가", "10이"),
    ("1박 2일", "으로/로", "1박 2일로"), ("서울", "으로/로", "서울로"), ("부산", "으로/로", "부산으로"),
    ("제주", "으로/로", "제주로"), ("'졸업 여행'", "이라는/라는", "'졸업 여행'이라는"),
    ("서울(특별시)", "이/가", "서울(특별시)가"),
])
def test_josa(word, particle, expected):
    assert T.josa(word, particle) == expected


def test_josa_rejects_unknown_particle():
    with pytest.raises(ValueError):
        T.josa("제주", "에/에서")
    with pytest.raises(ValueError):
        T.josa("제주", "께")
    assert T.pick_josa("나이", "이") == "가"  # 한 꼴만 적어도 짝을 찾음


def _clarify_candidates() -> set[str]:
    out = set(T.CLARIFY.values())
    fields = list(T.ROUTE_FIELD_LABELS)
    for n in range(1, 4):
        out |= {T.clarify_routes(c) for c in itertools.combinations(fields, n)}
    return out


# 바로 답변 문장(인사·감사·기능·거절)은 라우터가 낸 글을 그대로 쓰므로 템플릿 대상이 아니다. 생성기에서 ast로만 읽는다
DIRECT_ANSWERS = {contract.read_assigned_literal(contract.DATASET_SCRIPT_PATH, n)
                  for n in ("GREETING_ANSWER", "THANKS_ANSWER", "ABILITY_ANSWER", "DECLINE_ANSWER")}


def _assert_reproduced(answers) -> int:
    """학습 정답의 되묻기 문장을 코드가 글자 그대로 만드는지. 확인한 문장 수를 돌려준다."""
    candidates = _clarify_candidates()
    checked = 0
    for text in set(answers) - DIRECT_ANSWERS:
        m = SEARCH_RE.match(text)
        if m:
            assert T.clarify_search_region(m.group(1)) == text  # 조사까지 같게 만든다
        else:
            assert text in candidates, text
        checked += 1
    return checked


def test_human_style_clarify_answers_reproduced():
    answers = [r["messages"][-1]["content"] for r in contract.read_jsonl(contract.HUMAN_STYLE_PATH)
               if not r["messages"][-1].get("tool_calls")]
    assert _assert_reproduced(answers) == 8


@pytest.mark.needs_training_data("tool_calling_v2/train.jsonl")
def test_train_canned_clarify_answers_reproduced():
    texts = contract.canned_texts()
    # 정형 문장 30개 = 바로 답변 4개 + 되묻기 26개. 되묻기는 모두 코드로 똑같이 만든다
    assert DIRECT_ANSWERS <= set(texts)
    assert _assert_reproduced(texts) == len(texts) - 4 == 26


@pytest.mark.parametrize(("missing", "key"), [
    ({"region", "days", "places"}, "plan_all"),
    ({"days", "places"}, "plan_days_places"),
    ({"places"}, "plan_places"),
    ({"days"}, "plan_days_places"),        # 표에 없는 조합은 덮는 문장으로
    ({"region"}, "plan_all"),
    ({"region", "places", "title"}, "plan_all"),
])
def test_clarify_plan(missing, key):
    assert T.clarify_plan(missing) == T.CLARIFY[key]


def test_clarify_for_each_tool():
    assert T.clarify_for("get_weather", ["region"]) == "어느 지역 날씨를 알려드릴까요?"
    assert T.clarify_for("search_destinations", ["region"], category_word="숙소") == "어느 지역의 숙소를 찾아드릴까요?"
    assert T.clarify_for("search_destinations", ["region"]) == "어느 지역의 여행지를 찾아드릴까요?"
    assert T.clarify_for("recommend_similar_traveler_routes", ["age"]) == \
        "비슷한 여행자를 찾으려면 나이가 필요해요. 알려주시겠어요?"
    assert T.clarify_for("recommend_similar_traveler_routes", ["mbti", "age"]) == \
        "비슷한 여행자를 찾으려면 나이와 MBTI가 필요해요. 알려주시겠어요?"
    assert T.clarify_for("create_travel_plan", ["places"]) == "어떤 장소를 넣어서 저장할까요?"
    assert T.clarify_for("search_reviews", ["query"]) == T.CLARIFY["reviews_target"]
    assert T.clarify_for("recommend_by_keywords", ["keywords"]) == T.CLARIFY["keywords_any"]
    assert T.clarify_for("get_destination_detail", ["name"]) == T.CLARIFY_EXTRA["detail_name"]
    assert T.clarify_for("unknown_tool") is None
    with pytest.raises(ValueError):
        T.clarify_routes([])


# --- 도구별 템플릿 답과 병렬 이어 붙이기 --------------------------------------------------

def test_parallel_detail_empty_and_reviews_like_design_example(tools):
    # design.md 5.5의 1번: 상세(M6: S1에 바나힐 없음 → 빈 결과), 후기는 ok → 호출 순서대로 이어 붙임
    cards = [_card(tools["get_destination_detail"], {"name": "바나힐"}, "c1"),
             _card(tools["search_reviews"], {"query": "바나힐"}, "c2")]
    text = T.answer_for_cards(cards)
    assert text.startswith("Glople에 그 장소 정보가 없어요. 바나힐 관련 Glople 후기 3개를 찾았어요: ")
    assert "'다낭 3박 4일: 바나힐, 미케비치, 호이안'" in text and "<|im_end|>" not in text
    # 순서를 바꾸면 답 순서도 바뀐다
    assert T.answer_for_cards(cards[::-1]).startswith("바나힐 관련 Glople 후기 3개")


def test_parallel_same_message_said_once():
    off = {t.name: t for t in build_read_tools(FakeBackend(today=date(2026, 10, 17), weather="off"))}   # 날씨 끔
    cards = [_card(off["get_weather"], {"region": "부산", "when": "오늘"}, "c1"),
             _card(off["get_weather"], {"region": "부산", "when": "이번 주말"}, "c2"),
             _card(off["search_gloplers"], {"hashtag": "공주"}, "c3")]
    assert T.answer_for_cards(cards) == ("날씨 정보는 아직 연결하지 않았어요. 공주 글로플러를 찾지 못했어요. "
                                         "국내 글로플러는 아직 없어요(지금은 미국·이탈리아·일본).")


def test_parallel_weather_numbers_from_cards_and_shared_note_once(tools):
    # M6: 날씨 수치는 카드 값 그대로(코드), 오늘(토)·이번 주말(토·일)을 현지 날짜로. 같은 fixture note는 한 번만
    cards = [_card(tools["get_weather"], {"region": "부산", "when": "오늘"}, "c1"),
             _card(tools["get_weather"], {"region": "부산", "when": "이번 주말"}, "c2")]
    text = T.answer_for_cards(cards)
    today = cards[0]["data"]["items"][0]
    assert text.startswith(f"부산 오늘 날씨예요: 10월 17일(토) {today['summary']}, 최저 {today['tmin']}도·최고 {today['tmax']}도, "
                           f"강수 확률 {today['pop']}%. 오프라인 가짜 날씨(fixture)라 실제 날씨가 아니에요. "
                           f"부산 이번 주말 날씨예요: 10월 17일(토) ")
    assert "/ 10월 18일(일) " in text and text.count("오프라인 가짜 날씨") == 1
    assert [i["date"] for i in cards[1]["data"]["items"]] == ["2026-10-17", "2026-10-18"]
    assert all(c["data"]["attribution"] == "Weather data by Open-Meteo.com (CC BY 4.0)" for c in cards)


def test_join_answers_rules():
    assert T.join_answers(["첫 문장", None, "  ", "둘째 문장.", "첫 문장.", "셋째?"]) == "첫 문장. 둘째 문장. 셋째?"
    assert T.join_answers([]) == ""
    assert T.answer_for_cards([]) == T.NO_RESULT


@pytest.mark.parametrize(("name", "args", "expected"), [
    ("recommend_by_keywords", {"keywords": ["바다"]},
     "바다 취향으로 여행지 5곳을 찾았어요: 산토리니, 가거도, 가천 다랭이마을, 경포해변, 광안리해수욕장."),
    ("recommend_by_keywords", {"keywords": ["야경", "맛집"]},
     "야경 취향으로 여행지 5곳을 찾았어요: 산토리니, 감천문화마을, 광안리해수욕장, 남산서울타워, 민락수변공원. "
     "목록에 없어 반영하지 못한 키워드: 맛집."),
    ("recommend_by_keywords", {"keywords": ["맛집"]}, "그 취향으로 찾은 곳이 없어요. 목록에 없어 반영하지 못한 키워드: 맛집."),
    ("search_reviews", {"query": "리스본"}, "아직 리스본 후기가 없어요."),
    ("search_gloplers", {"hashtag": "혼행"}, "혼행 글로플러 2명을 찾았어요: 맨해튼러(미국), 눈길여행(일본)."),
    # M1: 순서는 가짜 Spring이 흉내 낸 실제 서버 규칙(6차원 코사인 유사도↓, UserInfoRecommendationService)
    ("recommend_similar_traveler_routes", {"age": 28, "gender": "남성", "mbti": "ENTP"},
     "나이·성별·MBTI가 비슷한 여행자 3명의 루트를 찾았어요: 20대 남성 ENTP(제주·부산·강릉), "
     "20대 남성 INTP(울릉도·독도·포항), 30대 남성 ENTJ(도쿄·오사카·교토)."),
    ("recommend_similar_traveler_routes", {"age": 90, "gender": "여성", "mbti": "ENFP"}, "비슷한 여행자를 찾지 못했어요."),
    # M6: 숙박은 수집 안 함(부르지 않음), S1 검색·상세는 fixture 행, 날씨는 가짜 Open-Meteo 값(좌표·날짜로 정해짐)
    ("search_destinations", {"region": "강릉", "category": "숙박"},
     "음식점·숙소 정보는 아직 모으지 않았어요. 관광지·문화시설·축제·레포츠는 찾아드릴 수 있어요."),
    ("search_destinations", {"region": "강릉", "category": "관광지", "limit": 5},
     "강릉 관광지 5곳을 찾았어요: 오죽헌, 안목해변, 경포해변, 정동진, 주문진항."),
    ("search_destinations", {"region": "도쿄", "category": "문화시설"},
     "도쿄 문화시설 3곳을 찾았어요: 도쿄 국립박물관, 국립 서양 미술관, 에도 도쿄 박물관."),
    ("get_destination_detail", {"name": "해동용궁사"},
     "부산광역시 기장군에 있는 해동용궁사 정보를 찾았어요. 자세한 소개는 카드에서 볼 수 있어요."),
    ("get_destination_detail", {"name": "보문사"}, "보문사는 인천 강화군과 서울특별시 성북구에 있어요. 어느 지역의 보문사인가요?"),
    ("get_destination_detail", {"name": "바나힐"}, "Glople에 그 장소 정보가 없어요."),
    ("get_weather", {"region": "부산"},
     "부산 오늘 날씨예요: 10월 17일(토) 맑음, 최저 12도·최고 20도, 강수 확률 2%. 오프라인 가짜 날씨(fixture)라 실제 날씨가 아니에요."),
    ("get_weather", {"region": "강원도"},
     "날씨 정보를 찾지 못했어요. 도 전체 날씨는 한 곳으로 정하기 어려워요. 시·군 이름으로 물어봐 주세요."),
])
def test_answer_for_card_per_tool(tools, name, args, expected):
    assert T.answer_for_card(_card(tools[name], args)) == expected


def test_template_answer_never_carries_card_text(tools):
    for kws in (["바다"], ["사찰"], ["야경"], list(contract.KEYWORDS)):
        text = T.answer_for_card(_card(tools["recommend_by_keywords"], {"keywords": kws}))
        assert "SENTINEL" not in text and "TYPE1-DESC" not in text and "WIKI-DESC" not in text


def test_error_card_answer():
    content = tool_error_to_json(ToolBackendError("search_reviews", "timeout"))
    msg = ToolMessage(content=content, tool_call_id="c9", name="search_reviews", status="error")
    card = card_from_tool_message(msg, turn_id="t1", args={"query": "바나힐"})
    assert T.answer_for_card(card) == json.loads(content)["message_ko"] == \
        "여행 후기 검색 응답이 늦어 결과를 가져오지 못했어요. 잠시 뒤에 다시 시도해 주세요."
    # 모르는 도구 이름(ToolNode 자체 문장)도 정해진 안내로
    odd = ToolMessage(content="Error: book_hotel is not a valid tool", tool_call_id="c10", name="book_hotel",
                      status="error")
    assert T.answer_for_card(card_from_tool_message(odd, turn_id="t1")) == T.error_message(None, None)


def test_ok_message_with_nothing_falls_back_to_empty_sentence():
    assert T.ok_message("search_reviews", {"query": "도동항"}, []) == "아직 도동항 후기가 없어요."
    assert T.status_message("search_destinations", "empty", {"region": "강릉"}) == "강릉에서 찾은 곳이 없어요."
    assert T.status_message("search_reviews", "unsupported") == "여행 후기 검색은 아직 지원하지 않아요."


# --- 안내·출처 문구 ---------------------------------------------------------------------

def test_fixed_notices():
    assert T.FALLBACK == "요청을 잘 이해하지 못했어요. 조금 다르게 말씀해 주시겠어요?"  # design.md 5.5의 4번
    assert T.fallback_text("model_error") != T.FALLBACK and T.fallback_text("whatever") == T.FALLBACK
    assert T.plan_saved_text("제주 2박 3일") == "'제주 2박 3일' 일정을 저장했어요."
    assert T.PLAN_CANCELLED == "저장하지 않았어요."
    assert T.PLAN_LOGIN_REQUIRED == "일정을 저장하려면 로그인이 필요해요."
    assert T.plan_notice_text("AUTH_EXPIRED").startswith("로그인이 만료됐어요.")
    assert T.plan_notice_text(None) is None


@pytest.mark.parametrize(("args", "expected"), [
    (("TOUR_API", "DESCRIPTION", "Type3"), "출처: 한국관광공사 TourAPI · 공공누리 제3유형(출처표시·변경금지)"),
    (("TOUR_API", "DESCRIPTION", " type 3 "), "출처: 한국관광공사 TourAPI · 공공누리 제3유형(출처표시·변경금지)"),
    (("TOUR_API", "DESCRIPTION", "Type1"), "출처: 한국관광공사 TourAPI · 공공누리 제1유형(출처표시)"),
    (("TOUR_API", "DESCRIPTION", None), "출처: 한국관광공사 TourAPI"),
    (("TOUR_API", "SUMMARY", "Type3"),
     "출처: 한국관광공사 TourAPI · 공공누리 제3유형(출처표시·변경금지) · "
     "이 소개는 TourAPI 분류 정보(지역·관광 타입·분류)로 만든 글입니다."),
    (("WIKIDATA", "SUMMARY", None), "출처: 위키데이터 (CC0) 항목 정보로 만든 소개"),
    ((None, "DESCRIPTION", None), None),
])
def test_attribution_text(args, expected):
    assert T.attribution_text(*args) == expected


def test_attribution_wikipedia_title():
    assert T.attribution_text("WIKIDATA", "DESCRIPTION", None, title="에펠탑") == \
        "출처: 한국어 위키백과 「에펠탑」 문서 도입부 발췌 (CC BY-SA 4.0) · 위키데이터 (CC0)"
