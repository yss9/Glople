"""가짜 채팅 모델(design.md 6.6): GPU·모델 서버 없이 그래프 전 구간을 돌린다.

- ScriptedChatModel: BaseChatModel 하위 클래스. 받은 (messages, kwargs)를 calls에 남기고 script가 만든 AIMessage를 돌려준다.
  GenericFakeChatModel은 bind_tools가 없고 스트리밍에 tool_calls를 안 내서 직접 만든다.
- 스크립트 세 가지
  - RuleScript: 정규식 데모 규칙(RULE_DEMOS)
  - OracleScript: v2 test 300 + human_style 90의 정답 assistant를 발화로 찾아 돌려줌
  - QuirkScript: 서버 quirk(병렬 "\\n", 빈 호출, length, invalid_tool_calls, 지어낸 인자, 연결 오류 …)를 순서대로 냄
- 응답은 mlx_lm.server 응답(choices[0].message) 모양을 만든 뒤 langchain-openai와 같은 규칙으로 AIMessage로 바꾼다
  (from_server). 그래서 content None → "", 병렬 content "\\n", 서버 uuid id, finish_reason이 실제와 같다.
- M2: CassetteScript(실제 서버 응답 녹화 재생, 키 = 정규화 요청 sha256, 없으면 CassetteMiss — llm/cassette.py),
  ResultEchoScript(가짜 답변 모델: 요청의 도구 결과에서 이름만 골라 짧게 답함, 가드를 통과하는 '착한 LLM')

정형 문장(인사·거절·되묻기)은 build_tool_dataset.py를 실행하지 않고 ast로 읽는다(contract.py와 같은 방식).
"""
import ast
import json
import re
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass
from functools import cache
from itertools import count
from pathlib import Path
from typing import Any, NamedTuple

import httpx2
import openai
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.tool import tool_call_chunk
from langchain_core.output_parsers.openai_tools import make_invalid_tool_call, parse_tool_call
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from glople_agent import contract
from glople_agent.answer.templates import josa
from glople_agent.llm.cassette import Cassette
from glople_agent.llm.normalize import new_call_id
from glople_agent.sanitize import clean_text

Script = Callable[[list[BaseMessage], dict], AIMessage]
IdFactory = Callable[[], str]

# 가짜 오류의 요청 주소. 실제 모델 서버(8090)가 아님을 드러내려고 .invalid 도메인을 쓴다(요청은 보내지 않음)
FAKE_REQUEST_URL = "http://scripted.invalid/v1/chat/completions"


def sequential_ids(prefix: str = "call_") -> IdFactory:
    """테스트용 결정적 tool_call id (call_1, call_2, …)."""
    counter = count(1)
    return lambda: f"{prefix}{next(counter)}"


# --- 서버 응답 모양 ----------------------------------------------------------

def server_tool_call(name: str, args: dict | str, *, id: str | None = None) -> dict:
    """mlx_lm.server tool_calls 원소 모양. arguments는 json.dumps(ensure_ascii=False) 문자열(server.py:73)."""
    raw = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    return {"function": {"name": name, "arguments": raw}, "type": "function", "id": id or new_call_id()}


def from_server(content: str | None, tool_calls: Sequence[dict] | None = None,
                finish_reason: str | None = None) -> AIMessage:
    """서버 응답 choices[0](message + finish_reason) → AIMessage.

    langchain-openai 1.6.7 `_convert_dict_to_message`와 같은 규칙이다(test_fake_llm에서 대조).
    arguments가 JSON이 아니면 invalid_tool_calls, JSON이지만 객체가 아니면 AIMessage 검증 오류가 난다.
    finish_reason을 안 주면 호출이 있을 때 tool_calls, 없을 때 stop.
    """
    calls, invalid = [], []
    for raw in tool_calls or []:
        try:
            calls.append(parse_tool_call(raw, return_id=True))
        except Exception as e:  # langchain-openai와 같게 모든 파싱 예외를 invalid로
            invalid.append(make_invalid_tool_call(raw, str(e)))
    if finish_reason is None:
        finish_reason = "tool_calls" if tool_calls else "stop"
    return AIMessage(content=content or "", tool_calls=calls, invalid_tool_calls=invalid,
                     response_metadata={"finish_reason": finish_reason})


def text_message(text: str) -> AIMessage:
    return from_server(text, None, "stop")


def tool_call_message(calls: Sequence[tuple[str, dict]], *, new_id: IdFactory = new_call_id) -> AIMessage:
    """호출 목록 → 서버가 낸 모양. 병렬이면 블록 사이 줄바꿈이 content로 남는다("\\n" × (n-1))."""
    raw = [server_tool_call(name, args, id=new_id()) for name, args in calls]
    content = "\n" * (len(raw) - 1) if len(raw) > 1 else None
    return from_server(content, raw, "tool_calls")


