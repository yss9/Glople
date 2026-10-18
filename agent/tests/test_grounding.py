"""근거 검사(graph/grounding.py, design.md 7절): 규칙 표, 정답 막힘 0, 모델 오답 잡음(시범판 숫자 재확인)."""
import pytest

from glople_agent import contract
from glople_agent.answer import templates as tpl
from glople_agent.eval import grounding_eval as GE
from glople_agent.eval import scoring
from glople_agent.eval.replay import replay_predictions
from glople_agent.graph import grounding as G
from glople_agent.graph.nodes import judge_call
from glople_agent.llm.fake import QuirkScript, tool_call_message

KNOWN = frozenset(contract.TOOL_NAMES)


# --- 글자 근거 ------------------------------------------------------------------------

@pytest.mark.parametrize(("value", "text", "ok"), [
    ("부산", "부산 날씨 알려줘", True),
    ("삼척", "삼척쪽에 가볼 만한 곳", True),            # '쪽'+조사
    ("할슈타트", "할슈타트가 어떤 곳이야", True),
    ("소쇄원", "소쇄원이랑 죽녹원", True),
    ("나가", "내일 나가는데 날씨 어떨 것 같아?", False),  # 뒤에 붙은 '는데'는 조사가 아님(v1 실제 오답)
    ("산", "부산 날씨", False),                         # 앞이 한글 → 다른 단어의 일부
    ("거기", "거기 후기 좀 찾아줘", False),              # 지시어 불허
    ("그쪽", "그쪽 날씨", False),
    ("혼행", "#혼행 글로플러", True),                   # '#' 앞은 경계
    ("#혼행", "혼행 좋아하는 글로플러", True),
    ("Bali", "bali 날씨", True),                        # 영문 대소문자 무시
    ("", "아무 말", False),
    (None, "아무 말", False),
])
def test_grounded_table(value, text, ok):
    assert G.grounded(value, text) is ok


@pytest.mark.parametrize(("age", "text", "ok"), [
    (28, "28살 남자 ENTP", True), (25, "스물다섯 살이에요", True), (30, "서른 여자", True),
    (28, "128번 버스 타고", False), (30, "ENTP 남자임", False), (True, "1살", False),
])
def test_age_grounded(age, text, ok):
    assert G.age_grounded(age, text) is ok


@pytest.mark.parametrize(("gender", "text", "ok"), [
    ("남성", "ENTP 남자임", True), ("여성", "27 여 ENFP", True), ("여성", "여행 좋아하는 ENFP", False),
    ("남성", "남해 가는 ENFP", False),
])
def test_gender_grounded(gender, text, ok):
    assert G.gender_grounded(gender, text) is ok


@pytest.mark.parametrize(("mbti", "text", "ok"), [
    ("ENFP", "엔프피 여자 27", True), ("ISTJ", "istj 남자 31", True), ("ENTP", "ENFP예요", False),
    ("XXXX", "XXXX", False),
])
def test_mbti_grounded(mbti, text, ok):
    assert G.mbti_grounded(mbti, text) is ok


@pytest.mark.parametrize(("days", "text", "ok"), [
    (3, "제주 2박 3일", True), (2, "제주 2박 3일", False), (1, "부산 당일치기", True), (1, "하루 코스", True),
    (2, "이틀 일정", True), (3, "사흘", True), (4, "나흘", True), (7, "일주일 동안", True), (3, "3일 코스", True),
    (2, "울진 가는데 일정으로 남겨두고 싶어요", False),
])
def test_days_grounded(days, text, ok):
    assert G.days_grounded(days, text) is ok


@pytest.mark.parametrize(("text", "when"), [
    ("담주 나고야 날씨", "다음 주"), ("이번 주말 부산", "이번 주말"), ("주말에 비 와?", "이번 주말"),
    ("낼 영월 날씨 어떰", "내일"), ("오늘 덥대?", "오늘"), ("보낼 사진 날씨", None), ("다음 달 날씨", None),
])
def test_when_in_text(text, when):
    assert G.when_in_text(text) == when


