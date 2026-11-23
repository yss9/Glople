"""M1 해석기: 키워드 94개 + 동의어(tools/keywords.py), 지역 해석기(tools/regions.py), Type3 판정·압축(tools/text_policy.py)."""
import csv
import json

import pytest

from glople_agent import contract
from glople_agent.tools import keywords as K
from glople_agent.tools import regions as R
from glople_agent.tools import text_policy as TP

# --- 키워드 -----------------------------------------------------------------------


def test_keywords_are_the_94_contract_keywords_and_synonyms_point_into_them():
    assert K.KEYWORDS == contract.KEYWORDS and len(K.KEYWORDS) == 94
    assert set(K.SYNONYMS.values()) <= K.KEYWORD_SET
    assert not set(K.SYNONYMS) & K.KEYWORD_SET            # 동의어 쪽 말은 목록 밖이어야 의미가 있음
    assert "맛집" not in K.SYNONYMS                         # 슬랭 '야경 맛집'(labeling_rules 4절)
    assert "바닷가" not in K.SYNONYMS                       # 후보가 둘 이상(바다/해변/해안)


@pytest.mark.parametrize(("given", "keyword", "how"), [
    ("바다", "바다", "exact"),
    (" 야경 ", "야경", "exact"),
    ("하이킹코스", "하이킹 코스", "spacing"),
    ("보트투어", "보트 투어", "spacing"),
    ("스노쿨링", "스노클링", "synonym"),            # 오타(labeling_rules 4절)
    ("와이너리", "와인", "synonym"),                # 동의어(뜻이 하나)
    ("와이너리 투어", "와인", "inner"),
    ("유네스코 세계유산", "유네스코", "inner"),
    ("고대 유적지", "고대 유적", "inner"),          # 가장 길게 맞는 것 하나
    ("캠핑장", "캠핑", "inner"),
    ("등산하고", "등산", "inner"),
    ("섬 여행", "섬", "inner"),                     # 한 글자 키워드는 홀로 쓴 단어만
    ("맛집", None, None),
    ("조용한", None, None),
    ("산책로", None, None),                         # '산'이 붙은 다른 말(Spring fromKoreanDescription과 같은 규칙)
    ("강변", None, None),
    ("", None, None),
])
def test_match_keyword_table(given, keyword, how):
    m = K.match_keyword(given)
    assert (m.keyword, m.how) == (keyword, how)


def test_resolve_keywords_dedupes_and_reports_unknown():
    res = K.resolve_keywords(["야경", "맛집", "야경", "스노쿨링", "조용한"])
    assert res.applied == ["야경", "스노클링"] and res.unknown == ["맛집", "조용한"]
    assert res.mapped == [("스노쿨링", "스노클링")]
    assert K.unknown_note(res.unknown) == "목록에 없어 반영하지 못한 키워드: 맛집, 조용한"
    assert K.unknown_note([]) is None


def test_keyword_in_text_uses_synonyms_and_spacing():
    assert K.keyword_in_text("와인", "와이너리 투어 할 수 있는 나라")
    assert K.keyword_in_text("하이킹 코스", "하이킹코스 좋은 산")
    assert not K.keyword_in_text("와인", "맥주 축제")


@pytest.mark.needs_training_data("tool_calling_v2/train.jsonl")
def test_every_gold_keyword_is_exact():
    for r in contract.read_jsonl(contract.V2_TRAIN_PATH):
        for tc in r["messages"][-1].get("tool_calls") or []:
            if tc["function"]["name"] == "recommend_by_keywords":
                for k in json.loads(tc["function"]["arguments"])["keywords"]:
                    assert K.match_keyword(k).how == "exact", k


# --- 지역 해석기 --------------------------------------------------------------------