def _fake_request() -> httpx2.Request:
    return httpx2.Request("POST", FAKE_REQUEST_URL)


def connection_error() -> openai.APIConnectionError:
    # 서버 KeyError로 헤더 뒤 연결이 끊긴 경우와 같은 예외(조사 2의 1-3)
    return openai.APIConnectionError(request=_fake_request())


def timeout_error() -> openai.APITimeoutError:
    return openai.APITimeoutError(request=_fake_request())


def server_error(status: int = 500) -> openai.APIStatusError:
    response = httpx2.Response(status, request=_fake_request())
    cls = openai.InternalServerError if status >= 500 else openai.APIStatusError
    return cls(f"Error code: {status}", response=response, body=None)


# --- 가짜 채팅 모델 ----------------------------------------------------------

class ScriptedChatModel(BaseChatModel):
    script: Script
    calls: list = Field(default_factory=list)  # 받은 (messages, kwargs) → 요청 모양 검사
    model_name: str = "scripted"

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools, **kwargs):  # ChatOpenAI처럼 변환하지 않고 받은 dict를 그대로 kwargs로
        return self.bind(tools=tools, **kwargs)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append((list(messages), dict(kwargs)))
        message = self.script(list(messages), dict(kwargs))
        if not isinstance(message, AIMessage):
            raise TypeError(f"스크립트는 AIMessage를 돌려줘야 합니다: {type(message).__name__}")
        message = message.model_copy(deep=True)  # 같은 객체를 돌려주는 스크립트여도 호출마다 새 메시지
        finish = message.response_metadata.get("finish_reason") or ("tool_calls" if message.tool_calls else "stop")
        return ChatResult(
            generations=[ChatGeneration(message=message, generation_info={"finish_reason": finish})],
            llm_output={"model_name": self.model_name, "model_provider": "scripted"},
        )

    def _stream(self, messages, stop=None, run_manager=None, **kwargs) -> Iterator[ChatGenerationChunk]:
        """글자 단위 content 다음, 호출마다 한 청크(서버처럼 arguments 전체), 마지막에 finish_reason."""
        generation = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs).generations[0]
        msg = generation.message
        content = msg.content if isinstance(msg.content, str) else ""
        for ch in content:
            yield ChatGenerationChunk(message=AIMessageChunk(content=ch))
        index = 0
        for tc in msg.tool_calls:
            chunk = tool_call_chunk(name=tc["name"], args=json.dumps(tc["args"], ensure_ascii=False),
                                    id=tc["id"], index=index)
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[chunk]))
            index += 1
        for itc in msg.invalid_tool_calls:  # 스트리밍은 parse_partial_json이라 합칠 때 결과가 달라질 수 있음
            chunk = tool_call_chunk(name=itc.get("name"), args=itc.get("args"), id=itc.get("id"), index=index)
            yield ChatGenerationChunk(message=AIMessageChunk(content="", tool_call_chunks=[chunk]))
            index += 1
        yield ChatGenerationChunk(message=AIMessageChunk(content="", chunk_position="last"),
                                  generation_info=dict(generation.generation_info or {}))


def fake_router_model(script: Script) -> ScriptedChatModel:
    """라우터 가짜: 스트리밍을 끈다(messages 모드에서도 한 번에 받음, design.md 6.6)."""
    return ScriptedChatModel(script=script, disable_streaming=True, tags=["nostream"])


def fake_answer_model(script: Script) -> ScriptedChatModel:
    """답변 가짜(M2): messages 스트림 모드에서 글자 단위로 흐른다."""
    return ScriptedChatModel(script=script)


def last_human_text(messages: Sequence[BaseMessage]) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    raise ValueError("HumanMessage가 없는 요청입니다")


# --- 학습 생성기의 정형 문장 --------------------------------------------------

_GENERATOR_NAMES = (
    "GREETING_ANSWER", "THANKS_ANSWER", "ABILITY_ANSWER", "DECLINE_ANSWER", "WEATHER_CLARIFY_ANSWER",
    "PLAN_CLARIFY_ANSWERS", "REVIEWS_CLARIFY_ANSWER", "OPEN_CLARIFY_ANSWER", "CATEGORY_WORDS",
)


@cache
def generator_literals() -> dict[str, Any]:
    """build_tool_dataset.py의 정형 문장·카테고리 말(ast로 한 번만 읽음, 모듈은 실행하지 않음)."""
    path = contract.DATASET_SCRIPT_PATH
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _GENERATOR_NAMES:
                    if target.id in found:
                        raise RuntimeError(f"{path.name}에 {target.id} 대입문이 둘 이상입니다")
                    found[target.id] = ast.literal_eval(node.value)
    missing = set(_GENERATOR_NAMES) - set(found)
    if missing:
        raise RuntimeError(f"{path.name}에서 못 찾음: {sorted(missing)}")
    return found