# --- 도구별 판정 ----------------------------------------------------------------------

@pytest.mark.parametrize(("name", "args", "text", "issues", "fixed"), [
    # v1 사람 말투의 실제 오답들(results/finetuned_human.predictions.jsonl)
    ("get_weather", {"region": "나가"}, "내일 나가는데 날씨 어떨 것 같아?", ["clarify:region", "fix:when"], None),
    ("recommend_similar_traveler_routes", {"age": 30, "gender": "남성", "mbti": "ENTP"},
     "나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임", ["clarify:age"], None),
    ("recommend_similar_traveler_routes", {"age": 28, "gender": "여성", "mbti": "ISFJ"},
     "28살 ISFJ예요 비슷한 사람들 여행 루트", ["clarify:gender"], None),
    ("search_reviews", {"query": "거기"}, "거기 후기 좀 찾아줘", ["clarify:query"], None),
    ("recommend_by_keywords", {"keywords": ["여행"]}, "여행지 아무거나 추천해줘",
     ["drop:unknown_keyword", "clarify:no_keyword"], None),
    ("create_travel_plan", {"region": "울진", "days": 2, "places": ["죽변항", "후포항", "백암온천"]},
     "다음 달에 울진 가는데 일정으로 미리 남겨두고 싶어요", ["clarify:days", "clarify:places"], None),
    # 고치거나 빼고 실행
    ("recommend_by_keywords", {"keywords": ["스노쿨링", "다이빙"]}, "스노쿨링이랑 다이빙 둘다 되는 곳 ㄱㄱ",
     ["fix:keyword_map"], {"keywords": ["스노클링", "다이빙"]}),
    ("recommend_by_keywords", {"keywords": ["야경", "맛집"]}, "야경 맛집인 여행지 알려줘",
     ["drop:unknown_keyword"], {"keywords": ["야경", "맛집"]}),   # 모르는 말은 어댑터가 빼고 notes로 알림
    ("get_weather", {"region": "나고야", "when": "이번 주말"}, "담주 나고야 날씨 봐주세여", ["fix:when"],
     {"region": "나고야", "when": "다음 주"}),
    ("get_weather", {"region": "영월"}, "낼 영월 날씨 어떰", ["fix:when"], {"region": "영월", "when": "내일"}),
    ("get_weather", {"region": "부산", "when": "내일"}, "부산 날씨", ["fix:when"], {"region": "부산"}),
    ("search_destinations", {"region": "오키나와", "category": "숙박", "query": "아이와 가기 좋은"},
     "오키나와 아이랑 가기 좋은 숙소 있음?", ["drop:query"], {"region": "오키나와", "category": "숙박"}),
    ("search_destinations", {"region": "보령", "category": "축제", "limit": 3}, "보령 축제랑 숙소 둘 다",
     ["drop:limit"], {"region": "보령", "category": "축제"}),
    ("search_destinations", {"region": "강릉", "limit": 3}, "강릉 관광지 세 곳", [], {"region": "강릉", "limit": 3}),
    ("create_travel_plan", {"region": "포르투", "days": 4, "places": ["동 루이스 1세 다리", "렐루 서점"]},
     "포르투 3박 4일 일정 저장해줘 동 루이스 1세 다리, 렐루", ["drop:some_places"], None),
    ("create_travel_plan", {"region": "제주", "days": 3, "places": ["우도"], "title": "여름휴가"},
     "제주 2박 3일 우도 넣어서 저장", ["drop:title"], {"region": "제주", "days": 3, "places": ["우도"]}),
    ("get_destination_detail", {"name": "바나힐"}, "바나힐 어떤 곳이야", [], {"name": "바나힐"}),
])
def test_check_call_table(name, args, text, issues, fixed):
    g = G.check_call(name, args, text)
    assert list(g.issues) == issues
    assert g.ok is not any(i.startswith("clarify:") for i in issues)
    if fixed is not None:
        assert g.args == fixed


