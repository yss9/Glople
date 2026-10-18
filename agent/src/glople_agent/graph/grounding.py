"""인자 근거 검사(design.md 7절): MVP 설계의 시범판 proto_validate.py를 이식한 것.

기준은 labeling_rules.md 9절의 "발화(router_input)에 글자 그대로, 조사·`쪽`·`#`만 뗀 상태"다. 모델이 지어낸
나이·성별·지역·장소를 그대로 도구에 보내지 않고 되묻거나(clarify), 근거 없는 선택 인자는 빼고(drop),
발화와 어긋난 값은 발화 기준으로 고친다(fix).

check_call()이 돌려주는 issues는 시범판과 같은 코드다(측정 표를 그대로 비교하려고).

| 코드 | 뜻 | 그래프에서 |
|---|---|---|
| clarify:<칸> | 필수 인자에 근거가 없음(region·name·query·hashtag·age·gender·mbti·days·places, no_keyword·keyword_vague) | 되묻기 |
| clarify:region_ambiguous | 지역 해석이 모호(광주) — region_clarify_tools에 든 도구만(M6부터 그래프 기본은 검색·날씨) | 후보 되묻기 |
| fix:region_choice | (M6) 모호한 지역인데 발화의 다른 낱말이 후보 하나를 가리킴('… 경기도요') | 그 후보 이름으로 고쳐 실행 |
| drop:query·limit·title | 근거 없는 선택 인자 | 빼고 실행 |
| drop:unknown_keyword | 목록 밖 키워드(바꿀 수 없음) | 어댑터가 서버에 안 보내고 notes로 알림 |
| drop:some_places | 일정 장소 일부만 근거 있음 | 근거 없는 장소는 MODEL_SUGGESTED(카드에서 기본 해제) |
| fix:keyword_map | 목록 표기로 바꿈(스노쿨링 → 스노클링, 유네스코 세계유산 → 유네스코) | 바꿔서 실행 |
| fix:when | 날씨 시점이 발화와 다르거나 빠짐(담주 → 다음 주, 낼 → 내일) | 발화 기준으로 고침(발화에 시점이 없으면 뺌) |
| note:keyword_not_in_text | 키워드가 발화에 글자로는 없음(뜻은 맞을 수 있음) | 기록만 |

시범판과 다르게 둔 것(api-notes): 키워드 동의어 표·띄어쓰기 무시(tools/keywords.py), 한 글자 키워드는 홀로 쓴 단어만,
나이는 숫자 경계(128살의 28은 아님), 지시어에 그쪽·이쪽·저쪽 추가, 장소는 memory.recent_places도 근거, 영문 대소문자 무시,
날씨 when이 빠졌는데 발화에 시점 말이 있으면 채움. 모르는 키(스키마)는 nodes.judge_call이 빼고 실행한다(drop:extra_key).
한계: 글자는 있지만 뜻이 틀린 경우("부산 사는 사람인데")와 도구를 잘못 고른 경우는 못 잡는다(학습의 몫).
"""
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from glople_agent.tools.keywords import keyword_in_text, resolve_keywords
from glople_agent.tools.regions import RegionResolution, narrow_by_text, region_query_label
from glople_agent.tools.regions import resolve as default_resolve

# 시범판 JOSA 그대로(값 바로 뒤에 붙어도 되는 말). '쪽'은 따로 떼고 본다
JOSA: frozenset[str] = frozenset({
    "", "은", "는", "이", "가", "을", "를", "에", "에서", "의", "도", "로", "으로", "랑", "이랑", "하고", "과", "와", "쪽", "까지",
    "만", "부터", "에도", "에서도", "엔", "이나", "나", "든", "이든", "같은", "처럼", "보다", "한테", "께", "이요", "요", "이에요",
    "예요", "야", "이야", "인데", "임", "ㅋ", "ㅋㅋ", "~",
})
DEMONSTRATIVES: frozenset[str] = frozenset({
    "거기", "여기", "저기", "그곳", "이곳", "저곳", "그쪽", "이쪽", "저쪽", "아무거나", "아무데나", "아무곳",
})
GENDER_WORDS: dict[str, tuple[str, ...]] = {"남성": ("남자", "남성", "남"), "여성": ("여자", "여성", "여")}
WHEN_WORDS: dict[str, tuple[str, ...]] = {
    "오늘": ("오늘",), "내일": ("내일", "낼"), "이번 주말": ("주말",), "다음 주": ("다음 주", "다음주", "담주", "담 주"),
}
# 발화에서 시점을 찾을 때의 순서와 패턴(fix:when). '낼'은 홀로 쓴 말만(보낼·낼까의 '낼'은 아님)
_WHEN_DETECT: tuple[tuple[re.Pattern, str], ...] = (
    (re.compile(r"이번\s*주말|주말"), "이번 주말"),
    (re.compile(r"다음\s*주|담\s*주"), "다음 주"),
    (re.compile(r"내일|(?<![가-힣])낼(?![가-힣])"), "내일"),
    (re.compile(r"오늘"), "오늘"),
)
NATIVE_TENS = {"열": 10, "스물": 20, "서른": 30, "마흔": 40, "쉰": 50, "예순": 60}
NATIVE_UNITS = {"하나": 1, "한": 1, "둘": 2, "두": 2, "셋": 3, "세": 3, "넷": 4, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7,
                "여덟": 8, "아홉": 9}
