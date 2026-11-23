"""M5 토큰 격리(design.md 10.1): sentinel 토큰으로 서비스 전 구간(조회·일정 확인·고치기·401 재승인·Spring 저장·
일정 검토·복원·목록)을 돌린 뒤 sqlite 파일(체크포인트·주인 표, -wal·-shm 포함)·로그·SSE·응답 JSON·모델 요청 어디에도 0건인지 본다.

토큰이 가도 되는 곳은 Spring의 로그인 경로(my-info·travel-plans)뿐이고, 그 요청에는 본인 토큰이 그대로 붙어야 한다.
사용자 id는 주인 표(threads.sqlite)에만 있고 체크포인트에는 없다.
"""
import json
import logging

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from glople_agent.checkpoints import db_files  # noqa: E402
from glople_agent.plan.spring_store import SpringPlanStore  # noqa: E402
from glople_agent.tools.fake_backend import FakeBackend  # noqa: E402
from glople_agent.tools.spring import SpringClient  # noqa: E402
from service_kit import ALICE, ALICE_TOKEN, API, SPRING, USERS, service_kit  # noqa: E402

ALICE_ID = USERS[ALICE_TOKEN]


class FakeTravelPlans:
    """S2 흉내: 첫 POST는 401(만료), 그다음 201. 저장한 일정을 GET /{id}로 돌려준다."""

    def __init__(self) -> None:
        self.plans: dict[int, dict] = {}
        self.posts = 0

    def create(self, request: httpx.Request) -> httpx.Response:
        self.posts += 1
        if self.posts == 1:
            return httpx.Response(401, json={"status": 401, "error": "UNAUTHORIZED"})
        body = json.loads(request.content)
        plan_id = len(self.plans) + 1
        self.plans[plan_id] = {"id": plan_id, **body, "createdAt": "2026-10-18T14:03:00"}
        return httpx.Response(201, json=self.plans[plan_id])

    def get(self, request: httpx.Request, plan_id: str) -> httpx.Response:
        plan = self.plans.get(int(plan_id))
        if plan is None:
            return httpx.Response(403, json={"status": 403, "error": "FORBIDDEN"})
        return httpx.Response(200, json=plan)


def test_sentinel_token_never_stored_logged_or_streamed(tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    plans = FakeTravelPlans()
    plan_client = SpringClient(SPRING)
    backend = FakeBackend()
    texts: list[str] = []
    with service_kit(tmp_path, store=SpringPlanStore(plan_client), answer_mode="llm", backend=backend) as kit:
        kit.spring.post("/api/travel-plans").mock(side_effect=plans.create)
        kit.spring.get(path__regex=r"^/api/travel-plans/(?P<plan_id>\d+)$").mock(side_effect=plans.get)
        tid = kit.new_thread()
        for text in ("바나힐 정보랑 사람들 리뷰 같이 알려줘", "사찰 야경 바다 섬 추천해줘", "28살 남자 ENTP 나랑 비슷한 여행자 루트"):
            texts.append(kit.say(tid, text).raw)
        s = kit.say(tid, "제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘")
        texts.append(s.raw)
        edit = kit.answer(tid, s.first("confirm")["interruptId"], "edit",
                          draft={"region": "제주", "days": 2, "places": ["성산일출봉", "우도", "오설록"]})
        texts.append(edit.raw)
        expired = kit.answer(tid, edit.first("confirm")["interruptId"], "approve")   # Spring 401 → 다시 로그인 카드
        texts.append(expired.raw)
        assert expired.first("confirm")["notice"] == "AUTH_EXPIRED"
        saved = kit.answer(tid, expired.first("confirm")["interruptId"], "approve")
        texts.append(saved.raw)
        assert saved.first("final")["kind"] == "plan" and plans.posts == 2 and len(plans.plans) == 1
        review = kit.client.post(f"{API}/plans/1/review", headers=ALICE)
        assert review.status_code == 200 and review.json()["kind"] == "llm"
        assert kit.client.post(f"{API}/plans/99/review", headers=ALICE).status_code == 403
        for path in (f"/threads/{tid}", "/threads"):
            r = kit.client.get(f"{API}{path}", headers=ALICE)
            assert r.status_code == 200
            texts.append(r.text)
        texts.append(review.text)
        spring_calls = list(kit.spring.calls)
        router_calls = repr(kit.router_model.calls)
        answer_calls = repr(kit.answer_model.calls) + repr(kit.review_model.calls)
        assert kit.answer_model.calls and kit.review_model.calls
        data_dir = kit.settings.data_dir
    plan_client.close()

    # Spring으로는 로그인 경로에만, 본인 토큰 그대로
    paths = {(c.request.method, c.request.url.path) for c in spring_calls}
    assert paths <= {("GET", "/api/users/my-info"), ("POST", "/api/travel-plans"), ("GET", "/api/travel-plans/1"),
                     ("GET", "/api/travel-plans/99")}
    assert all(c.request.headers["authorization"] == f"Bearer {ALICE_TOKEN}" for c in spring_calls)
    assert all("userId" not in (c.request.content or b"").decode() for c in spring_calls)   # 본문에 행위자 칸 없음
    # 읽기 도구(공개 API, fixture)에는 Authorization 없음
    assert backend.fixture.requests and not any(r.has_authorization for r in backend.fixture.requests)

    checkpoint_bytes = b"".join(p.read_bytes() for p in db_files(data_dir / "checkpoints.sqlite"))
    thread_bytes = b"".join(p.read_bytes() for p in db_files(data_dir / "threads.sqlite"))
    assert "바나힐".encode() in checkpoint_bytes and b"plan_confirm" in checkpoint_bytes   # 실제로 훑었는지
    assert ALICE_ID.encode() in thread_bytes                                                # 주인 표에는 id
    log_text = "\n".join(logging.Formatter().format(r) for r in caplog.records)
    places = {
        "체크포인트 sqlite": checkpoint_bytes,
        "주인 표 sqlite": thread_bytes,
        "SSE·응답 JSON": "\n".join(texts).encode(),
        "로그": (log_text + repr([r.__dict__ for r in caplog.records])).encode(),
        "라우터 모델 요청": router_calls.encode(),
        "답변·검토 모델 요청": answer_calls.encode(),
    }
    for name, blob in places.items():
        assert ALICE_TOKEN.encode() not in blob, f"{name}에 토큰"
        assert b"JWTMARK" not in blob, f"{name}에 토큰 조각"
    assert ALICE_ID.encode() not in checkpoint_bytes   # 사용자 id는 context로만(체크포인트에 없음)
    assert ALICE_ID not in router_calls and ALICE_ID not in answer_calls


def test_principal_and_context_hide_token(tmp_path):
    with service_kit(tmp_path) as kit:
        kit.new_thread()
        cached = next(iter(kit.svc.auth._cache.items()))
        assert ALICE_TOKEN not in repr(cached)
        principal = kit.client.portal.call(kit.svc.auth.authenticate, ALICE["Authorization"])
        ctx = kit.svc.context(principal, None)
        assert ALICE_TOKEN not in repr(principal) and ALICE_TOKEN not in repr(ctx)
        assert ctx.bearer.header() == {"Authorization": f"Bearer {ALICE_TOKEN}"}
