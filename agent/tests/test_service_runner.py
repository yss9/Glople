"""M5 비동기 부품(pytest-asyncio): astream_run + AsyncSqliteSaver(동기 노드가 스레드에서 돎), 주인 표(aiosqlite), 자리 잠금."""
import asyncio

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("pytest_asyncio")

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

from glople_agent.graph.build import build_graph, fake_deps  # noqa: E402
from glople_agent.llm.fake import RuleScript  # noqa: E402
from glople_agent.plan import InMemoryPlanStore, PlanDecision  # noqa: E402
from glople_agent.service.errors import ApiError  # noqa: E402
from glople_agent.service.limits import ModelSlot, RunRegistry  # noqa: E402
from glople_agent.service.runner import (astream_run, check_decision, final_payload, message_input,  # noqa: E402
                                         pending_interrupt, resume_input, thread_view)
from glople_agent.service.threads import ThreadStore, clip_title, is_thread_id  # noqa: E402


@pytest.mark.asyncio
async def test_astream_run_with_async_sqlite_saver(tmp_path, agent_context):
    store = InMemoryPlanStore()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.sqlite")) as saver:
        graph = build_graph(fake_deps(RuleScript(), store=store), saver)
        events: list[tuple[str, dict]] = []

        async def emit(name, data):
            events.append((name, data))

        out = await astream_run(graph, message_input("제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘", "turn-a"), "t1",
                                agent_context, emit)
        assert out.pending is not None and out.path == ["start_turn", "route", "validate", "plan_prepare"]
        assert [e for e, _ in events] == ["node", "node", "node", "card", "node"]
        pending, snapshot = await pending_interrupt(graph, "t1")
        assert pending.id == out.pending.id and snapshot.values["turn_id"] == "turn-a"
        with pytest.raises(Exception, match="이미 답했어요"):
            check_decision("other-id", pending, {"decision": "approve"})
        decision = check_decision(pending.id, pending, {"decision": "approve"})
        done = await astream_run(graph, resume_input(pending, decision), "t1", agent_context, None)
        assert done.pending is None and final_payload(done)["kind"] == "plan" and store.save_count == 1
        view = thread_view(done.snapshot)
        assert view["turns"][0]["turnId"] == "turn-a" and view["turns"][0]["answer"].endswith("저장했어요.")
        assert [c["kind"] for c in view["turns"][0]["cards"]] == ["plan_saved"]


@pytest.mark.asyncio
async def test_cards_only_filter(tmp_path, agent_context):
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "cp.sqlite")) as saver:
        graph = build_graph(fake_deps(RuleScript()), saver)
        await astream_run(graph, message_input("제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘", "turn-a"), "t1",
                          agent_context, None)
        pending, _ = await pending_interrupt(graph, "t1")
        events = []

        async def emit(name, data):
            events.append(name)

        out = await astream_run(graph, resume_input(pending, PlanDecision(decision="reject")), "t1", agent_context,
                                emit, only=("card",))
        assert events == ["card"] and out.pending is None


@pytest.mark.asyncio
async def test_thread_store_owner_checks(tmp_path):
    threads = await ThreadStore.open(tmp_path / "threads.sqlite")
    try:
        row = await threads.create("alice")
        assert is_thread_id(row.thread_id)
        assert (await threads.owned(row.thread_id, "alice")).owner_user_id == "alice"
        assert await threads.owned(row.thread_id, "bob") is None
        assert await threads.owned(row.thread_id.upper(), "alice") is None   # 서버가 만든 꼴 그대로만
        assert await threads.owned("'; DROP TABLE threads; --", "alice") is None
        await threads.touch(row.thread_id, first_text="  첫   메시지 " + "가" * 60, client_message_id="m1")
        await threads.touch(row.thread_id, first_text="둘째", client_message_id=None)
        again = await threads.owned(row.thread_id, "alice")
        assert again.title == clip_title("첫 메시지 " + "가" * 60) and len(again.title) == 40
        assert again.last_client_message_id == "m1"
        assert [r.thread_id for r in await threads.list_for("alice")] == [row.thread_id]
        await threads.delete(row.thread_id)
        assert await threads.list_for("alice") == [] and await threads.count_for("alice") == 0
    finally:
        await threads.close()


@pytest.mark.asyncio
async def test_registry_and_model_slot():
    registry = RunRegistry()
    registry.begin("t1")
    with pytest.raises(ApiError) as e:
        registry.begin("t1")
    assert e.value.status == 409 and e.value.code == "THREAD_BUSY"

    async def work():
        await asyncio.sleep(10)

    task = asyncio.create_task(work())
    registry.attach("t1", task)
    assert registry.cancel("t1")
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0)
    assert not registry.busy("t1")

    never = asyncio.create_task(work())
    registry.begin("t2")
    registry.attach("t2", never)
    never.cancel()   # 시작 전에 취소돼도 done 콜백이 자리를 푼다
    await asyncio.gather(never, return_exceptions=True)
    await asyncio.sleep(0)
    assert len(registry) == 0

    slot = ModelSlot(1, wait=0.01)
    async with slot.hold() as first:
        async with slot.hold() as second:
            assert first is True and second is False
    async with slot.hold() as again:
        assert again is True
