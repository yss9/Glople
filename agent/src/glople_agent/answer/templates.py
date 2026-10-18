"""코드가 만드는 문장: 도구별 템플릿 답, 되묻기, 안내, 출처 문구(design.md 5.3, 8.2, 9, 11).

- 템플릿 답은 M0~M1의 기본 답이고, M2부터는 LLM 답이 가드에 걸렸을 때의 대체 경로다.
- 되묻기 문장(CLARIFY)은 training/eval_sets/labeling_rules.md 6절 표를 글자 그대로 쓴다(학습 정답과 같은 말).
- 조사는 build_tool_dataset.py와 같은 규칙(마지막 글자 받침, 영문·숫자는 읽는 소리)이다. 학습 스크립트는 import하지 않는다.
- 이 모듈은 tools·graph를 import하지 않는다(도구 쪽이 여기 문장을 가져다 쓴다).
"""
import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

# --- 조사 ---------------------------------------------------------------------

_LATIN_FINAL = {"L": "ㄹ", "M": "ㅁ", "N": "ㄴ", "R": "ㄹ"}  # 엘, 엠, 엔, 알. 나머지 글자는 받침 없음
_DIGIT_FINAL = {"0": "ㅇ", "1": "ㄹ", "3": "ㅁ", "6": "ㄱ", "7": "ㄹ", "8": "ㄹ"}  # 영, 일, 삼, 육, 칠, 팔

# 받침 있을 때 → 없을 때
_JOSA_PAIRS = {"이": "가", "을": "를", "은": "는", "과": "와", "으로": "로", "이에요": "예요", "이랑": "랑",
               "아": "야", "이야": "야", "이고": "고", "이라는": "라는", "이나": "나"}


def final_consonant(word: str) -> str:
    """마지막 글자의 받침: '' (없음), 'ㄹ', 그 밖은 '받침'. 따옴표·괄호 같은 기호는 건너뛴다."""
    for ch in reversed(word):
        if "가" <= ch <= "힣":
            jong = (ord(ch) - ord("가")) % 28
            return "" if jong == 0 else ("ㄹ" if jong == 8 else "받침")
        if ch.isascii() and ch.isalpha():
            return _LATIN_FINAL.get(ch.upper(), "")
        if ch.isdigit():
            return _DIGIT_FINAL.get(ch, "")
    return ""


def has_batchim(word: str) -> bool:
    return final_consonant(word) != ""


def _split_pair(particle: str) -> tuple[str, str]:
    if "/" not in particle:
        if particle in _JOSA_PAIRS:
            return particle, _JOSA_PAIRS[particle]
        raise ValueError(f"조사는 '이/가'처럼 두 꼴을 적어 주세요: {particle!r}")
    a, b = particle.split("/", 1)
    if a in _JOSA_PAIRS and _JOSA_PAIRS[a] == b:
        return a, b
    if b in _JOSA_PAIRS and _JOSA_PAIRS[b] == a:
        return b, a          # '와/과'처럼 받침 없는 꼴을 앞에 적어도 됨
    raise ValueError(f"모르는 조사 짝입니다: {particle!r}")


def pick_josa(word: str, particle: str) -> str:
    """조사만 고른다. pick_josa('나이', '이/가') == '가', pick_josa('1박 2일', '으로/로') == '로'."""
    with_b, without_b = _split_pair(particle)
    final = final_consonant(word)
    if with_b == "으로":  # 받침 없거나 ㄹ 받침 뒤에는 '로'
        return with_b if final not in ("", "ㄹ") else without_b
    return with_b if final else without_b


def josa(word: str, particle: str) -> str:
    """단어에 맞는 조사를 붙인다. josa('성별', '이/가') == '성별이', josa('MBTI', '이/가') == 'MBTI가'."""
    return word + pick_josa(word, particle)


def join_words(words: Sequence[str]) -> str:
    """나열: 나이 / 나이와 성별 / 나이, 성별, MBTI. 뒤에 붙는 조사는 부르는 쪽이 마지막 단어에 맞춘다."""
    words = list(words)
    if not words:
        return ""
    if len(words) == 1:
        return words[0]
    if len(words) == 2:
        return josa(words[0], "과/와") + " " + words[1]
    return ", ".join(words)


# --- 되묻기 (labeling_rules.md 6절 표 그대로) ----------------------------------

