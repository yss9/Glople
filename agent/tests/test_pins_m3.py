"""M3 동작 고정(langgraph 1.2.12·checkpoint 4.2.0·checkpoint-sqlite 3.1.1): 서브그래프·시간 여행·SqliteSaver.

test_pins_langgraph.py처럼 우리 모듈(graph·tools·llm)에 기대지 않는 최소 예제다. api-notes의 'M3' 절이 이 테스트를 가리킨다.
버전을 올려 여기가 깨지면 graph/plan_subgraph.py·graph/build.py(fork·_stream)·checkpoints.py를 먼저 다시 본다.
"""
import json
import operator
import sqlite3
from typing import Annotated, Literal

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.managed import RemainingSteps
from langgraph.types import Command, StateSnapshot, interrupt
from pydantic import BaseModel, ValidationError
from typing_extensions import TypedDict


def _cfg(thread: str) -> dict:
    return {"configurable": {"thread_id": thread}, "recursion_limit": 25}


class Decision(BaseModel):
    decision: Literal["approve", "edit", "reject"]


class Parent(TypedDict, total=False):
    log: Annotated[list[str], operator.add]
    shared: str
    final: str
    seen_steps: Annotated[list[int], operator.add]
    remaining_steps: RemainingSteps


class Sub(TypedDict, total=False):
    log: Annotated[list[str], operator.add]
    shared: str
    final: str
    private: str
    seen_steps: Annotated[list[int], operator.add]
    remaining_steps: RemainingSteps


def _app(saver=None):
    """부모 start → plan(서브그래프: prep → review(interrupt) → save → END) → finish."""

    def start(s):
        return {"log": ["start"], "shared": "S", "private": "부모에는 없는 키"}

    def prep(s) -> Command[Literal["review"]]:
        get_stream_writer()({"event": "card", "where": "prep"})
        return Command(goto="review", update={"private": "draft", "log": ["prep"],
                                              "seen_steps": [s["remaining_steps"]]})

    def review(s) -> Command[Literal["save", "prep", "__end__"]]:
        ans = interrupt({"card": s["private"]}, response_schema=Decision)
        if ans.decision == "approve":
            return Command(goto="save", update={"log": ["review:ok"], "seen_steps": [s["remaining_steps"]]})
        if ans.decision == "edit":
            return Command(goto="prep", update={"log": ["review:edit"]})
        return Command(goto=END, update={"log": ["review:no"], "final": "rejected"})

    def save(s) -> Command[Literal["__end__"]]:
        return Command(goto=END, update={"log": ["save"], "final": "saved", "seen_steps": [s["remaining_steps"]]})

    sb = StateGraph(Sub)
    sb.add_node("prep", prep, destinations=("review",))
    sb.add_node("review", review, destinations=("save", "prep", END))
    sb.add_node("save", save, destinations=(END,))
    sb.add_edge(START, "prep")
    pb = StateGraph(Parent)
    pb.add_node("start", start)
    pb.add_node("plan", sb.compile())
    pb.add_node("finish", lambda s: {"log": ["finish"], "seen_steps": [s["remaining_steps"]]})
    pb.add_edge(START, "start")
    pb.add_edge("start", "plan")
    pb.add_edge("plan", "finish")
    pb.add_edge("finish", END)
    return pb.compile(checkpointer=saver if saver is not None else InMemorySaver())


# ---------------------------------------------------------------- 서브그래프


