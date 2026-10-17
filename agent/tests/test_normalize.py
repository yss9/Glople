"""서버 quirk 정규화(design.md 6.3 표 한 줄에 테스트 하나 이상).

입력은 mlx_lm.server 응답 모양(dict)을 실제 langchain-openai(ChatOpenAI)로 바꾼 AIMessage다.
네트워크는 쓰지 않는다: _generate가 준비한 응답을 _create_chat_result로 바로 변환한다.
"""
import json

import openai
import pydantic
import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

from glople_agent.llm import fake
from glople_agent.llm.normalize import (
    RETRY_OVERRIDES,
    is_model_connection_error,
    is_model_error,
    is_model_timeout_error,
    normalize_router,
    normalize_router_error,
    parse_tool_call_text,
    retry_overrides,
)

DETAIL = {"name": "get_destination_detail", "args": {"name": "바나힐"}}
REVIEWS = {"name": "search_reviews", "args": {"query": "바나힐"}}


class _CannedChatOpenAI(ChatOpenAI):
    canned: dict | None = None

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._create_chat_result(self.canned)  # 서버로 보내지 않고 준비한 응답을 변환


def _tc(name: str, args, call_id: str) -> dict:
    return fake.server_tool_call(name, args, id=call_id)


def real_message(content, tool_calls=None, finish_reason="stop") -> AIMessage:
    """mlx_lm.server 응답(choices[0]) → ChatOpenAI.invoke가 돌려주는 AIMessage."""
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    response = {
        "id": "chatcmpl-test", "object": "chat.completion", "model": "default_model", "created": 0,
        "system_fingerprint": "test",
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
        "usage": {"prompt_tokens": 1287, "completion_tokens": 20, "total_tokens": 1307},
    }
    llm = _CannedChatOpenAI(model="default_model", api_key="local-not-used", base_url="http://scripted.invalid/v1",
                            max_retries=0, disable_streaming=True, canned=response)
    return llm.invoke([HumanMessage("테스트")])


def _calls(outcome) -> list[dict]:
    return [{"name": c["name"], "args": c["args"]} for c in outcome.tool_calls]


def _json_ok(outcome) -> None:
    json.dumps(outcome.detail, ensure_ascii=False)  # trace(체크포인트)에 넣을 수 있는 값만


# 1. 호출 1개
def test_single_call_is_tool():
    msg = real_message(None, [_tc("get_destination_detail", {"name": "바나힐"}, "id-1")], "tool_calls")
    assert msg.content == "" and msg.response_metadata["finish_reason"] == "tool_calls"
    out = normalize_router(msg)
    assert out.kind == "tool"
    assert _calls(out) == [DETAIL]
    assert out.message.tool_calls[0]["id"] == "id-1"  # 서버 id 그대로(ToolMessage 짝)
    assert out.message.content == "" and out.message.id == msg.id
    assert out.retry_overrides is None
    _json_ok(out)


# 2. 병렬 호출: content "\n" → ""
def test_parallel_newline_content_becomes_empty():
    msg = real_message("\n", [_tc("get_destination_detail", {"name": "바나힐"}, "id-1"),
                              _tc("search_reviews", {"query": "바나힐"}, "id-2")], "tool_calls")
    assert msg.content == "\n"  # 서버 모양 그대로 받은 것
    out = normalize_router(msg)
    assert out.kind == "tool"
    assert out.message.content == ""  # 학습 분포·prefix 캐시 유지
    assert _calls(out) == [DETAIL, REVIEWS]  # 순서 유지
    assert [c["id"] for c in out.tool_calls] == ["id-1", "id-2"]
    assert out.detail["content_dropped"] == "\n"
    assert msg.content == "\n"  # 입력 메시지는 고치지 않음