CLARIFY: dict[str, str] = {
    "weather_region": "어느 지역 날씨를 알려드릴까요?",
    "plan_all": "어느 지역으로 며칠 동안, 어떤 장소를 넣어서 저장할까요?",
    "plan_days_places": "며칠 일정으로, 어떤 장소를 넣어서 저장할까요?",
    "plan_places": "어떤 장소를 넣어서 저장할까요?",
    "reviews_target": "어느 장소나 지역의 후기를 찾아드릴까요?",
    "keywords_any": ("어떤 여행을 좋아하시는지 알려주시겠어요? "
                     "바다, 야경, 사찰처럼 취향이나 가고 싶은 지역을 말씀해 주시면 찾아드릴게요."),
}
# 6절 표의 틀 문장. {…}에는 조사가 붙은 말이 들어간다(clarify_search_region, clarify_routes)
CLARIFY_SEARCH_REGION = "어느 지역의 {category_eul} 찾아드릴까요?"
CLARIFY_ROUTES = "비슷한 여행자를 찾으려면 {missing_i} 필요해요. 알려주시겠어요?"

# labeling_rules.md에 없는 문장(학습 정답에 없음). 필수 인자가 빠진 상세·글로플러 호출이 왔을 때만 쓴다
CLARIFY_EXTRA: dict[str, str] = {
    "detail_name": "어느 여행지의 정보를 찾아드릴까요?",
    "gloplers_target": "어느 지역이나 관심사의 글로플러를 찾아드릴까요?",
}

ROUTE_FIELD_LABELS = {"age": "나이", "gender": "성별", "mbti": "MBTI"}  # 순서도 이대로(나이, 성별, MBTI)
_PLAN_CLARIFY_COVERS = (  # 빠진 인자를 덮는 가장 작은 문장
    ("plan_places", frozenset({"places"})),
    ("plan_days_places", frozenset({"days", "places"})),
    ("plan_all", frozenset({"region", "days", "places"})),
)


def clarify_search_region(category_word: str | None = None) -> str:
    """검색의 지역이 빠졌을 때. category_word는 사용자가 쓴 카테고리 말(맛집·숙소…), 없으면 '여행지'."""
    word = (category_word or "").strip() or "여행지"
    return CLARIFY_SEARCH_REGION.format(category_eul=josa(word, "을/를"))


def clarify_routes(missing: Iterable[str]) -> str:
    """루트의 나이·성별·MBTI 중 빠진 것. missing은 인자 이름(age·gender·mbti)이나 한국어 이름."""
    given = set()
    for m in missing:
        given.add(ROUTE_FIELD_LABELS.get(m, m))
    words = [label for label in ROUTE_FIELD_LABELS.values() if label in given]
    if not words:
        raise ValueError("빠진 인자가 없습니다")
    return CLARIFY_ROUTES.format(missing_i=josa(join_words(words), "이/가"))


def clarify_plan(missing: Iterable[str]) -> str:
    """일정의 region·days·places 중 빠진 것. 표에 없는 조합(지역만 빠짐 등)은 그것을 덮는 문장을 쓴다."""
    need = frozenset(missing) & {"region", "days", "places"}
    if not need:
        raise ValueError("빠진 인자가 없습니다")
    for key, covers in _PLAN_CLARIFY_COVERS:
        if need <= covers:
            return CLARIFY[key]
    raise AssertionError("도달하지 않음")


# labeling_rules.md에 없는 문장(M1 근거 검사): 지역 해석이 모호할 때 후보를 보여 주며 묻는다(design.md 7절)
CLARIFY_REGION_CHOICE = "{candidates} 중 어느 곳을 말씀하시는 건가요?"


def clarify_region_choice(candidates: Sequence[str]) -> str:
    """모호한 지역(광주광역시 / 경기 광주시)의 후보 되묻기."""
    if len(candidates) < 2:
        raise ValueError("후보가 둘 이상이어야 합니다")
    names = list(candidates)
    joined = josa(names[0], "과/와") + " " + names[1] if len(names) == 2 else ", ".join(names)
    return CLARIFY_REGION_CHOICE.format(candidates=joined)


# M6 동명 장소(design.md 8.2 get_destination_detail: 여러 개면 후보 지역 → "어느 지역의 ○○인가요?")
CLARIFY_PLACE_CHOICE = "{name_eun} {places}에 있어요. 어느 지역의 {name}인가요?"


