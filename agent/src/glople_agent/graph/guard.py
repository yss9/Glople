"""답변 가드(design.md 6.5, M2). LLM 답(a3)이 하나라도 걸리면 answer_template으로 바꾼다. 이유와 비율은 A/B 지표(15.2).

| 이유 코드 | 실패 조건 |
|---|---|
| empty | 빈 답(공백만) |
| tool_calls | tool_calls·invalid_tool_calls가 있음, finish_reason이 tool_calls |
| tool_call_text | `<tool_call>`·`<tool_response>`·`<|im_end|>` 같은 특수 토큰 문자열 |
| json_fragment | 중괄호, `"name":`처럼 영문 키에 콜론이 붙은 JSON 조각(한글 따옴표 말은 건드리지 않음) |
| incomplete | finish_reason이 stop이 아님(max_tokens에서 잘림 length, 스트림이 끊겨 finish_reason 없음) — 설계 표에 더함 |
| canned | 학습 정형 문장 30개(인사·거절·되묻기, contract.canned_texts()) 중 하나와 같음(공백 정규화) |
| no_item_name | LLM이 본 결과의 항목 이름(장소·게시글 제목·닉네임·루트 목적지)을 하나도 말하지 않음 |
| unknown_number | 결과·사용자 발화에 없는 숫자(기온·개수·나이대·날짜). 고유어 수 + 단위('세 곳')도 셈 |
| card_overlap | 이번 턴 카드 원문(Type3 설명문·후기 발췌·글로플러 소개)과 공백 정규화 뒤 20자 이상 겹침 |
| too_long | 600자 초과 |

- 숫자: 결과 content(JSON 글자 그대로)와 사용자 발화(router_input)의 숫자, 결과마다의 항목 수와 그 합을 허용한다.
  날짜는 구성 숫자로 비교한다('2024-08-01' → 2024·8·1, 답의 '2024.08.01'·'8월 1일'도 같은 수). 소수는 그대로('4.8').
  고유어 수는 단위가 붙은 것만 센다(두 곳, 세 명, 열두 개). '한 번'·'한 가지' 같은 관용 표현 때문에 '한'은 세지 않는다.
- 이름: 공백을 지우고 대소문자를 무시해 비교한다. 게시글 제목은 길어서 10자 이상 그대로 겹치면 말한 것으로 본다.
- 가드는 답을 고치지 않는다. 판정만 하고, 바꾸는 것은 그래프(answer_llm → answer_template)가 한다.
"""
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cache
from typing import Any

from langchain_core.messages import AIMessage

from glople_agent import contract
from glople_agent.sanitize import SPECIAL_TOKEN_STRINGS
from glople_agent.tools.text_policy import OVERLAP_CHARS, card_raw_texts, overlaps

MAX_ANSWER_CHARS = 600
TITLE_MATCH_CHARS = 10
REASONS = ("empty", "tool_calls", "tool_call_text", "json_fragment", "incomplete", "canned", "no_item_name",
           "unknown_number", "card_overlap", "too_long")
NAME_KEYS = ("name", "nickname")        # 그대로 말해야 하는 이름
TITLE_KEYS = ("title",)                 # 길어서 일부(10자)만 겹쳐도 되는 이름
NAME_LIST_KEYS = ("destinations",)      # 이름 목록(비슷한 여행자 루트의 목적지)

_SPECIAL_RE = re.compile("|".join([re.escape(t) for t in SPECIAL_TOKEN_STRINGS] + [r"<\|[^<>|]*\|>"]))
_JSON_RE = re.compile(r'[{}]|"[A-Za-z_][A-Za-z0-9_]*"\s*:')   # 중괄호, "name": 같은 영문 키(봉투·호출 JSON)
_DIGITS_RE = re.compile(r"\d+(?:[.,]\d+)*")
# 고유어 수 + 단위. 한 자 수(두·세·네·열…)는 띄어 쓴 것만(세분화·네거리 같은 낱말을 피함)
_NATIVE_TENS = {"열": 10, "스무": 20, "스물": 20}
_NATIVE_UNITS = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9}
_COUNTERS = ("군데", "개국", "가지", "사람", "마리", "곳", "개", "명", "편", "건", "분", "권", "장", "팀", "살")
_NATIVE_RE = re.compile(r"(?<![가-힣])(열|스무|스물)?(한|두|세|네|다섯|여섯|일곱|여덟|아홉)?(\s*)("
                        + "|".join(_COUNTERS) + ")")
_UNSET: Any = object()


# --- 숫자 ----------------------------------------------------------------------------

def _canon(token: str) -> str:
    return format(float(token), "g") if "." in token else str(int(token))