# --- RuleScript ------------------------------------------------------------

class RuleDemo(NamedTuple):
    text: str
    path: str   # 그래프에서 기대하는 길: tool / parallel / clarify / direct / plan / fallback
    note: str


RULE_DEMOS: tuple[RuleDemo, ...] = (
    RuleDemo("바나힐 정보랑 사람들 리뷰 같이 알려줘", "parallel", "상세 + 후기 병렬, 서버 content \"\\n\""),
    RuleDemo("나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임", "clarify", "age 없는 호출 → validate가 나이 되묻기"),
    RuleDemo("안녕", "direct", "인사 정형 문장"),
    RuleDemo("제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘", "plan", "create_travel_plan → 확인 카드"),
    RuleDemo("깨진 응답 흉내: 부산 날씨 알려줘", "fallback", "빈 호출 + finish_reason=tool_calls → 재시도도 실패"),
    RuleDemo("바다 보이는 곳 추천해줘", "tool", "recommend_by_keywords 하나"),
    RuleDemo("부산 이번 주말 날씨 어때?", "tool", "get_weather(미지원 결과)"),
    RuleDemo("고마워", "direct", "감사 정형 문장"),
    RuleDemo("(잘림) 강릉 2박 3일 경포대랑 안목해변 일정 저장해줘", "plan", "length → max_tokens 768 재시도에서 정상"),
    RuleDemo("(지어냄) 나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임", "clarify",
             "v1처럼 age 30을 지어냄 → 근거 검사(M1)가 나이 되묻기"),
)

_PARTICLES = tuple(sorted(("에서", "에게", "이랑", "하고", "쪽에", "쪽", "근처", "에", "의", "은", "는", "을", "를"),
                          key=len, reverse=True))
_DEMONSTRATIVES = frozenset({"거기", "여기", "저기", "그곳", "그쪽", "이곳", "아무거나", "아무데나"})
_FILLER = frozenset({"좀", "혹시", "지금", "요즘", "그", "이번", "다음", "여행", "사람들", "사람", "실제", "방문", "다녀온",
                     "갔다온", "솔직한", "진짜", "관련", "현지", "전문", "잘", "아는", "같이", "일정", "코스", "장소"})
_MBTI_ALIASES = {
    "엔프피": "ENFP", "인프피": "INFP", "엔프제": "ENFJ", "인프제": "INFJ", "엔팁": "ENTP", "인팁": "INTP",
    "엔티제": "ENTJ", "인티제": "INTJ", "엣프피": "ESFP", "잇프피": "ISFP", "엣프제": "ESFJ", "잇프제": "ISFJ",
    "엣팁": "ESTP", "잇팁": "ISTP", "엣티제": "ESTJ", "잇티제": "ISTJ",
}  # labeling_rules.md 4절
_WHEN_PATTERNS = (
    (re.compile(r"이번\s*주말|주말에?"), "이번 주말"),
    (re.compile(r"다음\s*주|담주"), "다음 주"),
    (re.compile(r"내일|낼"), "내일"),
    (re.compile(r"오늘"), "오늘"),
)
_DAY_WORDS = (("당일치기", 1), ("하루", 1), ("이틀", 2), ("사흘", 3), ("나흘", 4), ("일주일", 7))
_TITLE_RE = re.compile(r"['‘\"“]([^'’\"”]+)['’\"”]\s*(?:(?:이)?라는\s*)?(?:이름으로|으로|로)?")
_MARKER_RE = re.compile(r"^\s*\((깨짐|잘림|끊김|지연|지어냄)\)\s*")
# '(지어냄)' 표시: v1 LoRA가 실제로 지어낸 값(design.md 5.5의 2번, revamp-todo.md:156)
_INVENTED_ROUTE_ARGS = {"age": 30, "gender": "여성", "mbti": "ENFP"}
_RETRY_MAX_TOKENS = 768


def _strip_particle(word: str) -> str:
    word = word.strip(" ,.!?~·'\"“”‘’()")
    for p in _PARTICLES:
        if word.endswith(p) and len(word) > len(p) + 1:
            return word[: -len(p)]
    return word


def _tokens(text: str) -> list[str]:
    return [w for w in (_strip_particle(t) for t in text.split()) if w and w not in _FILLER]


def _last_token(text: str) -> str | None:
    tokens = _tokens(text)
    return tokens[-1] if tokens else None


def _has_final_consonant(word: str) -> bool:
    ch = word[-1]
    return "가" <= ch <= "힣" and (ord(ch) - 0xAC00) % 28 != 0