def test_subgraph_interrupt_reaches_parent_and_subgraph_state_is_separate():
    app = _app()
    cfg = _cfg("t-sub")
    out = app.invoke({"log": []}, cfg, version="v2")
    [intr] = out.interrupts
    snap = app.get_state(cfg)
    assert snap.next == ("plan",) and [i.id for i in snap.interrupts] == [intr.id]   # 같은 id로 최상위까지
    # 부모 state에는 서브그래프 쓰기(prep의 log·private)가 아직 없다. 부모 노드가 돌려준 모르는 키(private)도 버려짐
    assert snap.values == {"log": ["start"], "shared": "S", "seen_steps": []}
    [task] = app.get_state(cfg, subgraphs=True).tasks
    assert task.name == "plan" and isinstance(task.state, StateSnapshot)
    assert task.state.next == ("review",) and task.state.values["private"] == "draft"
    assert task.state.config["configurable"]["checkpoint_ns"].startswith("plan:")   # 서브그래프 자기 namespace
    app.invoke(Command(resume={intr.id: {"decision": "approve"}}), cfg, version="v2")
    done = app.get_state(cfg).values
    # 끝나면 공유 키 '전체 값'이 부모 리듀서로 들어간다: operator.add 채널은 입력으로 받은 'start'가 한 번 더 쌓임
    assert done["log"] == ["start", "start", "prep", "review:ok", "save", "finish"]
    assert done["final"] == "saved" and "private" not in done


def test_subgraph_has_its_own_step_budget_and_counts_as_one_parent_step():
    app = _app()
    cfg = _cfg("t-steps")
    out = app.invoke({"log": []}, cfg, version="v2")
    app.invoke(Command(resume={out.interrupts[0].id: {"decision": "approve"}}), cfg, version="v2")
    # prep 24(서브그래프 첫 단계), 재개 invoke: review 26 → save 25(서브그래프 안), 부모 finish 25(plan이 한 단계)
    assert app.get_state(cfg).values["seen_steps"] == [24, 26, 25, 25]


def test_custom_events_from_subgraph_need_subgraphs_true():
    app = _app()
    plain = list(app.stream({"log": []}, _cfg("t-c1"), stream_mode=["updates", "custom"], version="v2"))
    assert not [p for p in plain if p["type"] == "custom"]   # 서브그래프 안 카드 이벤트가 안 보임
    parts = list(app.stream({"log": []}, _cfg("t-c2"), stream_mode=["updates", "custom"], subgraphs=True,
                            version="v2"))
    [card] = [p for p in parts if p["type"] == "custom"]
    assert card["data"] == {"event": "card", "where": "prep"} and card["ns"][0].startswith("plan:")
    updates = [(p["ns"][:1] and p["ns"][0].split(":")[0], list(p["data"])) for p in parts if p["type"] == "updates"]
    assert updates[:2] == [((), ["start"]), ("plan", ["prep"])]
    assert ("plan", ["__interrupt__"]) in updates and ((), ["__interrupt__"]) in updates   # 안쪽과 부모 둘 다


def test_new_input_while_subgraph_waits_starts_a_fresh_subgraph():
    app = _app()
    cfg = _cfg("t-new")
    first = app.invoke({"log": []}, cfg, version="v2").interrupts[0]
    second = app.invoke({"log": []}, cfg, version="v2").interrupts[0]   # 예전 interrupt는 버려지고 START부터
    assert second.id != first.id
    tasks = app.get_state(cfg, subgraphs=True).tasks
    assert [t.state.values["private"] for t in tasks] == ["draft"]   # 새 namespace의 새 서브그래프


def test_parent_fork_before_subgraph_restarts_it_from_first_node():
    app = _app()
    cfg = _cfg("t-fork-sub")
    out = app.invoke({"log": []}, cfg, version="v2")
    app.invoke(Command(resume={out.interrupts[0].id: {"decision": "approve"}}), cfg, version="v2")
    before = next(s for s in app.get_state_history(cfg) if s.next == ("plan",))
    again = app.invoke(None, {**before.config, "recursion_limit": 25}, version="v2")
    [intr] = again.interrupts   # 서브그래프가 prep부터 다시 돌아 새 카드
    assert intr.id != out.interrupts[0].id and app.get_state(cfg).next == ("plan",)
    [task] = app.get_state(cfg, subgraphs=True).tasks
    assert task.state.next == ("review",)