MBTI_KO: dict[str, tuple[str, ...]] = {   # labeling_rules.md 4절 + 시범판
    "ENFP": ("엔프피",), "INFP": ("인프피",), "ENTP": ("엔팁", "엔티피"), "INTP": ("인팁", "인티피"), "ENFJ": ("엔프제",),
    "INFJ": ("인프제",), "ENTJ": ("엔티제",), "INTJ": ("인티제",), "ESFP": ("엣프피", "에스에프피"),
    "ISFP": ("잇프피", "이스프피"), "ESTP": ("엣팁", "엣티피"), "ISTP": ("잇팁", "잇티피"), "ESFJ": ("엣프제",),
    "ISFJ": ("잇프제",), "ESTJ": ("엣티제",), "ISTJ": ("잇티제",),
}
MBTI_RE = re.compile(r"[EI][SN][TF][JP]")
_HANGUL = re.compile(r"[가-힣ㄱ-ㅎ]")
_VAGUE_RE = re.compile(r"아무(거나|데나|곳)")
_LIMIT_WORD_RE = re.compile(r"(한|두|세|네|다섯|열)\s*(군데|곳|개)")
_NATIVE_RE = re.compile("(" + "|".join(NATIVE_TENS) + ")(" + "|".join(sorted(NATIVE_UNITS, key=len, reverse=True)) + ")?")

RegionResolve = Callable[[str], RegionResolution]


# --- 글자 근거 -------------------------------------------------------------------

def _grounded_exact(v: str, text: str) -> bool:
    start = 0
    while True:
        i = text.find(v, start)
        if i < 0:
            return False
        # 앞 경계: 앞 글자가 한글이면 다른 단어의 일부('나가는데'의 앞, '부산'의 '산')
        if i > 0 and _HANGUL.match(text[i - 1]):
            start = i + 1
            continue
        j = k = i + len(v)
        while k < len(text) and _HANGUL.match(text[k]):
            k += 1
        rest = text[j:k]
        if rest.startswith("쪽"):
            rest = rest[1:]
        if rest in JOSA:
            return True
        start = i + 1


def grounded(value: Any, text: str) -> bool:
    """value가 발화에 글자 그대로(앞은 단어 경계, 뒤는 조사·'쪽'+조사만) 있는지. 지시어·빈 값은 False.
    영문은 대소문자를 무시한다(시범판은 그대로 비교)."""
    if not isinstance(value, str):
        return False
    v = value.strip().lstrip("#").strip()
    if not v or v in DEMONSTRATIVES:
        return False
    if _grounded_exact(v, text):
        return True
    return any("a" <= ch.lower() <= "z" for ch in v) and _grounded_exact(v.casefold(), text.casefold())


def native_numbers(text: str) -> set[int]:
    """고유어 수(스물여덟 → 28, 서른 → 30)."""
    return {NATIVE_TENS[m.group(1)] + (NATIVE_UNITS.get(m.group(2) or "") or 0) for m in _NATIVE_RE.finditer(text)}


def age_grounded(age: Any, text: str) -> bool:
    if isinstance(age, bool) or not isinstance(age, int):
        return False
    return bool(re.search(rf"(?<!\d){age}(?!\d)", text)) or age in native_numbers(text)


def gender_grounded(gender: Any, text: str) -> bool:
    words = GENDER_WORDS.get(gender, ())
    return any((w in text) if len(w) > 1 else grounded(w, text) for w in words)