def _digit_numbers(text: str) -> set[str]:
    out: set[str] = set()
    for m in _DIGITS_RE.finditer(text):
        token = m.group(0)
        if "," in token:
            head, *rest = token.split(",")
            if rest and all(len(r) == 3 and r.isdigit() for r in rest) and "." not in token:
                out.add(str(int(head + "".join(rest))))     # 1,000 → 1000
                continue
            parts = [p for p in token.split(",") if p]       # 3,4 → 3, 4
        else:
            parts = [token]
        for part in parts:
            pieces = part.split(".")
            if len(pieces) == 2 and pieces[0] and pieces[1] and int(pieces[0]) < 1000:
                out.add(_canon(part))                        # 4.8 (소수)
            else:
                out.update(str(int(p)) for p in pieces if p)  # 2024.08.01 → 2024, 8, 1
    return out


def _native_numbers(text: str) -> set[str]:
    out: set[str] = set()
    for m in _NATIVE_RE.finditer(text):
        tens, unit, space, _counter = m.groups()
        if not tens and not unit:
            continue
        if not tens and unit == "한":
            continue   # '한 번'·'한 가지' 같은 관용 표현
        word = (tens or "") + (unit or "")
        if len(word) == 1 and not space:
            continue   # 세분화·네거리처럼 붙어 있는 한 자는 수가 아닐 수 있음
        out.add(str(_NATIVE_TENS.get(tens or "", 0) + _NATIVE_UNITS.get(unit or "", 0)))
    return out


def numbers_in(text: str) -> set[str]:
    """글 속 숫자(정규형 문자열 집합): 아라비아 숫자(날짜는 구성 숫자로, 소수는 그대로) + 단위가 붙은 고유어 수."""
    return _digit_numbers(text or "") | _native_numbers(text or "")


def _number_parts(text: str) -> set[str]:
    """허용 쪽: 소수도 구성 숫자까지 넣는다(결과 '4.8' → 4.8·4·8)."""
    out = numbers_in(text)
    for m in _DIGITS_RE.finditer(text or ""):
        out.update(str(int(p)) for p in re.split(r"[.,]", m.group(0)) if p)
    return out


# --- 사실 모음 ------------------------------------------------------------------------

@cache
def canned_answers() -> frozenset[str]:
    """학습 텍스트 정답 30개(train.jsonl). 데이터가 없으면 생성기 정형 문장(ast) + 되묻기 표로 대신한다."""
    texts = contract.canned_texts()
    if texts is None:
        from glople_agent.answer.templates import CLARIFY
        from glople_agent.llm.fake import generator_literals

        lit = generator_literals()
        texts = [lit[k] for k in ("GREETING_ANSWER", "THANKS_ANSWER", "ABILITY_ANSWER", "DECLINE_ANSWER",
                                  "WEATHER_CLARIFY_ANSWER", "REVIEWS_CLARIFY_ANSWER", "OPEN_CLARIFY_ANSWER")]
        texts += list(lit["PLAN_CLARIFY_ANSWERS"].values()) + list(CLARIFY.values())
    return frozenset(" ".join(t.split()) for t in texts if t)


@dataclass(frozen=True)
class AnswerFacts:
    """가드가 대조할 사실. 답변 모델이 본 결과(content)·이번 턴 카드·사용자 발화에서 만든다(answer_facts)."""

    names: tuple[str, ...] = ()            # 장소·닉네임·목적지(그대로 말해야 함)
    titles: tuple[str, ...] = ()           # 게시글 제목(10자 겹침도 언급)
    numbers: frozenset[str] = frozenset()  # 써도 되는 숫자(정규형)
    card_texts: tuple[tuple[str, str], ...] = ()   # (위치, 카드 원문)
    canned: frozenset[str] = field(default_factory=frozenset)


def _items(content: str) -> list[dict]:
    try:
        obj = json.loads(content)
    except ValueError:
        return []
    return [i for i in (obj.get("items") or []) if isinstance(i, dict)] if isinstance(obj, dict) else []


def answer_facts(contents: Sequence[str], cards: Iterable[Mapping[str, Any]], user_text: str = "", *,
                 canned: Iterable[str] | None = None) -> AnswerFacts:
    """contents: 답변 모델에 넣은 도구 결과(LLM용 JSON). cards: 이번 턴 카드(원문 겹침 검사용, 모델에는 안 감)."""
    names: dict[str, None] = {}
    titles: dict[str, None] = {}
    numbers = set(_number_parts(user_text))
    total = 0
    for content in contents:
        items = _items(content)
        numbers |= _number_parts(content)
        numbers.add(str(len(items)))
        total += len(items)
        for item in items:
            for key in NAME_KEYS:
                if isinstance(item.get(key), str) and item[key].strip():
                    names.setdefault(item[key].strip(), None)
            for key in TITLE_KEYS:
                if isinstance(item.get(key), str) and item[key].strip():
                    titles.setdefault(item[key].strip(), None)
            for key in NAME_LIST_KEYS:
                for value in item.get(key) or []:
                    if isinstance(value, str) and value.strip():
                        names.setdefault(value.strip(), None)
    numbers.add(str(total))
    numbers.add(str(len(contents)))
    raw = tuple((f"{c.get('tool')}.{where}", text) for c in cards for where, text in card_raw_texts(c))
    canned_set = frozenset(" ".join(t.split()) for t in canned) if canned is not None else canned_answers()
    return AnswerFacts(tuple(names), tuple(titles), frozenset(numbers), raw, canned_set)


