"""SpringPlanStore(M3, plan/spring_store.py): S2 `/api/travel-plans` 계약을 respx로만 고정한다(8080 접속 없음).

- 로그인 경로(travel-plans)에만 사용자 토큰, 공개 경로(읽기 도구)에는 절대 없음 — 그래프 전 구간에서도 확인.
- MODEL_SUGGESTED(selected=false)는 보내지 않음, source는 USER·TOOL_RESULT·USER_EDIT 그대로.
- 201 새로 / 200 같은 키 / 400 검증 JSON → PlanRejected / 401 → AuthExpired(review로 돌아가 재승인) / 5xx → 재시도 1회.
"""
import json
import logging

import httpx
import pytest
import respx

from glople_agent.answer import templates as tpl
from glople_agent.context import AgentContext, BearerToken
from glople_agent.graph import make_plan_store, real_deps
from glople_agent.llm.fake import QuirkScript, RuleScript, tool_call_message
from glople_agent.plan import AuthExpired, PlanRejected, PlanStoreError, make_draft, save_body
from glople_agent.plan.spring_store import (PlanBadResponse, PlanForbidden, PlanRequestError, PlanUpstreamError,
                                            SpringPlanStore, outgoing_places, travel_plan_body)
from glople_agent.tools.spring import ENDPOINTS, SpringClient

BASE = "http://spring.test"
CALL_ID = "call_plan_1"


def plan_json(*, plan_id=7, key=CALL_ID, title="제주 2박 3일", places=None, **extra) -> dict:
    places = places if places is not None else [
        {"name": "성산일출봉", "dayNo": 1, "position": 1, "source": "USER"},
        {"name": "우도", "dayNo": 2, "position": 2, "source": "USER"}]
    return {"id": plan_id, "clientRequestId": key, "title": title, "region": "제주", "days": 3, "places": places,
            "createdAt": "2026-10-18T12:00:00", **extra}


def draft(places=("성산일출봉", "오설록", "우도"), recent=()) -> dict:
    return save_body(make_draft({"region": "제주", "days": 3, "places": list(places)}, client_request_id=CALL_ID,
                                utterance="제주 2박 3일 성산일출봉이랑 우도 저장해줘", recent_places=recent))


@pytest.fixture
def mock():
    with respx.mock(base_url=BASE, assert_all_called=False) as m:
        yield m


@pytest.fixture
def store():
    client = SpringClient(BASE)
    yield SpringPlanStore(client)
    client.close()


def _auth(route) -> list[str | None]:
    return [c.request.headers.get("authorization") for c in route.calls]


# --- 본문 -------------------------------------------------------------------------------

def test_body_has_contract_keys_and_never_model_suggested(sentinel_token):
    d = draft(recent=["오설록"])   # 오설록이 최근 결과에 있으면 TOOL_RESULT(보냄)
    assert [(i["name"], i["source"]) for i in d["items"]] == [("성산일출봉", "USER"), ("오설록", "TOOL_RESULT"),
                                                             ("우도", "USER")]
    body = travel_plan_body(d, CALL_ID)
    assert list(body) == ["clientRequestId", "title", "region", "days", "places"]   # userId 같은 행위자 필드 없음
    assert body["places"][1] == {"name": "오설록", "dayNo": 2, "position": 2, "source": "TOOL_RESULT"}
    plain = draft()   # 근거 없는 오설록 = MODEL_SUGGESTED(기본 해제) → 빠짐
    full = make_draft({"region": "제주", "days": 3, "places": ["성산일출봉", "오설록", "우도"]},
                      client_request_id=CALL_ID, utterance="제주 성산일출봉 우도")
    assert [i["source"] for i in full["items"]] == ["USER", "MODEL_SUGGESTED", "USER"]
    assert [p["name"] for p in outgoing_places(full)] == ["성산일출봉", "우도"]
    assert [p["position"] for p in outgoing_places(full)] == [1, 2]
    # 실수로 선택된 MODEL_SUGGESTED가 와도 보내지 않음(방어). USER_EDIT는 그대로
    odd = {**plain, "items": [{**plain["items"][0], "source": "MODEL_SUGGESTED"},
                              {**plain["items"][1], "source": "USER_EDIT"}]}
    assert [(p["name"], p["source"]) for p in outgoing_places(odd)] == [("우도", "USER_EDIT")]


# --- save -------------------------------------------------------------------------------

