"""Type3(공공누리 제3유형: 출처표시·변경금지) 판정과 LLM용 허용 필드 압축(design.md 11절).

판정은 이 파일의 text_policy() 한 함수다. 같은 표를 agent/resources/text_policy_cases.csv로 두고 pytest가 읽는다
(S0 때 Spring JUnit도 같은 CSV를 읽게 할 수 있음).

| source | infoKind | copyrightType(공백 제거·소문자) | textPolicy | LLM 입력 | 카드 |
|---|---|---|---|---|---|
| TOUR_API | DESCRIPTION | type3, 빈 값, 모르는 값(type2·type4 포함) | VERBATIM_ONLY | 글 없음. 이름·지역 + note "설명은 원문 카드" | 원문 + 제3유형 출처 |
| TOUR_API | DESCRIPTION | type1 | ATTRIBUTION | 1차는 사실 필드만 | 원문 + 제1유형 출처 |
| TOUR_API | SUMMARY | 무관 | FREE_FACTS | 틀 문장 60자까지 | 원문 + 출처 |
| WIKIDATA | DESCRIPTION | - | SHARE_ALIKE | 1차는 사실 필드만 | 원문 + 문서 + CC BY-SA |
| WIKIDATA | SUMMARY | - | FREE_FACTS | 틀 문장 60자까지 | 원문 + CC0 |
| 그 밖(NULL·글 종류 없음) | - | - | UNKNOWN | 이름(과 지역)만 | 이름만(글 없음) |

- 비교는 SourceAttribution.js와 같다(공백 제거·소문자). 서버가 textPolicy를 내려주면(S0/S1) 아는 값일 때 그것을 우선한다.
- LLM 항목은 **새 dict를 허용 필드로만** 만든다: 서버가 필드를 늘려도(imageUrl·좌표·원문) LLM 쪽에 새지 않는다.
- 원문(info)은 카드에만, 바꾸지 않고 그대로 둔다(변경 금지). 출처 문구는 코드가 붙인다(templates.attribution_text).
"""
from collections.abc import Mapping
from typing import Any, Literal, get_args
from urllib.parse import quote, unquote

from glople_agent.answer.templates import attribution_text, normalize_copyright
from glople_agent.config import AGENT_DIR
from glople_agent.sanitize import clean_text

TextPolicy = Literal["VERBATIM_ONLY", "ATTRIBUTION", "FREE_FACTS", "SHARE_ALIKE", "UNKNOWN"]
TEXT_POLICIES: tuple[str, ...] = get_args(TextPolicy)
CASES_CSV = AGENT_DIR / "resources" / "text_policy_cases.csv"

VERBATIM_NOTE = "설명은 원문 카드"
SUMMARY_CHARS = 60          # FREE_FACTS 틀 문장 상한
LLM_NAME_CHARS = 60
LLM_WHERE_CHARS = 40
DETAIL_PATH = "/recomendation/information/"   # 프론트 상세 화면(information.js가 state.place를 그대로 보여 줌)

# 정책별로 LLM이 글을 볼 수 있는지(1차: FREE_FACTS의 틀 문장만)
LLM_TEXT: dict[str, Literal["none", "summary"]] = {
    "VERBATIM_ONLY": "none", "ATTRIBUTION": "none", "FREE_FACTS": "summary", "SHARE_ALIKE": "none", "UNKNOWN": "none",
}


def _norm_key(value: str | None) -> str:
    return "".join((value or "").split()).upper()


def text_policy(source: str | None, info_kind: str | None, copyright_type: str | None,
                server_policy: str | None = None) -> TextPolicy:
    """11절 판정표. 모르는 값은 보수적으로(VERBATIM_ONLY·UNKNOWN). server_policy가 아는 값이면 그것을 쓴다."""
    if server_policy is not None and _norm_key(server_policy) in TEXT_POLICIES:
        return _norm_key(server_policy)  # type: ignore[return-value]
    src, kind = _norm_key(source), _norm_key(info_kind)
    if src == "TOUR_API":
        if kind == "SUMMARY":
            return "FREE_FACTS"
        if kind == "DESCRIPTION":
            return "ATTRIBUTION" if normalize_copyright(copyright_type) == "type1" else "VERBATIM_ONLY"
        return "UNKNOWN"
    if src == "WIKIDATA":
        if kind == "SUMMARY":
            return "FREE_FACTS"
        if kind == "DESCRIPTION":
            return "SHARE_ALIKE"
        return "UNKNOWN"
    return "UNKNOWN"


def llm_may_see_text(policy: str) -> bool:
    return LLM_TEXT.get(policy, "none") == "summary"


def wiki_title(source_url: str | None) -> str | None:
    prefix = "https://ko.wikipedia.org/wiki/"
    if source_url and source_url.startswith(prefix):
        return unquote(source_url[len(prefix):].split("#")[0].split("?")[0]).replace("_", " ") or None
    return None


def place_items(row: Mapping[str, Any]) -> tuple[dict, dict]:
    """여행지 한 행(KeywordResultDTO, S1 뒤 destination 응답) → (LLM 항목, 카드 항목).

    LLM 항목: {name, where, note?}만. note는 VERBATIM_ONLY면 "설명은 원문 카드", FREE_FACTS면 틀 문장 60자.
    카드 항목: 원문 그대로(UNKNOWN이면 글 없음) + 정책 + 출처 문구 + 상세 화면 경로.
    """
    name = row.get("destName") or row.get("name") or ""
    where = row.get("location") or row.get("country") or ""
    policy = text_policy(row.get("source"), row.get("infoKind"), row.get("copyrightType"), row.get("textPolicy"))
    llm: dict[str, Any] = {"name": clean_text(name, LLM_NAME_CHARS), "where": clean_text(where, LLM_WHERE_CHARS)}
    if policy == "VERBATIM_ONLY":
        llm["note"] = VERBATIM_NOTE
    elif llm_may_see_text(policy) and row.get("info"):
        llm["note"] = clean_text(row["info"], SUMMARY_CHARS)
    card = {
        "id": row.get("id"),
        "name": name,
        "country": row.get("country"),
        "location": row.get("location"),
        "text": None if policy == "UNKNOWN" else row.get("info"),   # 원문 그대로(변경 금지)
        "textKind": row.get("infoKind"),
        "textPolicy": policy,
        "source": row.get("source"),
        "copyrightType": row.get("copyrightType"),
        "license": row.get("license"),
        "sourceUrl": row.get("sourceUrl"),
        "attribution": attribution_text(row.get("source"), row.get("infoKind"), row.get("copyrightType"),
                                        title=wiki_title(row.get("sourceUrl")) or name),
        "detailPath": DETAIL_PATH + quote(name),
    }
    return llm, card


__all__ = [
    "CASES_CSV", "DETAIL_PATH", "LLM_TEXT", "SUMMARY_CHARS", "TEXT_POLICIES", "TextPolicy", "VERBATIM_NOTE",
    "llm_may_see_text", "place_items", "text_policy", "wiki_title",
]
