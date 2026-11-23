"""비동기 그래프 실행(M5): graph/build.py의 `_stream`·`resume`을 `astream`·`aget_state`로 옮긴 판.

AsyncSqliteSaver는 이벤트 루프에서 동기 메서드(get_state·stream)를 부를 수 없어서 서비스는 이 모듈만 쓴다.
노드는 그대로 동기 함수이고, langgraph가 astream에서 동기 노드를 스레드(run_in_executor)로 돌린다
(_internal/_runnable.py coerce_to_runnable). get_stream_writer·interrupt도 그 스레드에서 그대로 동작한다.

- 입력·config·context 규칙은 build.py와 같다: configurable에는 thread_id만, 토큰은 context=AgentContext로만.
- 바깥으로 내보내는 이벤트(SSE)는 LangGraph 내부 모양(StreamPart, `__interrupt__`)이 아니라 이 모듈의 작은 dict다.
- 확인 카드(interrupt)는 스트림 조각이 아니라 끝난 뒤 aget_state로 읽는다(build.py와 같음).
"""
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langgraph.errors import GraphRecursionError
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, Interrupt, StateSnapshot
from pydantic import ValidationError

from glople_agent.answer.templates import fallback_text
from glople_agent.context import AgentContext
from glople_agent.graph.build import (PLAN_GRAPH_META, InterruptNotPending, InvalidResume, plan_graph_of, run_config,
                                      subgraph_nodes)
from glople_agent.plan import PlanDecision

Emit = Callable[[str, dict], Awaitable[None]]   # (이벤트 이름, 값)


@dataclass
class RunOutcome:
    snapshot: StateSnapshot
    path: list[str] = field(default_factory=list)
    error: str | None = None   # "recursion"

    @property
    def interrupts(self) -> tuple[Interrupt, ...]:
        return tuple(self.snapshot.interrupts)

    @property
    def pending(self) -> Interrupt | None:
        return self.interrupts[0] if self.interrupts else None

    @property
    def values(self) -> dict:
        return dict(self.snapshot.values or {})


def public_card(card: Mapping[str, Any]) -> dict:
    """state.Card → 화면용(키 이름만 camelCase). data는 원문 그대로(Type3 원문·출처 문구는 코드가 만든 그대로)."""
    return {"cardId": card.get("card_id"), "turnId": card.get("turn_id"), "callId": card.get("call_id"),
            "tool": card.get("tool"), "kind": card.get("kind"), "status": card.get("status"),
            "data": card.get("data") or {}}


def public_confirm(pending: Interrupt) -> dict:
    """interrupt 값(plan_subgraph.confirm_payload) → 화면용 확인 카드. interruptId는 재개에 그대로 돌려보낼 값."""
    value = pending.value if isinstance(pending.value, Mapping) else {}
    draft = value.get("draft") or {}
    return {"interruptId": pending.id, "toolCallId": value.get("tool_call_id"), "draft": draft,
            "message": value.get("message"), "notice": value.get("notice"), "noticeText": value.get("notice_text"),
            "editsLeft": value.get("edits_left"), "problems": list(draft.get("problems") or [])}


def final_payload(outcome: RunOutcome) -> dict:
    """턴이 끝났을 때의 답(SSE final). replaced: 미리보기(token)를 보낸 뒤 가드가 템플릿으로 바꿨는지."""
    values = outcome.values
    if outcome.error == "recursion":
        return {"text": fallback_text("steps"), "kind": "fallback", "replaced": False}
    answer = (values.get("trace") or {}).get("answer_llm") or {}
    return {"text": values.get("final_text") or fallback_text(None), "kind": values.get("reply_kind") or "fallback",
            "replaced": bool(answer.get("replaced"))}


