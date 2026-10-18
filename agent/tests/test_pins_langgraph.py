"""LangGraph·langchain-core 동작 고정 테스트(design.md 부록 A, 14.2절).

설치 버전(requirements.lock)에서 그래프 코드가 기대는 동작을 최소 예제로 확인한다.
버전을 올려 여기가 깨지면 docs/api-notes.md와 설계의 해당 절을 먼저 고친다.
우리 모듈(graph·tools·llm)에는 기대지 않는다. 쓰는 것은 conftest 픽스처뿐.
"""
import json
import logging
import operator
import threading
import time
from dataclasses import dataclass
from importlib import metadata
from typing import Annotated, Any, Literal

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import (AIMessage, AIMessageChunk, AnyMessage, HumanMessage,
                                     RemoveMessage, ToolMessage)
from langchain_core.tools import StructuredTool
from langgraph import errors as lg_errors
from langgraph._internal._constants import CONFIG_KEY_RUNTIME
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.config import get_stream_writer
from langgraph.errors import GraphRecursionError, InvalidUpdateError
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.managed import RemainingSteps
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.prebuilt.tool_node import ToolInvocationError
from langgraph.runtime import Runtime
from langgraph.types import Command, GraphOutput, Overwrite, RetryPolicy, interrupt
from langgraph.warnings import LangGraphDeprecatedSinceV11
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from typing_extensions import TypedDict


# ---------------------------------------------------------------- 공용 도우미


class MsgState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]


class LogState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    log: Annotated[list[str], operator.add]
    remaining_steps: RemainingSteps


class QueryArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str


class DaysArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: int


def _cfg(tid: str, **configurable) -> dict:
    return {"configurable": {"thread_id": tid, **configurable}, "recursion_limit": 25}


def _call(name: str, args: dict, cid: str) -> dict:
    return {"name": name, "args": args, "id": cid, "type": "tool_call"}


def _artifact_tool(func, name: str, schema=QueryArgs) -> StructuredTool:
    return StructuredTool.from_function(func, name=name, description=f"{name} 설명", args_schema=schema,
                                        response_format="content_and_artifact")


def _run_tools(tools, calls: list[dict], **toolnode_kw) -> list[ToolMessage]:
    """ToolNode를 한 노드짜리 그래프에 얹어 실행하고 새로 생긴 ToolMessage만 돌려준다."""
    g = StateGraph(MsgState)
    g.add_node("tools", ToolNode(tools, **toolnode_kw))
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    out = g.compile().invoke({"messages": [AIMessage("", tool_calls=calls)]}, {"recursion_limit": 25})
    return out["messages"][1:]


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


def _saver_bytes(saver: InMemorySaver) -> bytes:
    return _flatten_bytes((saver.storage, saver.writes, saver.blobs))


def test_installed_versions_match_lock():
    # 아래 고정 내용은 이 버전에서 확인한 것
    expected = {"langgraph": "1.2.12", "langgraph-prebuilt": "1.1.0", "langgraph-checkpoint": "4.2.0",
                "langchain-core": "1.6.6", "pydantic": "2.13.5"}
    assert {name: metadata.version(name) for name in expected} == expected


# ---------------------------------------------------------------- A1 ToolNode


def test_toolnode_runs_content_and_artifact_tool():
    def search_reviews(query: str):
        content = json.dumps({"tool": "search_reviews", "status": "ok", "items": [query]}, ensure_ascii=False)
        return content, {"card_id": "post:1", "body": "카드에만 들어갈 원문"}

    [msg] = _run_tools([_artifact_tool(search_reviews, "search_reviews")],
                       [_call("search_reviews", {"query": "바나힐"}, "call_1")])
    assert isinstance(msg, ToolMessage)
    assert (msg.tool_call_id, msg.name, msg.status) == ("call_1", "search_reviews", "success")
    assert json.loads(msg.content) == {"tool": "search_reviews", "status": "ok", "items": ["바나힐"]}
    assert msg.artifact == {"card_id": "post:1", "body": "카드에만 들어갈 원문"}
    assert "카드에만" not in msg.content  # artifact는 content에 섞이지 않음


def test_content_and_artifact_tool_must_return_tuple():
    # 두 값 튜플이 아니면 ValueError. 기본 오류 처리는 이것을 다시 던져 그래프가 멈춘다
    def bad(query: str):
        return "content only"

    with pytest.raises(ValueError, match="two-tuple"):
        _run_tools([_artifact_tool(bad, "bad")], [_call("bad", {"query": "x"}, "c1")])


def test_toolnode_coerces_args_with_pydantic_schema():
    # 실행 쪽 Pydantic은 느슨한 모드: "3" → 3. 정수 검사는 validate 노드가 먼저 해야 함
    got = []

    def plan(days: int):
        got.append(days)
        return "ok", {}

    tool = _artifact_tool(plan, "plan", DaysArgs)
    [ok] = _run_tools([tool], [_call("plan", {"days": "3"}, "c1")])
    [bad] = _run_tools([tool], [_call("plan", {"days": "셋"}, "c2")])
    assert got == [3] and ok.status == "success"
    assert bad.status == "error" and got == [3]


def test_toolnode_parallel_calls_run_together_and_keep_call_order():
    barrier = threading.Barrier(2, timeout=5)  # 차례로 실행되면 여기서 BrokenBarrierError

    def slow(query: str):
        barrier.wait()
        time.sleep(0.2)
        return f"slow:{query}", {"q": query}

    def fast(query: str):
        barrier.wait()
        return f"fast:{query}", {"q": query}

    msgs = _run_tools([_artifact_tool(slow, "slow"), _artifact_tool(fast, "fast")],
                      [_call("slow", {"query": "a"}, "p1"), _call("fast", {"query": "b"}, "p2")])
    assert [(m.tool_call_id, m.content) for m in msgs] == [("p1", "slow:a"), ("p2", "fast:b")]