@pytest.mark.parametrize(("name", "status", "kind", "label"), [
    ("강릉", "ok", "DOMESTIC", "강원특별자치도 강릉시"),
    ("강릉시", "ok", "DOMESTIC", "강원특별자치도 강릉시"),         # 접미사 떼기
    ("서귀포", "ok", "DOMESTIC", "제주특별자치도 서귀포시"),
    ("울릉도", "ok", "DOMESTIC", "경상북도 울릉군"),               # 섬
    ("강화도", "ok", "DOMESTIC", "인천광역시 강화군"),
    ("독도", "ok", "DOMESTIC", "경상북도 울릉군"),
    ("경남", "ok", "DOMESTIC", "경상남도"),                       # 줄임말
    ("제주", "ok", "DOMESTIC", "제주특별자치도"),                 # 명시 별칭이 자동 별칭(제주시)을 이김
    ("제주도", "ok", "DOMESTIC", "제주특별자치도"),
    ("제주시", "ok", "DOMESTIC", "제주특별자치도 제주시"),
    ("부산광역시", "ok", "DOMESTIC", "부산광역시"),
    ("경기 광주", "ok", "DOMESTIC", "경기도 광주시"),             # 두 낱말은 시도로 거름
    ("강원 고성", "ok", "DOMESTIC", "강원특별자치도 고성군"),
    ("대구 군위군", "ok", "DOMESTIC", "대구광역시 군위군"),
    ("국내", "ok", "DOMESTIC", "국내"),
    ("한국", "ok", "DOMESTIC", "국내"),
    ("도쿄", "ok", "OVERSEAS", "도쿄"),
    ("도쿄도", "ok", "OVERSEAS", "도쿄"),
    ("#오사카", "ok", "OVERSEAS", "오사카"),
    ("베니스", "ok", "OVERSEAS", "베네치아"),
    ("LA", "ok", "OVERSEAS", "로스앤젤레스"),
    ("la", "ok", "OVERSEAS", "로스앤젤레스"),
    ("싱가포르", "ok", "OVERSEAS", "싱가포르"),                   # 도시 국가는 도시 하나로
    ("홍콩", "ok", "OVERSEAS", "홍콩"),
    ("중화인민공화국", "ok", "OVERSEAS", "중국"),                 # DB의 위키데이터 표기
    ("광주", "ambiguous", "DOMESTIC", None),                      # 광주광역시 / 경기도 광주시(8.3)
    ("고성", "ambiguous", "DOMESTIC", None),
    ("거문도", "not_found", None, None),
    ("아틀란티스", "not_found", None, None),
    ("", "not_found", None, None),
])
def test_resolve_table(name, status, kind, label):
    res = R.resolve(name)
    assert (res.status, res.kind, res.label) == (status, kind, label)


def test_ambiguous_candidates_and_agent_countries():
    gwangju = R.resolve("광주")
    assert gwangju.candidate_labels() == ["광주광역시", "경기 광주시"] and gwangju.region is None
    assert R.resolve("고성").candidate_labels() == ["강원 고성군", "경남 고성군"]
    assert {n: R.resolve(n).region.agent_country for n in ("나고야", "피렌체", "하와이", "파리", "괌")} == {
        "나고야": "일본", "피렌체": "이탈리아", "하와이": "미국", "파리": None, "괌": None}
    tokyo = R.resolve("도쿄").to_dict()
    assert tokyo["kind"] == "OVERSEAS" and tokyo["candidates"][0]["country_code"] == "JP"
    assert R.resolution_summary(gwangju) == {"status": "ambiguous", "kind": "DOMESTIC", "label": None,
                                             "candidates": ["광주광역시", "경기 광주시"]}


def test_region_tables_are_flat_and_consistent():
    domestic = json.loads((R.RESOURCES_DIR / "domestic.json").read_text(encoding="utf-8"))["entries"]
    labels = [e["label"] for e in domestic]
    assert len(labels) == len(set(labels))
    sigungu = [e for e in domestic if e["level"] == "SIGUNGU"]
    assert len(sigungu) == 159                                   # 시 75 + 군 82 + 광역시 군 … (자치구 제외)
    assert all(e["regionCode"] is None or (len(e["regionCode"]) == 2 and e["regionCode"].isdigit())
               for e in domestic)
    assert all(e["lat"] is None and e["lng"] is None and e["sigunguCode"] is None for e in domestic)  # 오너 DB 전
    cities = json.loads((R.RESOURCES_DIR / "overseas_cities.json").read_text(encoding="utf-8"))["entries"]
    countries = {c["name"] for c in json.loads((R.RESOURCES_DIR / "countries.json").read_text(encoding="utf-8"))["entries"]}
    assert all(c["country"] in countries for c in cities if c["country"])
    assert all(c["radiusKm"] > 0 for c in cities)
    # M6: 해외 도시는 도심 좌표를 채움(반경 검색·날씨), 국내는 아직 null(지오코딩)
    assert all(-90 <= c["lat"] <= 90 and -180 <= c["lng"] <= 180 for c in cities)
    tokyo = R.resolve("도쿄").region
    assert R.distance_km(tokyo.lat, tokyo.lng, 35.7188, 139.7765) < tokyo.radius_km < R.distance_km(
        tokyo.lat, tokyo.lng, 34.74, 139.40)   # 도쿄 국립박물관은 안, 이즈오섬은 밖