def mbti_grounded(mbti: Any, text: str) -> bool:
    m = str(mbti or "").upper()
    if not MBTI_RE.fullmatch(m):
        return False
    return m in text.upper() or any(alias in text for alias in MBTI_KO.get(m, ()))


def days_grounded(days: Any, text: str) -> bool:
    """N박 M일 → M, 당일·하루 → 1, 이틀·사흘·나흘, 일주일 → 7, 열흘 → 10, 'N일'(시범판 그대로)."""
    if "일주일" in text or "1주일" in text:
        return days == 7
    if "열흘" in text:
        return days == 10
    pairs = re.findall(r"(\d+)\s*박\s*(\d+)\s*일", text)
    if pairs:
        return any(int(m) == days for _, m in pairs)
    if "당일" in text or "하루" in text:
        return days == 1
    if any(int(x) == days for x in re.findall(r"(\d+)\s*일", text)):
        return True
    word = next((w for w in ("이틀", "사흘", "나흘") if w in text), None)
    return word is not None and {"이틀": 2, "사흘": 3, "나흘": 4}[word] == days


def when_in_text(text: str) -> str | None:
    """발화의 시점 말 → get_weather.when 값(없으면 None)."""
    for pattern, value in _WHEN_DETECT:
        if pattern.search(text):
            return value
    return None


def _norm_place(name: str) -> str:
    return " ".join(name.split()).casefold()


# --- 검사 결과 ----------------------------------------------------------------------

@dataclass(frozen=True)
class Grounding:
    name: str
    args: dict                                  # 고친 인자(drop·fix 반영). clarify면 원래 인자
    issues: tuple[str, ...] = ()                # 시범판 코드("clarify:age", "drop:query" …)
    clarify_fields: tuple[str, ...] = ()        # 되물을 인자 이름(되묻기 문장용)
    ungrounded_places: tuple[str, ...] = ()
    region: dict | None = None                  # 지역 해석 요약(trace용)
    candidates: tuple[str, ...] = ()            # 모호한 지역의 후보 이름

    @property
    def ok(self) -> bool:
        return not any(i.startswith("clarify:") for i in self.issues)

    @property
    def caught(self) -> bool:
        """시범판의 '걸림': clarify·drop·fix 중 하나라도(note는 기록만)."""
        return any(i.startswith(("clarify:", "drop:", "fix:")) for i in self.issues)

    def as_trace(self) -> dict[str, Any]:
        out: dict[str, Any] = {"issues": list(self.issues)}
        if self.region:
            out["region"] = self.region
        if self.ungrounded_places:
            out["ungrounded_places"] = len(self.ungrounded_places)   # 장소 이름은 남기지 않음(궤적용)
        return out


@dataclass
class _Check:
    args: dict
    issues: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    ungrounded_places: list[str] = field(default_factory=list)
    region: dict | None = None
    candidates: list[str] = field(default_factory=list)

    def clarify(self, code: str, arg: str) -> None:
        self.issues.append(f"clarify:{code}")
        if arg not in self.fields:
            self.fields.append(arg)


REGION_TOOLS = frozenset({"search_destinations", "get_weather"})   # region을 해석해 쓰는 도구(S1·M6)
NOT_COLLECTED_CATEGORIES = ("음식점", "숙박")   # 국내는 수집 안 함(tools/adapters/destinations.NOT_COLLECTED와 같음)