class RuleScript:
    """정규식 데모 규칙. 라우터 요청의 마지막 HumanMessage만 본다(첫 규칙이 이김).

    quirk 표시(데모용):
    - 문장에 '깨진' 또는 앞에 '(깨짐)': 빈 호출 + finish_reason=tool_calls (재시도해도 같음 → fallback)
    - 앞에 '(잘림)': finish_reason=length. max_tokens≥768로 다시 부르면 표시를 뗀 문장으로 규칙을 탐
    - 앞에 '(끊김)': APIConnectionError, '(지연)': APITimeoutError
    - 앞에 '(지어냄)': 유사 여행자 루트에서 발화에 없는 나이·성별·MBTI를 지어 채움(v1 실제 오답 흉내 → M1 근거 검사가 잡음)
    맞는 규칙이 없으면 학습한 열린 되묻기 문장(OPEN_CLARIFY_ANSWER)을 낸다.
    """

    def __init__(self, *, new_id: IdFactory = new_call_id):
        self.new_id = new_id
        self.texts = generator_literals()
        self._category_words = sorted(
            ((word, category) for category, words in self.texts["CATEGORY_WORDS"].items() for word in words),
            key=lambda wc: len(wc[0]), reverse=True)
        self._keywords = sorted(contract.KEYWORDS, key=len, reverse=True)
        self._inventing = False
        self._rules: tuple[Callable[[str], AIMessage | None], ...] = (
            self._off_topic, self._thanks, self._ability, self._plan, self._routes, self._detail_and_reviews,
            self._weather, self._reviews, self._detail, self._gloplers, self._search, self._keywords_rule,
            self._greeting,
        )

    def __call__(self, messages: list[BaseMessage], kwargs: dict) -> AIMessage:
        text = last_human_text(messages)
        marker = _MARKER_RE.match(text)
        if marker:
            kind, rest = marker.group(1), text[marker.end():]
            if kind == "깨짐":
                return from_server(None, None, "tool_calls")
            if kind == "끊김":
                raise connection_error()
            if kind == "지연":
                raise timeout_error()
            if kind == "지어냄":
                self._inventing = True
                try:
                    return self.respond(rest)
                finally:
                    self._inventing = False
            if (kwargs.get("max_tokens") or 0) < _RETRY_MAX_TOKENS:  # 잘림: 첫 시도만
                return from_server(None, None, "length")
            text = rest
        if "깨진" in text:
            return from_server(None, None, "tool_calls")
        return self.respond(text)

    def respond(self, text: str) -> AIMessage:
        for rule in self._rules:
            message = rule(text)
            if message is not None:
                return message
        return text_message(self.texts["OPEN_CLARIFY_ANSWER"])

    def _call(self, *calls: tuple[str, dict]) -> AIMessage:
        return tool_call_message(calls, new_id=self.new_id)

    # 바로 답변
    def _off_topic(self, text):
        if re.search(r"주식|코인|코딩|코드|파이썬|숙제|과제|번역|이메일|자소서|로또", text):
            return text_message(self.texts["DECLINE_ANSWER"])

    def _thanks(self, text):
        if re.search(r"고마워|고맙|감사|땡큐|thank", text, re.I):
            return text_message(self.texts["THANKS_ANSWER"])

    def _ability(self, text):
        if re.search(r"뭐 할 수 있|뭘 할 수 있|무엇을 할 수|무슨 기능|뭐 해줄 수|뭘 해줄 수|넌 누구|너는 누구|누구세요", text):
            return text_message(self.texts["ABILITY_ANSWER"])

    def _greeting(self, text):
        if re.match(r"^\s*(안녕|ㅎㅇ|하이|반가워|hello|hi)", text, re.I):
            return text_message(self.texts["GREETING_ANSWER"])

    # 도구
    def _plan(self, text):
        if not re.search(r"저장|남겨", text):
            return None
        title = None
        if m := _TITLE_RE.search(text):  # 따옴표로 정한 일정 이름은 먼저 떼어 둔다
            title, text = m.group(1), text[: m.start()] + " " + text[m.end():]
        days, span = None, None
        if m := re.search(r"(\d+)\s*박\s*(\d+)\s*일", text):
            days, span = int(m.group(2)), m.span()
        else:
            for word, n in _DAY_WORDS:
                if (i := text.find(word)) >= 0:
                    days, span = n, (i, i + len(word))
                    break
            else:
                if m := re.search(r"(\d+)\s*일", text):
                    days, span = int(m.group(1)), m.span()
        head = text[: span[0]] if span else text
        head_tokens = _tokens(re.split(r"저장|남겨|일정", head)[0])
        region = head_tokens[0] if head_tokens and head_tokens[0] not in _DEMONSTRATIVES else None
        places: list[str] = []
        if span:
            tail = re.split(r"넣어서|넣고|포함해서|일정|으로|저장|남겨", text[span[1]:])[0]
            parts = re.split(r"(?:이랑|랑|하고|그리고|및)\s+|\s*,\s*|\s+", tail)
            places = [p for p in (_strip_particle(x) for x in parts) if p and p not in _FILLER]
        if region and days and places:
            args: dict[str, Any] = {"region": region, "days": days, "places": places}
            if title:
                args["title"] = title
            return self._call((contract.PLAN_TOOL_NAME, args))
        answers = self.texts["PLAN_CLARIFY_ANSWERS"]
        key = "none" if not region else ("region_days" if days else "region")
        return text_message(answers[key])

    def _routes(self, text):
        if not re.search(r"비슷한 여행자|여행자 루트|나랑 비슷한|비슷한 사람", text):
            return None
        args: dict[str, Any] = {}  # 찾은 것만 넣는다(빠진 필수값은 validate가 되묻기)
        if m := re.search(r"(\d{1,3})\s*(?:살|세)", text) or re.search(r"(?<!\d)(\d{2})\s*(?:남|여)(?![가-힣])", text):
            args["age"] = int(m.group(1))
        if re.search(r"남자|남성|(?<![가-힣])남(?![가-힣])", text):
            args["gender"] = "남성"
        elif re.search(r"여자|여성|(?<![가-힣])여(?![가-힣])", text):
            args["gender"] = "여성"
        if m := re.search(r"(?<![A-Za-z])([EIei][SNsn][TFtf][JPjp])(?![A-Za-z])", text):
            args["mbti"] = m.group(1).upper()
        else:
            for alias, mbti in _MBTI_ALIASES.items():
                if alias in text:
                    args["mbti"] = mbti
                    break
        if self._inventing:
            args = {**_INVENTED_ROUTE_ARGS, **args}   # 빠진 값을 지어 채움(순서는 도구 스키마 순)
            args = {k: args[k] for k in ("age", "gender", "mbti")}
        return self._call(("recommend_similar_traveler_routes", args))

    def _detail_and_reviews(self, text):
        m = re.search(r"(\S+?)\s*(?:정보|소개)", text)
        if m and re.search(r"후기|리뷰", text):
            name = _strip_particle(m.group(1))
            return self._call(("get_destination_detail", {"name": name}), ("search_reviews", {"query": name}))

    def _weather(self, text):
        if "날씨" not in text:
            return None
        when, rest = None, text
        for pattern, value in _WHEN_PATTERNS:
            if m := pattern.search(rest):
                when, rest = value, rest[: m.start()] + " " + rest[m.end():]
                break
        region = _last_token(rest[: rest.find("날씨")])
        if not region or region in _DEMONSTRATIVES:
            return text_message(self.texts["WEATHER_CLARIFY_ANSWER"])
        args = {"region": region}
        if when:
            args["when"] = when
        return self._call(("get_weather", args))

    def _reviews(self, text):
        m = re.search(r"후기|리뷰", text)
        if not m:
            return None
        query = _last_token(text[: m.start()])
        if not query or query in _DEMONSTRATIVES:
            return text_message(self.texts["REVIEWS_CLARIFY_ANSWER"])
        return self._call(("search_reviews", {"query": query}))

    def _detail(self, text):
        if m := re.search(r"(\S+?)\s*(?:정보|소개|어떤 곳|어떤 데|뭐 하는 데)", text):
            name = _strip_particle(m.group(1))
            if name and name not in _DEMONSTRATIVES:
                return self._call(("get_destination_detail", {"name": name}))

    def _gloplers(self, text):
        m = re.search(r"글로플러|현지 가이드|가이드|현지 전문가", text)
        if not m:
            return None
        if tag := re.search(r"#(\S+)", text):
            hashtag = _strip_particle(tag.group(1))
        else:
            hashtag = _last_token(text[: m.start()])
        if not hashtag or hashtag in _DEMONSTRATIVES:
            return None
        return self._call(("search_gloplers", {"hashtag": hashtag}))

    def _search(self, text):
        for word, category in self._category_words:
            if (i := text.find(word)) < 0:
                continue
            region = _last_token(text[:i])
            if region and region not in _DEMONSTRATIVES:
                return self._call(("search_destinations", {"region": region, "category": category}))
            if word in contract.KEYWORDS:  # 지역 없는 박물관·축제는 취향 키워드(labeling_rules.md 4절)
                return self._call(("recommend_by_keywords", {"keywords": [word]}))
            josa = "을" if _has_final_consonant(word) else "를"
            return text_message(f"어느 지역의 {word}{josa} 찾아드릴까요?")
        return None

    def _keywords_rule(self, text):
        taken: list[tuple[int, int, str]] = []
        for kw in self._keywords:  # 긴 키워드 먼저(고대 유적 > 유적)
            start = text.find(kw)
            while start >= 0:
                end = start + len(kw)
                if all(end <= s or start >= e for s, e, _ in taken):
                    taken.append((start, end, kw))
                    break
                start = text.find(kw, start + 1)
        if taken:
            return self._call(("recommend_by_keywords", {"keywords": [kw for _, _, kw in sorted(taken)]}))


