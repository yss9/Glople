"""M5 서비스 API(design.md 10.2·12, 13절 M5 확인 항목): 인증·인가·본문 규칙·SSE 이벤트·실행 수명.

가짜 Spring(respx, spring.test)·가짜 모델(RuleScript)·임시 sqlite만 쓴다. 8080·8090·8095에 접속하지 않는다.
"""
import time

import pytest

pytest.importorskip("fastapi")

import httpx  # noqa: E402

from glople_agent.graph.build import run_config  # noqa: E402
from service_kit import ALICE, API, BOB, Blocker, auth, in_thread, service_kit  # noqa: E402

OTHER_ID = "00000000-0000-4000-8000-000000000000"
PLAN_TEXT = "제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘"


@pytest.fixture
def kit(tmp_path):
    with service_kit(tmp_path) as k:
        yield k


# --- 인증 ---------------------------------------------------------------------------------

def test_health_is_public_and_hides_addresses(kit):
    r = kit.client.get(f"{API}/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "llm": "rule", "spring": "down"}
    assert "spring.test" not in r.text and "8090" not in r.text


@pytest.mark.parametrize("header", [None, "", "Bearer", "Bearer ", "Bearer undefined", "Bearer null", "Basic abc",
                                    "Bearer 한글토큰", "Bearer a b", "Bearer " + "x" * 5000])
def test_missing_or_unusable_token_is_401_without_asking_spring(kit, header):
    headers = {} if header is None else {"Authorization": header.encode("utf-8")}   # 한글은 바이트로만 보낼 수 있음
    r = kit.client.get(f"{API}/threads", headers=headers)
    assert r.status_code == 401
    assert r.json() == {"status": 401, "error": "UNAUTHORIZED", "message": "로그인이 필요합니다.",
                        "path": f"{API}/threads", "code": "UNAUTHORIZED"}
    assert kit.svc.auth.spring_calls == 0


def test_unknown_token_is_401_after_my_info(kit):
    r = kit.client.post(f"{API}/threads", headers=auth("expired-token"))
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHORIZED"
    route = kit.spring.routes[0]
    assert route.call_count == 1
    assert route.calls[0].request.headers["authorization"] == "Bearer expired-token"


def test_my_info_is_cached_by_token_hash(kit):
    for _ in range(3):
        assert kit.client.get(f"{API}/threads", headers=ALICE).status_code == 200
    assert kit.client.get(f"{API}/threads", headers=BOB).status_code == 200
    assert kit.svc.auth.spring_calls == 2   # 사용자마다 한 번
    assert all(ALICE["Authorization"].split()[1] not in k for k in kit.svc.auth._cache)   # 키는 sha256


def test_spring_down_is_503(kit):
    kit.spring.get("/api/users/my-info").mock(side_effect=httpx.ConnectError("down"))
    r = kit.client.get(f"{API}/threads", headers=ALICE)
    assert r.status_code == 503 and r.json()["code"] == "AUTH_UNAVAILABLE"


def test_401_before_body_validation(kit):
    r = kit.client.post(f"{API}/threads/{OTHER_ID}/messages", json={"text": "안녕", "userId": "bob"})
    assert r.status_code == 401


# --- 스레드 주인 ---------------------------------------------------------------------------

def test_create_list_get_delete_own_thread(kit):
    tid = kit.new_thread()
    assert kit.client.get(f"{API}/threads", headers=ALICE).json()["threads"][0]["threadId"] == tid
    assert kit.client.get(f"{API}/threads", headers=BOB).json() == {"threads": []}
    view = kit.client.get(f"{API}/threads/{tid}", headers=ALICE).json()
    assert view["threadId"] == tid and view["turns"] == [] and view["pendingConfirm"] is None
    assert kit.client.delete(f"{API}/threads/{tid}", headers=ALICE).status_code == 204
    assert kit.client.get(f"{API}/threads/{tid}", headers=ALICE).status_code == 403


@pytest.mark.parametrize("target", ["bob", "missing", "malformed"])
def test_other_users_or_missing_thread_is_403_everywhere(kit, target):
    alice_thread = kit.new_thread()
    tid = {"bob": alice_thread, "missing": OTHER_ID, "malformed": "not-a-uuid"}[target]
    headers = BOB if target == "bob" else ALICE
    expected = {"status": 403, "error": "FORBIDDEN", "message": "권한이 없습니다.", "code": "FORBIDDEN"}
    calls = [
        kit.client.get(f"{API}/threads/{tid}", headers=headers),
        kit.client.delete(f"{API}/threads/{tid}", headers=headers),
        kit.client.post(f"{API}/threads/{tid}/messages", json={"text": "안녕"}, headers=headers),
        kit.client.post(f"{API}/threads/{tid}/resume", json={"interruptId": "x", "decision": "approve"},
                        headers=headers),
        kit.client.post(f"{API}/threads/{tid}/cancel", headers=headers),
    ]
    for r in calls:
        assert r.status_code == 403, r.text
        body = r.json()
        assert {k: body[k] for k in expected} == expected
    # 앨리스의 대화는 그대로
    assert kit.client.get(f"{API}/threads/{alice_thread}", headers=ALICE).status_code == 200


def test_bob_cannot_read_alices_turns(kit):
    tid = kit.new_thread()
    kit.say(tid, "바나힐 정보랑 사람들 리뷰 같이 알려줘")
    r = kit.client.get(f"{API}/threads/{tid}", headers=BOB)
    assert r.status_code == 403 and "바나힐" not in r.text


# --- 본문 규칙 -----------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    {"text": "안녕", "userId": "bob-uid-2290"},
    {"text": "안녕", "nickname": "밥"},
    {"text": ""},
    {"text": "   "},
    {"text": "가" * 501},
    {"text": 3},
    {},
    {"text": "안녕", "clientMessageId": "a b"},
])
def test_message_body_rules_are_422(kit, body):
    tid = kit.new_thread()
    r = kit.client.post(f"{API}/threads/{tid}/messages", json=body, headers=ALICE)
    assert r.status_code == 422
    data = r.json()
    assert data["status"] == 422 and data["code"] == "UNPROCESSABLE" and data["errors"]
    assert "bob-uid-2290" not in r.text and "가" * 50 not in r.text   # 입력 값을 되돌려 주지 않음