def check_call(name: str, args: Mapping[str, Any], text: str, *, recent_places: Iterable[str] = (),
               resolve_region: RegionResolve = default_resolve,
               region_clarify_tools: frozenset[str] = frozenset()) -> Grounding:
    """호출 하나의 근거 검사. text는 라우터에 넣은 한 마디(router_input). 스키마 검사를 통과한 인자를 받는다."""
    c = _Check(args=dict(args))
    a = c.args
    if name == "recommend_by_keywords":
        res = resolve_keywords(a.get("keywords") or [])
        for m in res.matches:
            if m.keyword is None:
                c.issues.append("drop:unknown_keyword")   # 어댑터가 서버에 안 보내고 notes에 알림
            elif m.changed:
                c.issues.append("fix:keyword_map")
        known = res.applied
        if not known:
            c.clarify("no_keyword", "keywords")
        else:
            a["keywords"] = [*known, *res.unknown]   # 목록 표기로 바꾸고, 모르는 말은 뒤에 남겨 어댑터가 알림
            if not any(keyword_in_text(k, text) for k in known):
                if _VAGUE_RE.search(text):
                    c.clarify("keyword_vague", "keywords")
                else:
                    c.issues.append("note:keyword_not_in_text")
    if name in ("search_destinations", "get_weather"):
        region = a.get("region", "")
        if not grounded(region, text):
            c.clarify("region", "region")
        elif name in region_clarify_tools:
            res = resolve_region(region)
            c.region = {"status": res.status, "label": res.label, "candidates": res.candidate_labels()}
            not_collected = (name == "search_destinations" and a.get("category") in NOT_COLLECTED_CATEGORIES
                             and res.kind == "DOMESTIC")   # 어느 광주든 '숙소 정보는 아직' 답이라 묻지 않음
            if res.status == "ambiguous" and not not_collected:
                # M6: 발화의 다른 낱말이 후보 하나를 가리키면 그 후보로('광주 축제 … 경기도요' → 경기도 광주시)
                picked = narrow_by_text(res, text)
                if picked is not None:
                    c.issues.append("fix:region_choice")
                    a["region"] = region_query_label(picked)
                    c.region = {**c.region, "label": picked.label}
                else:
                    c.clarify("region_ambiguous", "region")
                    c.candidates = res.candidate_labels()
    if name == "search_destinations":
        if "query" in a and a["query"] not in text:
            c.issues.append("drop:query")
            a.pop("query")
        if "limit" in a and not re.search(rf"(?<!\d){a['limit']}(?!\d)", text) and not _LIMIT_WORD_RE.search(text):
            c.issues.append("drop:limit")
            a.pop("limit")
    if name == "get_weather":
        found = when_in_text(text)
        if "when" in a and not any(w in text for w in WHEN_WORDS.get(a["when"], ())):
            c.issues.append("fix:when")
            if found:
                a["when"] = found
            else:
                a.pop("when")   # 시점을 말하지 않았으면 생략(도구가 오늘로 처리, labeling_rules 4절)
        elif "when" not in a and found:   # 시범판에 없던 규칙: 발화에 시점 말이 있는데 빠뜨림(낼 영월 날씨)
            c.issues.append("fix:when")
            a["when"] = found
    if name == "get_destination_detail" and not grounded(a.get("name", ""), text):
        c.clarify("name", "name")
    if name == "search_reviews" and not grounded(a.get("query", ""), text):
        c.clarify("query", "query")
    if name == "search_gloplers" and not grounded(a.get("hashtag", ""), text):
        c.clarify("hashtag", "hashtag")
    if name == "recommend_similar_traveler_routes":
        if not age_grounded(a.get("age"), text):
            c.clarify("age", "age")
        if not gender_grounded(a.get("gender"), text):
            c.clarify("gender", "gender")
        if not mbti_grounded(a.get("mbti"), text):
            c.clarify("mbti", "mbti")
    if name == "create_travel_plan":
        if not grounded(a.get("region", ""), text):
            c.clarify("region", "region")
        if not days_grounded(a.get("days", -1), text):
            c.clarify("days", "days")
        recent = {_norm_place(p) for p in recent_places if isinstance(p, str)}
        places = [p for p in a.get("places") or [] if isinstance(p, str)]
        good = [p for p in places if grounded(p, text) or _norm_place(p) in recent]
        if not good:
            c.clarify("places", "places")
        elif len(good) < len(places):
            c.issues.append("drop:some_places")
            c.ungrounded_places = [p for p in places if p not in good]
        if "title" in a and a["title"] not in text:
            c.issues.append("drop:title")
            a.pop("title")
    clarifying = any(i.startswith("clarify:") for i in c.issues)
    return Grounding(name=name, args=dict(args) if clarifying else a, issues=tuple(c.issues),
                     clarify_fields=tuple(c.fields), ungrounded_places=tuple(c.ungrounded_places), region=c.region,
                     candidates=tuple(c.candidates))


__all__ = [
    "DEMONSTRATIVES", "GENDER_WORDS", "Grounding", "JOSA", "MBTI_KO", "REGION_TOOLS", "WHEN_WORDS", "age_grounded",
    "check_call", "days_grounded", "gender_grounded", "grounded", "mbti_grounded", "native_numbers", "when_in_text",
]