def _boom(query: str):
    raise RuntimeError("upstream down")


def _ok(query: str):
    return f"ok:{query}", {"q": query}


def test_toolnode_default_error_handling():
    tools = [_artifact_tool(_boom, "boom"), _artifact_tool(_ok, "ok")]
    # 인자 검증 실패(ToolInvocationError) → ToolMessage(error)
    [bad_args] = _run_tools(tools, [_call("ok", {"query": "x", "extra": 1}, "c1")])
    assert bad_args.status == "error" and "extra" in bad_args.content and bad_args.artifact is None
    # 없는 도구 이름 → ToolMessage(error)
    [unknown] = _run_tools(tools, [_call("nope", {}, "c2")])
    assert unknown.status == "error" and "is not a valid tool" in unknown.content
    # 일반 예외 → 다시 던짐(그래프가 죽음). 그래서 tool_error_to_json을 꼭 넘긴다
    with pytest.raises(RuntimeError, match="upstream down"):
        _run_tools(tools, [_call("boom", {"query": "x"}, "c3")])


def test_toolnode_callable_error_handler_returns_tool_message():
    seen: list[type] = []

    def to_json(e: Exception) -> str:
        seen.append(type(e))
        return json.dumps({"status": "error", "error": type(e).__name__}, ensure_ascii=False)

    msgs = _run_tools([_artifact_tool(_boom, "boom"), _artifact_tool(_ok, "ok")],
                      [_call("boom", {"query": "x"}, "c1"), _call("ok", {"query": "y", "extra": 1}, "c2"),
                       _call("ok", {"query": "z"}, "c3")],
                      handle_tool_errors=to_json)
    assert [m.tool_call_id for m in msgs] == ["c1", "c2", "c3"]
    assert [m.status for m in msgs] == ["error", "error", "success"]
    assert json.loads(msgs[0].content) == {"status": "error", "error": "RuntimeError"}
    # 인자 검증 실패도 같은 함수로 온다 → 함수 안에서 ToolInvocationError를 bad_request로 나눠야 함
    assert json.loads(msgs[1].content) == {"status": "error", "error": "ToolInvocationError"}
    assert seen == [RuntimeError, ToolInvocationError]
    assert msgs[0].artifact is None and msgs[2].artifact == {"q": "z"}  # 다른 호출의 결과는 그대로


def test_toolnode_handler_annotation_limits_caught_types():
    # 함수의 첫 인자 타입 주석으로 잡을 예외를 정한다. 좁게 적으면 나머지는 다시 던짐
    def only_timeout(e: TimeoutError) -> str:
        return "timeout"

    def slow_tool(query: str):
        raise TimeoutError("read timeout")

    [msg] = _run_tools([_artifact_tool(slow_tool, "slow")], [_call("slow", {"query": "x"}, "c1")],
                       handle_tool_errors=only_timeout)
    assert (msg.status, msg.content) == ("error", "timeout")
    with pytest.raises(RuntimeError):
        _run_tools([_artifact_tool(_boom, "boom")], [_call("boom", {"query": "x"}, "c2")],
                   handle_tool_errors=only_timeout)


def test_toolnode_needs_graph_runtime():
    # ToolNode는 그래프 밖에서 바로 invoke하면 실패한다(Runtime 주입 필요). 단위 테스트도 그래프에 얹을 것
    node = ToolNode([_artifact_tool(_ok, "ok")])
    with pytest.raises(ValueError, match="Missing required config key"):
        node.invoke({"messages": [AIMessage("", tool_calls=[_call("ok", {"query": "x"}, "c1")])]})


def test_tools_condition_ignores_invalid_tool_calls():
    broken = AIMessage("", invalid_tool_calls=[{"name": "search_reviews", "args": '{"query": "바나',
                                                "id": "i1", "error": "JSON 깨짐", "type": "invalid_tool_call"}])
    good = AIMessage("", tool_calls=[_call("search_reviews", {"query": "바나힐"}, "c1")])
    # 깨진 호출만 있으면 END로 감 → 재시도 판단은 우리 normalize·after_route가 함
    assert tools_condition({"messages": [broken]}) == "__end__"
    assert tools_condition({"messages": [good]}) == "tools"


def test_tool_message_artifact_survives_checkpoint():
    # STRICT msgpack에서도 artifact(dict)가 체크포인트를 왕복함 → 재개 뒤 collect가 읽을 수 있음
    def node(state):
        return {"messages": [AIMessage("", tool_calls=[_call("t", {}, "c1")]),
                             ToolMessage('{"status": "ok"}', tool_call_id="c1", name="t",
                                         artifact={"card": {"body": "원문"}})]}

    g = StateGraph(MsgState)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    app = g.compile(checkpointer=InMemorySaver())
    app.invoke({"messages": [HumanMessage("x")]}, _cfg("t-art"))
    assert app.get_state(_cfg("t-art")).values["messages"][-1].artifact == {"card": {"body": "원문"}}


# ---------------------------------------------------------------- A5 리듀서


def test_add_messages_replaces_same_id_in_place():
    left = [HumanMessage("질문", id="h1"), AIMessage("원출력", id="r1"), AIMessage("뒤", id="o1")]
    merged = add_messages(left, [AIMessage("정리본", id="r1")])
    assert [(m.id, m.content) for m in merged] == [("h1", "질문"), ("r1", "정리본"), ("o1", "뒤")]
    assert left[1].content == "원출력"  # 원래 목록은 그대로
    # id가 없는 새 메시지에는 id를 붙여 뒤에 덧붙임
    appended = add_messages(left, [AIMessage("새 답")])
    assert appended[-1].content == "새 답" and appended[-1].id