def test_three_parallel_calls_and_preamble_text_are_dropped():
    calls = [_tc("get_weather", {"region": "군산", "when": "이번 주말"}, "a"),
             _tc("search_destinations", {"region": "군산", "category": "음식점"}, "b"),
             _tc("search_reviews", {"query": "군산"}, "c")]
    out = normalize_router(real_message("\n\n", calls, "tool_calls"))
    assert out.kind == "tool" and len(out.tool_calls) == 3 and out.message.content == ""
    # 호출 앞에 설명 문장을 붙인 경우도 content는 비운다
    out = normalize_router(real_message("바나힐 정보를 찾아볼게요.\n", [_tc(**DETAIL, call_id="x")], "tool_calls"))
    assert out.kind == "tool" and out.message.content == ""
    assert out.detail["content_dropped"].startswith("바나힐 정보를")


# 3. JSON 깨짐: 서버가 호출을 버리고 finish_reason=tool_calls만 남김
def test_broken_json_is_dropped_and_retried_with_temperature():
    msg = real_message(None, None, "tool_calls")
    assert msg.content == "" and msg.tool_calls == [] and msg.invalid_tool_calls == []
    out = normalize_router(msg)
    assert out.kind == "dropped" and out.message is None
    assert out.detail["reason"] == "empty_tool_calls"
    assert out.retry_overrides == {"temperature": 0.2}
    # 병렬 둘 다 깨지면 블록 사이 줄바꿈만 남는다
    assert normalize_router(real_message("\n", None, "tool_calls")).kind == "dropped"


# 4. ChatOpenAI 파싱 실패: invalid_tool_calls
def test_invalid_tool_calls_only_is_dropped():
    msg = real_message(None, [_tc("search_reviews", '{"query": "바나', "bad")], "tool_calls")
    assert msg.tool_calls == [] and len(msg.invalid_tool_calls) == 1
    out = normalize_router(msg)
    assert out.kind == "dropped" and out.detail["reason"] == "invalid_tool_calls"
    assert out.detail["invalid"][0]["name"] == "search_reviews"
    assert out.retry_overrides == {"temperature": 0.2}
    _json_ok(out)


def test_invalid_with_valid_call_keeps_only_valid():
    msg = real_message("\n", [_tc("get_destination_detail", {"name": "바나힐"}, "ok"),
                              _tc("search_reviews", '{"query": "바나', "bad")], "tool_calls")
    out = normalize_router(msg)
    assert out.kind == "tool"
    assert _calls(out) == [DETAIL]
    assert out.message.invalid_tool_calls == []  # 다음 요청에 짝 없는 호출이 섞이지 않게
    assert [i["name"] for i in out.detail["invalid"]] == ["search_reviews"]


def test_arguments_not_object_raises_in_chat_openai_and_is_dropped():
    # arguments가 JSON 문자열·배열이면 invalid_tool_calls가 아니라 AIMessage 검증 오류(docs/api-notes.md)
    with pytest.raises(pydantic.ValidationError) as info:
        real_message(None, [_tc("get_weather", '"{부산}"', "s")], "tool_calls")
    assert is_model_error(info.value)
    out = normalize_router(info.value)
    assert out.kind == "dropped" and out.detail["reason"] == "args_not_object"
    assert out.retry_overrides == {"temperature": 0.2}
    _json_ok(out)


# 5. max_tokens에서 끊김
def test_length_is_truncated_and_retried_with_more_tokens():
    out = normalize_router(real_message(None, None, "length"))
    assert out.kind == "truncated" and out.message is None
    assert out.retry_overrides == {"max_tokens": 768}


def test_length_with_complete_first_call_is_still_truncated():
    # 병렬 중 뒤 호출만 잘림: 앞 호출만 쓰면 요청이 빠지므로 다시 받는다
    out = normalize_router(real_message(None, [_tc(**DETAIL, call_id="a")], "length"))
    assert out.kind == "truncated"
    assert out.detail["partial_tool_calls"] == [DETAIL]


def test_long_text_cut_by_length_is_truncated():
    assert normalize_router(real_message("어느 지역의 관광지를 찾아", None, "length")).kind == "truncated"