# --- OracleScript ----------------------------------------------------------

class OracleMiss(KeyError):
    """정답 세트에 없는 발화."""


class RequestShapeError(AssertionError):
    """라우터 요청이 학습 분포(system + 도구 8개 + user 한 마디)와 다름."""


@dataclass(frozen=True)
class OracleCase:
    utterance: str
    answer: dict     # 정답 assistant 메시지(파일 그대로)
    task: str
    source: str      # 파일 이름
    line: int        # 1부터

    @property
    def is_call(self) -> bool:
        return bool(self.answer.get("tool_calls"))

    @property
    def expected_calls(self) -> list[tuple[str, dict]]:
        return [(c["function"]["name"], json.loads(c["function"]["arguments"]))
                for c in self.answer.get("tool_calls") or []]


def default_oracle_paths() -> tuple[Path, ...]:
    return (contract.V2_TEST_PATH, contract.HUMAN_STYLE_PATH)


def load_oracle_cases(paths: Iterable[Path] | None = None, *, skip_missing: bool = False) -> list[OracleCase]:
    """정답 레코드 → OracleCase. 같은 발화에 다른 정답이 있으면 ValueError."""
    cases: list[OracleCase] = []
    seen: dict[str, OracleCase] = {}
    for path in paths or default_oracle_paths():
        path = Path(path)
        if not path.is_file():
            if skip_missing:
                continue
            raise FileNotFoundError(f"정답 파일이 없습니다: {path}")
        for i, record in enumerate(contract.read_jsonl(path), 1):
            msgs = record["messages"]
            if [m["role"] for m in msgs] != ["system", "user", "assistant"]:
                raise ValueError(f"{path.name}:{i} 메시지가 system·user·assistant 셋이 아닙니다")
            case = OracleCase(msgs[1]["content"], msgs[2], record.get("task", ""), path.name, i)
            if (prev := seen.get(case.utterance)) is not None:
                if prev.answer != case.answer:
                    raise ValueError(f"같은 발화에 정답이 둘: {prev.source}:{prev.line}, {path.name}:{i}")
                continue
            seen[case.utterance] = case
            cases.append(case)
    return cases