def test_201_then_same_key_200_with_token_only_on_that_request(store, mock, sentinel_token, sentinel_raw):
    route = mock.post("/api/travel-plans").mock(side_effect=[httpx.Response(201, json=plan_json()),
                                                             httpx.Response(200, json=plan_json())])
    first = store.save("u1", draft(), client_request_id=CALL_ID, auth=sentinel_token)
    again = store.save("u1", draft(), client_request_id=CALL_ID, auth=sentinel_token)
    assert (first.plan_id, first.created, first.status) == (7, True, 201)
    assert (again.plan_id, again.created, again.status) == (7, False, 200)
    assert first.created_at == "2026-10-18T12:00:00"
    assert first.plan["clientRequestId"] == CALL_ID and [i["name"] for i in first.plan["items"]] == ["성산일출봉", "우도"]
    assert _auth(route) == [f"Bearer {sentinel_raw}"] * 2
    sent = [json.loads(c.request.content) for c in route.calls]
    assert sent[0] == sent[1] and sent[0]["clientRequestId"] == CALL_ID   # 같은 멱등키
    assert "authorization" not in store.client._http.headers   # 기본 헤더에 남지 않음


def test_400_validation_json_becomes_problems(store, mock, sentinel_token):
    mock.post("/api/travel-plans").respond(400, json={
        "status": 400, "error": "Bad Request", "message": "검증 실패", "path": "/api/travel-plans",
        "errors": [{"field": "days", "code": "Range", "message": "일수는 1~30일이에요."}]})
    with pytest.raises(PlanRejected) as info:
        store.save("u1", draft(), client_request_id=CALL_ID, auth=sentinel_token)
    assert info.value.problems == [{"field": "days", "code": "Range", "message": "일수는 1~30일이에요."}]
    mock.post("/api/travel-plans").respond(400, json={"status": 400, "message": "제목이 너무 길어요"})
    with pytest.raises(PlanRejected) as info:
        store.save("u1", draft(), client_request_id=CALL_ID, auth=sentinel_token)
    assert info.value.problems[0]["message"] == "제목이 너무 길어요" and not info.value.retryable


@pytest.mark.parametrize("status,error,retryable", [
    (401, AuthExpired, False), (403, PlanForbidden, False), (404, PlanRequestError, False),
    (500, PlanUpstreamError, True), (503, PlanUpstreamError, True)])
def test_status_codes_map_to_store_errors(store, mock, sentinel_token, status, error, retryable):
    route = mock.post("/api/travel-plans").respond(status, json={"status": status})
    with pytest.raises(error) as info:
        store.save("u1", draft(), client_request_id=CALL_ID, auth=sentinel_token)
    assert info.value.retryable is retryable and route.call_count == 1   # POST는 클라이언트가 재시도하지 않음


def test_connection_error_and_timeout_are_retryable(store, mock, sentinel_token):
    mock.post("/api/travel-plans").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(PlanUpstreamError) as info:
        store.save("u1", draft(), client_request_id=CALL_ID, auth=sentinel_token)
    assert info.value.code == "upstream_unavailable" and info.value.retryable
    mock.post("/api/travel-plans").mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(PlanUpstreamError) as info:
        store.save("u1", draft(), client_request_id=CALL_ID, auth=sentinel_token)
    assert info.value.code == "timeout" and info.value.retryable   # 같은 멱등키로 다시 보내도 안전


@pytest.mark.parametrize("body", [plan_json(plan_id=None), plan_json(plan_id=True), plan_json(key="other-key"),
                                  plan_json(places=[{"dayNo": 1}]), ["not", "an", "object"]])
def test_bad_responses_are_not_retried(store, mock, sentinel_token, body):
    mock.post("/api/travel-plans").respond(201, json=body)
    with pytest.raises(PlanBadResponse) as info:
        store.save("u1", draft(), client_request_id=CALL_ID, auth=sentinel_token)
    assert not info.value.retryable


def test_no_token_or_no_places_sends_nothing(store, mock, sentinel_token):
    route = mock.post("/api/travel-plans").respond(201, json=plan_json())
    with pytest.raises(AuthExpired):
        store.save("u1", draft(), client_request_id=CALL_ID, auth=None)
    only_suggested = make_draft({"region": "제주", "days": 2, "places": ["지어낸 곳"]}, client_request_id=CALL_ID,
                                utterance="제주 일정")
    with pytest.raises(PlanRejected):
        store.save("u1", only_suggested, client_request_id=CALL_ID, auth=sentinel_token)
    with pytest.raises(ValueError):
        store.save("u1", draft(), client_request_id="different", auth=sentinel_token)
    with pytest.raises(TypeError):
        store.save("u1", draft(), client_request_id=CALL_ID, auth="Bearer raw")   # type: ignore[arg-type]
    with pytest.raises(AuthExpired):   # JWT는 ASCII. 아닌 값은 보내지 않고 다시 로그인하게
        store.save("u1", draft(), client_request_id=CALL_ID, auth=BearerToken("다시-로그인한-토큰"))
    assert route.call_count == 0


