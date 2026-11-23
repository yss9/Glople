"""BearerToken·AgentContext: 토큰이 문자열·직렬화·state로 새지 않는지."""
import copy
import dataclasses
import json
import pickle

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from pydantic import SecretStr

from glople_agent.context import AgentContext, BearerToken
from glople_agent.state import AgentState


def test_header_has_raw_token(sentinel_token, sentinel_raw):
    assert sentinel_token.header() == {"Authorization": f"Bearer {sentinel_raw}"}


def test_repr_and_str_hide_token(sentinel_token, sentinel_raw):
    assert repr(sentinel_token) == "BearerToken(***)"
    assert str(sentinel_token) == "BearerToken(***)"
    for shown in (f"{sentinel_token}", f"{sentinel_token!r}", "%s" % sentinel_token, repr([sentinel_token]),
                  json.dumps({"t": sentinel_token}, default=str)):
        assert sentinel_raw not in shown


def test_no_secret_value_accessor_and_no_dict(sentinel_token):
    # get_secret_value가 있으면 체크포인트 직렬화기가 평문으로 저장한다
    assert not hasattr(sentinel_token, "get_secret_value")
    assert not hasattr(sentinel_token, "__dict__")
    with pytest.raises(TypeError):
        vars(sentinel_token)


@pytest.mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_pickle_refused(sentinel_token, protocol):
    with pytest.raises(TypeError, match="직렬화하지 않습니다"):
        pickle.dumps(sentinel_token, protocol=protocol)


def test_copy_refused(sentinel_token):
    # __reduce__를 막은 결과 copy도 막힌다(그래프는 context를 복사하지 않음: 아래 invoke 테스트)
    with pytest.raises(TypeError):
        copy.copy(sentinel_token)
    with pytest.raises(TypeError):
        copy.deepcopy(sentinel_token)


@pytest.mark.parametrize("raw", ["", "   "])
def test_rejects_empty_token(raw):
    with pytest.raises(ValueError):
        BearerToken(raw)


@pytest.mark.parametrize("raw", [None, 123, b"bytes-token"])
def test_rejects_non_string_token(raw):
    with pytest.raises(TypeError):
        BearerToken(raw)  # type: ignore[arg-type]


@pytest.mark.parametrize("pickle_fallback", [False, True])
def test_checkpoint_serializer_refuses_token(sentinel_token, pickle_fallback):
    serde = JsonPlusSerializer(pickle_fallback=pickle_fallback)
    with pytest.raises(TypeError):
        serde.dumps_typed({"leak": sentinel_token})


def test_checkpoint_serializer_refuses_context(agent_context):
    # dataclass는 필드별로 직렬화되므로 안의 BearerToken에서 막힌다
    with pytest.raises(TypeError):
        JsonPlusSerializer().dumps_typed(agent_context)


def test_secretstr_would_be_stored_in_plain_text(sentinel_raw):
    # BearerToken을 따로 만든 이유(design.md E4): SecretStr은 평문으로 저장된다
    _, data = JsonPlusSerializer().dumps_typed({"leak": SecretStr(sentinel_raw)})
    assert sentinel_raw.encode() in data


def test_context_is_frozen(agent_context):
    with pytest.raises(dataclasses.FrozenInstanceError):
        agent_context.user_id = "someone-else"  # type: ignore[misc]


def test_context_repr_hides_token(agent_context, sentinel_raw):
    assert "BearerToken(***)" in repr(agent_context)
    assert sentinel_raw not in repr(agent_context)


def test_context_rejects_raw_string_token(fixed_now, sentinel_raw):
    with pytest.raises(TypeError):
        AgentContext(user_id="u1", bearer=sentinel_raw, request_id="r", now=fixed_now, seed=1)  # type: ignore[arg-type]


def test_context_requires_timezone(fixed_now):
    with pytest.raises(ValueError):
        AgentContext(user_id=None, bearer=None, request_id="r", now=fixed_now.replace(tzinfo=None), seed=1)


def _graph(node, saver):
    g = StateGraph(AgentState, context_schema=AgentContext)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    return g.compile(checkpointer=saver)


def _flatten_bytes(obj) -> bytes:
    """InMemorySaver 저장소를 훑어 저장된 바이트·문자열을 모두 이어 붙인다."""
    if isinstance(obj, (bytes, bytearray)):
        return bytes(obj)
    if isinstance(obj, str):
        return obj.encode()
    if isinstance(obj, dict):
        return b"".join(_flatten_bytes(k) + _flatten_bytes(v) for k, v in obj.items())
    if isinstance(obj, (list, tuple)):
        return b"".join(_flatten_bytes(v) for v in obj)
    return repr(obj).encode()


def _config(tid: str) -> dict:
    return {"configurable": {"thread_id": tid}, "recursion_limit": 25}


def test_context_reaches_node_without_leaking(agent_context, sentinel_raw):
    seen = {}

    def node(state: AgentState, runtime: Runtime[AgentContext]):
        seen["header"] = runtime.context.bearer.header()
        return {"final_text": "ok"}

    saver = InMemorySaver()
    out = _graph(node, saver).invoke({"messages": [HumanMessage("안녕")]}, _config("t-ok"),
                                     context=agent_context, version="v2")
    assert out.value["final_text"] == "ok"
    assert seen["header"] == {"Authorization": f"Bearer {sentinel_raw}"}
    stored = _flatten_bytes((saver.storage, saver.writes, saver.blobs))
    assert "안녕".encode() in stored  # 저장 내용을 실제로 훑었는지
    assert sentinel_raw.encode() not in stored


@pytest.mark.parametrize("update", [
    lambda t: {"trace": {"bearer": t}},
    lambda t: {"memory": {"last_region": t}},
    lambda t: {"plan_notice": t},
])
def test_token_in_state_channel_fails_checkpoint(agent_context, update):
    def node(state: AgentState, runtime: Runtime[AgentContext]):
        return update(runtime.context.bearer)

    with pytest.raises(TypeError):
        _graph(node, InMemorySaver()).invoke({"messages": [HumanMessage("안녕")]}, _config("t-leak"),
                                             context=agent_context, version="v2")


def test_context_object_in_state_fails_checkpoint(agent_context):
    def node(state: AgentState, runtime: Runtime[AgentContext]):
        return {"trace": {"ctx": runtime.context}}

    with pytest.raises(TypeError):
        _graph(node, InMemorySaver()).invoke({"messages": [HumanMessage("안녕")]}, _config("t-ctx"),
                                             context=agent_context, version="v2")
