"""일정 저장소(design.md 9.1 save_plan, 9.2 세 번째 겹). M0는 메모리, M3에 Spring(S2, plan/spring_store.py)을 더했다
(기본은 여전히 메모리: graph.build.make_plan_store, real_deps(plan_store="spring")로 켬).

멱등키 (user_id, client_request_id)는 Spring UNIQUE(user_id, client_request_id)와 같은 규칙이다.
같은 키로 다시 오면 새로 만들지 않고 처음 결과를 돌려준다(Spring 200).
토큰(auth)은 요청 헤더를 만들 때만 쓰고 어디에도 보관하지 않는다.
"""
import copy
import threading
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from glople_agent.context import BearerToken
from glople_agent.plan.draft import PlanProblem, check_draft
from glople_agent.state import PlanDraft


class PlanStoreError(Exception):
    """저장 실패. status는 Spring 응답 코드에 맞춘다. save_plan 노드가 정해진 문장으로 바꾼다."""

    status: int = 500
    code: str = "upstream_unavailable"
    default_message = "일정 저장소에 연결하지 못했어요."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.default_message)

    @property
    def retryable(self) -> bool:
        # 연결 오류·5xx만 1회 재시도(서버 멱등키가 중복을 막음). 400·401·403은 다시 보내도 같다
        return self.status >= 500


class AuthExpired(PlanStoreError):
    """401: 토큰 만료·없음. save_plan은 review로 돌아가 notice=AUTH_EXPIRED 카드를 다시 띄운다."""

    status = 401
    code = "auth_expired"
    default_message = "로그인이 만료됐어요."


class PlanRejected(PlanStoreError):
    """400: 검증 위반(일수 1~30, 장소 1~30곳, 이름 50자, 제목 100자)."""

    status = 400
    code = "bad_request"
    default_message = "저장할 수 없는 일정이에요."

    def __init__(self, problems: list[PlanProblem], message: str | None = None) -> None:
        super().__init__(message)
        self.problems = problems


@dataclass(frozen=True)
class SaveResult:
    plan_id: int
    created: bool     # True = 새로 저장(201), False = 같은 키 재요청이라 기존 일정(200)
    plan: PlanDraft   # 저장된 본문(사본). Spring 저장소는 서버가 돌려준 일정을 같은 모양으로
    created_at: str | None = None   # M3 Spring 응답의 createdAt(메모리 저장소는 None)

    @property
    def status(self) -> int:
        return 201 if self.created else 200


@runtime_checkable
class PlanStore(Protocol):
    def save(self, user_id: str, plan: PlanDraft, *, client_request_id: str,
             auth: BearerToken | None) -> SaveResult:
        """plan은 save_body()로 만든 저장 본문. 실패는 PlanStoreError 하위 예외로 알린다."""
        ...


class InMemoryPlanStore:
    """메모리 저장소(M0). 저장 횟수 카운터와 401·5xx 흉내를 둔다.

    - save_count: 새로 저장한 건수(같은 키 재요청·실패는 빼고)
    - call_count: save를 부른 횟수(실패 포함)
    - expire_auth=N 또는 expire_next_save(N): 다음 save N번을 AuthExpired로
    """

    def __init__(self, *, expire_auth: int = 0, validate: bool = True) -> None:
        self._lock = threading.Lock()
        self._ids: dict[tuple[str, str], int] = {}      # (user_id, client_request_id) → plan_id
        self._plans: dict[int, tuple[str, PlanDraft]] = {}  # plan_id → (user_id, 저장 본문)
        self._failures: list[PlanStoreError] = []
        self.validate = validate  # Spring 400 검증 흉내
        self.save_count = 0
        self.call_count = 0
        self.expire_next_save(expire_auth)

    def expire_next_save(self, times: int = 1) -> None:
        """401 흉내: 다음 save times번이 AuthExpired를 던진다(아무것도 저장 안 함)."""
        if times < 0:
            raise ValueError("times는 0 이상이어야 합니다")
        with self._lock:
            self._failures.extend(AuthExpired() for _ in range(times))

    def fail_next(self, error: PlanStoreError) -> None:
        """다음 save 한 번을 주어진 오류로(5xx·403 흉내)."""
        if not isinstance(error, PlanStoreError):
            raise TypeError("PlanStoreError만 넣을 수 있습니다")
        with self._lock:
            self._failures.append(error)

    def save(self, user_id: str, plan: PlanDraft, *, client_request_id: str,
             auth: BearerToken | None) -> SaveResult:
        with self._lock:
            self.call_count += 1
            if self._failures:  # 인증 필터가 먼저 거르는 것처럼 다른 검사보다 앞
                raise self._failures.pop(0)
            if auth is None:
                raise AuthExpired("로그인 토큰이 없어요.")
            if not isinstance(auth, BearerToken):
                raise TypeError("auth는 BearerToken이어야 합니다")
            auth.header()  # 헤더를 만들 수 있는지만 보고 버린다(저장하지 않음)
            if not user_id:
                raise ValueError("user_id가 필요합니다")
            if not client_request_id:
                raise ValueError("client_request_id가 필요합니다")
            body_key = plan.get("clientRequestId")
            if body_key is not None and body_key != client_request_id:
                raise ValueError("본문의 clientRequestId와 client_request_id가 다릅니다")
            if self.validate and (problems := check_draft(plan)):  # @Valid가 멱등 검사보다 먼저
                raise PlanRejected(problems)
            key = (user_id, client_request_id)
            if key in self._ids:
                plan_id = self._ids[key]
                return SaveResult(plan_id, False, copy.deepcopy(self._plans[plan_id][1]))
            plan_id = len(self._plans) + 1
            stored = copy.deepcopy(plan)
            self._ids[key] = plan_id
            self._plans[plan_id] = (user_id, stored)
            self.save_count += 1
            return SaveResult(plan_id, True, copy.deepcopy(stored))

    def get(self, plan_id: int) -> PlanDraft | None:
        with self._lock:
            found = self._plans.get(plan_id)
            return None if found is None else copy.deepcopy(found[1])

    def list_plans(self, user_id: str) -> list[PlanDraft]:
        """그 사용자의 일정(저장한 순서)."""
        with self._lock:
            return [copy.deepcopy(p) for owner, p in self._plans.values() if owner == user_id]

    def __len__(self) -> int:
        with self._lock:
            return len(self._plans)