def test_invalid_unkeyed_resume_leaks_into_the_next_interrupt_of_the_same_subgraph_run():
    # id 없이 보낸 잘못된 재개 값은 ValidationError로 거절되지만 부모 체크포인트에 '전역 재개 값'으로 남는다.
    # 다음 재개(id 있음, edit)에서 첫 interrupt는 id로 답을 받고, 같은 서브그래프 실행 안에서 다시 뜬 두 번째 interrupt가
    # 남은 값을 집어 또 ValidationError. 평평한 그래프는 다음 review가 새 단계(새 체크포인트)라 걸리지 않는다.
    # → 호출부 resume()는 그래프에 보내기 전에 검사하고, 늘 {interrupt_id: 값}으로만 보낸다(build.resume)
    app = _app()
    cfg = _cfg("t-stale")
    iid = app.invoke({"log": []}, cfg, version="v2").interrupts[0].id
    with pytest.raises(ValidationError):
        app.invoke(Command(resume={"decision": "maybe"}), cfg, version="v2")
    with pytest.raises(ValidationError) as info:
        app.invoke(Command(resume={iid: {"decision": "edit"}}), cfg, version="v2")
    assert info.value.errors()[0]["input"] == "maybe"
    # id 있는 잘못된 값은 남지 않는다(같은 카드에 다시 답할 수 있음)
    app2 = _app()
    cfg2 = _cfg("t-keyed")
    iid2 = app2.invoke({"log": []}, cfg2, version="v2").interrupts[0].id
    with pytest.raises(ValidationError):
        app2.invoke(Command(resume={iid2: {"decision": "maybe"}}), cfg2, version="v2")
    out = app2.invoke(Command(resume={iid2: {"decision": "edit"}}), cfg2, version="v2")
    assert len(out.interrupts) == 1 and out.interrupts[0].id != iid2   # edit 뒤 새 카드


# ---------------------------------------------------------------- 시간 여행(평평한 그래프)


def _plain_review_app():
    seen: list[str] = []

    class S(TypedDict, total=False):
        log: Annotated[list[str], operator.add]

    def review(s) -> Command[Literal["save", "__end__"]]:
        ans = interrupt("card")
        seen.append(ans)
        return Command(goto="save" if ans == "approve" else END, update={"log": [f"review:{ans}"]})

    b = StateGraph(S)
    b.add_node("review", review, destinations=("save", END))
    b.add_node("save", lambda s: {"log": ["save"]})
    b.add_edge(START, "review")
    b.add_edge("save", END)
    return b.compile(checkpointer=InMemorySaver()), seen


def test_resume_sent_to_an_answered_checkpoint_replays_the_old_answer():
    # 지난 체크포인트에 Command(resume)를 바로 보내면 그 체크포인트에 남은 예전 재개 값(approve)이 먼저 쓰인다
    app, seen = _plain_review_app()
    cfg = _cfg("t-old")
    app.invoke({"log": []}, cfg)
    iid = app.get_state(cfg).interrupts[0].id
    app.invoke(Command(resume={iid: "approve"}), cfg)
    waiting = next(s for s in app.get_state_history(cfg) if s.next == ("review",))
    assert [i.id for i in waiting.interrupts] == [iid]   # 기록에는 대기 카드가 그대로 보임
    app.invoke(Command(resume={iid: "reject"}), waiting.config)
    assert seen == ["approve", "approve"] and app.get_state(cfg).values["log"] == ["review:approve", "save"]