def test_add_messages_replace_and_remove_inside_graph():
    def route(state):
        return {"messages": [AIMessage("", id="r1", tool_calls=[_call("a", {}, "c1"), _call("b", {}, "c2")])]}

    def validate(state):
        return {"messages": [AIMessage("", id="r1", tool_calls=[_call("a", {}, "c1")])]}  # 통과한 호출만

    def drop(state):
        return {"messages": [RemoveMessage(id="r1")]}  # 전부 탈락이면 삭제

    g = StateGraph(MsgState)
    for name, fn in [("route", route), ("validate", validate), ("drop", drop)]:
        g.add_node(name, fn)
    g.add_edge(START, "route")
    g.add_edge("route", "validate")
    g.add_edge("validate", "drop")
    g.add_edge("drop", END)
    app = g.compile(checkpointer=InMemorySaver())
    app.invoke({"messages": [HumanMessage("질문", id="h1")]}, _cfg("t-msg"))
    history = {s.metadata["step"]: s.values["messages"] for s in app.get_state_history(_cfg("t-msg"))}
    assert [len(m.tool_calls) for m in history[2] if isinstance(m, AIMessage)] == [1]  # validate 뒤 교체
    assert [m.id for m in history[2]] == ["h1", "r1"]
    assert [m.id for m in history[3]] == ["h1"]  # drop 뒤 삭제


def test_remove_message_with_unknown_id_raises():
    def drop(state):
        return {"messages": [RemoveMessage(id="없는-id")]}

    g = StateGraph(MsgState)
    g.add_node("drop", drop)
    g.add_edge(START, "drop")
    g.add_edge("drop", END)
    with pytest.raises(ValueError, match="doesn't exist"):
        g.compile().invoke({"messages": [HumanMessage("x")]}, {"recursion_limit": 25})


def test_overwrite_clears_operator_add_channel_each_turn():
    def start_turn(state):
        return {"log": Overwrite([])}

    def tools(state):
        return {"log": [f"call:{state['messages'][-1].content}"]}

    g = StateGraph(LogState)
    g.add_node("start_turn", start_turn)
    g.add_node("tools", tools)
    g.add_edge(START, "start_turn")
    g.add_edge("start_turn", "tools")
    g.add_edge("tools", END)
    app = g.compile(checkpointer=InMemorySaver())
    cfg = _cfg("t-ow")
    assert app.invoke({"messages": [HumanMessage("1")], "log": ["입력값"]}, cfg)["log"] == ["call:1"]
    assert app.invoke({"messages": [HumanMessage("2")]}, cfg)["log"] == ["call:2"]  # 지난 턴 값이 남지 않음
    assert app.get_state(cfg).values["log"] == ["call:2"]


def test_two_overwrites_in_one_step_raise():
    g = StateGraph(LogState)
    g.add_node("a", lambda s: {"log": Overwrite(["a"])})
    g.add_node("b", lambda s: {"log": Overwrite(["b"])})
    g.add_edge(START, "a")
    g.add_edge(START, "b")
    g.add_edge("a", END)
    g.add_edge("b", END)
    with pytest.raises(InvalidUpdateError):
        g.compile().invoke({}, {"recursion_limit": 25})


# ---------------------------------------------------------------- RemainingSteps·recursion_limit


def _chain(n: int):
    """노드 n개를 한 줄로 잇고 각 노드가 본 remaining_steps를 log에 남긴다."""
    g = StateGraph(LogState)
    names = [f"n{i}" for i in range(n)]
    for name in names:
        g.add_node(name, lambda s, name=name: {"log": [f"{name}:{s['remaining_steps']}"]})
    g.add_edge(START, names[0])
    for a, b in zip(names, names[1:]):
        g.add_edge(a, b)
    g.add_edge(names[-1], END)
    return g.compile()


def test_remaining_steps_counts_down_from_limit_minus_one():
    out = _chain(3).invoke({}, {"recursion_limit": 25})
    assert out["log"] == ["n0:24", "n1:23", "n2:22"]


def test_last_node_needs_one_remaining_step():
    # remaining_steps가 0인 노드는 실행되지만, 다음이 END여도 그 뒤에 GraphRecursionError가 난다.
    # → 노드 n개짜리 경로는 recursion_limit ≥ n+1, "앞으로 k개 더"는 remaining_steps ≥ k+1이어야 함
    assert _chain(3).invoke({}, {"recursion_limit": 4})["log"] == ["n0:3", "n1:2", "n2:1"]
    with pytest.raises(GraphRecursionError):
        _chain(3).invoke({}, {"recursion_limit": 3})


def test_self_loop_stops_with_graph_recursion_error():
    seen: list[int] = []

    def loop(state):
        seen.append(state["remaining_steps"])
        return {}

    g = StateGraph(LogState)
    g.add_node("route", loop)
    g.add_edge(START, "route")
    g.add_edge("route", "route")
    with pytest.raises(GraphRecursionError) as err:
        g.compile().invoke({}, {"recursion_limit": 5})
    assert isinstance(err.value, RecursionError)
    assert seen == [4, 3, 2, 1, 0]