async def astream_run(graph: CompiledStateGraph, inp: Any, thread_id: str, context: AgentContext | None,
                      emit: Emit | None, *, only: Sequence[str] = ("node", "card", "token")) -> RunOutcome:
    """한 번 실행(새 말·재개). only에 든 이벤트만 emit한다(대기 카드 닫기는 card만)."""
    config = run_config(thread_id)
    config = {**config, "metadata": {PLAN_GRAPH_META: plan_graph_of(graph)}}
    containers = subgraph_nodes(graph)
    path: list[str] = []
    error = None

    async def send(name: str, data: dict) -> None:
        if emit is not None and name in only:
            await emit(name, data)

    try:
        async for part in graph.astream(inp, config, context=context, stream_mode=["updates", "custom"],
                                        subgraphs=True, version="v2"):
            ns = part.get("ns") or ()
            if part["type"] == "updates":
                for name in part["data"]:
                    if name == "__interrupt__" or (not ns and name in containers):
                        continue
                    path.append(name)
                    await send("node", {"node": name})
            elif part["type"] == "custom" and isinstance(part["data"], Mapping):
                event = part["data"].get("event")
                if event == "card":
                    await send("card", {"card": public_card(part["data"]["card"])})
                elif event == "token":
                    await send("token", {"text": part["data"]["text"]})
    except GraphRecursionError:
        error = "recursion"
    snapshot = await graph.aget_state(run_config(thread_id))
    if error == "recursion" and not snapshot.next and path[-1:] == ["finish"]:
        error = None   # build._stream과 같은 예외(finish가 답을 저장한 뒤 remaining_steps 0에서 끝남)
    return RunOutcome(snapshot, path, error)


async def pending_interrupt(graph: CompiledStateGraph, thread_id: str) -> tuple[Interrupt | None, StateSnapshot]:
    snapshot = await graph.aget_state(run_config(thread_id))
    return (snapshot.interrupts[0] if snapshot.interrupts else None), snapshot


def check_decision(interrupt_id: str, pending: Interrupt | None,
                   decision: PlanDecision | Mapping[str, Any]) -> PlanDecision:
    """build.resume과 같은 검사(그래프를 부르기 전): 대기 id가 같아야 하고 PlanDecision 모양이어야 한다."""
    if pending is None or interrupt_id != pending.id:
        raise InterruptNotPending("대기 중인 확인 카드가 없거나 이미 답했어요.")
    try:
        return decision if isinstance(decision, PlanDecision) else PlanDecision.model_validate(decision)
    except ValidationError as e:
        raise InvalidResume(e) from None


def resume_input(pending: Interrupt, decision: PlanDecision) -> Command:
    # 모델 객체가 아니라 dict(as_resume)로(STRICT 역직렬화, build.resume과 같음)
    return Command(resume={pending.id: decision.as_resume()})


def message_input(text: str, turn_id: str) -> dict:
    """새 말. HumanMessage id를 서버가 정해(start_turn이 turn_id로 씀) meta 이벤트에 바로 실을 수 있게 한다."""
    return {"messages": [HumanMessage(content=text, id=turn_id)]}


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b if isinstance(b, str) else str(b.get("text", "")) for b in content
                       if isinstance(b, str) or isinstance(b, dict))
    return ""


def thread_view(snapshot: StateSnapshot) -> dict:
    """화면 복원(AI4): turns = [{turnId, user, cards, answer, kind?}], pendingConfirm.

    - turn은 HumanMessage 하나부터 다음 HumanMessage 앞까지. answer는 그 사이의 마지막 최종 AIMessage(tool_calls 없음).
    - cards는 turn_id가 같은 카드(최근 60장까지만 state에 남음). 라우터 호출·ToolMessage(LLM용 봉투)는 내보내지 않는다.
    - kind(reply_kind)는 state에 마지막 턴 것만 있다.
    """
    values = snapshot.values or {}
    messages: list[BaseMessage] = list(values.get("messages") or [])
    cards = [public_card(c) for c in values.get("cards") or []]
    turns: list[dict] = []
    for m in messages:
        if isinstance(m, HumanMessage):
            turns.append({"turnId": m.id, "user": _text(m.content), "cards": [], "answer": None})
        elif isinstance(m, AIMessage) and not m.tool_calls and turns:
            turns[-1]["answer"] = _text(m.content)
    by_turn = {t["turnId"]: t for t in turns}
    for card in cards:
        if card["turnId"] in by_turn:
            by_turn[card["turnId"]]["cards"].append(card)
    pending = snapshot.interrupts[0] if snapshot.interrupts else None
    if turns and pending is None and values.get("turn_id") == turns[-1]["turnId"]:
        turns[-1]["kind"] = values.get("reply_kind")
    return {"turns": turns, "pendingConfirm": public_confirm(pending) if pending is not None else None}


__all__ = ["Emit", "RunOutcome", "astream_run", "check_decision", "final_payload", "message_input",
           "pending_interrupt", "public_card", "public_confirm", "resume_input", "thread_view"]
