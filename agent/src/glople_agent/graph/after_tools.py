"""M7 도구 결과 뒤 루프(design.md 5.4·15.3, README 'M7 도구 결과 뒤 루프'). Deps.after_tools="loop"일 때만 그래프에 붙는다.

    collect → route_after_tools ─글─→ 가드 통과 → finish / 가드 실패 → answer_template
                               └호출→ validate_after_tools ─통과→ tools → collect → route_after_tools(2라운드) …
                                                           └모두 막힘→ answer_template

- 요청은 v4 학습 기록(training/data/tool_calling_v4의 after_*)과 같은 모양이다: [System(SYSTEM_PROMPT), Human(router_input),
  AI(호출, content ""), Tool(결과)…(라운드마다)] + 도구 8개(route 노드와 같은 bind_tools, 강제 옵션 없음). 결과 content는 도구가
  만든 LLM용 봉투 그대로다(길이를 줄이지 않음, 학습 기록도 그대로). 카드 원문(Type3)은 원래 봉투에 없고 보내기 전에 한 번 더 본다.
- 라운드 = 이번 턴에 도구가 실행된 호출 묶음(AIMessage 하나). 최대 MAX_TOOL_ROUNDS(2). 2라운드 결과 뒤에도 한 번 더 묻되,
  호출이 오면 받지 않고 템플릿으로 답한다(학습 after2_answer: 2라운드 뒤에는 늘 답).
- 글: 기존 답변 가드(guard.check_answer)로 본다. 사실(이름·숫자)은 라우터가 본 결과 전부(모든 라운드)·이번 턴 카드·발화.
  통과하면 reply_kind="llm"(첫 라운드에서 막힌 병렬 호출의 되묻기 문장을 뒤에), 걸리면 answer_template(같은 턴의 템플릿 답).
  판정은 trace["answer_llm"]에 answer_llm 노드와 같은 모양(via="after_tools")으로 남겨 CLI·서비스·궤적·A/B가 그대로 읽는다.
  라운드마다의 판단은 trace["after_tools"] 목록(round·kind·decision·next)에 남는다.
- 두 번째 호출(validate_after_tools): judge_call(이름·스키마·근거)을 근거 = 발화 + 앞 결과 항목의 name·where·title·nickname·
  destinations + 앞 호출의 글 인자로 돌린다(training/scripts/build_multiturn_dataset.py validate_second_call과 같은 근거).
  그 밖에 같은 서명(call_log) 금지, 앞 결과가 빈 결과·오류·준비 중·미지원이었던 도구는 다시 부르지 않음(학습 after_limit),
  일정 저장은 받지 않음(아래), 턴당 호출 상한(MAX_CALLS_PER_TURN). 통과 0개면 answer_template이 앞 결과로 답하고, 근거가 없어
  막힌 호출의 되묻기 문장(·일정 안내)을 뒤에 붙인다.
- 일정(create_travel_plan)을 루프 안에서 받지 않는다(기존 plan 경로로 보내지 않음). 이유:
  (1) v4 학습에 결과 뒤 일정 호출이 0건이고, 일정이 다른 도구와 섞인 기록도 0건이다(분포 밖 판단).
  (2) 루프의 판단은 도구 결과(다른 사용자의 후기 제목·글로플러 소개 같은 외부 글)를 본 뒤에 나온다. 유일한 쓰기 경로를 거기에
      열면 결과 속 글이 저장 요청을 끌어낼 수 있다. 확인 카드가 있어도 쓰기 표면은 사용자의 말에서 바로 나온 판단으로만 둔다.
  (3) 확인 카드·재개·서브그래프 판·라운드 계산이 지금 그대로 남는다. 찾은 장소는 memory.recent_places에 남아, 다음 턴에 사용자가
      일정을 부탁하면 장소 근거가 된다(답 뒤에 그 안내 문장 tpl.PLAN_IN_LOOP를 붙임).
"""
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (AIMessage, AnyMessage, BaseMessage, HumanMessage, RemoveMessage, SystemMessage,
                                     ToolMessage)
from langchain_core.messages.tool import tool_call as make_tool_call