# --- 목록·조회·삭제 ----------------------------------------------------------------------------

def test_list_get_delete_use_token_and_hide_others(store, mock, sentinel_token, sentinel_raw):
    lst = mock.get("/api/travel-plans").respond(200, json=[plan_json(), plan_json(plan_id=8, key="k2")])
    mine = mock.get("/api/travel-plans/7").respond(200, json=plan_json())
    other = mock.get("/api/travel-plans/9").respond(403, json={"status": 403})
    gone = mock.delete("/api/travel-plans/7").respond(204)
    not_mine = mock.delete("/api/travel-plans/9").respond(403)
    assert [p.plan_id for p in store.list_plans(auth=sentinel_token)] == [7, 8]
    found = store.get(7, auth=sentinel_token)
    assert found is not None and found.plan["title"] == "제주 2박 3일" and found.created_at == "2026-10-18T12:00:00"
    assert store.get(9, auth=sentinel_token) is None   # 남의 것·없는 것은 구별하지 않음(403)
    assert store.delete(7, auth=sentinel_token) is True and store.delete(9, auth=sentinel_token) is False
    for route in (lst, mine, other, gone, not_mine):
        assert _auth(route) == [f"Bearer {sentinel_raw}"]
    mock.get("/api/travel-plans").respond(401)
    with pytest.raises(AuthExpired):
        store.list_plans(auth=sentinel_token)


def test_get_retries_once_but_delete_does_not(store, mock, sentinel_token):
    get = mock.get("/api/travel-plans/7").mock(side_effect=[httpx.Response(503), httpx.Response(200, json=plan_json())])
    assert store.get(7, auth=sentinel_token).plan_id == 7 and get.call_count == 2
    delete = mock.delete("/api/travel-plans/7").respond(503)
    with pytest.raises(PlanUpstreamError):
        store.delete(7, auth=sentinel_token)
    assert delete.call_count == 1


# --- SpringClient 경로 변수 ---------------------------------------------------------------------

def test_path_variable_is_a_positive_int_only(mock, sentinel_token):
    client = SpringClient(BASE)
    route = mock.get("/api/travel-plans/3").respond(200, json=plan_json(plan_id=3))
    for bad in ({"id": 0}, {"id": -1}, {"id": True}, {"id": "3"}, {"id": "3/../admin"}, {}, {"id": 3, "x": 1}):
        with pytest.raises(ValueError):
            client.call("travel_plan_get", path_params=bad, auth=sentinel_token)
    with pytest.raises(ValueError):
        client.call("travel_plan_list", path_params={"id": 3}, auth=sentinel_token)   # 변수 없는 경로
    for path in ("/api/travel-plans/abc", "/api/travel-plans/0", "/api/travel-plans/3/places", "/api/admin/3"):
        with pytest.raises(PermissionError):
            client.request("GET", path, auth=sentinel_token)
    with pytest.raises(PermissionError):
        client.request("DELETE", "/api/travel-plans", auth=sentinel_token)   # 목록 삭제 같은 경로는 없음
    with pytest.raises(ValueError, match="BearerToken"):
        client.call("travel_plan_get", path_params={"id": 3})                # 로그인 경로는 토큰 필수
    with pytest.raises(ValueError, match="4xx"):
        client.call("travel_plan_get", path_params={"id": 3}, auth=sentinel_token, accept={500})
    assert client.request("GET", "/api/travel-plans/3", auth=sentinel_token).data["id"] == 3
    assert route.call_count == 1 and not [c for c in mock.calls if c.request.url.path != "/api/travel-plans/3"]
    assert {ENDPOINTS[n].access for n in ENDPOINTS if n.startswith("travel_plan")} == {"user"}
    client.close()


# --- 그래프 전 구간 ---------------------------------------------------------------------------

PLAN_TEXT = "제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘"


@pytest.fixture
def spring_kit(make_graph, request):
    store = SpringPlanStore(SpringClient(BASE))
    kit = make_graph(RuleScript(), store=store, plan_graph=getattr(request, "param", "flat"))
    yield kit
    store.client.close()