def test_actor_fields_rejected_on_create_and_resume_and_review(kit):
    assert kit.client.post(f"{API}/threads", json={"userId": "x"}, headers=ALICE).status_code == 422
    tid = kit.new_thread()
    r = kit.client.post(f"{API}/threads/{tid}/resume", json={"interruptId": "x", "decision": "approve",
                                                              "userId": "x"}, headers=ALICE)
    assert r.status_code == 422
    r = kit.client.post(f"{API}/threads/{tid}/resume", json={"interruptId": "x", "decision": "maybe"}, headers=ALICE)
    assert r.status_code == 422
    r = kit.client.post(f"{API}/threads/{tid}/resume", json={"interruptId": "x", "decision": "edit"}, headers=ALICE)
    assert r.status_code == 422 and r.json()["errors"][0]["field"] == "draft"
    assert kit.client.post(f"{API}/plans/1/review", json={"userId": "x"}, headers=ALICE).status_code == 422


def test_body_over_limit_is_413(kit):
    tid = kit.new_thread()
    r = kit.client.post(f"{API}/threads/{tid}/messages", content=b'{"text": "' + b"a" * 20_000 + b'"}',
                        headers={**ALICE, "content-type": "application/json"})
    assert r.status_code == 413 and r.json()["code"] == "PAYLOAD_TOO_LARGE"


def test_unknown_route_is_json_404(kit):
    r = kit.client.get(f"{API}/nope", headers=ALICE)
    assert r.status_code == 404 and r.json()["status"] == 404


# --- SSE ---------------------------------------------------------------------------------