def clarify_place_choice(name: str, places: Sequence[str]) -> str:
    """같은 이름의 여행지가 여러 지역에 있을 때: '보문사는 인천 강화군과 서울특별시 성북구에 있어요. 어느 지역의 보문사인가요?'"""
    names = [p for p in dict.fromkeys(places) if p]
    if not names:
        raise ValueError("후보 지역이 없습니다")
    joined = names[0] if len(names) == 1 else (
        josa(names[0], "과/와") + " " + names[1] if len(names) == 2 else ", ".join(names))
    return CLARIFY_PLACE_CHOICE.format(name_eun=josa(name, "은/는"), places=joined, name=name)


_CLARIFY_FRAMES = (
    ("search_region", re.compile(r"^어느 지역의 .+[을를] 찾아드릴까요\?$")),
    ("routes", re.compile(r"^비슷한 여행자를 찾으려면 .+[이가] 필요해요\. 알려주시겠어요\?$")),
    ("region_choice", re.compile(r"^.+ 중 어느 곳을 말씀하시는 건가요\?$")),
    ("place_choice", re.compile(r"^.+에 있어요\. 어느 지역의 .+인가요\?$")),
)


def clarify_kind(text: str | None) -> str | None:
    """되묻기 문장이면 그 종류(CLARIFY·CLARIFY_EXTRA 키나 틀 이름), 아니면 None.
    라우터가 직접 낸 되묻기(direct_reply)를 알아보고 다음 턴에 발화를 이어 붙일지 정할 때 쓴다(M1)."""
    t = " ".join((text or "").split())
    for key, value in (*CLARIFY.items(), *CLARIFY_EXTRA.items()):
        if t == value:
            return key
    for key, pattern in _CLARIFY_FRAMES:
        if pattern.match(t):
            return key
    return None


def clarify_for(tool: str, missing: Iterable[str] = (), *, category_word: str | None = None) -> str | None:
    """도구와 빠진(또는 근거 없는) 필수 인자로 되묻기 문장. 이 도구에 맞는 문장이 없으면 None."""
    missing = list(missing)
    if tool == "get_weather":
        return CLARIFY["weather_region"]
    if tool == "search_destinations":
        return clarify_search_region(category_word)
    if tool == "recommend_similar_traveler_routes":
        return clarify_routes(missing or ROUTE_FIELD_LABELS)
    if tool == "create_travel_plan":
        return clarify_plan(missing or ("region", "days", "places"))
    if tool == "search_reviews":
        return CLARIFY["reviews_target"]
    if tool == "recommend_by_keywords":
        return CLARIFY["keywords_any"]
    if tool == "get_destination_detail":
        return CLARIFY_EXTRA["detail_name"]
    if tool == "search_gloplers":
        return CLARIFY_EXTRA["gloplers_target"]
    return None


# --- 대체 안내 -------------------------------------------------------------------

FALLBACK = "요청을 잘 이해하지 못했어요. 조금 다르게 말씀해 주시겠어요?"
FALLBACK_MESSAGES: dict[str, str] = {
    "unparsed": FALLBACK,                 # 재시도해도 호출·문장을 못 얻음
    "no_valid_call": FALLBACK,            # 통과한 호출 0개(되묻기 사유 없음)
    "model_error": "지금은 도우미 모델에 연결하지 못했어요. 잠시 뒤에 다시 시도해 주세요.",
    "timeout": "답을 만드는 데 시간이 너무 오래 걸렸어요. 잠시 뒤에 다시 시도해 주세요.",
    "steps": "요청을 처리하는 단계가 너무 길어졌어요. 조금 더 간단히 말씀해 주시겠어요?",  # remaining_steps·recursion_limit
}


def fallback_text(reason: str | None = None) -> str:
    return FALLBACK_MESSAGES.get(reason or "", FALLBACK)


# --- 일정 저장(확인 카드) 안내 --------------------------------------------------

