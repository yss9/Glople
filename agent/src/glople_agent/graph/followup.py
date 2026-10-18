"""후속 발화 풀이(design.md 5.3 start_turn, M1): 라우터는 학습대로 '한 마디'만 받으므로 다턴 맥락은 코드가 한 마디로 만든다.

1. 되묻기에 대한 답: 지난 턴이 되묻기로 끝났으면(memory.pending_clarify) 그 발화 뒤에 이번 답을 이어 붙인다.
   "나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임" → (나이 되묻기) → "28살이요"
   ⇒ "나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임 28살이요"
   이번 말이 FOLLOWUP_MAX_CHARS(40자)보다 길거나, 인사·감사이거나, 요청 말(추천·알려·찾아·저장·날씨·후기 …)이
   들어 있으면 새 요청으로 보고 잇지 않는다(되묻기 답은 "28살이요", "부산", "2박 3일, 우도"처럼 짧고 요청 말이
   없다는 가정. 한계는 api-notes).
2. 지시어: '거기·그곳·그쪽'(+조사)을 memory.last_region으로 바꾼다. 조사는 바꾼 지역 이름 받침에 맞춘다
   ("거기 날씨" → "제주 날씨", "그곳은" → "부산은", "거기서" → "강릉에서").

라우터에 넣는 문장(router_input)에만 반영하고, 대화 기록의 HumanMessage 원문은 그대로 둔다.
pending_clarify는 바로 다음 턴 한 번만 쓰고 비운다(start_turn이 None으로 덮음).
"""
import re
from collections.abc import Mapping
from typing import Any

from glople_agent.answer.templates import pick_josa
from glople_agent.sanitize import clean_text

FOLLOWUP_MAX_CHARS = 40
# 새 요청으로 보는 말: 인사·감사로 시작하거나, 도구를 부르는 요청 말이 있음
_GREETING_RE = re.compile(r"^\s*(안녕|하이|ㅎㅇ|반가워|고마워|고맙|감사|땡큐|thank|hello|hi\b|bye|잘\s*가)", re.I)
_REQUEST_RE = re.compile(r"추천|알려|찾아|보여|저장|남겨|검색|골라|날씨|후기|리뷰|글로플러|가이드|어때|어떰|어떨까|뭐\s*있|뭐야")
DEMONSTRATIVE_WORDS = ("거기", "그곳", "그쪽")

# 지시어 뒤에 붙는 조사: 받침과 무관한 것은 그대로, 받침에 따라 바뀌는 것은 짝으로
_KEEP = ("에서는", "에서도", "에서", "에는", "에도", "에", "의", "도", "만", "까지", "부터", "하고")
_PAIRED = {"은": "은/는", "는": "은/는", "이": "이/가", "가": "이/가", "을": "을/를", "를": "을/를",
           "으로": "으로/로", "로": "으로/로", "이랑": "이랑/랑", "랑": "이랑/랑"}
_SHORT = {"서": "에서", "엔": "에는"}   # 거기서 → ○○에서, 거기엔 → ○○에는
_PARTICLES = sorted((*_KEEP, *_PAIRED, *_SHORT), key=len, reverse=True)
_DEMONSTRATIVE_RE = re.compile(
    r"(?<![가-힣])(" + "|".join(DEMONSTRATIVE_WORDS) + r")(" + "|".join(_PARTICLES) + r")?(?![가-힣])")


def _particle_for(region: str, particle: str | None) -> str:
    if not particle:
        return ""
    if particle in _SHORT:
        return _SHORT[particle]
    if particle in _PAIRED:
        return pick_josa(region, _PAIRED[particle])
    return particle


def replace_demonstratives(text: str, region: str | None) -> tuple[str, int]:
    """지시어를 region으로 바꾼 문장과 바꾼 수. region이 없으면 그대로."""
    if not region:
        return text, 0
    count = 0

    def sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        return region + _particle_for(region, m.group(2))

    return _DEMONSTRATIVE_RE.sub(sub, text), count


def looks_like_new_request(text: str) -> bool:
    """되묻기에 대한 답이 아니라 새 요청으로 보이는지(길이·인사·요청 말)."""
    return len(text) > FOLLOWUP_MAX_CHARS or bool(_GREETING_RE.search(text)) or bool(_REQUEST_RE.search(text))


def compose_router_input(text: str, memory: Mapping[str, Any] | None) -> tuple[str, dict[str, Any]]:
    """이번 발화(정제됨) + memory → (라우터 한 마디, trace용 정보). 정보에는 원문을 넣지 않는다."""
    memory = memory or {}
    info: dict[str, Any] = {}
    out = text
    pending = memory.get("pending_clarify")
    if isinstance(pending, Mapping) and pending.get("utterance") and text:
        if not looks_like_new_request(text):
            out = clean_text(f"{pending['utterance']} {text}")
            info["joined"] = {"reason": pending.get("reason"), "tool": pending.get("tool")}
        else:
            info["joined"] = {"skipped": "new_request", "reason": pending.get("reason")}
    out, n = replace_demonstratives(out, memory.get("last_region"))
    if n:
        info["demonstratives"] = n
    return out, info


__all__ = ["DEMONSTRATIVE_WORDS", "FOLLOWUP_MAX_CHARS", "compose_router_input", "looks_like_new_request",
           "replace_demonstratives"]