def test_retry_guard_with_remaining_steps_reaches_end():
    # after_route 방식: 남은 단계가 넉넉할 때만 자기 루프, 아니면 마무리 노드로
    def route(state):
        return {"log": [f"route:{state['remaining_steps']}"]}

    def after_route(state) -> str:
        return "route" if state["remaining_steps"] > 3 else "fallback"

    g = StateGraph(LogState)
    g.add_node("route", route)
    g.add_node("fallback", lambda s: {"log": [f"fallback:{s['remaining_steps']}"]})
    g.add_node("finish", lambda s: {"log": [f"finish:{s['remaining_steps']}"]})
    g.add_edge(START, "route")
    g.add_conditional_edges("route", after_route, ["route", "fallback"])
    g.add_edge("fallback", "finish")
    g.add_edge("finish", END)
    log = g.compile().invoke({}, {"recursion_limit": 8})["log"]
    assert log == ["route:7", "route:6", "route:5", "route:4", "route:3", "fallback:2", "finish:1"]


def test_recursion_limit_must_be_top_level():
    # 주지 않으면 기본 10007(사실상 무제한). configurable 안에 넣으면 무시됨
    assert _chain(1).invoke({}, {})["log"] == ["n0:10006"]
    assert _chain(1).invoke({}, {"configurable": {"recursion_limit": 5}})["log"] == ["n0:10006"]
    assert _chain(1).invoke({}, {"recursion_limit": 5})["log"] == ["n0:4"]


# ---------------------------------------------------------------- RetryPolicy


def test_retry_policy_max_attempts_counts_total_runs():
    attempts: list[int] = []

    def flaky(state):
        attempts.append(1)
        if len(attempts) == 1:
            raise ConnectionError("끊김")
        return {"log": ["ok"]}

    def always_down(state):
        attempts.append(1)
        raise ConnectionError("계속 끊김")

    policy = RetryPolicy(max_attempts=2, initial_interval=0.01, jitter=False, retry_on=ConnectionError)
    for fn in (flaky, always_down):
        g = StateGraph(LogState)
        g.add_node("route", fn, retry_policy=policy)
        g.add_edge(START, "route")
        g.add_edge("route", END)
        attempts.clear()
        if fn is flaky:
            assert g.compile().invoke({}, {"recursion_limit": 25})["log"] == ["ok"]
        else:
            with pytest.raises(ConnectionError):
                g.compile().invoke({}, {"recursion_limit": 25})
        assert len(attempts) == 2  # max_attempts는 첫 실행을 포함한 총 횟수


# ---------------------------------------------------------------- Command(goto)·destinations


def test_destinations_draw_edges_and_command_goto_routes():
    def prep(state) -> Command[Literal["review"]]:  # 반환 주석으로도 목적지를 알림
        return Command(goto="review", update={"log": ["prep"]})

    def review(state):
        return Command(goto="save", update={"log": ["review"]})

    g = StateGraph(LogState)
    g.add_node("prep", prep)
    g.add_node("review", review, destinations=("save", "prep", "finish"))
    g.add_node("save", lambda s: {"log": ["save"]})
    g.add_node("finish", lambda s: {"log": ["finish"]})
    g.add_edge(START, "prep")
    g.add_edge("save", "finish")
    g.add_edge("finish", END)
    app = g.compile()
    mermaid = app.get_graph().draw_mermaid()
    for edge in ("prep -.-> review", "review -.-> save", "review -.-> prep", "review -.-> finish"):
        assert edge in mermaid
    assert app.invoke({}, {"recursion_limit": 25})["log"] == ["prep", "review", "save", "finish"]


def test_goto_outside_destinations_still_runs():
    # destinations는 그림용 선언일 뿐 실행 때 막지 않는다
    g = StateGraph(LogState)
    g.add_node("review", lambda s: Command(goto="finish", update={"log": ["review"]}), destinations=("save",))
    g.add_node("save", lambda s: {"log": ["save"]})
    g.add_node("finish", lambda s: {"log": ["finish"]})
    g.add_edge(START, "review")
    g.add_edge("save", END)
    g.add_edge("finish", END)
    assert g.compile().invoke({}, {"recursion_limit": 25})["log"] == ["review", "finish"]


def test_goto_unknown_node_is_ignored_with_warning(caplog):
    # 오타 난 goto는 예외 없이 무시되고 그래프가 그냥 끝난다 → goto 이름은 테스트로 지켜야 함
    g = StateGraph(LogState)
    g.add_node("review", lambda s: Command(goto="sav_plan", update={"log": ["review"]}))
    g.add_edge(START, "review")
    with caplog.at_level(logging.WARNING):
        out = g.compile().invoke({}, {"recursion_limit": 25})
    assert out["log"] == ["review"]
    assert "unknown channel branch:to:sav_plan" in caplog.text


def test_command_goto_adds_to_static_edges():
    # 정적 엣지가 있는 노드에서 Command(goto)를 내면 둘 다 실행됨(대체가 아님) → Command 노드에는 엣지를 달지 않음
    g = StateGraph(LogState)
    g.add_node("a", lambda s: Command(goto="b", update={"log": ["a"]}))
    g.add_node("b", lambda s: {"log": ["b"]})
    g.add_node("c", lambda s: {"log": ["c"]})
    g.add_edge(START, "a")
    g.add_edge("a", "c")
    g.add_edge("b", END)
    g.add_edge("c", END)
    assert sorted(g.compile().invoke({}, {"recursion_limit": 25})["log"]) == ["a", "b", "c"]


# ---------------------------------------------------------------- 사람 확인 그래프(interrupt)


class Draft(BaseModel):
    region: str
    days: int


class Decision(BaseModel):
    decision: Literal["approve", "edit", "reject"]
    draft: Draft | None = None


class PlanState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    log: Annotated[list[str], operator.add]
    remaining_steps: RemainingSteps


@dataclass
class PlainCtx:
    """평문 문자열을 담은 context. 체크포인트에 저장된다면 바이트에서 바로 보인다."""
    user_id: str
    secret: str


class Recorder:
    def __init__(self):
        self.events: list[tuple] = []
        self.answers: list[Any] = []

    def count(self, name: str) -> int:
        return sum(1 for e in self.events if e[0] == name)