from glople_agent import contract
from glople_agent.answer import templates as tpl
from glople_agent.graph.edges import MAX_TOOL_ROUNDS, ROUTE_RETRY_ATTEMPTS, STEPS_LOOP_CALL
from glople_agent.graph.guard import MAX_ANSWER_CHARS, answer_facts, check_answer, input_leaks
from glople_agent.graph.nodes import (MAX_CALLS_PER_TURN, PLAN, REJECTED_TEXT_MAX, Ctx, _answer_trace,
                                      _clarify_questions, _signature, _text, _with_id, judge_call, router_message,
                                      turn_cards, turn_messages)
from glople_agent.llm.normalize import (is_model_connection_error, is_model_error, normalize_router,
                                        normalize_router_error)
from glople_agent.state import ParentState
from glople_agent.tools.args import ARGS_MODELS
from glople_agent.tools.envelope import parse_content

EVIDENCE_ITEM_KEYS = ("name", "where", "title", "nickname")   # 근거로 쓰는 결과 항목 칸(학습 validate_second_call과 같음)
LIMIT_STATUSES = frozenset({"empty", "error", "unsupported", "pending_feature"})   # 같은 도구를 다시 부르지 않는 결과
VIA = "after_tools"   # trace["answer_llm"]["via"]: 루프의 글 답을 a3 답(answer_llm 노드)과 구별

Round = tuple[AIMessage, list[ToolMessage]]


# --- 라운드·요청·근거 ---------------------------------------------------------------------

def tool_rounds(messages: Sequence[AnyMessage]) -> list[Round]:
    """이번 턴에 도구가 실행된 라운드(순서대로): (호출 AIMessage, 호출 순서의 ToolMessage). 결과가 없는 호출은 뺀다.
    아직 실행 전인 호출(route_after_tools가 막 넣은 AIMessage)은 라운드가 아니다."""
    turn = turn_messages(messages)
    by_id = {m.tool_call_id: m for m in turn if isinstance(m, ToolMessage)}
    out: list[Round] = []
    for m in turn:
        if isinstance(m, AIMessage) and m.tool_calls:
            results = [by_id[tc["id"]] for tc in m.tool_calls if tc["id"] in by_id]
            if results:
                out.append((m, results))
    return out


def _calls_with_results(ai: AIMessage, results: Sequence[ToolMessage]) -> list[dict]:
    answered = {m.tool_call_id for m in results}
    return [tc for tc in ai.tool_calls if tc["id"] in answered]


def after_tools_request(router_input: str, rounds: Sequence[Round]) -> list[BaseMessage]:
    """라우터에 보낼 메시지(v4 학습 기록의 앞부분과 같은 모양). AIMessage는 호출만 새로 만든다(content "", 응답 메타데이터 없음)."""
    request: list[BaseMessage] = [SystemMessage(content=contract.SYSTEM_PROMPT), HumanMessage(content=router_input)]
    for ai, results in rounds:
        calls = _calls_with_results(ai, results)
        by_id = {m.tool_call_id: m for m in results}
        request.append(AIMessage(content="", tool_calls=[
            make_tool_call(name=tc["name"], args=dict(tc.get("args") or {}), id=tc["id"]) for tc in calls]))
        request += [ToolMessage(content=_text(by_id[tc["id"]].content), tool_call_id=tc["id"], name=tc["name"])
                    for tc in calls]
    return request


def evidence_text(router_input: str, rounds: Sequence[Round]) -> str:
    """두 번째 호출의 근거 글: 발화 + 앞 결과 항목의 이름·지역·제목·닉네임·목적지 + 앞 호출의 글 인자(줄바꿈으로 이음).
    training/scripts/build_multiturn_dataset.py validate_second_call의 evidence와 같은 순서·같은 칸."""
    parts = [router_input]
    for ai, results in rounds:
        by_id = {m.tool_call_id: m for m in results}
        for tc in _calls_with_results(ai, results):
            body = parse_content(by_id[tc["id"]].content) or {}
            for item in body.get("items") or []:
                if isinstance(item, dict):
                    parts += [str(item.get(k)) for k in EVIDENCE_ITEM_KEYS if item.get(k)]
                    parts += [str(d) for d in item.get("destinations") or []]
            parts += [str(v) for v in (tc.get("args") or {}).values() if isinstance(v, str)]
    return "\n".join(parts)


def limited_tools(results: Iterable[Mapping[str, Any]]) -> frozenset[str]:
    """이번 턴 결과가 빈 결과·오류·준비 중·미지원이었던 도구(루프에서 다시 부르지 않음)."""
    return frozenset(r.get("tool") or "" for r in results if r.get("status") in LIMIT_STATUSES)


