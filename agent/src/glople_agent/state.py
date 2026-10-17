"""그래프 상태(design.md 5.1). 모델·백엔드·저장소·토큰은 state에 넣지 않는다."""
import operator
from typing import Annotated, Literal, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from langgraph.managed import RemainingSteps

MAX_CARDS = 60  # 체크포인트 크기 상한

CardKind = Literal["destination_list", "destination_detail", "review_list", "glopler_list",
                   "traveler_routes", "weather", "plan_confirm", "plan_saved", "notice"]
ResultStatus = Literal["ok", "empty", "error", "unsupported", "pending_feature"]
RouterKind = Literal["tool", "text", "dropped", "truncated", "unparsed", "error"]
ReplyKind = Literal["direct", "clarify", "template", "llm", "fallback", "plan"]
PlaceSource = Literal["USER", "TOOL_RESULT", "MODEL_SUGGESTED", "USER_EDIT"]


class Card(TypedDict):
    card_id: str    # "dest:123", "post:45", "plan:draft:<tool_call_id>"
    turn_id: str
    call_id: str    # 이 카드를 만든 tool_call_id
    tool: str
    kind: CardKind
    status: ResultStatus
    data: dict      # 원문(Type3 포함)·이미지·출처 문구(코드가 만듦). LLM 입력에 절대 안 들어감


def merge_cards(old: list[Card] | None, new: list[Card] | None) -> list[Card]:
    """사용자 정의 리듀서 ①: card_id가 같으면 새 카드로 바꾸고 최근 MAX_CARDS장만 남긴다."""
    by_id = {c["card_id"]: c for c in old or []}
    by_id.update({c["card_id"]: c for c in new or []})
    return list(by_id.values())[-MAX_CARDS:]


class Memory(TypedDict, total=False):  # 턴을 넘어 남는 기억 (M1부터 사용)
    last_region: str
    recent_places: list[str]          # 최근 결과의 장소 이름(최대 50) → 일정 places 근거
    pending_clarify: dict | None      # {"utterance", "reason", "tool"}


def merge_memory(old: Memory | None, new: Memory | None) -> Memory:
    """사용자 정의 리듀서 ②: 키 단위로 덮어쓴다(None을 넣으면 그 키를 비움)."""
    return {**(old or {}), **(new or {})}


class PlanItem(TypedDict):
    position: int            # 1부터
    dayNo: int | None        # 순서대로 나눈 제안(floor(i·days/n)+1)
    daySuggested: bool
    name: str
    source: PlaceSource      # MODEL_SUGGESTED는 기본 해제
    selected: bool


class PlanDraft(TypedDict):
    """glople.plan.v1 축약판(확인 카드·저장 본문 공용). 지역 해석(M1) 전이라 region은 문자열."""
    schema: Literal["glople.plan.v1"]
    title: str
    region: str
    days: int
    items: list[PlanItem]
    createdVia: Literal["AGENT"]
    clientRequestId: str       # = create_travel_plan의 tool_call_id (멱등키)
    problems: NotRequired[list[str]]  # edit 검사에서 걸린 점(확인 카드에 표시)


class AgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]  # Human / 라우터 AIMessage / ToolMessage / 최종 AIMessage
    cards: Annotated[list[Card], merge_cards]
    memory: Annotated[Memory, merge_memory]
    call_log: Annotated[list[str], operator.add]         # 이번 턴 (이름+정규화 인자) 서명. start_turn이 Overwrite([])로 비움
    # 아래는 리듀서 없는 채널(이번 턴 값)
    turn_id: str
    user_text: str                   # 정제한 원문
    router_input: str                # 라우터에 넣을 한 마디(M1부터 이어 붙이기·지시어 풀이 반영)
    router_kind: RouterKind | None
    router_attempts: int
    verdicts: list[dict]             # 호출별 {tool_call_id, name, ok, reason}
    results: list[dict]              # 호출별 {tool_call_id, tool, status}
    plan_draft: PlanDraft | None
    plan_edits: int
    plan_notice: str | None          # "AUTH_EXPIRED" 등
    final_text: str | None
    reply_kind: ReplyKind | None
    trace: dict                      # 궤적 로그용(라우터 원출력, 가드 결과). UI로 안 보냄
    remaining_steps: RemainingSteps