def test_fork_with_none_reruns_the_waiting_node_with_a_new_interrupt():
    # 그래서 /fork는 invoke(None, 지난 config): 새 갈래(source=fork)에서 review가 다시 돌고 새 id로 멈춘다
    app, seen = _plain_review_app()
    cfg = _cfg("t-fork")
    app.invoke({"log": []}, cfg)
    iid = app.get_state(cfg).interrupts[0].id
    app.invoke(Command(resume={iid: "approve"}), cfg)
    waiting = next(s for s in app.get_state_history(cfg) if s.next == ("review",))
    out = app.invoke(None, waiting.config, version="v2")
    [fresh] = out.interrupts
    head = app.get_state(cfg)
    assert fresh.id != iid and head.metadata["source"] == "fork" and head.next == ("review",)
    app.invoke(Command(resume={fresh.id: "reject"}), cfg)   # 머리에서 다른 결정
    assert seen == ["approve", "reject"] and app.get_state(cfg).values["log"] == ["review:reject"]


# ---------------------------------------------------------------- SqliteSaver


def test_sqlite_saver_copies_configurable_scalars_into_metadata_column(tmp_path, sentinel_raw):
    # E5를 SqliteSaver에서: configurable의 문자열 값이 metadata 열에 JSON 평문으로 들어간다(키 이름으로 거르지 않음)
    class S(TypedDict, total=False):
        log: Annotated[list[str], operator.add]

    b = StateGraph(S)
    b.add_node("n", lambda s: {"log": ["n"]})
    b.add_edge(START, "n")
    b.add_edge("n", END)
    path = tmp_path / "meta.sqlite"
    with SqliteSaver.from_conn_string(str(path)) as saver:
        app = b.compile(checkpointer=saver)
        app.invoke({"log": []}, {"configurable": {"thread_id": "t-meta", "auth_token": sentinel_raw,
                                                  "__hidden": "HIDDEN-SECRET"}, "recursion_limit": 25})
        rows = saver.conn.execute("SELECT metadata FROM checkpoints").fetchall()
    metas = [json.loads(r[0]) for r in rows]
    assert metas and all(m["auth_token"] == sentinel_raw for m in metas)
    assert all("__hidden" not in m and "thread_id" not in m for m in metas)
    assert sentinel_raw.encode() in path.read_bytes()   # 그래서 토큰은 context로만(10.1)


def test_sqlite_saver_uses_wal_and_merges_on_close(tmp_path):
    path = tmp_path / "wal.sqlite"
    conn = sqlite3.connect(str(path), check_same_thread=False)
    saver = SqliteSaver(conn)
    app = _app(saver)
    app.invoke({"log": []}, _cfg("t-wal"), version="v2")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert (tmp_path / "wal.sqlite-wal").exists()   # 열려 있는 동안 최근 쓰기는 -wal 파일에
    conn.close()
    assert not (tmp_path / "wal.sqlite-wal").exists() and b"draft" in path.read_bytes()


def test_sqlite_saver_keeps_pending_interrupt_across_reopen(tmp_path):
    # CLI를 끄고 다시 켜도(새 연결·새 그래프 객체) 같은 카드에 같은 id로 답할 수 있다
    path = tmp_path / "reopen.sqlite"
    with SqliteSaver.from_conn_string(str(path)) as saver:
        first = _app(saver).invoke({"log": []}, _cfg("t-reopen"), version="v2").interrupts[0]
    with SqliteSaver.from_conn_string(str(path)) as saver:
        app = _app(saver)
        [again] = app.get_state(_cfg("t-reopen")).interrupts
        assert again.id == first.id and again.value == {"card": "draft"}
        app.invoke(Command(resume={again.id: {"decision": "approve"}}), _cfg("t-reopen"), version="v2")
        assert app.get_state(_cfg("t-reopen")).values["final"] == "saved"
        newest = next(iter(saver.list(None, limit=1)))
        assert newest.config["configurable"]["thread_id"] == "t-reopen"   # list(None)은 모든 스레드에서 최신부터


def test_sqlite_package_is_pinned():
    from importlib import metadata
    assert metadata.version("langgraph-checkpoint-sqlite") == "3.1.1"   # design.md 3.3(2026-10-03 이전 업로드)