# 6. content에 <tool_call> 텍스트가 남음 → 직접 파싱
def test_tool_call_text_is_parsed_into_tool():
    text = ('<tool_call>\n{"name": "get_destination_detail", "arguments": {"name": "바나힐"}}\n</tool_call>\n'
            '<tool_call>\n{"name": "search_reviews", "arguments": "{\\"query\\": \\"바나힐\\"}"}\n</tool_call>')
    ids = iter(["p1", "p2"])
    out = normalize_router(real_message(text), new_id=lambda: next(ids))
    assert out.kind == "tool"
    assert _calls(out) == [DETAIL, REVIEWS]  # arguments 문자열도 evaluate.py처럼 한 번 더 읽음
    assert [c["id"] for c in out.tool_calls] == ["p1", "p2"]
    assert out.message.content == ""
    assert out.detail["text_parse"] == {"calls": 2, "invalid": 0}


def test_broken_tool_call_text_is_unparsed():
    broken = '<tool_call>\n{"name": "get_weather", "arguments": {"region": "부산"\n</tool_call>'
    out = normalize_router(real_message(broken))
    assert out.kind == "unparsed" and out.message is None
    assert out.retry_overrides == {"temperature": 0.2}
    unclosed = '<tool_call>\n{"name": "get_weather", "arguments": {"region": "부산"}}'
    assert normalize_router(real_message(unclosed)).kind == "unparsed"
    assert normalize_router(real_message(unclosed, None, "length")).kind == "truncated"


def test_parse_tool_call_text_matches_evaluate_rules():
    ok = '<tool_call>{"name": "a", "arguments": {}}</tool_call>'
    assert parse_tool_call_text(ok) == ([{"name": "a", "arguments": {}}], 0)
    assert parse_tool_call_text('<tool_call>[1]</tool_call>') == ([], 1)          # 객체가 아님
    assert parse_tool_call_text('<tool_call>{"name": 1}</tool_call>') == ([], 1)  # 이름이 문자열 아님
    assert parse_tool_call_text(ok + "<tool_call>{") == ([{"name": "a", "arguments": {}}], 1)  # 안 닫힘


# 7. 연결 끊김·타임아웃
def test_connection_error_is_error_and_retried_by_policy():
    exc = fake.connection_error()
    assert is_model_connection_error(exc) and not is_model_timeout_error(exc)
    out = normalize_router(exc)
    assert out.kind == "error" and out.message is None
    assert out.detail["error"] == "connection" and out.detail["retry_policy"] is True
    assert out.retry_overrides is None
    _json_ok(out)


def test_timeout_is_error_without_retry():
    exc = fake.timeout_error()
    assert isinstance(exc, openai.APIConnectionError)  # 타임아웃은 연결 오류의 하위 클래스
    assert not is_model_connection_error(exc) and is_model_timeout_error(exc)
    out = normalize_router_error(exc)
    assert out.kind == "error" and out.detail["error"] == "timeout" and out.detail["retry_policy"] is False


def test_server_status_and_unknown_errors():
    out = normalize_router_error(fake.server_error(500))
    assert out.kind == "error" and out.detail["error"] == "server" and out.detail["status_code"] == 500
    assert not is_model_connection_error(fake.server_error(500))
    bug = KeyError("우리 코드 버그")
    assert not is_model_error(bug)  # route 노드가 삼키면 안 되는 예외
    assert normalize_router_error(bug).detail["error"] == "unknown"


# 8. 텍스트(되묻기·거절·인사)
def test_text_answer_is_text():
    msg = real_message("어느 지역 날씨를 알려드릴까요?\n")
    out = normalize_router(msg)
    assert out.kind == "text"
    assert out.text == "어느 지역 날씨를 알려드릴까요?"
    assert out.message.tool_calls == [] and out.message.id == msg.id
    assert out.retry_overrides is None


def test_empty_text_is_dropped():
    out = normalize_router(real_message(None))
    assert out.kind == "dropped" and out.detail["reason"] == "empty_text"


def test_retry_override_table_and_copies():
    assert set(RETRY_OVERRIDES) == {"dropped", "unparsed", "truncated"}
    got = retry_overrides("truncated")
    got["max_tokens"] = 1
    assert retry_overrides("truncated") == {"max_tokens": 768}  # 표를 고치지 않음
    for kind in ("tool", "text", "error"):
        assert retry_overrides(kind) is None


def test_rejects_non_message():
    with pytest.raises(TypeError):
        normalize_router("텍스트")