def _plan_graph(rec: Recorder, saver: InMemorySaver):
    """plan_prepare → review(interrupt) → save_plan → finish. review는 edit면 plan_prepare로 돌아감."""

    def plan_prepare(state, runtime: Runtime[PlainCtx]):
        rec.events.append(("plan_prepare", runtime.context, state["remaining_steps"]))
        return {"log": ["plan_prepare"]}

    def review(state, runtime: Runtime[PlainCtx]) -> Command[Literal["save_plan", "plan_prepare", "finish"]]:
        rec.events.append(("review", runtime.context, state["remaining_steps"]))  # interrupt 앞(부작용 없음)
        ans = interrupt({"type": "plan_confirm", "draft": {"region": "제주", "days": 3}},
                        response_schema=Decision)
        rec.answers.append(ans)
        if ans.decision == "approve":
            return Command(goto="save_plan", update={"log": ["review:approve"]})
        if ans.decision == "edit":
            return Command(goto="plan_prepare", update={"log": ["review:edit"]})
        return Command(goto="finish", update={"log": ["review:reject"]})

    def save_plan(state, runtime: Runtime[PlainCtx]):
        rec.events.append(("save_plan", runtime.context, state["remaining_steps"]))
        return {"log": ["save_plan"]}

    g = StateGraph(PlanState, context_schema=PlainCtx)
    g.add_node("plan_prepare", plan_prepare)
    g.add_node("review", review)
    g.add_node("save_plan", save_plan)
    g.add_node("finish", lambda s: {"log": ["finish"]})
    g.add_edge(START, "plan_prepare")
    g.add_edge("plan_prepare", "review")
    g.add_edge("save_plan", "finish")
    g.add_edge("finish", END)
    return g.compile(checkpointer=saver)


@pytest.fixture
def plan_run(sentinel_raw):
    rec, saver = Recorder(), InMemorySaver()
    app = _plan_graph(rec, saver)
    ctx = PlainCtx(user_id="u1", secret=sentinel_raw)
    return rec, saver, app, ctx


def test_invoke_v2_returns_graph_output_with_interrupts(plan_run):
    rec, _, app, ctx = plan_run
    out = app.invoke({"messages": [HumanMessage("제주 일정 저장")]}, _cfg("t-v2"), context=ctx, version="v2")
    assert isinstance(out, GraphOutput)
    assert out.value["log"] == ["plan_prepare"]
    [intr] = out.interrupts
    assert intr.value == {"type": "plan_confirm", "draft": {"region": "제주", "days": 3}}
    # response_schema는 클라이언트용 JSON Schema(dict)로 실려 나옴
    assert intr.response_schema == TypeAdapter(Decision).json_schema()
    with pytest.warns(LangGraphDeprecatedSinceV11):
        assert out["__interrupt__"] == out.interrupts  # 예전 방식 접근은 경고


def test_invoke_default_version_returns_plain_dict(plan_run):
    _, _, app, ctx = plan_run
    out = app.invoke({"messages": [HumanMessage("x")]}, _cfg("t-v1"), context=ctx)
    assert isinstance(out, dict) and "__interrupt__" in out  # 그래서 항상 version="v2"를 넘긴다


def test_get_state_interrupts_holds_only_pending(plan_run):
    rec, _, app, ctx = plan_run
    cfg = _cfg("t-pending")
    first = app.invoke({"messages": [HumanMessage("x")]}, cfg, context=ctx, version="v2")
    snap = app.get_state(cfg)
    assert [i.id for i in snap.interrupts] == [first.interrupts[0].id] and snap.next == ("review",)

    # edit → plan_prepare → review: 같은 노드라도 새 interrupt는 id가 다르고, 대기 목록에는 새 것만
    second = app.invoke(Command(resume={"decision": "edit", "draft": {"region": "제주", "days": 2}}), cfg,
                        context=ctx, version="v2")
    snap = app.get_state(cfg)
    assert second.interrupts[0].id != first.interrupts[0].id
    assert [i.id for i in snap.interrupts] == [second.interrupts[0].id]

    done = app.invoke(Command(resume={"decision": "approve"}), cfg, context=ctx, version="v2")
    assert done.interrupts == () and app.get_state(cfg).interrupts == () and app.get_state(cfg).next == ()
    assert done.value["log"] == ["plan_prepare", "review:edit", "plan_prepare", "review:approve",
                                 "save_plan", "finish"]


def test_interrupt_response_schema_returns_validated_model(plan_run):
    rec, _, app, ctx = plan_run
    cfg = _cfg("t-schema")
    app.invoke({"messages": [HumanMessage("x")]}, cfg, context=ctx, version="v2")
    app.invoke(Command(resume={"decision": "edit", "draft": {"region": "제주", "days": "3"}}), cfg,
               context=ctx, version="v2")
    [ans] = rec.answers
    assert isinstance(ans, Decision) and isinstance(ans.draft, Draft)
    assert ans.draft.days == 3  # 느슨한 검증: "3" → 3. 엄격히 하려면 모델에 strict=True


def test_invalid_resume_raises_validation_error_and_can_answer_again(plan_run):
    rec, saver, app, ctx = plan_run
    cfg = _cfg("t-invalid")
    first = app.invoke({"messages": [HumanMessage("x")]}, cfg, context=ctx, version="v2")
    history_before = len(list(app.get_state_history(cfg)))

    with pytest.raises(ValidationError):
        app.invoke(Command(resume={"decision": "maybe"}), cfg, context=ctx, version="v2")
    # 1.2.12에는 is_invalid_resume이 없다(설계는 1.2.16 소스 기준) → 호출부가 직접 판별
    assert not hasattr(lg_errors, "is_invalid_resume")
    snap = app.get_state(cfg)
    assert [i.id for i in snap.interrupts] == [first.interrupts[0].id]  # 같은 interrupt가 그대로 대기
    assert len(list(app.get_state_history(cfg))) == history_before  # 새 체크포인트 없음
    assert rec.count("review") == 2  # 실패한 재개에서도 interrupt 앞 코드는 다시 돌았음
    assert rec.answers == []

    done = app.invoke(Command(resume={"decision": "approve"}), cfg, context=ctx, version="v2")
    assert done.interrupts == () and rec.count("save_plan") == 1