def after_tools_log(state: Mapping[str, Any]) -> list[dict]:
    return list((state.get("trace") or {}).get(VIA) or [])


# --- route_after_tools --------------------------------------------------------------------

def make_route_after_tools(router_llm: BaseChatModel, *, max_rounds: int = MAX_TOOL_ROUNDS,
                           max_connection_attempts: int = ROUTE_RETRY_ATTEMPTS,
                           max_answer_chars: int = MAX_ANSWER_CHARS):
    """도구 결과 뒤 라우터: 글이면 가드를 거쳐 답, 호출이면 (라운드·단계가 남을 때만) messages에 넣어 validate_after_tools로.
    연결 오류는 route와 같게 RetryPolicy가 한 번 더 부르고, 마지막 시도에서는 잡아서 템플릿 답으로 보낸다."""
    bound = router_llm.bind_tools(contract.TOOLS)   # route와 같은 묶음(tool_choice·strict·parallel_tool_calls 없음)

    def route_after_tools(state: ParentState, runtime: Ctx) -> dict:
        rounds = tool_rounds(state.get("messages") or [])
        n = len(rounds)
        router_input = state.get("router_input", "")
        info = getattr(runtime, "execution_info", None)
        node_attempt = info.node_attempt if info is not None else 1
        trace = dict(state.get("trace") or {})
        log = after_tools_log(state)

        def done(decision: str, next_node: str, *, kind: str | None = None, answer: dict | None = None,
                 update: dict | None = None, **extra: Any) -> dict:
            entry = {"round": n, "node_attempt": node_attempt, "kind": kind, "decision": decision, "next": next_node,
                     **extra}
            trace[VIA] = [*log, entry]
            if answer is not None:
                trace["answer_llm"] = answer
            return {**(update or {}), "trace": trace}

        if not rounds:   # after_collect가 결과가 있을 때만 보냄(방어)
            return done("no_results", "answer_template")
        cards = turn_cards(state)
        contents = [_text(m.content) for _, results in rounds for m in results]
        facts = answer_facts(contents, cards, router_input)
        visible = [tc["name"] for ai, rs in rounds for tc in _calls_with_results(ai, rs)]
        base = {"via": VIA, "round": n, "visible": visible, "removed": [], "input_chars": sum(len(c) for c in contents),
                "prompt": "router"}
        if leaks := input_leaks(contents, facts):   # 어댑터 실수로 카드 원문이 섞였으면 보내지 않음(answer_llm과 같음)
            return done("input_overlap", "answer_template",
                        answer=_answer_trace(base, ok=False, reasons=["input_overlap"], replaced=False,
                                             input_overlap=leaks))
        try:
            outcome = normalize_router(bound.invoke(after_tools_request(router_input, rounds)))
        except Exception as e:  # noqa: BLE001 - 모델 오류만 템플릿으로, 나머지(우리 버그)는 다시 던짐
            if is_model_connection_error(e) and node_attempt < max_connection_attempts:
                raise   # RetryPolicy(retry_on=is_model_connection_error)가 한 번 더
            if not is_model_error(e):
                raise
            outcome = normalize_router_error(e)
        kind = outcome.kind
        if kind == "text":
            assert outcome.message is not None
            verdict = check_answer(outcome.message, facts, max_chars=max_answer_chars)
            text = _text(outcome.message.content).strip()
            if verdict.ok:
                final = " ".join(p for p in (text, tpl.join_answers(_clarify_questions(state))) if p)
                answer = _answer_trace(base, ok=True, reasons=(), replaced=False, **verdict.details)
                return done("answer", "finish", kind=kind, answer=answer,
                            update={"final_text": final, "reply_kind": "llm"})
            extra: dict[str, Any] = dict(verdict.details)
            if "card_overlap" not in verdict.reasons:
                extra["rejected_text"] = text[:REJECTED_TEXT_MAX]
            answer = _answer_trace(base, ok=False, reasons=verdict.reasons, replaced=False, **extra)
            return done("guard_fail", "answer_template", kind=kind, answer=answer)
        if kind == "tool":
            names = [tc["name"] for tc in outcome.tool_calls]
            if n >= max_rounds:   # 2라운드 뒤에는 늘 답(학습 after2_answer): 호출은 받지 않음
                return done("round_limit", "answer_template", kind=kind, calls=names)
            if state.get("remaining_steps", 0) < STEPS_LOOP_CALL:   # 한 번 더 돌면 END 전에 단계가 모자람
                return done("no_steps", "answer_template", kind=kind, calls=names)
            return done("call", "validate_after_tools", kind=kind, calls=names,
                        update={"messages": [_with_id(outcome)]})
        # 빈 호출·잘림·파싱 실패·모델 오류: 다시 묻지 않고 앞 결과로 템플릿 답
        return done(kind or "unknown", "answer_template", kind=kind, detail=outcome.detail)

    return route_after_tools