PLAN_SAVED = "'{title}' 일정을 저장했어요."
PLAN_CANCELLED = "저장하지 않았어요."
PLAN_PREVIOUS_CANCELLED = "이전 일정 저장은 취소했어요."
PLAN_LOGIN_REQUIRED = "일정을 저장하려면 로그인이 필요해요."
PLAN_CONFIRM = "'{title}' 일정을 이대로 저장할까요?"
PLAN_SAVE_FAILED = "일정을 저장하지 못했어요. 잠시 뒤에 다시 시도해 주세요."
PLAN_EDIT_LIMIT = "일정은 세 번까지 고칠 수 있어요. 저장하지 않았어요."
PLAN_ONE_AT_A_TIME = "한 번에 하나씩 처리할게요."   # 일정과 섞여 온 다른 호출의 ToolMessage(error)
PLAN_NO_PLACES = "일정에 넣을 장소가 없어 저장하지 않았어요."
# M7 loop: 도구 결과 뒤 라우터가 일정 저장을 부르면 받지 않고 이 문장을 템플릿 답 뒤에 붙인다(graph/after_tools.py).
# 다음 턴에 사용자가 직접 부탁하면 찾은 장소(memory.recent_places)가 일정 장소 근거가 된다
PLAN_IN_LOOP = "일정은 따로 확인을 받고 만들어요. 찾은 곳으로 일정을 짜려면 한 번 더 말씀해 주세요."
# M3: 같은 확인 카드(멱등키 = tool_call_id)로 이미 다른 내용이 저장돼 있음(시간 여행 /fork 뒤 고쳐서 승인한 경우)
PLAN_ALREADY_SAVED = "이 요청으로 저장한 일정이 이미 있어서 바꾼 내용은 저장하지 않았어요. 새 일정으로 저장하려면 다시 요청해 주세요."
PLAN_NOTICES: dict[str, str] = {    # state.plan_notice → 확인 카드에 띄우는 문장
    "AUTH_EXPIRED": "로그인이 만료됐어요. 다시 로그인한 뒤 저장을 눌러 주세요.",
}


def plan_saved_text(title: str) -> str:
    return PLAN_SAVED.format(title=title)


def plan_confirm_text(title: str) -> str:
    return PLAN_CONFIRM.format(title=title)


def plan_notice_text(notice: str | None) -> str | None:
    if not notice:
        return None
    return PLAN_NOTICES.get(notice, PLAN_SAVE_FAILED)


# --- 출처 문구 (LLM이 쓰지 않고 코드가 붙임, design.md 11절) -----------------------

TOUR_API_LABEL = "출처: 한국관광공사 TourAPI"
KOGL_LABELS = {  # copyrightType(공백 제거·소문자) → 공공누리 유형
    "type1": "공공누리 제1유형(출처표시)",
    "type2": "공공누리 제2유형(출처표시·상업적 이용금지)",
    "type3": "공공누리 제3유형(출처표시·변경금지)",
    "type4": "공공누리 제4유형(출처표시·상업적 이용금지·변경금지)",
}
TOUR_API_SUMMARY_NOTE = "이 소개는 TourAPI 분류 정보(지역·관광 타입·분류)로 만든 글입니다."
WIKIDATA_SUMMARY_ATTRIBUTION = "출처: 위키데이터 (CC0) 항목 정보로 만든 소개"
WIKIPEDIA_ATTRIBUTION = "출처: 한국어 위키백과 {doc}도입부 발췌 (CC BY-SA 4.0) · 위키데이터 (CC0)"
WEATHER_ATTRIBUTION = "Weather data by Open-Meteo.com (CC BY 4.0)"   # M6: Open-Meteo 출처 조건(카드에 그대로)


def normalize_copyright(copyright_type: str | None) -> str:
    """SourceAttribution.js와 같은 비교: 공백 제거·소문자. 없으면 ''."""
    return "".join((copyright_type or "").split()).lower()


def attribution_text(source: str | None, info_kind: str | None = None, copyright_type: str | None = None,
                     *, title: str | None = None) -> str | None:
    """카드에 붙일 출처 문구. 출처를 모르는 글(source 없음)은 None(이름만 보여 줌)."""
    if source == "TOUR_API":
        kogl = KOGL_LABELS.get(normalize_copyright(copyright_type))
        text = TOUR_API_LABEL + (f" · {kogl}" if kogl else "")
        if info_kind == "SUMMARY":
            text += " · " + TOUR_API_SUMMARY_NOTE
        return text
    if source == "WIKIDATA":
        if info_kind == "SUMMARY":
            return WIKIDATA_SUMMARY_ATTRIBUTION
        return WIKIPEDIA_ATTRIBUTION.format(doc=f"「{title}」 문서 " if title else "")
    return None


# --- 도구별 템플릿 답 ---------------------------------------------------------------

TOOL_LABELS: dict[str, str] = {
    "recommend_by_keywords": "취향 여행지 추천",
    "search_destinations": "지역별 여행지 검색",
    "recommend_similar_traveler_routes": "비슷한 여행자 루트 추천",
    "get_destination_detail": "여행지 상세 정보",
    "search_reviews": "여행 후기 검색",
    "search_gloplers": "글로플러 찾기",
    "get_weather": "날씨 정보",
    "create_travel_plan": "일정 저장",
}