def test_resume_reexecutes_interrupt_node_from_start(plan_run):
    rec, _, app, ctx = plan_run
    cfg = _cfg("t-rerun")
    app.invoke({"messages": [HumanMessage("x")]}, cfg, context=ctx, version="v2")
    assert (rec.count("plan_prepare"), rec.count("review")) == (1, 1)
    app.invoke(Command(resume={"decision": "reject"}), cfg, context=ctx, version="v2")
    # review는 처음부터 다시 돌고, 앞 노드(plan_prepare)는 다시 돌지 않음
    assert (rec.count("plan_prepare"), rec.count("review")) == (1, 2)
    assert rec.count("save_plan") == 0


def test_resume_gets_fresh_step_budget(plan_run):
    rec, _, app, ctx = plan_run
    cfg = _cfg("t-budget")
    app.invoke({"messages": [HumanMessage("x")]}, cfg, context=ctx, version="v2")
    app.invoke(Command(resume={"decision": "approve"}), cfg, context=ctx, version="v2")
    steps = [(e[0], e[2]) for e in rec.events]
    # 첫 실행: 24, 23. 재개 실행은 invoke마다 새 예산(재실행된 review가 26부터)
    assert steps == [("plan_prepare", 24), ("review", 23), ("review", 26), ("save_plan", 25)]


def test_resume_without_pending_interrupt_is_noop(plan_run):
    rec, _, app, ctx = plan_run
    cfg = _cfg("t-twice")
    app.invoke({"messages": [HumanMessage("x")]}, cfg, context=ctx, version="v2")
    app.invoke(Command(resume={"decision": "approve"}), cfg, context=ctx, version="v2")
    events_before = list(rec.events)
    again = app.invoke(Command(resume={"decision": "approve"}), cfg, context=ctx, version="v2")
    assert rec.events == events_before and rec.count("save_plan") == 1  # 아무 노드도 돌지 않음
    assert again.interrupts == () and again.value["log"].count("save_plan") == 1


def test_resume_by_interrupt_id_ignores_stale_id(plan_run):
    # 값만 보낸 재개는 지금 대기 중인 interrupt에 그대로 들어감(오래된 화면의 승인이 새 카드에 적용될 수 있음).
    # {interrupt_id: 값}으로 보내면 id가 다를 때 적용되지 않고 같은 카드가 다시 뜸
    rec, _, app, ctx = plan_run
    cfg = _cfg("t-stale")
    first = app.invoke({"messages": [HumanMessage("x")]}, cfg, context=ctx, version="v2")
    old_id = first.interrupts[0].id
    second = app.invoke(Command(resume={old_id: {"decision": "edit", "draft": {"region": "제주", "days": 2}}}),
                        cfg, context=ctx, version="v2")
    new_id = second.interrupts[0].id

    stale = app.invoke(Command(resume={old_id: {"decision": "approve"}}), cfg, context=ctx, version="v2")
    assert [i.id for i in stale.interrupts] == [new_id] and rec.count("save_plan") == 0

    plain = app.invoke(Command(resume={"decision": "approve"}), cfg, context=ctx, version="v2")
    assert plain.interrupts == () and rec.count("save_plan") == 1


# ---------------------------------------------------------------- context·configurable과 체크포인트


def test_context_is_passed_as_is_and_never_checkpointed(plan_run, sentinel_raw):
    rec, saver, app, ctx = plan_run
    cfg = _cfg("t-ctx")
    app.invoke({"messages": [HumanMessage("제주 일정 저장")]}, cfg, context=ctx, version="v2")
    app.invoke(Command(resume={"decision": "approve"}), cfg, context=ctx, version="v2")
    assert all(e[1] is ctx for e in rec.events)  # 복사하지 않고 같은 객체를 넘김(BearerToken은 복사 거부)

    stored = _saver_bytes(saver)
    assert "제주 일정 저장".encode() in stored and b"plan_confirm" in stored  # 저장소를 실제로 훑었는지
    assert sentinel_raw.encode() not in stored
    history = list(app.get_state_history(cfg))
    assert len(history) >= 5
    assert sentinel_raw not in repr(history)  # values·metadata·config·tasks 어디에도 없음


def test_context_rides_on_dunder_configurable_key(sentinel_raw):
    # context는 configurable["__pregel_runtime"]에 실린다. "__" 접두사라 메타데이터 복사에서 빠짐
    seen = {}

    def node(state, config):
        seen["runtime_ctx"] = config["configurable"][CONFIG_KEY_RUNTIME].context
        return {"log": ["n"]}

    g = StateGraph(LogState, context_schema=PlainCtx)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    ctx = PlainCtx(user_id="u1", secret=sentinel_raw)
    app = g.compile(checkpointer=InMemorySaver())
    app.invoke({"log": []}, _cfg("t-dunder"), context=ctx)
    assert CONFIG_KEY_RUNTIME == "__pregel_runtime"
    assert seen["runtime_ctx"] is ctx
    assert CONFIG_KEY_RUNTIME not in app.get_state(_cfg("t-dunder")).metadata