@pytest.mark.parametrize("spring_kit", ["flat", "subgraph"], indirect=True)
def test_graph_saves_through_spring_after_approval_only(spring_kit, mock, agent_context, sentinel_raw, caplog):
    caplog.set_level(logging.DEBUG)
    route = mock.post("/api/travel-plans").respond(201, json=plan_json(key=None))
    kit = spring_kit
    lookup = kit.turn("바나힐 정보랑 사람들 리뷰 같이 알려줘", agent_context)   # 읽기 도구 = 가짜 Spring(MockTransport)
    assert lookup.reply_kind == "template"
    first = kit.turn(PLAN_TEXT, agent_context)
    assert route.call_count == 0   # 확인 전에는 저장 요청이 나가지 않음
    call_id = first.pending.value["tool_call_id"]
    route.respond(201, json=plan_json(key=call_id))
    done = kit.resume({"decision": "approve"}, agent_context)
    assert done.final_text == "'제주 2박 3일' 일정을 저장했어요." and route.call_count == 1
    [call] = route.calls
    assert call.request.headers["authorization"] == f"Bearer {sentinel_raw}"
    assert json.loads(call.request.content)["clientRequestId"] == call_id
    [card] = [c for c in done.cards if c["kind"] == "plan_saved"]
    assert card["data"]["plan_id"] == 7 and card["data"]["saved"]["clientRequestId"] == call_id
    # 읽기 도구는 공개 API만, 토큰 없이(가짜 Spring 요청 기록)
    assert kit.backend.fixture.requests and not any(r.has_authorization for r in kit.backend.fixture.requests)
    log_text = "\n".join(logging.Formatter().format(r) for r in caplog.records)
    assert sentinel_raw not in log_text and "api/travel-plans" in log_text   # httpx 로그에 경로는 있어도 토큰은 없음


@pytest.mark.parametrize("spring_kit", ["flat", "subgraph"], indirect=True)
def test_graph_401_shows_card_again_then_new_token_saves_once(spring_kit, mock, agent_context, fixed_now, seed):
    kit = spring_kit
    first = kit.turn(PLAN_TEXT, agent_context)
    call_id = first.pending.value["tool_call_id"]
    route = mock.post("/api/travel-plans").mock(side_effect=[httpx.Response(401),
                                                             httpx.Response(201, json=plan_json(key=call_id))])
    again = kit.resume({"decision": "approve"}, agent_context)
    assert again.pending.value["notice"] == "AUTH_EXPIRED" and route.call_count == 1
    fresh = AgentContext(user_id=agent_context.user_id, bearer=BearerToken("fresh-token-after-login"),
                         request_id="r2", now=fixed_now, seed=seed)
    done = kit.resume({"decision": "approve"}, fresh)
    assert done.done and route.call_count == 2
    assert [c.request.headers["authorization"] for c in route.calls][1] == "Bearer fresh-token-after-login"


@pytest.mark.parametrize("spring_kit", ["flat"], indirect=True)
def test_graph_5xx_is_retried_once_with_the_same_key(spring_kit, mock, agent_context):
    kit = spring_kit
    first = kit.turn(PLAN_TEXT, agent_context)
    call_id = first.pending.value["tool_call_id"]
    route = mock.post("/api/travel-plans").mock(side_effect=[httpx.Response(502),
                                                             httpx.Response(200, json=plan_json(key=call_id))])
    done = kit.resume({"decision": "approve"}, agent_context)
    assert done.final_text == "'제주 2박 3일' 일정을 저장했어요." and route.call_count == 2
    keys = {json.loads(c.request.content)["clientRequestId"] for c in route.calls}
    assert keys == {call_id}


@pytest.mark.parametrize("spring_kit", ["flat"], indirect=True)
def test_graph_400_gives_fixed_failure_message(spring_kit, mock, agent_context):
    kit = spring_kit
    kit.turn(PLAN_TEXT, agent_context)
    mock.post("/api/travel-plans").respond(400, json={"status": 400, "message": "검증 실패"})
    done = kit.resume({"decision": "approve"}, agent_context)
    assert done.done and done.final_text == tpl.PLAN_SAVE_FAILED