_STATUS_DEFAULTS: dict[tuple[str, str], str] = {
    ("search_destinations", "pending_feature"): "지역별 여행지 검색은 아직 준비 중이에요.",
    # M6: 음식점(국내)·숙박은 수집하지 않아 부르지 않음(design.md 8.2 그대로)
    ("search_destinations", "unsupported"): "음식점·숙소 정보는 아직 모으지 않았어요. 관광지·문화시설·축제·레포츠는 찾아드릴 수 있어요.",
    ("get_destination_detail", "pending_feature"): "여행지 상세 정보는 아직 준비 중이에요.",
    ("get_weather", "unsupported"): "날씨 정보는 아직 연결하지 않았어요.",
    ("recommend_by_keywords", "empty"): "그 취향으로 찾은 곳이 없어요.",
    ("recommend_similar_traveler_routes", "empty"): "비슷한 여행자를 찾지 못했어요.",
    ("get_destination_detail", "empty"): "Glople에 그 장소 정보가 없어요.",
    ("get_weather", "empty"): "날씨 정보를 찾지 못했어요.",
}

ERROR_MESSAGES: dict[str, str] = {
    "timeout": "{label} 응답이 늦어 결과를 가져오지 못했어요. 잠시 뒤에 다시 시도해 주세요.",
    "bad_request": "{label} 요청 값이 올바르지 않아 결과를 가져오지 못했어요.",
    "upstream_unavailable": "{label} 결과를 지금 가져오지 못했어요. 잠시 뒤에 다시 시도해 주세요.",
}
NO_RESULT = "이번 요청으로 보여드릴 결과가 없어요."

LIST_NAMES_MAX = 5  # 템플릿 답에 이름을 늘어놓는 최대 개수(나머지는 카드에서)
WEATHER_DAYS_MAX = 7  # 날씨 답에 말하는 최대 일수(다음 주 = 7일)


def tool_label(tool: str | None) -> str:
    return TOOL_LABELS.get(tool or "", "도구")


def error_message(tool: str | None, code: str | None) -> str:
    """도구 오류 안내(tool_error_to_json의 message_ko). 내부 오류 문자열은 넣지 않는다."""
    template = ERROR_MESSAGES.get(code or "", ERROR_MESSAGES["upstream_unavailable"])
    return template.format(label=tool_label(tool))


def status_message(tool: str, status: str, args: Mapping[str, Any] | None = None) -> str:
    """ok가 아닌 결과(empty·pending_feature·unsupported·error)의 기본 문장."""
    args = args or {}
    if status == "error":
        return error_message(tool, None)
    if (tool, status) in _STATUS_DEFAULTS:
        return _STATUS_DEFAULTS[(tool, status)]
    label = tool_label(tool)
    if status == "pending_feature":
        return f"{josa(label, '은/는')} 아직 준비 중이에요."
    if status == "unsupported":
        return f"{josa(label, '은/는')} 아직 지원하지 않아요."
    # empty
    if tool == "search_reviews":
        query = args.get("query")
        return f"아직 {query} 후기가 없어요." if query else "아직 그 장소 후기가 없어요."
    if tool == "search_gloplers":
        tag = args.get("hashtag")
        return f"{tag} 글로플러를 찾지 못했어요." if tag else "글로플러를 찾지 못했어요."
    if tool == "search_destinations":
        region = args.get("region")
        return f"{region}에서 찾은 곳이 없어요." if region else "찾은 곳이 없어요."
    return f"{label} 결과가 없어요."


def _names(values: Iterable[str]) -> str:
    return ", ".join(v for v in values if v)