def test_resume_needs_context_again(plan_run):
    # context는 저장되지 않으므로 재개 때마다 다시 넘겨야 함. 새 토큰으로 재개하면 새 context가 보임
    rec, _, app, ctx = plan_run
    cfg = _cfg("t-ctx-resume")
    app.invoke({"messages": [HumanMessage("x")]}, cfg, context=ctx, version="v2")
    fresh = PlainCtx(user_id="u1", secret="다시 로그인한 토큰")
    app.invoke(Command(resume={"decision": "edit", "draft": {"region": "제주", "days": 2}}), cfg,
               context=fresh, version="v2")
    app.invoke(Command(resume={"decision": "approve"}), cfg, version="v2")
    seen = [(e[0], e[1]) for e in rec.events]
    assert seen == [("plan_prepare", ctx), ("review", ctx), ("review", fresh), ("plan_prepare", fresh),
                    ("review", fresh), ("review", None), ("save_plan", None)]


def test_configurable_scalars_are_copied_into_checkpoint_metadata(sentinel_raw):
    # 그래서 토큰은 configurable·metadata에 절대 넣지 않는다(design.md E5, 10.1)
    def node(state):
        return {"log": ["n"]}

    g = StateGraph(LogState)
    g.add_node("n", node)
    g.add_edge(START, "n")
    g.add_edge("n", END)
    saver = InMemorySaver()
    app = g.compile(checkpointer=saver)
    cfg = {"configurable": {"thread_id": "t-meta", "auth_token": sentinel_raw, "n": 3, "flag": True,
                            "nested": {"token": "NESTED-SECRET"}, "__hidden": "HIDDEN-SECRET"},
           "metadata": {"request_note": "META-SECRET"}, "recursion_limit": 25}
    app.invoke({"log": []}, cfg)

    meta = app.get_state({"configurable": {"thread_id": "t-meta"}}).metadata
    assert meta["auth_token"] == sentinel_raw  # 키 이름(token)으로 거르지 않음
    assert (meta["n"], meta["flag"], meta["request_note"]) == (3, True, "META-SECRET")
    assert "nested" not in meta and "__hidden" not in meta and "thread_id" not in meta
    history = list(app.get_state_history({"configurable": {"thread_id": "t-meta"}}))
    assert history and all(s.metadata["auth_token"] == sentinel_raw for s in history)
    stored = _saver_bytes(saver)
    assert sentinel_raw.encode() in stored  # 평문으로 저장됨
    assert b"NESTED-SECRET" not in stored and b"HIDDEN-SECRET" not in stored


# ---------------------------------------------------------------- 스트리밍


def test_stream_v2_updates_and_custom():
    def collect(state):
        writer = get_stream_writer()
        writer({"event": "card", "card_id": "dest:1"})
        writer({"event": "card", "card_id": "post:2"})
        return {"log": ["collect"]}

    def review(state):
        interrupt({"type": "plan_confirm"})
        return {}

    g = StateGraph(LogState)
    g.add_node("collect", collect)
    g.add_node("review", review)
    g.add_edge(START, "collect")
    g.add_edge("collect", "review")
    g.add_edge("review", END)
    app = g.compile(checkpointer=InMemorySaver())
    parts = list(app.stream({"log": []}, _cfg("t-stream"), stream_mode=["updates", "custom"], version="v2"))

    assert all(set(p) == {"type", "ns", "data"} and p["ns"] == () for p in parts)
    assert [p["type"] for p in parts] == ["custom", "custom", "updates", "updates"]  # card가 노드 갱신보다 먼저
    assert [p["data"]["card_id"] for p in parts[:2]] == ["dest:1", "post:2"]
    assert parts[2]["data"] == {"collect": {"log": ["collect"]}}
    [intr] = parts[3]["data"]["__interrupt__"]  # interrupt는 updates 조각의 __interrupt__ 키로 옴
    assert intr.value == {"type": "plan_confirm"}
    assert [i.id for i in app.get_state(_cfg("t-stream")).interrupts] == [intr.id]  # 대기 카드는 get_state로 읽음

    # version을 빼면 v1 모양(노드 이름 dict)
    v1 = list(app.stream({"log": []}, _cfg("t-stream-v1"), stream_mode="updates"))
    assert v1[0] == {"collect": {"log": ["collect"]}}


def test_stream_writer_is_noop_without_custom_mode():
    def collect(state):
        get_stream_writer()({"event": "card"})
        return {"log": ["collect"]}

    g = StateGraph(LogState)
    g.add_node("collect", collect)
    g.add_edge(START, "collect")
    g.add_edge("collect", END)
    assert g.compile().invoke({}, {"recursion_limit": 25})["log"] == ["collect"]


def _model_node_graph(model, node_name: str, return_message: bool = True):
    def node(state):
        msg = model.invoke(state["messages"])
        return {"messages": [msg]} if return_message else {"log": [msg.content]}

    g = StateGraph(LogState)
    g.add_node(node_name, node)
    g.add_edge(START, node_name)
    g.add_edge(node_name, END)
    return g.compile()


def _messages_parts(app) -> list[tuple[type, str, str]]:
    parts = app.stream({"messages": [HumanMessage("안녕")]}, {"recursion_limit": 25}, stream_mode="messages",
                       version="v2")
    return [(type(p["data"][0]), p["data"][0].content, p["data"][1]["langgraph_node"]) for p in parts]