def test_message_stream_order_cards_before_answer(kit):
    tid = kit.new_thread()
    s = kit.say(tid, "바나힐 정보랑 사람들 리뷰 같이 알려줘")
    assert s.status == 200
    assert s.headers["content-type"].startswith("text/event-stream")
    assert s.headers["cache-control"] == "no-cache, no-transform" and s.headers["x-accel-buffering"] == "no"
    names = s.names()
    assert names[0] == "meta" and names[-2:] == ["final", "done"]
    assert names.index("card") < names.index("final")
    nodes = [d["node"] for d in s.all("node")]
    assert nodes == ["start_turn", "route", "validate", "tools", "collect", "answer_template", "finish"]
    meta = s.first("meta")
    assert meta["threadId"] == tid and meta["turnId"].startswith("turn-")
    cards = [d["card"] for d in s.all("card")]
    assert [c["tool"] for c in cards] == ["get_destination_detail", "search_reviews"]
    assert all(c["turnId"] == meta["turnId"] and c["cardId"] and c["data"] for c in cards)
    final = s.first("final")
    assert final["kind"] == "template" and "바나힐" in final["text"] and final["replaced"] is False
    assert s.first("done") == {"reason": "final"}
    assert "__interrupt__" not in s.raw and "trace" not in s.raw   # LangGraph 내부 모양·trace 없음


def test_llm_mode_streams_tokens_then_final(tmp_path):
    with service_kit(tmp_path, answer_mode="llm") as kit:
        tid = kit.new_thread()
        s = kit.say(tid, "바나힐 정보랑 사람들 리뷰 같이 알려줘")
        tokens = "".join(d["text"] for d in s.all("token"))
        final = s.first("final")
        assert final["kind"] == "llm" and tokens.strip() == final["text"]
        names = s.names()
        assert names.index("card") < names.index("token") < names.index("final")


def test_plan_confirm_approve_saves_once(kit):
    tid = kit.new_thread()
    s = kit.say(tid, PLAN_TEXT)
    assert s.names()[-2:] == ["confirm", "done"] and s.first("done") == {"reason": "confirm"}
    confirm = s.first("confirm")
    assert confirm["interruptId"] and confirm["draft"]["region"] == "제주"
    assert [i["name"] for i in confirm["draft"]["items"]] == ["성산일출봉", "우도"]
    assert confirm["editsLeft"] == 3 and confirm["message"].startswith("'제주 2박 3일'")
    view = kit.client.get(f"{API}/threads/{tid}", headers=ALICE).json()
    assert view["pendingConfirm"]["interruptId"] == confirm["interruptId"]

    done = kit.answer(tid, confirm["interruptId"], "approve")
    assert done.first("meta")["turnId"] == s.first("meta")["turnId"]
    assert done.first("final") == {"text": "'제주 2박 3일' 일정을 저장했어요.", "kind": "plan", "replaced": False}
    saved = [d["card"] for d in done.all("card") if d["card"]["kind"] == "plan_saved"]
    assert saved and saved[0]["data"]["plan_id"] == 1
    assert kit.store.save_count == 1
    again = kit.answer(tid, confirm["interruptId"], "approve")   # 같은 재개 두 번 → 409, 저장 1건 그대로
    assert again.status == 409 and again.body["code"] == "INTERRUPT_NOT_PENDING"
    assert kit.store.save_count == 1
    view = kit.client.get(f"{API}/threads/{tid}", headers=ALICE).json()
    assert view["pendingConfirm"] is None and view["turns"][-1]["answer"] == "'제주 2박 3일' 일정을 저장했어요."
    assert view["turns"][-1]["kind"] == "plan"


def test_plan_edit_then_reject(kit):
    tid = kit.new_thread()
    first = kit.say(tid, PLAN_TEXT).first("confirm")
    edited = kit.answer(tid, first["interruptId"], "edit",
                        draft={"region": "제주", "days": 2, "places": ["성산일출봉", "우도", "오설록"]})
    second = edited.first("confirm")
    assert second["interruptId"] != first["interruptId"] and second["editsLeft"] == 2
    assert [i["name"] for i in second["draft"]["items"]] == ["성산일출봉", "우도", "오설록"]
    assert kit.answer(tid, first["interruptId"], "approve").status == 409   # 예전 카드 id
    bad = kit.answer(tid, second["interruptId"], "edit", draft={"region": "제주", "days": 0, "places": ["우도"]})
    third = bad.first("confirm")
    assert third["problems"]   # 범위 밖 일수 → 카드에 문제 표시(저장 안 함)
    rejected = kit.answer(tid, third["interruptId"], "reject")
    assert rejected.first("final")["text"] == "저장하지 않았어요."
    assert kit.store.save_count == 0