def ok_message(tool: str, args: Mapping[str, Any] | None, items: Sequence[Mapping[str, Any]]) -> str:
    """ok 결과의 문장: 도구별 정해진 틀 + 결과 이름·지역·개수(카드에 있는 사실만, 개수 = 카드 항목 수)."""
    args = args or {}
    items = list(items)
    if not items:
        return status_message(tool, "empty", args)
    shown = items[:LIST_NAMES_MAX]
    n = len(items)
    if tool == "recommend_by_keywords":
        kws = "·".join(args.get("keywords") or [])
        head = f"{kws} 취향으로 " if kws else ""
        return f"{head}여행지 {n}곳을 찾았어요: {_names(i.get('name', '') for i in shown)}."
    if tool == "search_destinations":
        region = args.get("region") or ""
        what = args.get("category") or "여행지"
        return f"{region} {what} {n}곳을 찾았어요: {_names(i.get('name', '') for i in shown)}.".strip()
    if tool == "recommend_similar_traveler_routes":
        people = []
        for i in shown:
            who = " ".join(x for x in (i.get("ageBand"), i.get("gender"), i.get("mbti")) if x)
            dests = "·".join((i.get("destinations") or [])[:3])
            people.append(f"{who}({dests})" if dests else who)
        return f"나이·성별·MBTI가 비슷한 여행자 {n}명의 루트를 찾았어요: {_names(people)}."
    if tool == "get_destination_detail":
        name = shown[0].get("name") or args.get("name") or "그 장소"
        where = shown[0].get("location") or shown[0].get("country")
        head = f"{where}에 있는 {name}" if where else name
        return f"{head} 정보를 찾았어요. 자세한 소개는 카드에서 볼 수 있어요."
    if tool == "search_reviews":
        query = args.get("query")
        head = f"{query} 관련 " if query else ""
        titles = _names(f"'{i['title']}'" for i in shown if i.get("title"))
        return f"{head}Glople 후기 {n}개를 찾았어요: {titles}."
    if tool == "search_gloplers":
        tag = args.get("hashtag")
        head = f"{tag} " if tag else ""
        people = []
        for i in shown:
            nick, country = i.get("nickname") or "", i.get("country") or ""
            people.append(f"{nick}({country})" if country else nick)
        return f"{head}글로플러 {n}명을 찾았어요: {_names(people)}."
    if tool == "get_weather":   # 수치는 모두 카드 값 그대로(코드가 만든 답, design.md 6.1). 다음 주는 7일 모두
        region = args.get("region") or ""
        when = args.get("when") or "오늘"
        days = []
        for i in items[:WEATHER_DAYS_MAX]:
            parts = [i.get("summary")]
            if i.get("tmin") is not None and i.get("tmax") is not None:
                parts.append(f"최저 {i['tmin']}도·최고 {i['tmax']}도")
            if i.get("pop") is not None:
                parts.append(f"강수 확률 {i['pop']}%")
            days.append(f"{i.get('label') or i.get('date', '')} " + ", ".join(p for p in parts if p))
        return f"{region} {when} 날씨예요: {' / '.join(d.strip() for d in days)}.".strip()
    return f"{tool_label(tool)} 결과 {n}개를 찾았어요."


def _sentence(text: str) -> str:
    text = " ".join(text.split())
    if text and text[-1] not in ".?!…":
        text += "."
    return text


def join_answers(parts: Iterable[str | None]) -> str:
    """병렬 결과 답을 호출 순서대로 잇는다. 빈 조각은 빼고 같은 문장은 한 번만."""
    seen: dict[str, None] = {}
    for part in parts:
        if part and part.strip():
            seen.setdefault(_sentence(part), None)
    return " ".join(seen)


def card_sentences(card: Mapping[str, Any]) -> list[str]:
    """도구 결과 카드 하나의 템플릿 문장들. ok면 틀 문장 + notes, 그 밖은 notes(이유) 또는 기본 문장."""
    tool, status = card.get("tool") or "", card.get("status") or "error"
    data = card.get("data") or {}
    notes = [n for n in data.get("notes") or [] if isinstance(n, str)]
    if status == "ok" and data.get("needsClarification") and data.get("clarify"):
        return [data["clarify"]]   # M6 동명 장소: 후보 카드 + 되묻기 한 문장
    if status == "ok":
        args = dict(data.get("args") or {})
        if data.get("appliedKeywords"):   # 목록 밖 키워드는 빼고 말한다(그 키워드는 notes가 따로 알림)
            args["keywords"] = data["appliedKeywords"]
        return [ok_message(tool, args, data.get("items") or []), *notes]
    return notes or [data.get("message") or status_message(tool, status, data.get("args"))]


def answer_for_card(card: Mapping[str, Any]) -> str:
    """도구 결과 카드 하나의 템플릿 답."""
    return join_answers(card_sentences(card))


def answer_for_cards(cards: Iterable[Mapping[str, Any]]) -> str:
    """이번 턴 도구 카드들(호출 순서)의 답을 이어 붙인다. 같은 문장(병렬 날씨 둘의 같은 note 등)은 한 번만.
    카드가 없으면 NO_RESULT."""
    return join_answers(s for c in cards for s in card_sentences(c)) or NO_RESULT