def test_places_grounded_by_recent_results_and_region_ambiguity_switch():
    g = G.check_call("create_travel_plan", {"region": "통영", "days": 1, "places": ["가거도"]},
                     "통영 당일치기로 아까 그 섬 일정 저장", recent_places=["가거도"])
    assert g.ok and g.issues == ()
    # 모호한 지역은 region_clarify_tools에 든 도구만 되묻는다(M1 기본은 없음: 검색·날씨 백엔드가 아직 없음)
    plain = G.check_call("search_destinations", {"region": "광주", "category": "축제"}, "광주 축제 알려줘")
    assert plain.ok
    asked = G.check_call("search_destinations", {"region": "광주", "category": "축제"}, "광주 축제 알려줘",
                         region_clarify_tools=frozenset({"search_destinations"}))
    assert asked.issues == ("clarify:region_ambiguous",) and asked.candidates == ("광주광역시", "경기 광주시")
    verdict, clean = judge_call("search_destinations", {"region": "광주", "category": "축제"}, "광주 축제 알려줘",
                                known=KNOWN, region_clarify_tools=frozenset({"search_destinations"}))
    assert clean is None and verdict["clarify"] == "광주광역시와 경기 광주시 중 어느 곳을 말씀하시는 건가요?"
    assert tpl.clarify_kind(verdict["clarify"]) == "region_choice"


def test_judge_call_drops_unknown_keys_then_grounds():
    verdict, clean = judge_call("search_reviews", {"query": "거제", "limit": 3}, "거제 후기 몇 개만 보여줄래?",
                                known=KNOWN)
    assert clean == {"query": "거제"} and verdict["grounding"]["issues"] == ["drop:extra_key"]
    verdict, clean = judge_call("search_reviews", {"limit": 3}, "후기", known=KNOWN)
    assert clean is None and verdict["reason"] == "schema" and verdict["clarify"] == tpl.CLARIFY["reviews_target"]
    verdict, clean = judge_call("book_hotel", {}, "호텔", known=KNOWN)
    assert clean is None and verdict["reason"] == "unknown_tool"


def test_grounding_clarify_in_graph_removes_router_message(make_graph, agent_context):
    raw = tool_call_message([("recommend_similar_traveler_routes", {"age": 30, "gender": "남성", "mbti": "ENTP"})])
    kit = make_graph(QuirkScript([raw]))
    r = kit.turn("나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임", agent_context)
    assert r.path == ["start_turn", "route", "validate", "clarify", "finish"]
    assert r.final_text == "비슷한 여행자를 찾으려면 나이가 필요해요. 알려주시겠어요?"
    assert kit.backend.calls == []   # info-recommend에 지어낸 나이를 보내지 않음
    [v] = kit.values()["verdicts"]
    assert v["reason"] == "grounding" and v["grounding"]["issues"] == ["clarify:age"]
    assert [type(m).__name__ for m in kit.values()["messages"]] == ["HumanMessage", "AIMessage"]
    assert kit.sink[-1]["verdicts"] == [{"name": "recommend_similar_traveler_routes", "ok": False,
                                         "reason": "grounding", "issues": ["clarify:age"]}]


def test_fixed_args_reach_the_tool(make_graph, agent_context):
    raw = tool_call_message([("recommend_by_keywords", {"keywords": ["스노쿨링", "맛집"]})])
    kit = make_graph(QuirkScript([raw]))
    r = kit.turn("스노쿨링 맛집 어디", agent_context)
    assert r.reply_kind == "template" and "목록에 없어 반영하지 못한 키워드: 맛집" in r.final_text
    assert kit.backend.calls == [("recommend_by_keywords", {"keywords": ["스노클링", "맛집"]})]
    assert kit.backend.fixture.requests[-1].body == {"keywords": ["스노클링"]}   # 서버에는 목록 표기만