def test_new_message_while_card_pending_cancels_card(kit):
    tid = kit.new_thread()
    confirm = kit.say(tid, PLAN_TEXT).first("confirm")
    s = kit.say(tid, "안녕")
    cancelled = [d["card"] for d in s.all("card") if d["card"]["kind"] == "plan_confirm"]
    assert cancelled and cancelled[0]["status"] == "error" and cancelled[0]["data"]["cancelled"] is True
    assert s.first("final")["kind"] == "direct"
    assert kit.answer(tid, confirm["interruptId"], "approve").status == 409
    view = kit.client.get(f"{API}/threads/{tid}", headers=ALICE).json()
    assert [t["user"] for t in view["turns"]] == [PLAN_TEXT, "안녕"]
    assert view["turns"][0]["answer"] == "저장하지 않았어요." and view["pendingConfirm"] is None


def test_thread_view_restores_turns_with_cards(kit):
    tid = kit.new_thread()
    kit.say(tid, "바나힐 정보랑 사람들 리뷰 같이 알려줘")
    kit.say(tid, "안녕")
    view = kit.client.get(f"{API}/threads/{tid}", headers=ALICE).json()
    assert view["title"] == "바나힐 정보랑 사람들 리뷰 같이 알려줘"
    first, second = view["turns"]
    assert [c["tool"] for c in first["cards"]] == ["get_destination_detail", "search_reviews"]
    assert first["answer"] and second["cards"] == [] and second["kind"] == "direct"
    assert "ToolMessage" not in str(view) and "tool_calls" not in str(view)


def test_delete_removes_checkpoints(kit):
    tid = kit.new_thread()
    kit.say(tid, "안녕")
    graph = kit.svc.graph
    assert kit.client.portal.call(graph.aget_state, run_config(tid)).values
    assert kit.client.delete(f"{API}/threads/{tid}", headers=ALICE).status_code == 204
    assert kit.client.portal.call(graph.aget_state, run_config(tid)).values == {}
    assert kit.client.get(f"{API}/threads", headers=ALICE).json() == {"threads": []}


def test_duplicate_client_message_id_is_409(kit):
    tid = kit.new_thread()
    assert kit.say(tid, "안녕", clientMessageId="m-1").status == 200
    dup = kit.say(tid, "안녕", clientMessageId="m-1")
    assert dup.status == 409 and dup.body["code"] == "DUPLICATE_MESSAGE"
    assert kit.say(tid, "안녕", clientMessageId="m-2").status == 200


def test_rate_limit_is_429(tmp_path):
    with service_kit(tmp_path, rate_limit_turns=2) as kit:
        tid = kit.new_thread()
        assert kit.say(tid, "안녕").status == 200
        assert kit.say(tid, "안녕").status == 200
        third = kit.say(tid, "안녕")
        assert third.status == 429 and third.body["code"] == "RATE_LIMITED"
        assert kit.say(kit.new_thread(BOB), "안녕", headers=BOB).status == 200   # 사용자별


def test_thread_limit(tmp_path):
    with service_kit(tmp_path, max_threads_per_user=1) as kit:
        kit.new_thread()
        r = kit.client.post(f"{API}/threads", headers=ALICE)
        assert r.status_code == 409 and r.json()["code"] == "THREAD_LIMIT"


# --- 실행 수명 ------------------------------------------------------------------------------

def test_second_run_on_same_thread_is_409_busy(tmp_path):
    blocker = Blocker()
    with service_kit(tmp_path, router=blocker) as kit:
        tid = kit.new_thread()
        other = kit.new_thread()
        t, out = in_thread(lambda: kit.say(tid, "안녕"))
        assert blocker.entered.wait(5)
        busy = kit.say(tid, "안녕")
        assert busy.status == 409 and busy.body["code"] == "THREAD_BUSY"
        assert kit.client.delete(f"{API}/threads/{tid}", headers=ALICE).status_code == 409
        listed = {r["threadId"]: r["running"] for r in kit.client.get(f"{API}/threads", headers=ALICE).json()["threads"]}
        assert listed == {tid: True, other: False}
        blocker.release()
        t.join(10)
        assert out["value"].first("final")["kind"] == "direct"
        assert kit.say(tid, "안녕").status == 200   # 끝나면 자리가 풀림
        assert len(kit.svc.registry) == 0