def test_model_suggested_place_is_not_sent_from_graph(make_graph, mock, agent_context):
    raw = tool_call_message([("create_travel_plan", {"region": "제주", "days": 3,
                                                     "places": ["성산일출봉", "오설록", "우도"]})])
    store = SpringPlanStore(SpringClient(BASE))
    kit = make_graph(QuirkScript([raw]), store=store)
    first = kit.turn("제주 2박 3일 성산일출봉이랑 우도 저장해줘", agent_context)
    route = mock.post("/api/travel-plans").respond(201, json=plan_json(key=first.pending.value["tool_call_id"]))
    kit.resume({"decision": "approve"}, agent_context)
    places = json.loads(route.calls.last.request.content)["places"]
    assert [(p["name"], p["source"], p["position"]) for p in places] == [("성산일출봉", "USER", 1), ("우도", "USER", 2)]
    store.client.close()


# --- 연결(real_deps·Settings) -----------------------------------------------------------------

def test_real_deps_flag_picks_store_without_network(monkeypatch):
    from glople_agent.config import Settings
    from glople_agent.plan import InMemoryPlanStore

    assert isinstance(real_deps("http://127.0.0.1:1/v1").plan_store, InMemoryPlanStore)   # 기본은 메모리
    deps = real_deps("http://127.0.0.1:1/v1", plan_store="spring", spring_base_url=BASE)
    assert isinstance(deps.plan_store, SpringPlanStore) and deps.plan_store.client.base_url == BASE
    settings = Settings.from_env({"GLOPLE_PLAN_STORE": "spring", "GLOPLE_SPRING_BASE_URL": BASE})
    assert isinstance(real_deps(settings).plan_store, SpringPlanStore)
    assert Settings.from_env({}).plan_store == "memory"
    with pytest.raises(ValueError):
        Settings.from_env({"GLOPLE_PLAN_STORE": "mysql"})
    with pytest.raises(ValueError):
        make_plan_store("mysql")   # type: ignore[arg-type]


def test_store_errors_keep_plan_store_error_contract():
    for cls in (PlanForbidden, PlanBadResponse, PlanUpstreamError):
        assert issubclass(cls, PlanStoreError)
    assert PlanUpstreamError("timeout").code == "timeout" and PlanRequestError(404).status == 404


# --- 리뷰에서 고친 것 ---------------------------------------------------------------------

def test_token_with_control_characters_is_never_sent_or_printed(store, mock, sentinel_raw):
    import traceback

    route = mock.post("/api/travel-plans").respond(201, json=plan_json())
    for bad in (sentinel_raw + "\nX-Evil: 1", sentinel_raw + " tail", "토큰"):
        with pytest.raises(AuthExpired) as info:
            store.save("u1", draft(), client_request_id=CALL_ID, auth=BearerToken(bad))
        assert sentinel_raw not in "".join(traceback.format_exception(info.value))
    assert route.call_count == 0


def test_local_protocol_error_is_mapped_without_the_header_text(store, mock, sentinel_token, sentinel_raw):
    import traceback

    mock.post("/api/travel-plans").mock(
        side_effect=httpx.LocalProtocolError(f"Illegal header value b'Bearer {sentinel_raw}'"))
    with pytest.raises(PlanUpstreamError) as info:
        store.save("u1", draft(), client_request_id=CALL_ID, auth=sentinel_token)
    text = "".join(traceback.format_exception(info.value))
    assert sentinel_raw not in text and info.value.retryable


@pytest.mark.parametrize("status", [405, 413, 429])
def test_other_4xx_are_not_retried(make_graph, mock, agent_context, status):
    store = SpringPlanStore(SpringClient(BASE))
    kit = make_graph(RuleScript(), store=store)
    kit.turn(PLAN_TEXT, agent_context)
    route = mock.post("/api/travel-plans").respond(status)
    done = kit.resume({"decision": "approve"}, agent_context)
    assert done.final_text == tpl.PLAN_SAVE_FAILED and route.call_count == 1   # save_plan도 다시 보내지 않음
    with pytest.raises(PlanRequestError) as info:
        store.save("u1", draft(), client_request_id=CALL_ID, auth=BearerToken("tok"))
    assert info.value.status == status and not info.value.retryable
    store.client.close()


def test_token_store_needs_https_or_local_address():
    from glople_agent.plan.spring_store import token_safe_base_url

    for ok in ("https://glople.example.com", "http://127.0.0.1:8080", "http://localhost:8080", "http://spring.test",
               "http://[::1]:8080"):
        assert token_safe_base_url(ok), ok
    for bad in ("http://glople.example.com", "http://10.0.0.5:8080", "ftp://127.0.0.1"):
        assert not token_safe_base_url(bad), bad
    with pytest.raises(ValueError, match="https"):
        SpringPlanStore(SpringClient("http://glople.example.com"))