# --- 측정: 정답은 막지 않고, 모델 오답은 잡는다 --------------------------------------------

def _report_gold(name):
    return GE.measure_gold(name, GE.GOLD_SETS[name])


def test_human_style_gold_calls_untouched():
    rep = _report_gold("human")
    assert rep.calls == 86 and (rep.blocked, rep.changed) == (0, 0) and not rep.notes


@pytest.mark.needs_training_data("tool_calling_v2/train.jsonl", "tool_calling_v2/valid.jsonl",
                                 "tool_calling_v2/test.jsonl")
def test_v2_gold_calls_untouched():
    reps = [_report_gold(n) for n in ("v2-train", "v2-valid", "v2-test")]
    assert [r.calls for r in reps] == [2913, 194, 290]   # 설계의 3,289 = train 2,913 + test 290 + human 86
    assert all((r.blocked, r.changed) == (0, 0) for r in reps), [(r.name, r.examples) for r in reps]


def _pred(name):
    path = GE.PREDICTION_SETS[name]
    if not path.is_file():
        pytest.skip(f"{path.name} 없음(training/results)")
    return GE.measure_predictions(name, path)


def test_v1_human_predictions_reproduce_design_claim():
    rep = _pred("v1-human")
    assert (rep.n, len(rep.correct), len(rep.wrong)) == (90, 66, 24)
    assert rep.correct_flagged == [] and rep.broken() == 0        # 정답 66개 막힘 0
    assert len(rep.wrong_caught) >= 16                           # 설계: 24개 중 16개 이상(이식판 17)
    clarified = {c.user for c in rep.wrong_clarify}
    assert len(clarified) == 6 and {"거기 후기 좀 찾아줘", "여행지 아무거나 추천해줘", "내일 나가는데 날씨 어떨 것 같아?"} <= clarified


@pytest.mark.parametrize("name", list(GE.PREDICTION_SETS))
def test_predictions_correct_answers_never_touched(name):
    rep = _pred(name)
    assert rep.correct_flagged == [] and rep.broken() == 0
    assert rep.after_correct() >= len(rep.correct)


@pytest.mark.parametrize("name", list(GE.PREDICTION_SETS))
def test_scoring_matches_training_evaluation(name):
    # evaluate.py에서 ast로 가져온 채점이 학습 평가가 남긴 correct와 같다
    path = GE.PREDICTION_SETS[name]
    if not path.is_file():
        pytest.skip(f"{path.name} 없음")
    for row in contract.read_jsonl(path):
        calls, invalid = scoring.parse_output(row["output"])
        if isinstance(row["expected"], list):
            ok = invalid == 0 and scoring.canonical(calls) == scoring.canonical(row["expected"])
        else:
            ok = not calls and not invalid
        assert ok == (str(row["correct"]) == "True"), row["user"]


def test_graph_replay_of_predictions_matches_function_level():
    # 같은 예측을 그래프(route → validate → tools/plan)에 흘려도 함수 단위 측정과 숫자가 같다
    rep = _pred("v1-human")
    graph = replay_predictions("v1-human", GE.PREDICTION_SETS["v1-human"])
    assert graph.errors == 0 and graph.correct_changed == 0
    assert (graph.before_correct, graph.after_correct) == (len(rep.correct), rep.after_correct())


def test_format_report_has_both_tables():
    golds, preds, missing = GE.run(gold=("human",), predictions=("v1-human",))
    text = GE.format_report(golds, preds, missing, details=True)
    assert "| human | 86 | 0 | 0 | 0 |" in text
    if GE.PREDICTION_SETS["v1-human"].is_file():
        assert "| v1-human | 90 | 66 | 0 | 24 |" in text
    else:
        assert "없는 파일(건너뜀): v1-human" in text