def test_messages_mode_streams_chunks_and_node_outputs():
    def fake(**kw):
        return GenericFakeChatModel(messages=iter([AIMessage("좋은 아침")]), **kw)

    # 스트리밍 모델: 토큰 조각(AIMessageChunk), metadata에 노드 이름
    assert _messages_parts(_model_node_graph(fake(), "answer_llm")) == [
        (AIMessageChunk, "좋은", "answer_llm"), (AIMessageChunk, " ", "answer_llm"),
        (AIMessageChunk, "아침", "answer_llm")]
    # nostream 태그·disable_streaming이어도 노드가 돌려준 메시지는 통째로(AIMessage) 한 번 나온다
    for kw in ({"tags": ["nostream"]}, {"disable_streaming": True}):
        assert _messages_parts(_model_node_graph(fake(**kw), "route")) == [(AIMessage, "좋은 아침", "route")]
    # nostream이고 노드가 메시지를 state에 돌려주지 않으면 아무것도 안 나옴
    assert _messages_parts(_model_node_graph(fake(tags=["nostream"]), "route", return_message=False)) == []


def test_messages_mode_routes_invoke_through_stream():
    # messages 모드에서는 model.invoke도 _stream을 탄다. GenericFakeChatModel은 내용 없는 tool_calls를
    # 스트리밍하지 못해 실패 → 라우터 가짜는 disable_streaming=True이거나 _stream에서 tool_call을 내야 함
    tool_only = AIMessage("", tool_calls=[_call("search_reviews", {"query": "바나힐"}, "c1")])

    def fake(**kw):
        return GenericFakeChatModel(messages=iter([tool_only]), **kw)

    assert _model_node_graph(fake(), "route").invoke({"messages": [HumanMessage("x")]},
                                                     {"recursion_limit": 25})["messages"][-1].tool_calls
    with pytest.raises(ValueError, match="No generations found in stream"):
        _messages_parts(_model_node_graph(fake(), "route"))
    [(kind, _, node)] = _messages_parts(_model_node_graph(fake(disable_streaming=True), "route"))
    assert (kind, node) == (AIMessage, "route")


# ---------------------------------------------------------------- 그래프 조립이 기대는 것(M0 graph)


def test_runtime_execution_info_counts_retry_attempts():
    # route는 runtime.execution_info.node_attempt로 마지막 시도인지 알고, 그때만 연결 오류를 잡아 fallback으로 보냄
    seen: list[int] = []

    def route(state, runtime: Runtime):
        seen.append(runtime.execution_info.node_attempt)
        if len(seen) == 1:
            raise ConnectionError("끊김")
        return {"log": [f"attempt:{runtime.execution_info.node_attempt}"]}

    g = StateGraph(LogState)
    g.add_node("route", route, retry_policy=RetryPolicy(max_attempts=2, initial_interval=0, jitter=False,
                                                        retry_on=ConnectionError))
    g.add_edge(START, "route")
    g.add_edge("route", END)
    assert g.compile().invoke({}, {"recursion_limit": 25})["log"] == ["attempt:2"]
    assert seen == [1, 2]  # 1부터 세는 실행 번호(재시도는 같은 단계 안이라 remaining_steps를 쓰지 않음)


def test_new_input_while_interrupt_pending_drops_it(plan_run):
    # 확인 카드가 떠 있는데 Command가 아닌 새 입력을 보내면 START부터 다시 돌고 예전 interrupt는 사라짐
    rec, _, app, ctx = plan_run
    cfg = _cfg("t-new-input")
    first = app.invoke({"messages": [HumanMessage("x")]}, cfg, context=ctx, version="v2")
    second = app.invoke({"messages": [HumanMessage("y")]}, cfg, context=ctx, version="v2")
    assert second.interrupts[0].id != first.interrupts[0].id
    assert [i.id for i in app.get_state(cfg).interrupts] == [second.interrupts[0].id]
    assert second.value["log"] == ["plan_prepare", "plan_prepare"] and rec.answers == []


# ---------------------------------------------------------------- M1: 도구에 실행 문맥 넘기기

class _StrictArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str


@dataclass
class _Ctx:
    now: str


def _one_tool_graph(tool):
    g = StateGraph(_ToolState, context_schema=_Ctx)
    g.add_node("tools", ToolNode([tool], handle_tool_errors=True))
    g.add_edge(START, "tools")
    g.add_edge("tools", END)
    return g.compile()


class _ToolState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]


def _call_q():
    return {"messages": [AIMessage("", tool_calls=[{"name": "t", "args": {"q": "x"}, "id": "c1"}])]}


def test_tool_runtime_injection_breaks_extra_forbid_args_schema():
    # ToolNode는 runtime(ToolRuntime)을 args에 넣은 채 args_schema로 검증한다 → extra="forbid"면 ToolInvocationError
    # (메시지가 비어 있음). 그래서 registry는 ToolRuntime 인자 대신 langgraph.runtime.get_runtime()을 쓴다(api-notes M1)
    from langgraph.prebuilt import ToolRuntime

    def run(runtime: ToolRuntime = None, **kw: Any) -> str:
        return f"ok {kw} {getattr(runtime, 'context', None)}"

    tool = StructuredTool.from_function(func=run, name="t", description="d", args_schema=_StrictArgs)
    out = _one_tool_graph(tool).invoke(_call_q(), context=_Ctx("NOW"))
    msg = out["messages"][-1]
    assert msg.status == "error" and "ToolInvocationError" in msg.content and "NOW" not in msg.content


def test_get_runtime_inside_tool_under_toolnode_sees_context():
    from langgraph.runtime import get_runtime

    def run(**kw: Any) -> str:
        return f"ok {kw['q']} {get_runtime().context.now}"

    tool = StructuredTool.from_function(func=run, name="t", description="d", args_schema=_StrictArgs)
    out = _one_tool_graph(tool).invoke(_call_q(), context=_Ctx("NOW"))
    assert out["messages"][-1].content == "ok x NOW"
    with pytest.raises(RuntimeError):   # 그래프 밖(실행 config 없음)에서는 RuntimeError → registry가 None으로
        get_runtime()