def oracle_message(answer: dict, *, new_id: IdFactory = new_call_id) -> AIMessage:
    """정답 assistant → 서버가 냈을 응답. arguments 문자열은 dict로, 병렬이면 content에 줄바꿈."""
    calls = answer.get("tool_calls") or []
    if not calls:
        return text_message(answer["content"])
    return tool_call_message([(c["function"]["name"], json.loads(c["function"]["arguments"])) for c in calls],
                             new_id=new_id)


def check_router_request(messages: Sequence[BaseMessage], kwargs: dict) -> None:
    """학습 분포 그대로인지: [System(SYSTEM_PROMPT), Human(한 마디)] + tools == glople_tools.json, 강제 옵션 없음."""
    if len(messages) != 2 or not isinstance(messages[0], SystemMessage) or not isinstance(messages[1], HumanMessage):
        raise RequestShapeError(f"라우터 요청은 [System, Human] 두 개여야 합니다: {[type(m).__name__ for m in messages]}")
    if messages[0].content != contract.SYSTEM_PROMPT:
        raise RequestShapeError("system이 학습 SYSTEM_PROMPT와 다릅니다")
    tools = kwargs.get("tools")
    if tools is None or json.dumps(tools, ensure_ascii=False) != json.dumps(contract.TOOLS, ensure_ascii=False):
        raise RequestShapeError("tools가 glople_tools.json과 다릅니다(순서·키 포함)")
    extra = sorted({"tool_choice", "parallel_tool_calls", "strict"} & set(kwargs))
    if extra:
        raise RequestShapeError(f"학습에 없던 옵션: {extra}")


