"""일정 초안(glople.plan.v1 축약판) 만들기·고치기·검사(design.md 9.1).

장소 출처는 USER(발화에 그대로 있음) > TOOL_RESULT(memory.recent_places에 있음) > MODEL_SUGGESTED(근거 없음)
순서로 정하고, MODEL_SUGGESTED는 확인 카드에서 기본 해제(selected=false)한다.
범위·길이 같은 의미 검사는 interrupt의 response_schema(PlanDecision) 밖에서 한다. 스키마 위반은 재개 자체가
ValidationError로 거절되고(아무것도 저장 안 됨), 의미 문제는 problems로 카드에 담아 다시 띄우기 때문이다.
"""
import copy
import unicodedata
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from glople_agent.sanitize import clean_text
from glople_agent.state import PlaceSource, PlanDraft, PlanItem

PLAN_SCHEMA = "glople.plan.v1"
# Spring S2 검증(400)과 같은 한도
MIN_DAYS, MAX_DAYS = 1, 30
MAX_PLACES = 30
MAX_NAME_LEN = 50
MAX_TITLE_LEN = 100
MAX_REGION_LEN = 50  # 설계에 없는 한도. 기본 제목이 MAX_TITLE_LEN을 넘지 않게

# 근거가 있는 출처(사용자가 고칠 때 그대로 둠). MODEL_SUGGESTED는 사용자가 고쳐 넣으면 USER_EDIT가 됨
GROUNDED_SOURCES: frozenset[str] = frozenset({"USER", "TOOL_RESULT", "USER_EDIT"})

ProblemField = Literal["region", "days", "places", "title", "draft"]
PlaceMatcher = Callable[[str, str], bool]  # (장소 이름, 발화) → 발화에 근거가 있는지


class PlanProblem(TypedDict):
    field: ProblemField
    code: str
    message: str  # 확인 카드에 그대로 보이는 문장


def _problem(field: ProblemField, code: str, message: str) -> PlanProblem:
    return {"field": field, "code": code, "message": message}


# ---- 재개 값 스키마 (interrupt response_schema) ----

# 아래 두 모델의 docstring은 JSON 스키마 description이 되어 Interrupt.response_schema에 실린다(짧게 둠)
class PlanDraftIn(BaseModel):
    """확인 카드에서 고친 일정."""

    # 모양(타입·필수)만 본다. 범위·길이는 check_edit에서
    model_config = ConfigDict(extra="forbid")

    region: str
    days: int
    # 저장할(선택한) 장소 목록. 여기 넣은 장소는 모두 선택된 것으로 보고, 빠진 장소는 저장하지 않는다.
    # 해제된 제안(MODEL_SUGGESTED)을 화면 기본값으로 다시 넣지 않는다(cli ask_edit, M5 PlanConfirmCard)
    places: list[str] = Field(description="저장할(선택한) 장소. 해제된 제안은 넣지 않음")
    title: str | None = None  # None이면 기존 제목 유지(기본 제목이었으면 새 기본값)

    @field_validator("days", mode="before")
    @classmethod
    def _days_not_bool(cls, v: Any) -> Any:
        if isinstance(v, bool):  # 그대로 두면 True가 1일이 된다
            raise ValueError("days에는 참·거짓이 아니라 숫자를 넣어 주세요")
        return v


class PlanDecision(BaseModel):
    """확인 카드의 답: approve(저장), edit(고침), reject(취소)."""

    # interrupt(..., response_schema=PlanDecision)가 재개 값을 이 모델로 검증해 돌려준다
    model_config = ConfigDict(extra="forbid")

    # 설계 9.1의 이름은 decision. 작업 지시에 나온 action도 받는다(둘 다 오면 extra로 거절)
    decision: Literal["approve", "edit", "reject"] = Field(
        validation_alias=AliasChoices("decision", "action"))
    draft: PlanDraftIn | None = None  # edit일 때 고친 값

    @property
    def action(self) -> str:
        return self.decision

    def as_resume(self) -> dict:
        """Command(resume=...)에 넣을 dict. 모델 객체를 넣으면 체크포인트에 그대로 저장되고
        STRICT 역직렬화 허용 목록에 없어 막힌다(api-notes)."""
        return self.model_dump(exclude_none=True)


# ---- 출처·제목·일자 ----

def _norm(s: str) -> str:
    """비교용: NFC, 공백 하나로, 대소문자 무시."""
    return " ".join(unicodedata.normalize("NFC", s).split()).casefold()


FIELD_CLEAN_FACTOR = 4  # 한도를 넘었는지는 알아야 하므로 필드 한도의 4배까지만 정제(처리 비용 상한)