# --- 판정 -----------------------------------------------------------------------------

@dataclass(frozen=True)
class GuardVerdict:
    ok: bool
    reasons: tuple[str, ...] = ()
    details: dict = field(default_factory=dict)   # trace에 넣을 근거(JSON 값만)

    @property
    def reason(self) -> str | None:
        return self.reasons[0] if self.reasons else None


def _bare(s: str) -> str:
    return "".join(s.split()).lower()


def mentioned_names(text: str, facts: AnswerFacts) -> list[str]:
    """답에 나온 결과 이름(공백 무시·대소문자 무시). 게시글 제목은 10자 이상 그대로 겹쳐도 인정."""
    bare = _bare(text)
    found = [n for n in facts.names if _bare(n) and _bare(n) in bare]
    for title in facts.titles:
        t = _bare(title)
        if not t:
            continue
        if t in bare or (len(t) > TITLE_MATCH_CHARS
                         and any(t[i:i + TITLE_MATCH_CHARS] in bare for i in range(len(t) - TITLE_MATCH_CHARS + 1))):
            found.append(title)
    return found


def _message_parts(answer: AIMessage | str) -> tuple[str, bool, str | None]:
    if isinstance(answer, str):
        return answer, False, "stop"
    content = answer.content
    if isinstance(content, list):
        content = "".join(b if isinstance(b, str) else str(b.get("text", "")) for b in content
                          if isinstance(b, str) or (isinstance(b, dict) and b.get("type") == "text"))
    calls = bool(answer.tool_calls or answer.invalid_tool_calls)
    return content or "", calls, (answer.response_metadata or {}).get("finish_reason")


def check_answer(answer: AIMessage | str, facts: AnswerFacts, *, finish_reason: Any = _UNSET,
                 max_chars: int = MAX_ANSWER_CHARS) -> GuardVerdict:
    """LLM 답 하나를 판정한다. 걸린 이유를 모두 모아 REASONS 순서로 돌려준다(첫 이유가 대표).

    finish_reason을 주지 않으면 AIMessage의 response_metadata 값(없으면 stop으로 봄)을 쓴다. answer_llm 노드는
    스트림을 합친 값을 그대로 넘겨서, 끊긴 스트림(finish_reason 없음)은 incomplete가 된다."""
    text, has_calls, meta_finish = _message_parts(answer)
    finish = (meta_finish if meta_finish is not None else "stop") if finish_reason is _UNSET else finish_reason
    stripped = text.strip()
    reasons: set[str] = set()
    details: dict[str, Any] = {"answer_chars": len(stripped), "finish_reason": finish}
    if not stripped:
        reasons.add("empty")
    if has_calls or finish == "tool_calls":
        reasons.add("tool_calls")
    if _SPECIAL_RE.search(text):
        reasons.add("tool_call_text")
    if _JSON_RE.search(text):
        reasons.add("json_fragment")
    if finish not in ("stop", "tool_calls"):
        reasons.add("incomplete")
    if stripped and " ".join(stripped.split()) in facts.canned:
        reasons.add("canned")
    if stripped and (facts.names or facts.titles):
        found = mentioned_names(stripped, facts)
        details["mentioned"] = found
        if not found:
            reasons.add("no_item_name")
    unknown = sorted(numbers_in(stripped) - facts.numbers, key=lambda s: (len(s), s))
    if unknown:
        details["unknown_numbers"] = unknown
        reasons.add("unknown_number")
    leaks = [where for where, raw in facts.card_texts if overlaps(stripped, raw, OVERLAP_CHARS)]
    if leaks:
        details["card_overlap"] = leaks
        reasons.add("card_overlap")
    if len(stripped) > max_chars:
        reasons.add("too_long")
    ordered = tuple(r for r in REASONS if r in reasons)
    return GuardVerdict(ok=not ordered, reasons=ordered, details=details)


def input_leaks(contents: Sequence[str], facts: AnswerFacts) -> list[str]:
    """답변 모델에 보낼 도구 결과에 카드 원문이 섞였는지(어댑터 실수 대비, 보내기 전에 막음)."""
    joined = "\n".join(contents)
    return [where for where, raw in facts.card_texts if overlaps(joined, raw, OVERLAP_CHARS)]


__all__ = ["AnswerFacts", "GuardVerdict", "MAX_ANSWER_CHARS", "REASONS", "TITLE_MATCH_CHARS", "answer_facts",
           "canned_answers", "check_answer", "input_leaks", "mentioned_names", "numbers_in"]
