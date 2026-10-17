"""외부 문자열 정제: 사용자 발화·게시글 제목·대행자 소개를 LLM에 넣기 전에 쓴다.

Qwen 토크나이저는 본문에 든 `<|im_end|>`·`<tool_call>` 같은 문자열을 제어 토큰으로 바꾼다(design.md 1.3의 2번).
그래서 이런 문자열과 HTML 태그를 지우고, 지운 자리에서 다시 생기는 조합도 남지 않을 때까지 반복한다.
반복은 MAX_PASSES번까지다(일부러 겹친 입력은 한 바퀴에 한 겹씩 벗겨져 길이만큼 돌기 때문). 그때까지
안정되지 않으면 꺾쇠를 모두 지운다. 특수 토큰·태그는 모두 '<'로 시작하므로 남을 수 없다.
"""
import html
import re
import unicodedata

DEFAULT_MAX_LEN = 500
MAX_PASSES = 8          # 정상 입력은 1~2번이면 안정됨
INPUT_FACTOR = 8        # max_len이 있으면 정제 전에 max(max_len×8, 1024)자로 먼저 자름(처리 비용 상한)
MIN_INPUT_CUT = 1024

# <|...|> 꼴(<|im_start|>, <|im_end|>, <|endoftext|> …)은 패턴으로, 나머지는 이름으로 지운다
SPECIAL_TOKEN_STRINGS = (
    "<tool_call>", "</tool_call>", "<tool_response>", "</tool_response>", "<think>", "</think>",
)
_PIPE_TOKEN_RE = re.compile(r"<\|[^<>]*?\|>")
_NAMED_TOKEN_RE = re.compile("|".join(re.escape(t) for t in SPECIAL_TOKEN_STRINGS))
_COMMENT_RE = re.compile(r"<!--.*?(?:-->|$)", re.S)
_TAG_RE = re.compile(r"</?([A-Za-z][A-Za-z0-9_:-]*)(?:\s[^<>]*)?/?>")
# 글자 사이에 붙는 태그는 붙여서, 블록 태그는 띄워서 지운다
_INLINE_TAGS = frozenset({"a", "abbr", "b", "code", "em", "font", "i", "mark", "s", "small", "span",
                          "strike", "strong", "sub", "sup", "u"})
# 눈에 안 보이는 글자(제로폭 공백·방향 표시·BOM·soft hyphen). 이모지 ZWJ(U+200D)는 남긴다
_INVISIBLE_RE = re.compile("[­​‌‎‏‪-‮⁠-⁤﻿]")
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_SPACE_RE = re.compile(r"\s+")


def _replace_tag(m: re.Match) -> str:
    return "" if m.group(1).lower() in _INLINE_TAGS else " "


def clean_text(s: str | None, max_len: int | None = DEFAULT_MAX_LEN) -> str:
    """HTML 태그·특수 토큰 문자열 제거, 공백 정리, 길이 제한(기본 500자)."""
    if s is None:
        return ""
    if not isinstance(s, str):
        raise TypeError(f"clean_text는 문자열만 받습니다: {type(s).__name__}")
    if max_len is not None and max_len < 0:
        raise ValueError("max_len은 0 이상이어야 합니다")
    if max_len is not None:   # 결과는 어차피 max_len자라 앞부분만 정제(태그가 많아도 넉넉한 배수)
        s = s[: max(max_len * INPUT_FACTOR, MIN_INPUT_CUT)]
    text = unicodedata.normalize("NFC", html.unescape(s))
    text = _INVISIBLE_RE.sub("", _CONTROL_RE.sub(" ", text))
    for _ in range(MAX_PASSES):  # '<tool<tool_call>_call>'처럼 지운 자리에서 다시 생기는 것까지
        before = text
        text = _PIPE_TOKEN_RE.sub(" ", text)
        text = _NAMED_TOKEN_RE.sub(" ", text)
        text = _COMMENT_RE.sub(" ", text)
        text = _TAG_RE.sub(_replace_tag, text)
        if text == before:
            break
    else:   # MAX_PASSES번에도 안정되지 않음(일부러 겹친 입력) → 꺾쇠를 모두 지움
        text = text.replace("<", " ").replace(">", " ")
    text = _SPACE_RE.sub(" ", text).strip()
    if max_len is not None and len(text) > max_len:
        text = text[:max_len].rstrip()
    return text