def _clean(s: str, limit: int) -> str:
    return clean_text(s, max_len=limit * FIELD_CLEAN_FACTOR)


def appears_in(name: str, utterance: str) -> bool:
    """발화에 이름이 글자 그대로 있는지(공백 수·대소문자만 무시). M1에서 grounding 규칙으로 바꿔 끼운다."""
    n = _norm(name)
    return bool(n) and n in _norm(utterance)


def classify_place(name: str, utterance: str, recent_places: Iterable[str] = (), *,
                   matcher: PlaceMatcher = appears_in) -> PlaceSource:
    if matcher(name, utterance):
        return "USER"
    if _norm(name) in {_norm(p) for p in recent_places if isinstance(p, str)}:
        return "TOOL_RESULT"
    return "MODEL_SUGGESTED"


def default_title(region: str, days: int) -> str:
    """'{region} {days-1}박 {days}일', 당일은 '{region} 당일치기'."""
    if days <= 1:
        return f"{region} 당일치기".strip()
    return f"{region} {days - 1}박 {days}일".strip()


def suggest_day_numbers(n: int, days: int) -> list[int]:
    """장소 n개를 순서대로 days일에 나눈 제안: dayNo = floor(i·days/n)+1."""
    return [i * days // n + 1 for i in range(n)]


def _make_items(entries: list[tuple[str, PlaceSource, bool]], days: int) -> list[PlanItem]:
    day_nos = suggest_day_numbers(len(entries), days)
    return [{"position": i + 1, "dayNo": day_nos[i], "daySuggested": True, "name": name,
             "source": source, "selected": selected}
            for i, (name, source, selected) in enumerate(entries)]


def _redistribute(items: list[PlanItem], days: int) -> list[PlanItem]:
    entries = [(it["name"], it["source"], it["selected"]) for it in items]
    return _make_items(entries, days)


def _as_mapping(args: Any) -> Mapping[str, Any]:
    if isinstance(args, BaseModel):  # tools/args.py의 인자 모델도 받는다
        return args.model_dump(exclude_none=True)
    if not isinstance(args, Mapping):
        raise TypeError(f"create_travel_plan 인자는 dict여야 합니다: {type(args).__name__}")
    return args


def _clip_name(name: str) -> str:
    return name if len(name) <= 20 else name[:20] + "…"


# ---- 초안 만들기 (plan_prepare) ----

def make_draft(args: Mapping[str, Any] | BaseModel, *, client_request_id: str, utterance: str,
               recent_places: Iterable[str] = (), matcher: PlaceMatcher = appears_in) -> PlanDraft:
    """create_travel_plan 인자 → PlanDraft. 한도를 넘어 고치거나 뺀 값은 problems에 남긴다
    (빈 칸·같은 장소만 조용히 뺌).

    utterance는 라우터에 넣은 한 마디(router_input), recent_places는 memory.recent_places.
    """
    if not isinstance(client_request_id, str) or not client_request_id:
        raise ValueError("client_request_id(= tool_call_id)가 필요합니다")
    a = _as_mapping(args)
    region_raw, days_raw, places_raw = a.get("region"), a.get("days"), a.get("places")
    title_raw = a.get("title")
    if not isinstance(region_raw, str):
        raise TypeError("region은 문자열이어야 합니다")
    if isinstance(days_raw, bool) or not isinstance(days_raw, int):
        raise TypeError("days는 정수여야 합니다")
    if not isinstance(places_raw, (list, tuple)):
        raise TypeError("places는 목록이어야 합니다")
    if title_raw is not None and not isinstance(title_raw, str):
        raise TypeError("title은 문자열이어야 합니다")

    problems: list[str] = []
    region = _clean(region_raw, MAX_REGION_LEN)
    if not region:
        problems.append("여행 지역이 비어 있어요. 지역을 적어 주세요.")
    elif len(region) > MAX_REGION_LEN:
        problems.append(f"여행 지역은 {MAX_REGION_LEN}자까지 적을 수 있어요.")

    days = min(max(days_raw, MIN_DAYS), MAX_DAYS)
    if days != days_raw:
        problems.append(f"여행 일수 {days_raw}일은 저장할 수 없어 {days}일로 바꿨어요. 맞는지 확인해 주세요.")

    utterance = utterance or ""
    recent = list(recent_places)
    seen: set[str] = set()
    entries: list[tuple[str, PlaceSource, bool]] = []
    not_text, too_long = 0, []
    for raw in places_raw:
        if not isinstance(raw, str):
            not_text += 1
            continue
        name = _clean(raw, MAX_NAME_LEN)
        if not name or _norm(name) in seen:  # 빈 이름·같은 장소는 조용히 뺌
            continue
        seen.add(_norm(name))
        if len(name) > MAX_NAME_LEN:
            too_long.append(name)
            continue
        source = classify_place(name, utterance, recent, matcher=matcher)
        entries.append((name, source, source != "MODEL_SUGGESTED"))
    if not_text:
        problems.append(f"알아볼 수 없는 장소 값 {not_text}개를 뺐어요.")
    for name in too_long:
        problems.append(f"장소 이름은 {MAX_NAME_LEN}자까지라 뺐어요: '{_clip_name(name)}'")
    if len(entries) > MAX_PLACES:
        problems.append(f"장소는 {MAX_PLACES}곳까지라 앞의 {MAX_PLACES}곳만 넣었어요.")
        entries = entries[:MAX_PLACES]

    title = _clean(title_raw, MAX_TITLE_LEN) if title_raw is not None else ""
    if len(title) > MAX_TITLE_LEN:
        problems.append(f"제목이 {MAX_TITLE_LEN}자를 넘어 기본 제목으로 바꿨어요.")
        title = ""

    draft: PlanDraft = {
        "schema": PLAN_SCHEMA,
        "title": title or default_title(region, days),
        "region": region,
        "days": days,
        "items": _make_items(entries, days),
        "createdVia": "AGENT",
        "clientRequestId": client_request_id,
    }
    if problems:
        draft["problems"] = problems
    return draft


# ---- 고친 값 검사·적용 (review → plan_prepare) ----

@dataclass(frozen=True)
class _CleanEdit:
    region: str
    days: int
    places: list[str]
    title: str | None  # None = 보내지 않음, "" = 비움
    problems: list[PlanProblem]


def _parse_edit(edit: PlanDraftIn | Mapping[str, Any]) -> PlanDraftIn:
    return edit if isinstance(edit, PlanDraftIn) else PlanDraftIn.model_validate(edit)


def _clean_edit(e: PlanDraftIn) -> _CleanEdit:
    problems: list[PlanProblem] = []
    region = _clean(e.region, MAX_REGION_LEN)
    if not region:
        problems.append(_problem("region", "empty", "여행 지역을 적어 주세요."))
    elif len(region) > MAX_REGION_LEN:
        problems.append(_problem("region", "too_long", f"여행 지역은 {MAX_REGION_LEN}자까지 적을 수 있어요."))

    if not MIN_DAYS <= e.days <= MAX_DAYS:
        problems.append(_problem("days", "out_of_range",
                                 f"여행 일수는 {MIN_DAYS}~{MAX_DAYS}일로 정해 주세요(받은 값 {e.days}일)."))

    places: list[str] = []
    seen: set[str] = set()
    for raw in e.places:
        name = _clean(raw, MAX_NAME_LEN)
        if name and _norm(name) not in seen:  # 빈 칸·같은 장소는 조용히 뺌
            seen.add(_norm(name))
            places.append(name)
    long_names = [n for n in places if len(n) > MAX_NAME_LEN]
    if not places:
        problems.append(_problem("places", "empty", "장소를 1곳 이상 넣어 주세요."))
    elif len(places) > MAX_PLACES:
        problems.append(_problem("places", "too_many",
                                 f"장소는 {MAX_PLACES}곳까지 넣을 수 있어요(받은 수 {len(places)}곳)."))
    for name in long_names:
        problems.append(_problem("places", "name_too_long",
                                 f"장소 이름은 {MAX_NAME_LEN}자까지예요: '{_clip_name(name)}'"))

    title = None if e.title is None else _clean(e.title, MAX_TITLE_LEN)
    if title is not None and len(title) > MAX_TITLE_LEN:
        problems.append(_problem("title", "too_long", f"제목은 {MAX_TITLE_LEN}자까지 적을 수 있어요."))
    return _CleanEdit(region, e.days, places, title, problems)


def check_edit(edit: PlanDraftIn | Mapping[str, Any] | None) -> list[PlanProblem]:
    """고친 값의 의미 검사: 일수 1~30, 장소 1~30곳, 이름 50자, 제목 100자, 지역 1~50자.
    특수 토큰·HTML을 지운 뒤에 잰다. 문제가 없으면 []."""
    if edit is None:
        return [_problem("draft", "missing", "고친 내용을 받지 못했어요. 다시 고쳐 주세요.")]
    return _clean_edit(_parse_edit(edit)).problems


def apply_edit(draft: PlanDraft, edit: PlanDraftIn | Mapping[str, Any] | None,
               problems: list[PlanProblem] | None = None) -> PlanDraft:
    """고친 값을 초안에 반영한 새 초안(원본은 그대로). problems가 걸린 칸은 이전 값을 두고,
    그 문장을 problems에 담아 확인 카드에 다시 보여 준다. 그래서 이전 초안이 저장할 수 있는 값이었으면
    결과도 그렇다(문제 있는 고친 값이 승인으로 저장되지 않음)."""
    new: PlanDraft = copy.deepcopy(draft)
    new.pop("problems", None)
    if edit is None:
        found = problems if problems is not None else check_edit(None)
        new["problems"] = [p["message"] for p in found]
        return new
    c = _clean_edit(_parse_edit(edit))
    found = c.problems if problems is None else problems
    bad = {p["field"] for p in found}
    if "draft" in bad:
        new["problems"] = [p["message"] for p in found]
        return new

    region = draft["region"] if "region" in bad else c.region
    days = draft["days"] if "days" in bad else c.days
    if "places" in bad:
        items = _redistribute(draft["items"], days)
    else:
        old_by_name = {_norm(it["name"]): it for it in draft["items"]}
        entries: list[tuple[str, PlaceSource, bool]] = []
        for name in c.places:
            old = old_by_name.get(_norm(name))
            # 근거 있던 장소는 출처 유지, 새로 넣었거나 MODEL_SUGGESTED를 고른 것은 USER_EDIT
            source: PlaceSource = old["source"] if old and old["source"] in GROUNDED_SOURCES else "USER_EDIT"
            entries.append((name, source, True))  # 고친 목록에 넣은 장소는 사용자가 고른 것
        items = _make_items(entries, days)

    # 제목: 사용자가 정한 제목은 지키고, 기본 제목이었으면 지역·일수에 맞춰 다시 만든다
    old_default = default_title(draft["region"], draft["days"])
    was_custom = draft["title"] != old_default
    if "title" in bad or c.title is None:
        title = draft["title"] if was_custom else default_title(region, days)
    elif c.title and c.title != old_default:
        title = c.title
    else:  # 빈 제목·이전 기본 제목 그대로 → 새 기본값
        title = default_title(region, days)

    new.update(region=region, days=days, items=items, title=title)
    if found:
        new["problems"] = [p["message"] for p in found]
    return new


# ---- 저장 전 (save_plan) ----

def selected_items(draft: PlanDraft) -> list[PlanItem]:
    return [it for it in draft["items"] if it.get("selected", True)]


def save_body(draft: PlanDraft) -> PlanDraft:
    """저장 본문: selected=true인 장소만(MODEL_SUGGESTED는 기본 해제라 빠짐), position은 1부터 다시,
    problems(카드 전용)는 뺀다. dayNo는 카드에 보인 제안 그대로 둔다."""
    body: PlanDraft = copy.deepcopy(draft)
    body.pop("problems", None)
    body["items"] = [{**it, "position": i + 1} for i, it in enumerate(selected_items(body))]
    return body


def check_draft(draft: PlanDraft) -> list[PlanProblem]:
    """저장해도 되는 초안인지(Spring 400 규칙과 같음). selected인 장소만 센다. 문제가 없으면 []."""
    problems: list[PlanProblem] = []
    region = draft.get("region")
    if not isinstance(region, str) or not region.strip():
        problems.append(_problem("region", "empty", "여행 지역을 적어 주세요."))
    elif len(region) > MAX_REGION_LEN:
        problems.append(_problem("region", "too_long", f"여행 지역은 {MAX_REGION_LEN}자까지 적을 수 있어요."))
    days = draft.get("days")
    if isinstance(days, bool) or not isinstance(days, int) or not MIN_DAYS <= days <= MAX_DAYS:
        problems.append(_problem("days", "out_of_range", f"여행 일수는 {MIN_DAYS}~{MAX_DAYS}일로 정해 주세요."))
    chosen = selected_items(draft)
    if not chosen:
        problems.append(_problem("places", "empty", "저장할 장소가 없어요. 장소를 1곳 이상 골라 주세요."))
    elif len(chosen) > MAX_PLACES:
        problems.append(_problem("places", "too_many", f"장소는 {MAX_PLACES}곳까지 넣을 수 있어요."))
    for it in chosen:
        name = it.get("name")
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_LEN:
            problems.append(_problem("places", "bad_name", f"장소 이름은 1~{MAX_NAME_LEN}자여야 해요."))
            break
    title = draft.get("title")
    if not isinstance(title, str) or not title.strip() or len(title) > MAX_TITLE_LEN:
        problems.append(_problem("title", "bad_title", f"제목은 1~{MAX_TITLE_LEN}자여야 해요."))
    return problems