# --- validate_after_tools -----------------------------------------------------------------

def make_validate_after_tools(tool_names: Iterable[str], *, region_clarify_tools: Iterable[str] = ()):
    """두 번째(이후) 호출 검사. 통과한 호출만 남기도록 방금 넣은 AIMessage를 같은 id로 바꾸고, 전부 막히면 지운다.
    verdicts는 첫 라운드 판정 뒤에 round 번호를 붙여 덧붙인다(answer_template이 막힌 호출의 되묻기·안내 문장을 읽음)."""
    known = (set(tool_names) & set(ARGS_MODELS)) | {PLAN}
    region_tools = frozenset(region_clarify_tools)

    def validate_after_tools(state: ParentState) -> dict:
        ai = router_message(state)
        messages = state.get("messages") or []
        if ai is None or not messages or getattr(messages[-1], "id", None) != ai.id:   # 방금 넣은 호출이 아님(방어)
            return {"trace": {**(state.get("trace") or {}), "validate_after_tools": [{"error": "no_pending_call"}]}}
        rounds = tool_rounds(messages)   # 실행된 라운드만(방금 들어온 호출은 결과가 없어 빠짐)
        n = len(rounds) + 1
        text = evidence_text(state.get("router_input", ""), rounds)
        limited = limited_tools(state.get("results") or [])
        seen = set(state.get("call_log") or [])
        used = len(seen)
        verdicts: list[dict] = []
        passed: list[dict] = []
        for tc in ai.tool_calls:
            name, args, cid = tc["name"], tc.get("args") or {}, tc["id"]
            clean = None
            if name == PLAN:
                verdict: dict[str, Any] = {"name": name, "ok": False, "reason": "plan_in_loop",
                                           "notice": tpl.PLAN_IN_LOOP}
            elif name in limited:
                verdict = {"name": name, "ok": False, "reason": "retry_after_limit"}
            else:
                verdict, clean = judge_call(name, args, text, known=known, region_clarify_tools=region_tools)
            verdict = {"tool_call_id": cid, **verdict, "round": n}
            if clean is not None:
                sig = _signature(name, clean)
                if sig in seen:
                    verdict.update(ok=False, reason="duplicate")
                elif used + len(passed) >= MAX_CALLS_PER_TURN:
                    verdict.update(ok=False, reason="over_limit")
                else:
                    seen.add(sig)
                    verdict["signature"] = sig
                    passed.append(make_tool_call(name=name, args=clean, id=cid))
            verdicts.append(verdict)
        if passed:
            new_ai = AIMessage(content="", tool_calls=passed, id=ai.id, name=ai.name,
                               response_metadata=dict(ai.response_metadata or {}), usage_metadata=ai.usage_metadata)
            message_update: list[AnyMessage] = [new_ai]   # 같은 id → add_messages가 제자리에서 교체
        else:
            message_update = [RemoveMessage(id=ai.id)]     # 전부 막힘 → 짝 없는 호출이 남지 않게 삭제
        trace = dict(state.get("trace") or {})
        raw = [{"id": tc["id"], "name": tc["name"], "args": tc.get("args")} for tc in ai.tool_calls]
        trace["validate_after_tools"] = [*(trace.get("validate_after_tools") or []),
                                         {"round": n, "raw": raw, "passed": len(passed)}]
        return {
            "messages": message_update,
            "verdicts": [*(state.get("verdicts") or []), *verdicts],
            "call_log": [v["signature"] for v in verdicts if v["ok"]],
            "trace": trace,
        }

    return validate_after_tools


__all__ = ["EVIDENCE_ITEM_KEYS", "LIMIT_STATUSES", "MAX_TOOL_ROUNDS", "VIA", "after_tools_log", "after_tools_request",
           "evidence_text", "limited_tools", "make_route_after_tools", "make_validate_after_tools", "tool_rounds"]
