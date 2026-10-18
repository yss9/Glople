"""LLM 답변(a3) 입력 만들기(design.md 6.4). answer_llm 노드와 after_collect 엣지가 쓴다.

메시지: [System(ANSWER_SYSTEM_PROMPT), Human(router_input), AIMessage(LLM이 볼 호출만, content ""), ToolMessage(결과)…]

- 도구 목록(tools)은 보내지 않는다. 이전 대화도 넣지 않는다(이번 턴만, 라우터와 같은 원칙).
- ToolMessage content는 도구가 이미 만든 LLM용 봉투 그대로다({"tool", "status", "items", "notes"}, ensure_ascii=False,
  어댑터가 허용 필드로만 만든 값). 카드(ToolMessage.artifact·state.cards)의 원문·이미지·출처는 여기에 절대 넣지 않는다.
  "tool" 키가 없으면 채운다(Qwen 템플릿이 tool_call_id·이름을 렌더링하지 않아 병렬 결과가 순서로만 짝지어지기 때문).
- 템플릿이 답해야 하는 결과(날씨·미지원·준비 중·빈 결과·오류)는 호출과 결과를 **짝째로** 빼고, 그 템플릿 문장을
  prefix로 돌려준다(노드가 LLM 답 앞에 붙임).
- 도구 결과 전체는 TOOL_SECTION_MAX_CHARS(한글 약 1,000자 ≈ 800토큰) 이하. 넘으면 긴 결과부터 뒤 항목을 뺀다
  (envelope.refit_content, 빠진 항목은 카드에 남고 note로 알림).
"""
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.messages.tool import tool_call as make_tool_call

from glople_agent.answer import templates as tpl
from glople_agent.llm.prompts import ANSWER_SYSTEM_PROMPT
from glople_agent.tools.envelope import parse_content, refit_content

# 결과가 ok여도 늘 템플릿으로 답하는 도구: 날씨 수치(design.md 6.1 '날씨 수치·저장 결과·빈 결과·오류 → 코드 템플릿')
TEMPLATE_ONLY_TOOLS = frozenset({"get_weather"})
TOOL_SECTION_MAX_CHARS = 1000


def llm_answerable(tool: str | None, status: str | None) -> bool:
    """LLM이 답할 결과인지: ok이고 템플릿 전용 도구가 아님. 그 밖(빈 결과·미지원·준비 중·오류)은 템플릿."""
    return status == "ok" and (tool or "") not in TEMPLATE_ONLY_TOOLS


@dataclass(frozen=True)
class AnswerInput:
    messages: list[BaseMessage]
    visible: list[dict]    # LLM이 보는 결과(호출 순서): {"tool_call_id", "tool", "content"}
    removed: list[dict]    # 짝째로 뺀 결과: {"tool_call_id", "tool", "status"}
    prefix: str            # 뺀 결과의 템플릿 문장(호출 순서, LLM 답 앞에 붙임)

    @property
    def tool_chars(self) -> int:
        return sum(len(v["content"]) for v in self.visible)

    @property
    def contents(self) -> list[str]:
        return [v["content"] for v in self.visible]


def _with_tool_key(content: str, tool: str) -> str:
    """봉투에 "tool"이 없거나 다르면 채워 다시 직렬화한다(대부분은 그대로 돌려줌)."""
    obj = parse_content(content)
    if obj is None:
        raise ValueError(f"{tool}: 결과 봉투가 아닌 content는 LLM에 넣지 않습니다")
    if obj.get("tool") == tool:
        return content
    return json.dumps({"tool": tool, **{k: v for k, v in obj.items() if k != "tool"}}, ensure_ascii=False)


def section_caps(sizes: Sequence[int], budget: int) -> list[int]:
    """결과별 상한(물 채우기): 짧은 결과는 그대로 두고 남는 몫을 긴 결과에 나눠 준다. 합이 budget 이하."""
    sizes = list(sizes)
    if sum(sizes) <= budget:
        return sizes
    caps = [0] * len(sizes)
    remaining = budget
    order = sorted(range(len(sizes)), key=lambda i: sizes[i])
    for k, i in enumerate(order):
        caps[i] = min(sizes[i], remaining // (len(sizes) - k))
        remaining -= caps[i]
    return caps


def build_answer_input(router_input: str, calls: Sequence[Mapping[str, Any]],
                       tool_messages: Mapping[str, ToolMessage], cards_by_call: Mapping[str, Mapping[str, Any]],
                       *, max_chars: int = TOOL_SECTION_MAX_CHARS) -> AnswerInput:
    """이번 턴 호출(validate가 남긴 순서)·ToolMessage·카드 → 답변 모델 입력과 템플릿 prefix."""
    visible: list[dict] = []
    removed: list[dict] = []
    prefix_parts: list[str] = []
    for tc in calls:
        cid, name = tc["id"], tc["name"]
        msg = tool_messages.get(cid)
        if msg is None:
            continue   # 짝 없는 호출(finish가 닫음)은 LLM에도 템플릿에도 없음
        card = cards_by_call.get(cid)
        status = (card or {}).get("status") or (parse_content(msg.content) or {}).get("status") or "error"
        if llm_answerable(name, status) and isinstance(msg.content, str):
            visible.append({"tool_call_id": cid, "tool": name, "args": dict(tc.get("args") or {}),
                            "content": _with_tool_key(msg.content, name)})
        else:
            removed.append({"tool_call_id": cid, "tool": name, "status": status})
            prefix_parts.append(tpl.answer_for_card(card) if card is not None
                                else tpl.status_message(name, status, tc.get("args")))
    caps = section_caps([len(v["content"]) for v in visible], max_chars)
    for v, cap in zip(visible, caps):
        v["content"] = refit_content(v["content"], cap)
    messages: list[BaseMessage] = [SystemMessage(content=ANSWER_SYSTEM_PROMPT), HumanMessage(content=router_input)]
    if visible:
        messages.append(AIMessage(content="", tool_calls=[make_tool_call(name=v["tool"], args=v["args"],
                                                                         id=v["tool_call_id"]) for v in visible]))
        messages += [ToolMessage(content=v["content"], tool_call_id=v["tool_call_id"], name=v["tool"])
                     for v in visible]
    for v in visible:
        del v["args"]   # 인자는 AIMessage에만(trace·가드에는 결과 content만)
    return AnswerInput(messages=messages, visible=visible, removed=removed, prefix=tpl.join_answers(prefix_parts))


__all__ = ["AnswerInput", "TEMPLATE_ONLY_TOOLS", "TOOL_SECTION_MAX_CHARS", "build_answer_input", "llm_answerable",
           "section_caps"]
