"""Spring 일정 저장소(M3, design.md 8.4 S2·9.1 save_plan): PlanStore를 Spring `/api/travel-plans`로.

S2 API(다른 담당자가 만드는 중, 계약은 아래로 고정. 실제 서버 확인은 오너가 함):

| 요청 | 응답 |
|---|---|
| POST /api/travel-plans, Bearer 사용자 토큰, 본문 {"clientRequestId", "title", "region", "days", "places": [{"name", "dayNo", "position", "source"}]} | 201 새 일정 JSON {id, clientRequestId, title, region, days, places, createdAt} / 같은 clientRequestId 다시 → 200 기존 일정 / 400 검증 JSON / 401 토큰 없음·만료 |
| GET /api/travel-plans | 내 일정 목록 |
| GET /api/travel-plans/{id} | 내 일정(남의 것·없는 것은 403, authorization.md C01) |
| DELETE /api/travel-plans/{id} | 삭제 |

- 주인은 Spring이 토큰으로 정한다(currentUser.requireUser()). 본문에 userId를 넣지 않는다. save()의 user_id는 PlanStore 계약이라
  받기만 하고 보내지 않는다.
- 토큰은 SpringClient 허용 목록의 로그인 경로(access="user")에만 그 요청의 헤더로 붙는다. 공개 경로(읽기 도구)에는 절대 안 붙음.
- MODEL_SUGGESTED(모델이 지어낸 장소, 확인 카드에서 기본 해제)는 selected 여부와 상관없이 **보내지 않는다**. 보내는 source는
  USER·TOOL_RESULT·USER_EDIT 셋뿐이고 이름이 같다. 보낼 장소가 0곳이면 요청 없이 PlanRejected.
- 상태 코드 → 예외: 201·200 → SaveResult(created=201), 400 → PlanRejected(본문의 검증 오류를 problems로), 401 → AuthExpired
  (save_plan이 review로 돌아가 '다시 로그인' 카드), 403·404·그 밖 4xx → PlanStoreError(재시도 없음), 5xx·연결 오류·타임아웃
  → 재시도할 수 있는 PlanStoreError(save_plan이 1회 더. 같은 clientRequestId라 서버가 중복을 막음).
- 응답 본문은 일정 id(양의 정수)와 clientRequestId(보낸 것과 같아야 함)를 확인하고, 맞지 않으면 저장 실패로 본다.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from glople_agent.context import BearerToken
from glople_agent.plan.draft import PLAN_SCHEMA, PlanProblem, selected_items
from glople_agent.plan.store import AuthExpired, PlanRejected, PlanStoreError, SaveResult
from glople_agent.state import PlanDraft
from glople_agent.tools.spring import SpringClient, SpringError, SpringResponse

SENT_SOURCES = ("USER", "TOOL_RESULT", "USER_EDIT")   # Spring source 값(우리 PlaceSource와 이름이 같음)
ACCEPT = frozenset({400, 401, 403, 404, 409, 422})    # 예외 대신 상태 코드로 받아 직접 나눌 4xx


class PlanForbidden(PlanStoreError):
    """403: 남의 일정·없는 일정(Spring은 둘 다 403), 또는 권한 없음."""

    status = 403
    code = "forbidden"
    default_message = "그 일정에 접근할 수 없어요."


class PlanUpstreamError(PlanStoreError):
    """5xx·연결 오류·타임아웃(재시도 가능). code는 upstream_unavailable 또는 timeout."""

    status = 503

    def __init__(self, code: str = "upstream_unavailable", message: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class PlanBadResponse(PlanStoreError):
    """응답 모양이 계약과 다름(id 없음, clientRequestId 다름 …). 다시 보내도 같아서 재시도하지 않는다."""

    status = 502
    code = "bad_response"
    default_message = "일정 저장소의 응답을 읽지 못했어요."

    @property
    def retryable(self) -> bool:
        return False


class PlanRequestError(PlanStoreError):
    """그 밖의 4xx(404: S2 배포 전 등)."""

    code = "bad_request"

    def __init__(self, status: int, message: str | None = None) -> None:
        super().__init__(message or f"일정 저장소가 요청을 받지 않았어요(HTTP {status}).")
        self.status = status


def outgoing_places(plan: PlanDraft) -> list[dict]:
    """보낼 장소: 선택된 것 중 MODEL_SUGGESTED가 아닌 것만, position은 1부터 다시."""
    out: list[dict] = []
    for item in selected_items(plan):
        if item.get("source") not in SENT_SOURCES:   # MODEL_SUGGESTED(또는 모르는 출처)는 보내지 않음
            continue
        out.append({"name": item["name"], "dayNo": item.get("dayNo"), "position": len(out) + 1,
                    "source": item["source"]})
    return out


def travel_plan_body(plan: PlanDraft, client_request_id: str) -> dict:
    """POST /api/travel-plans 본문(계약 그대로, 다른 키 없음)."""
    return {"clientRequestId": client_request_id, "title": plan["title"], "region": plan["region"],
            "days": plan["days"], "places": outgoing_places(plan)}


def _problems_from(body: Any) -> list[PlanProblem]:
    """400 검증 JSON → PlanProblem. Spring 기본 오류({status, error, message, path})와 errors 목록 둘 다 받는다."""
    problems: list[PlanProblem] = []
    if isinstance(body, Mapping):
        for err in body.get("errors") or body.get("fieldErrors") or []:
            if isinstance(err, Mapping):
                field = str(err.get("field") or "draft")
                problems.append({"field": field if field in ("region", "days", "places", "title") else "draft",
                                 "code": str(err.get("code") or "invalid"),
                                 "message": str(err.get("message") or err.get("defaultMessage") or "검증 오류")})
        if not problems and body.get("message"):
            problems.append({"field": "draft", "code": "invalid", "message": str(body["message"])})
    return problems or [{"field": "draft", "code": "invalid", "message": "저장할 수 없는 일정이에요."}]


def _positive_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def draft_from_response(data: Any) -> PlanDraft:
    """서버 일정 JSON → PlanDraft 모양(카드 표시용, plan_saved 카드의 saved). 모양이 틀리면 PlanBadResponse."""
    if not isinstance(data, Mapping) or _positive_int(data.get("id")) is None:
        raise PlanBadResponse("일정 응답에 id가 없어요.")
    places = data.get("places") if isinstance(data.get("places"), list) else []
    items = []
    for i, p in enumerate(places, 1):
        if not isinstance(p, Mapping) or not isinstance(p.get("name"), str):
            raise PlanBadResponse("일정 응답의 장소 모양이 달라요.")
        items.append({"position": _positive_int(p.get("position")) or i, "dayNo": p.get("dayNo"),
                      "daySuggested": False, "name": p["name"], "source": p.get("source") or "USER",
                      "selected": True})
    draft: dict = {"schema": PLAN_SCHEMA, "title": data.get("title") or "", "region": data.get("region") or "",
                   "days": data.get("days"), "items": items, "createdVia": "AGENT",
                   "clientRequestId": data.get("clientRequestId") or ""}
    return draft   # type: ignore[return-value]


@dataclass(frozen=True)
class StoredPlan:
    """목록·조회 결과 한 건(서버 값 그대로 + 카드 모양)."""

    plan_id: int
    plan: PlanDraft
    created_at: str | None
    raw: dict


def token_safe_base_url(base_url: str) -> bool:
    """사용자 토큰을 보내도 되는 주소: https, 또는 http면 이 컴퓨터(127.0.0.1·localhost·::1)나 테스트용 예약 이름
    (.localhost·.test·.invalid, RFC 6761). 원격 http로는 토큰이 평문으로 가므로 막는다."""
    parts = urlsplit(base_url)
    host = (parts.hostname or "").lower()
    if parts.scheme == "https":
        return True
    return parts.scheme == "http" and (host in ("127.0.0.1", "localhost", "::1")
                                       or host.endswith((".localhost", ".test", ".invalid")))


class SpringPlanStore:
    """PlanStore(save) + 목록·조회·삭제. 토큰은 부를 때 받아 그 요청에만 쓰고 보관하지 않는다."""

    def __init__(self, client: SpringClient) -> None:
        if not token_safe_base_url(client.base_url):
            raise ValueError(f"사용자 토큰을 보내는 일정 저장소는 https 또는 로컬 주소만 받아요: {client.base_url}")
        self.client = client

    # --- PlanStore ---
    def save(self, user_id: str, plan: PlanDraft, *, client_request_id: str,
             auth: BearerToken | None) -> SaveResult:
        if auth is None:
            raise AuthExpired("로그인 토큰이 없어요.")   # 요청 없이(InMemoryPlanStore와 같은 규칙)
        if not isinstance(auth, BearerToken):
            raise TypeError("auth는 BearerToken이어야 합니다")
        if not client_request_id:
            raise ValueError("client_request_id가 필요합니다")
        body_key = plan.get("clientRequestId")
        if body_key is not None and body_key != client_request_id:
            raise ValueError("본문의 clientRequestId와 client_request_id가 다릅니다")
        body = travel_plan_body(plan, client_request_id)
        if not body["places"]:
            raise PlanRejected([{"field": "places", "code": "empty",
                                 "message": "저장할 장소가 없어요. 장소를 1곳 이상 골라 주세요."}])
        resp = self._call("travel_plan_create", json_body=body, auth=auth)
        if resp.status in (200, 201):
            saved = draft_from_response(resp.data)
            if saved["clientRequestId"] != client_request_id:
                raise PlanBadResponse("일정 응답의 clientRequestId가 보낸 것과 달라요.")
            raw = dict(resp.data)
            return SaveResult(int(raw["id"]), resp.status == 201, saved, created_at=raw.get("createdAt"))
        raise self._error(resp)

    # --- 목록·조회·삭제 ---
    def list_plans(self, *, auth: BearerToken) -> list[StoredPlan]:
        resp = self._call("travel_plan_list", auth=auth)
        if resp.status != 200:
            raise self._error(resp)
        rows = resp.data if isinstance(resp.data, list) else []
        return [self._stored(r) for r in rows]

    def get(self, plan_id: int, *, auth: BearerToken) -> StoredPlan | None:
        """본인 일정. 남의 것·없는 것(403·404)은 None(어느 쪽인지 구별하지 않음)."""
        resp = self._call("travel_plan_get", path_params={"id": plan_id}, auth=auth)
        if resp.status == 200:
            return self._stored(resp.data)
        if resp.status in (403, 404):
            return None
        raise self._error(resp)

    def delete(self, plan_id: int, *, auth: BearerToken) -> bool:
        """지웠으면 True, 남의 것·없는 것이면 False."""
        resp = self._call("travel_plan_delete", path_params={"id": plan_id}, auth=auth)
        if resp.status in (200, 202, 204):
            return True
        if resp.status in (403, 404):
            return False
        raise self._error(resp)

    # --- 내부 ---
    def _call(self, name: str, *, auth: BearerToken, json_body: Any = None,
              path_params: Mapping[str, Any] | None = None) -> SpringResponse:
        if not auth.sendable():   # 헤더로 못 보내는 값(ASCII 밖·공백·제어 문자)은 만료와 같게(다시 로그인), 요청 없음
            raise AuthExpired("로그인 토큰 형식이 올바르지 않아요.")
        try:
            return self.client.call(name, json_body=json_body, auth=auth, path_params=path_params, accept=ACCEPT)
        except SpringError as e:
            if e.code == "bad_request":   # accept 밖 4xx(405·413·429 …): 다시 보내도 같으니 재시도 없음
                raise PlanRequestError(e.status or 400) from None
            # 5xx(GET은 1회 재시도 뒤)·연결 오류·타임아웃·리다이렉트·2MB 초과 → save_plan이 1회 더(같은 멱등키)
            raise PlanUpstreamError("timeout" if e.code == "timeout" else "upstream_unavailable") from None

    @staticmethod
    def _stored(data: Any) -> StoredPlan:
        plan = draft_from_response(data)
        return StoredPlan(int(data["id"]), plan, data.get("createdAt"), dict(data))

    @staticmethod
    def _error(resp: SpringResponse) -> PlanStoreError:
        if resp.status == 400 or resp.status == 422:
            return PlanRejected(_problems_from(resp.data))
        if resp.status == 401:
            return AuthExpired()
        if resp.status == 403:
            return PlanForbidden()
        if 200 <= resp.status < 300:
            return PlanBadResponse(f"예상하지 못한 응답이에요(HTTP {resp.status}).")
        return PlanRequestError(resp.status)


__all__ = ["ACCEPT", "PlanBadResponse", "PlanForbidden", "PlanRequestError", "PlanUpstreamError", "SENT_SOURCES",
           "SpringPlanStore", "StoredPlan", "draft_from_response", "outgoing_places", "token_safe_base_url",
           "travel_plan_body"]
