"""키워드 94개 해석(design.md 7·8.2, labeling_rules.md 4절).

Spring `/api/search`는 목록에 없는 키워드를 **조용히 버린다**(KeywordMask.fromKeywords). 그래서 에이전트가 먼저
목록과 맞춰 보고, 바꿀 수 있는 말은 목록 표기로 바꾸고, 바꿀 수 없는 말은 '반영 안 됨'으로 드러낸다.

맞추는 순서(앞에서 맞으면 끝):
1. exact   목록 표기 그대로(앞뒤 공백 제거)
2. spacing 띄어쓰기만 다름(`하이킹코스` → `하이킹 코스`)
3. synonym 아래 SYNONYMS 표(뜻이 하나로 정해지는 것만: 오타·동의어)
4. inner   값 안에 든 가장 긴 목록 키워드(`유네스코 세계유산` → `유네스코`, `캠핑장` → `캠핑`).
           한 글자 키워드(산·섬·성·탑·강)는 홀로 쓴 단어(+조사)일 때만 인정한다(`산책로`의 '산'은 아님,
           Spring KeywordMask.fromKoreanDescription과 같은 규칙)
5. 없음    unknown → 서버에 보내지 않고 notes에 '반영하지 못한 키워드'로 남김

SYNONYMS는 labeling_rules.md 4절의 "목록에서 뜻이 같은 항목이 하나뿐일 때만 옮긴다"를 따른다.
바닷가(바다/해변/해안), 기차여행(기차/철도 여행)처럼 후보가 둘 이상인 말과 슬랭 '맛집'(야경 맛집 = 야경 명소)은 넣지 않는다.
규칙은 train으로만 다듬고 test·human_style로 잰다(design.md 7절).
"""
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

from glople_agent import contract

KEYWORDS: tuple[str, ...] = contract.KEYWORDS
KEYWORD_SET: frozenset[str] = frozenset(KEYWORDS)

# 오타·동의어 → 목록 표기(뜻이 하나로 정해지는 것만)
SYNONYMS: dict[str, str] = {
    "스노쿨링": "스노클링",
    "스노클": "스노클링",
    "트래킹": "트레킹",
    "와이너리": "와인",
    "세계유산": "유네스코",
    "세계문화유산": "유네스코",
    "세계자연유산": "유네스코",
}

# Spring KeywordMask의 PARTICLES(한 글자 키워드 뒤에 붙어도 같은 단어로 보는 조사)
ONE_CHAR_PARTICLES: frozenset[str] = frozenset({
    "은", "이", "을", "과", "의", "에", "에서", "에는", "에도", "으로", "으로는", "까지", "부터", "처럼",
    "이다", "이며", "이고", "이자", "이나", "이라는", "이었다",
})
_HANGUL = re.compile(r"[가-힣]")

MatchHow = Literal["exact", "spacing", "synonym", "inner"]


def _squash(s: str) -> str:
    return "".join(s.split())


_BY_SQUASHED: dict[str, str] = {_squash(k): k for k in KEYWORDS}
_SYNONYMS_SQUASHED: dict[str, str] = {_squash(k): v for k, v in SYNONYMS.items()}
_MULTI = sorted((k for k in KEYWORDS if len(_squash(k)) > 1), key=lambda k: len(_squash(k)), reverse=True)
_ONE = [k for k in KEYWORDS if len(k) == 1]


def _standalone(text: str, word: str) -> bool:
    """한 글자 word가 text에 홀로 쓴 단어(+조사)로 있는지. Spring containsStandaloneWord와 같은 규칙."""
    start = text.find(word)
    while start >= 0:
        end = start + len(word)
        word_end = end
        while word_end < len(text) and _HANGUL.match(text[word_end]):
            word_end += 1
        begins = start == 0 or not _HANGUL.match(text[start - 1])
        suffix = text[end:word_end]
        if begins and (not suffix or suffix in ONE_CHAR_PARTICLES):
            return True
        start = text.find(word, start + 1)
    return False


@dataclass(frozen=True)
class KeywordMatch:
    given: str                 # 모델이 낸 값
    keyword: str | None        # 목록 표기(못 찾으면 None)
    how: MatchHow | None

    @property
    def changed(self) -> bool:
        return self.keyword is not None and self.how != "exact"


@dataclass(frozen=True)
class KeywordResolution:
    matches: tuple[KeywordMatch, ...]

    @property
    def applied(self) -> list[str]:
        """서버에 보낼 목록 키워드(중복 없이, 처음 나온 순서)."""
        return list(dict.fromkeys(m.keyword for m in self.matches if m.keyword))

    @property
    def mapped(self) -> list[tuple[str, str]]:
        """목록 표기로 바꾼 것 (원래 값, 키워드)."""
        return [(m.given, m.keyword) for m in self.matches if m.changed and m.keyword]

    @property
    def unknown(self) -> list[str]:
        return list(dict.fromkeys(m.given for m in self.matches if m.keyword is None))


def match_keyword(value: str) -> KeywordMatch:
    given = value
    v = " ".join(value.split())
    if v in KEYWORD_SET:
        return KeywordMatch(given, v, "exact")
    sq = _squash(v)
    if not sq:
        return KeywordMatch(given, None, None)
    if sq in _BY_SQUASHED:
        return KeywordMatch(given, _BY_SQUASHED[sq], "spacing")
    if sq in _SYNONYMS_SQUASHED:
        return KeywordMatch(given, _SYNONYMS_SQUASHED[sq], "synonym")
    for kw in _MULTI:   # 긴 키워드 먼저(고대 유적 > 유적)
        if _squash(kw) in sq:
            return KeywordMatch(given, kw, "inner")
    for syn, kw in _SYNONYMS_SQUASHED.items():
        if syn in sq:
            return KeywordMatch(given, kw, "inner")
    for kw in _ONE:
        if _standalone(v, kw):
            return KeywordMatch(given, kw, "inner")
    return KeywordMatch(given, None, None)


def resolve_keywords(values: Iterable[str]) -> KeywordResolution:
    return KeywordResolution(tuple(match_keyword(v) for v in values if isinstance(v, str)))


def keyword_in_text(keyword: str, text: str) -> bool:
    """목록 키워드가 발화에 근거가 있는지: 표기 그대로(띄어쓰기 무시) 또는 그 키워드로 옮기는 동의어가 있음.
    시범판(proto_validate.py)은 `keyword in text`만 봤다(와이너리 → 와인이 note로 남았던 이유)."""
    if keyword in text or _squash(keyword) in _squash(text):
        return True
    return any(target == keyword and syn in _squash(text) for syn, target in _SYNONYMS_SQUASHED.items())


def unknown_note(unknown: Sequence[str]) -> str | None:
    """서버가 조용히 버릴 키워드를 사용자·LLM에게 드러내는 문장."""
    return f"목록에 없어 반영하지 못한 키워드: {', '.join(unknown)}" if unknown else None


__all__ = [
    "KEYWORDS", "KEYWORD_SET", "KeywordMatch", "KeywordResolution", "ONE_CHAR_PARTICLES", "SYNONYMS",
    "keyword_in_text", "match_keyword", "resolve_keywords", "unknown_note",
]