def _gold_region_values(paths):
    keys = {"search_destinations": "region", "get_weather": "region", "create_travel_plan": "region"}
    for path in paths:
        for r in contract.read_jsonl(path):
            for tc in r["messages"][-1].get("tool_calls") or []:
                key = keys.get(tc["function"]["name"])
                if key:
                    yield json.loads(tc["function"]["arguments"])[key]


def _assert_regions_covered(paths):
    statuses = {v: R.resolve(v).status for v in _gold_region_values(paths)}
    assert statuses, "지역 값이 없음"
    bad = {v: s for v, s in statuses.items() if s != "ok" and v != "광주"}
    assert bad == {}                                             # 의도한 모호함은 '광주'뿐
    return statuses


def test_human_style_regions_all_resolve():
    _assert_regions_covered([contract.HUMAN_STYLE_PATH])


@pytest.mark.needs_training_data("tool_calling_v2/train.jsonl", "tool_calling_v2/valid.jsonl",
                                 "tool_calling_v2/test.jsonl")
def test_v2_regions_all_resolve_or_intended_ambiguous():
    statuses = _assert_regions_covered([contract.V2_TRAIN_PATH, contract.V2_DATA_DIR / "valid.jsonl",
                                        contract.V2_TEST_PATH])
    assert statuses.get("광주") == "ambiguous" and len(statuses) > 70


# --- Type3 판정·압축 ------------------------------------------------------------------

def _cases():
    with TP.CASES_CSV.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["why"])
def test_text_policy_shared_csv(case):
    # 같은 CSV를 S0 때 Spring JUnit도 읽게 한다(design.md 11절)
    none = lambda v: v or None  # noqa: E731 - CSV 빈 칸 = null
    got = TP.text_policy(none(case["source"]), none(case["infoKind"]), none(case["copyrightType"]),
                         none(case["serverPolicy"]))
    assert got == case["textPolicy"]
    assert TP.LLM_TEXT[got] == case["llmText"]


def test_csv_covers_every_policy():
    assert {c["textPolicy"] for c in _cases()} == set(TP.TEXT_POLICIES)


def test_place_items_whitelist_does_not_leak_new_server_fields():
    row = {"id": 1, "destName": "가거도", "location": "전라남도 신안군", "country": "대한민국",
           "info": "TYPE3-SENTINEL 원문", "source": "TOUR_API", "copyrightType": "Type3", "infoKind": "DESCRIPTION",
           # 서버가 나중에 늘릴 필드(S0): LLM 쪽에 새면 안 됨
           "imageUrl": "https://example.com/a.jpg", "latitude": 34.1, "longitude": 125.1, "summary": "요약 원문"}
    llm, card = TP.place_items(row)
    assert llm == {"name": "가거도", "where": "전라남도 신안군", "note": "설명은 원문 카드"}
    assert card["text"] == "TYPE3-SENTINEL 원문" and card["textPolicy"] == "VERBATIM_ONLY"
    assert card["attribution"] == "출처: 한국관광공사 TourAPI · 공공누리 제3유형(출처표시·변경금지)"
    assert "imageUrl" not in json.dumps(llm) and "SENTINEL" not in json.dumps(llm, ensure_ascii=False)


def test_place_items_free_facts_note_is_capped_and_unknown_has_no_text():
    summary = "강원특별자치도 강릉시의 관광지입니다. " * 5
    llm, card = TP.place_items({"destName": "경포해변", "location": "강원특별자치도 강릉시", "info": summary,
                                "source": "TOUR_API", "infoKind": "SUMMARY", "copyrightType": "Type3"})
    assert len(llm["note"]) <= TP.SUMMARY_CHARS and card["text"] == summary
    llm, card = TP.place_items({"destName": "산토리니", "country": "그리스", "info": "LEGACY", "infoKind": "DESCRIPTION"})
    assert llm == {"name": "산토리니", "where": "그리스"} and card["text"] is None and card["attribution"] is None
    # 서버 textPolicy(S0)가 아는 값이면 그것을 따른다
    llm, _ = TP.place_items({"destName": "x", "info": "틀 문장", "source": "TOUR_API", "infoKind": "DESCRIPTION",
                             "copyrightType": "Type3", "textPolicy": "FREE_FACTS"})
    assert llm["note"] == "틀 문장"