class OracleScript:
    """정답 assistant를 발화로 찾아 돌려준다(학습한 그대로 답하는 '완벽한 라우터').

    fallback을 주면 못 찾은 발화를 그 스크립트로 넘기고, 없으면 OracleMiss.
    check_request=True면 요청이 학습 분포와 다를 때 RequestShapeError.
    """

    def __init__(self, paths: Iterable[Path] | None = None, *, cases: Sequence[OracleCase] | None = None,
                 fallback: Script | None = None, check_request: bool = False, skip_missing: bool = False,
                 new_id: IdFactory = new_call_id):
        self.cases = list(cases) if cases is not None else load_oracle_cases(paths, skip_missing=skip_missing)
        self.fallback = fallback
        self.check_request = check_request
        self.new_id = new_id
        self._index: dict[str, OracleCase] = {c.utterance: c for c in self.cases}
        for c in self.cases:  # 정제한 문장으로도 찾게(route 노드가 clean_text를 거친 문장을 보냄)
            self._index.setdefault(clean_text(c.utterance), c)

    def __len__(self) -> int:
        return len(self.cases)

    def find(self, utterance: str) -> OracleCase | None:
        return self._index.get(utterance) or self._index.get(clean_text(utterance))

    def __call__(self, messages: list[BaseMessage], kwargs: dict) -> AIMessage:
        if self.check_request:
            check_router_request(messages, kwargs)
        text = last_human_text(messages)
        case = self.find(text)
        if case is None:
            if self.fallback is not None:
                return self.fallback(messages, kwargs)
            raise OracleMiss(text)
        return oracle_message(case.answer, new_id=self.new_id)


# --- QuirkScript -----------------------------------------------------------

class ScriptExhausted(AssertionError):
    """QuirkScript 순서를 다 썼는데 then이 없음."""


DEFAULT_QUIRKS = ("parallel_newline", "dropped", "truncated", "invalid", "invented_arg", "connection_error")
QUIRK_KINDS = (
    "parallel_newline",  # 병렬 2개, content "\n"
    "dropped",           # JSON 깨짐: 호출 없음 + finish_reason=tool_calls
    "truncated",         # max_tokens에서 끊김: finish_reason=length
    "invalid",           # invalid_tool_calls만
    "invalid_partial",   # 정상 호출 1 + invalid 1
    "invented_arg",      # 발화에 없는 age 30을 지어냄(v1 실제 사례)
    "unparsed",          # content에 <tool_call> 텍스트가 남음(읽을 수 있음)
    "unparsed_broken",   # content에 깨진 <tool_call> 텍스트
    "empty",             # 빈 답 + stop
    "args_not_object",   # arguments가 객체가 아닌 JSON → langchain-openai 검증 오류
    "connection_error",  # APIConnectionError
    "timeout",           # APITimeoutError
    "server_error",      # 500
)
_BANAHILL_DETAIL = ("get_destination_detail", {"name": "바나힐"})
_BANAHILL_REVIEWS = ("search_reviews", {"query": "바나힐"})


def quirk_message(kind: str, *, new_id: IdFactory = new_call_id) -> AIMessage:
    """quirk 하나를 서버 응답 모양으로 만든다. 오류 종류는 예외를 던진다."""
    if kind == "parallel_newline":
        return tool_call_message([_BANAHILL_DETAIL, _BANAHILL_REVIEWS], new_id=new_id)
    if kind == "dropped":
        return from_server(None, None, "tool_calls")
    if kind == "truncated":
        return from_server(None, None, "length")
    if kind == "invalid":
        return from_server(None, [server_tool_call("search_reviews", '{"query": "바나', id=new_id())], "tool_calls")
    if kind == "invalid_partial":
        return from_server("\n", [server_tool_call(*_BANAHILL_DETAIL, id=new_id()),
                                  server_tool_call("search_reviews", '{"query": "바나', id=new_id())], "tool_calls")
    if kind == "invented_arg":
        return tool_call_message([("recommend_similar_traveler_routes",
                                   {"age": 30, "gender": "남성", "mbti": "ENTP"})], new_id=new_id)
    if kind == "unparsed":
        return text_message('<tool_call>\n{"name": "get_destination_detail", "arguments": {"name": "바나힐"}}\n'
                            "</tool_call>")
    if kind == "unparsed_broken":
        return text_message('<tool_call>\n{"name": "get_weather", "arguments": {"region": "부산"\n</tool_call>')
    if kind == "empty":
        return from_server(None, None, "stop")
    if kind == "args_not_object":
        return from_server(None, [server_tool_call("get_weather", '"{부산}"', id=new_id())], "tool_calls")
    if kind == "connection_error":
        raise connection_error()
    if kind == "timeout":
        raise timeout_error()
    if kind == "server_error":
        raise server_error(500)
    raise ValueError(f"모르는 quirk: {kind!r} (가능: {', '.join(QUIRK_KINDS)})")


QuirkItem = str | AIMessage | BaseException | Script