def test_cancel_stops_running_turn(tmp_path):
    blocker = Blocker()
    with service_kit(tmp_path, router=blocker) as kit:
        tid = kit.new_thread()
        t, out = in_thread(lambda: kit.say(tid, "안녕"))
        assert blocker.entered.wait(5)
        r = kit.client.post(f"{API}/threads/{tid}/cancel", headers=ALICE)
        assert r.status_code == 202 and r.json() == {"cancelled": True}
        t.join(10)
        s = out["value"]
        assert s.first("error")["code"] == "CANCELLED" and s.first("done") == {"reason": "cancelled"}
        blocker.release()
        deadline = time.time() + 5
        while len(kit.svc.registry) and time.time() < deadline:
            time.sleep(0.02)
        assert kit.client.post(f"{API}/threads/{tid}/cancel", headers=ALICE).json() == {"cancelled": False}
        assert kit.say(tid, "안녕").first("final")["kind"] == "direct"   # 다음 턴은 정상


def test_turn_timeout_emits_error(tmp_path):
    blocker = Blocker(timeout=1.0)
    with service_kit(tmp_path, router=blocker, turn_timeout=0.2) as kit:
        tid = kit.new_thread()
        s = kit.say(tid, "안녕")
        assert s.status == 200 and s.first("error")["code"] == "TURN_TIMEOUT" and s.first("error")["retryable"]
        assert s.names()[-1] == "done"
        blocker.release()


def test_model_busy_when_slot_is_taken(tmp_path):
    with service_kit(tmp_path, model_wait_seconds=0.05) as kit:
        tid = kit.new_thread()
        kit.client.portal.call(kit.svc.slot._sem.acquire)
        try:
            s = kit.say(tid, "안녕")
            assert s.first("error")["code"] == "MODEL_BUSY" and s.names()[-1] == "done"
        finally:
            kit.svc.slot._sem.release()
        assert kit.say(tid, "안녕").first("final")["kind"] == "direct"


def test_confirm_expired_is_409(tmp_path):
    with service_kit(tmp_path, confirm_ttl_seconds=-1) as kit:
        tid = kit.new_thread()
        confirm = kit.say(tid, PLAN_TEXT).first("confirm")
        r = kit.answer(tid, confirm["interruptId"], "approve")
        assert r.status == 409 and r.body["code"] == "CONFIRM_EXPIRED"
        assert kit.store.save_count == 0


def test_graph_error_becomes_error_event(tmp_path):
    def broken(messages, kwargs):
        raise RuntimeError("우리 쪽 버그 흉내")

    with service_kit(tmp_path, router=broken) as kit:
        tid = kit.new_thread()
        s = kit.say(tid, "안녕")
        assert s.first("error") == {"code": "INTERNAL", "message": "답을 만들지 못했어요. 잠시 뒤 다시 시도해 주세요.",
                                    "retryable": True}
        assert "버그 흉내" not in s.raw
        assert len(kit.svc.registry) == 0


def test_persisted_threads_survive_restart(tmp_path):
    with service_kit(tmp_path) as kit:
        tid = kit.new_thread()
        confirm = kit.say(tid, PLAN_TEXT).first("confirm")
    with service_kit(tmp_path) as kit:   # 같은 data_dir로 다시 띄움(AsyncSqliteSaver·주인 표)
        view = kit.client.get(f"{API}/threads/{tid}", headers=ALICE).json()
        assert view["pendingConfirm"]["interruptId"] == confirm["interruptId"]
        assert kit.client.get(f"{API}/threads/{tid}", headers=BOB).status_code == 403
        assert kit.answer(tid, confirm["interruptId"], "approve").first("final")["kind"] == "plan"


def test_settings_reject_bad_values():
    from glople_agent.config import SettingsError
    from glople_agent.service.settings import ServiceSettings

    with pytest.raises(SettingsError):
        ServiceSettings(llm="gpt")
    s = ServiceSettings.from_env({"GLOPLE_SERVICE_LLM": "fake"})
    assert s.plan_store == "memory" and s.host == "127.0.0.1" and s.port == 8095
    assert ServiceSettings.from_env({}).plan_store == "spring"