class QuirkScript:
    """seq를 호출마다 하나씩 낸다. 항목은 quirk 이름, AIMessage, 예외(던짐), 스크립트 함수.

    다 쓰면 then 스크립트로 넘기고(예: 첫 시도만 깨지고 재시도는 Oracle), then이 없으면 ScriptExhausted.
    """

    def __init__(self, seq: Sequence[QuirkItem] = DEFAULT_QUIRKS, *, then: Script | None = None,
                 new_id: IdFactory = new_call_id):
        for item in seq:
            if isinstance(item, str) and item not in QUIRK_KINDS:
                raise ValueError(f"모르는 quirk: {item!r}")
        self.seq = tuple(seq)
        self.then = then
        self.new_id = new_id
        self.position = 0

    @property
    def remaining(self) -> int:
        return len(self.seq) - self.position

    def __call__(self, messages: list[BaseMessage], kwargs: dict) -> AIMessage:
        if self.position >= len(self.seq):
            if self.then is None:
                raise ScriptExhausted(f"QuirkScript {len(self.seq)}개를 다 썼습니다")
            return self.then(messages, kwargs)
        item = self.seq[self.position]
        self.position += 1
        if isinstance(item, str):
            return quirk_message(item, new_id=self.new_id)
        if isinstance(item, AIMessage):
            return item.model_copy(deep=True)
        if isinstance(item, BaseException):
            raise item
        return item(messages, kwargs)


# --- CassetteScript (M2) ------------------------------------------------------

def _error_from_record(record: dict) -> BaseException:
    kind = record.get("error")
    if kind == "connection":
        return connection_error()
    if kind == "timeout":
        return timeout_error()
    if kind == "server":
        return server_error(int(record.get("status") or 500))
    if kind == "bad_response":
        return json.JSONDecodeError("Expecting value", "", 0)
    if kind == "empty_stream":
        return ValueError("No generation chunks were returned")
    raise ValueError(f"카세트의 모르는 오류 기록: {kind!r}")


def message_from_record(record: dict, *, new_id: IdFactory = new_call_id) -> AIMessage:
    """카세트 응답 기록 → 서버가 냈던 AIMessage(from_server로 다시 만듦: invalid_tool_calls·병렬 "\\n"도 그대로)."""
    raw = [server_tool_call(c["name"], c["arguments"], id=new_id())
           for c in [*(record.get("tool_calls") or []), *(record.get("invalid_tool_calls") or [])]]
    return from_server(record.get("content") or None, raw or None, record.get("finish_reason"))


class CassetteScript:
    """녹화한 실제 서버 응답을 요청 키로 찾아 돌려준다(design.md 6.6). 라우터·답변 가짜 모두에 끼울 수 있다.

        script = CassetteScript("cassettes/demo.json")
        deps = fake_deps(script, answer_mode="llm", answer_llm=fake_answer_model(script))

    녹화에 없는 요청이면 CassetteMiss(KeyError 하위 클래스라 route 노드도 잡지 않고 그대로 올라옴). 오류 기록이면
    녹화 때와 같은 종류의 예외를 낸다. hits에 찾은 키 수를 센다."""

    def __init__(self, cassette: Cassette | str | Path, *, new_id: IdFactory = new_call_id):
        self.cassette = cassette if isinstance(cassette, Cassette) else Cassette.load(cassette)
        self.new_id = new_id
        self.hits = 0

    def __call__(self, messages: list[BaseMessage], kwargs: dict) -> AIMessage:
        record = self.cassette.lookup(messages, kwargs)   # 없으면 CassetteMiss
        self.hits += 1
        if "error" in record:
            raise _error_from_record(record)
        return message_from_record(record, new_id=self.new_id)


# --- ResultEchoScript (M2 가짜 답변 모델) ----------------------------------------

def _result_names(item: dict) -> list[str]:
    for key in ("name", "nickname"):
        if isinstance(item.get(key), str) and item[key]:
            return [item[key]]
    if isinstance(item.get("title"), str) and item["title"]:
        return [f"'{item['title']}'"]
    return [d for d in item.get("destinations") or [] if isinstance(d, str)][:1]


class ResultEchoScript:
    """가짜 답변 모델(CLI --answer llm 데모·그래프 테스트): 요청의 ToolMessage(LLM용 봉투)에서 항목 이름 3개와
    notes만 옮겨 2~3문장으로 답한다. 숫자를 지어내지 않고 이름을 말하므로 가드를 통과한다."""

    def __call__(self, messages: list[BaseMessage], kwargs: dict) -> AIMessage:
        names: list[str] = []
        notes: list[str] = []
        for m in messages:
            if not isinstance(m, ToolMessage) or not isinstance(m.content, str):
                continue
            try:
                body = json.loads(m.content)
            except ValueError:
                continue
            for item in body.get("items") or []:
                if isinstance(item, dict):
                    names += [n for n in _result_names(item) if n not in names]
            notes += [n for n in body.get("notes") or [] if isinstance(n, str)]
        if not names:
            return text_message("결과를 찾았어요. 자세한 내용은 화면 카드에서 볼 수 있어요.")
        shown = ", ".join(names[:3])
        parts = [f"찾은 결과 중에는 {josa(shown, '이/가')} 있어요.", *notes, "자세한 내용은 화면 카드에서 볼 수 있어요."]
        return text_message(" ".join(p if p.endswith((".", "?", "!")) else p + "." for p in parts))
