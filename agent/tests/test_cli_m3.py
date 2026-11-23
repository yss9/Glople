"""CLI(M3): chat --db/--resume/--thread(껐다 켠 뒤 잇기), /history·/fork(시간 여행), --plan-graph subgraph,
--plan-store spring(respx), graph --xray. 체크포인트 파일은 늘 tmp_path에 만든다(agent/var를 건드리지 않음)."""
import io
import json

import pytest
import respx

from glople_agent import checkpoints, cli
from glople_agent.answer import templates as tpl

PLAN_TEXT = "제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘"
SAVED = "도우미> '제주 2박 3일' 일정을 저장했어요.   [plan]"


def session(lines, **kw) -> tuple[cli.ChatSession, io.StringIO]:
    out = io.StringIO()
    s = cli.ChatSession("rules", user=cli.CLI_USER, inp=io.StringIO("".join(f"{x}\n" for x in lines)), out=out, **kw)
    return s, out


def run(lines, **kw) -> tuple[int, str, cli.ChatSession]:
    s, out = session(lines, **kw)
    return s.run(), out.getvalue(), s


# --- 껐다 켠 뒤 잇기 -----------------------------------------------------------------------

@pytest.mark.parametrize("plan_graph", ["flat", "subgraph"])
def test_quit_with_card_pending_then_resume_and_approve(tmp_path, plan_graph):
    db = tmp_path / "checkpoints.sqlite"
    code, text, first = run([PLAN_TEXT], db=db, plan_graph=plan_graph)   # 카드가 뜬 채 EOF(= CLI 끔)
    assert code == 0 and "[확인 카드]" in text and f"--thread {first.thread}" in text
    assert first._conn is None and not (tmp_path / "checkpoints.sqlite-wal").exists()   # 닫으면서 WAL 합침
    code, text, second = run(["y"], db=db, resume_latest=True, plan_graph=plan_graph)
    assert code == 0 and second.thread == first.thread
    assert f"이어서: thread {first.thread}" in text and "(끄기 전에 떠 있던 확인 카드)" in text
    assert "[확인 카드] '제주 2박 3일' 일정을 이대로 저장할까요?" in text and SAVED in text
    assert second.store.save_count == 1
    code, text, third = run(["y"], db=db, thread=first.thread, plan_graph=plan_graph)   # 같은 승인 다시 → 카드 없음
    assert third.continued and "확인 카드" not in text.split("이어서")[1] and third.store.save_count == 0


def test_thread_and_resume_errors(tmp_path):
    db = tmp_path / "c.sqlite"
    with pytest.raises(ValueError, match="이어 갈 대화가 없어요"):
        session([], db=db, resume_latest=True)
    with pytest.raises(ValueError, match="그 thread의 대화가 없어요"):
        session([], db=db, thread="no-such-thread", resume_latest=True)
    code, text, s = run(["안녕"], db=db, thread="my-thread")
    assert s.thread == "my-thread" and not s.continued and "[direct]" in text
    code, text, s = run(["/state"], db=db, thread="my-thread")
    assert s.continued and "thread my-thread" in text and "메시지 2개" in text
    out = io.StringIO()   # cmd_chat은 같은 오류를 종료 코드 2로
    assert cli.main(["chat", "--db", str(tmp_path / "empty.sqlite"), "--resume"], out=out) == 2
    assert "이어 갈 대화가 없어요" in out.getvalue()


def test_memory_flag_and_default_db_path(tmp_path, monkeypatch):
    args = cli.parser().parse_args(["chat"])
    assert args.db == str(checkpoints.DEFAULT_DB) and checkpoints.DEFAULT_DB.parent.name == "var"
    assert (args.memory, args.thread, args.resume, args.plan_graph, args.plan_store) == (False, None, False, "flat",
                                                                                         None)   # None = 설정(memory)
    monkeypatch.setattr("sys.stdin", io.StringIO("안녕\n"))
    out = io.StringIO()
    assert cli.main(["chat", "--memory", "--db", str(tmp_path / "x.sqlite")], out=out) == 0
    assert not (tmp_path / "x.sqlite").exists() and "[direct]" in out.getvalue()


# --- /history·/fork ------------------------------------------------------------------------

@pytest.mark.parametrize("plan_graph", ["flat", "subgraph"])
def test_fork_back_to_the_card_and_decide_differently(tmp_path, plan_graph):
    s, out = session([], db=tmp_path / "f.sqlite", plan_graph=plan_graph)
    s.turn(PLAN_TEXT)
    assert s.answer_card("y") and s.store.save_count == 1
    s.print_history()
    text = out.getvalue()
    assert "* = 지금 갈래" in text and "*#0" in text and "대기 카드" in text
    [waiting] = [r for r in s._rows if r.pending]
    s.fork_at(str(waiting.index))   # 카드를 기다리던 자리에서 다시: 새 id의 카드가 뜸
    assert s.shown_card is not None and "다시 실행" in out.getvalue()
    assert s.answer_card("n")
    v = s.graph.get_state(cli.run_config(s.thread)).values
    assert v["final_text"] == tpl.PLAN_CANCELLED and s.store.save_count == 1   # 새 갈래는 취소, 저장은 그대로 1건
    rows = checkpoints.history(s.graph, s.thread)
    assert any(r.source == "fork" for r in rows) and any(not r.on_head for r in rows)   # 예전 갈래도 남아 있음
    s.close()


def test_fork_at_turn_end_continues_from_there(tmp_path):
    s, out = session([], db=tmp_path / "g.sqlite")
    s.turn("안녕")
    s.turn("고마워")
    s.print_history("all")
    end_of_first = next(r for r in s._rows if not r.next and r.messages == 2)
    s.fork_at(str(end_of_first.index))
    assert s.fork_from == end_of_first.checkpoint_id and "다음 말이 새 갈래가 됩니다" in out.getvalue()
    s.turn("바다 보이는 곳 추천해줘")
    messages = s.graph.get_state(cli.run_config(s.thread)).values["messages"]
    texts = [m.content for m in messages if m.type == "human"]
    assert texts == ["안녕", "바다 보이는 곳 추천해줘"] and s.fork_from is None   # '고마워' 턴은 다른 갈래에
    s.fork_at("999")
    s.fork_at("x")
    assert "#999 체크포인트가 없어요" in out.getvalue() and "/fork N 꼴로" in out.getvalue()
    s.close()


def test_history_and_fork_through_run_loop(tmp_path):
    code, text, s = run([PLAN_TEXT, "n", "/history 1", "/fork 2", "/help"], db=tmp_path / "h.sqlite")
    assert code == 0 and "*#0 " in text and "*#1 " not in text and "/history [N|all]" in text
    # 줄여 본 목록(#0)에 없는 #2도 전체 목록에서 찾음: plan_prepare 뒤 확인 카드 자리(reject 흐름) → 카드가 다시 뜸
    assert "#2(step 4)에서 다시 실행: review부터" in text and text.count("[확인 카드]") == 2


# --- 서브그래프 판 화면 ------------------------------------------------------------------------

def test_subgraph_chat_shows_qualified_path_and_edit_works():
    lines = [PLAN_TEXT, "e", "", "2", "성산일출봉, 우도, 오설록", "", "/state", "y"]
    code, text, s = run(lines, plan_graph="subgraph")
    assert code == 0 and "M3·M4: 일정 서브그래프" in text
    assert "경로: start_turn → route → validate → plan/plan_prepare → plan/review(확인 대기)" in text
    assert "경로: plan/review → plan/plan_prepare → plan/review(확인 대기)" in text
    assert "3. [x] 2일차 오설록 (USER_EDIT)" in text and "plan_edits: 1" in text
    assert "도우미> '제주 1박 2일' 일정을 저장했어요.   [plan]" in text
    assert "경로: plan/review → plan/save_plan → finish" in text


def test_graph_command_draws_subgraph():
    out = io.StringIO()
    assert cli.main(["graph", "--plan-graph", "subgraph", "--xray"], out=out) == 0
    assert "subgraph plan" in out.getvalue() and "plan\\3areview -.-> plan\\3asave_plan;" in out.getvalue()
    out = io.StringIO()
    assert cli.main(["graph"], out=out) == 0 and "subgraph" not in out.getvalue()


# --- --plan-store spring -------------------------------------------------------------------

def test_spring_store_needs_token_env(monkeypatch):
    monkeypatch.delenv(cli.ENV_SPRING_TOKEN, raising=False)
    out = io.StringIO()
    assert cli.main(["chat", "--memory", "--plan-store", "spring"], out=out) == 2
    assert cli.ENV_SPRING_TOKEN in out.getvalue()
    monkeypatch.setenv(cli.ENV_SPRING_TOKEN, "tok")
    assert cli.main(["chat", "--memory", "--plan-store", "spring", "--anon"], out=io.StringIO()) == 2


def test_spring_store_saves_with_env_token_and_never_prints_it(monkeypatch, sentinel_raw):
    monkeypatch.setenv(cli.ENV_SPRING_TOKEN, sentinel_raw)
    monkeypatch.setenv("GLOPLE_SPRING_BASE_URL", "http://spring.test")
    monkeypatch.setattr("sys.stdin", io.StringIO(f"{PLAN_TEXT}\ny\n"))
    out = io.StringIO()
    with respx.mock(base_url="http://spring.test", assert_all_called=False) as mock:
        def created(request):
            body = json.loads(request.content)
            return respx.MockResponse(201, json={"id": 11, "clientRequestId": body["clientRequestId"], "title": body["title"],
                                                 "region": body["region"], "days": body["days"],
                                                 "places": body["places"], "createdAt": "2026-10-18T12:00:00"})

        route = mock.post("/api/travel-plans").mock(side_effect=created)
        assert cli.main(["chat", "--memory", "--plan-store", "spring"], out=out) == 0
    text = out.getvalue()
    assert route.call_count == 1 and route.calls.last.request.headers["authorization"] == f"Bearer {sentinel_raw}"
    assert "[카드] 일정 저장됨 · plan_id=11" in text and "일정 저장 SpringPlanStore" in text
    assert sentinel_raw not in text


# --- 리뷰에서 고친 것 ---------------------------------------------------------------------

def test_later_fork_to_a_card_cancels_pending_turn_end_fork(tmp_path):
    s, out = session([], db=tmp_path / "k.sqlite")
    s.turn(PLAN_TEXT)
    s.answer_card("y")
    s.print_history("all")
    turn_end = next(r for r in s._rows if not r.next)
    s.fork_at(str(turn_end.index))
    assert s.fork_from is not None
    s.print_history("all")
    [waiting] = [r for r in s._rows if r.pending]
    s.fork_at(str(waiting.index))   # 카드 자리로 다시 /fork → 앞의 '다음 말 붙이기'는 취소
    assert s.fork_from is None and s.shown_card is not None
    s.close()
    code, text, _ = run(["/history ²", "/fork ²", "/history 1"], db=tmp_path / "k2.sqlite")
    assert code == 0 and "/fork N 꼴로" in text   # 유니코드 숫자로 죽지 않음


def test_resume_with_the_other_plan_graph_stops_before_losing_the_card(tmp_path):
    db = tmp_path / "pg.sqlite"
    run([PLAN_TEXT], db=db, plan_graph="subgraph")
    with pytest.raises(ValueError, match="--plan-graph subgraph"):
        session([], db=db, resume_latest=True)          # 평평한 판으로 이으면 대기 노드(plan)가 없음
    out = io.StringIO()
    assert cli.main(["chat", "--db", str(db), "--resume"], out=out) == 2 and "--plan-graph subgraph" in out.getvalue()
    code, text, s = run(["y"], db=db, resume_latest=True, plan_graph="subgraph")
    assert SAVED in text and s.store.save_count == 1


def test_env_settings_reach_chat_and_bad_values_exit_cleanly(monkeypatch):
    monkeypatch.setenv("GLOPLE_ANSWER_PROMPT", "v2")
    monkeypatch.setattr("sys.stdin", io.StringIO("안녕\n"))
    out = io.StringIO()
    assert cli.main(["chat", "--memory"], out=out) == 0 and "답변 문구 v2" in out.getvalue()
    monkeypatch.setenv("GLOPLE_PLAN_STORE", "mysql")
    out = io.StringIO()
    assert cli.main(["chat", "--memory"], out=out) == 2 and "설정 오류" in out.getvalue()
    monkeypatch.setenv("GLOPLE_PLAN_STORE", "spring")   # 환경 변수로 spring을 켜도 토큰이 없으면 시작하지 않음
    monkeypatch.delenv(cli.ENV_SPRING_TOKEN, raising=False)
    assert cli.main(["chat", "--memory"], out=io.StringIO()) == 2


def test_spring_token_with_control_chars_or_remote_http_is_refused(monkeypatch, sentinel_raw):
    monkeypatch.setenv(cli.ENV_SPRING_TOKEN, sentinel_raw + "\nX-Evil: 1")
    out = io.StringIO()
    assert cli.main(["chat", "--memory", "--plan-store", "spring"], out=out) == 2 and sentinel_raw not in out.getvalue()
    monkeypatch.setenv(cli.ENV_SPRING_TOKEN, sentinel_raw)
    monkeypatch.setenv("GLOPLE_SPRING_BASE_URL", "http://api.example.com")
    out = io.StringIO()
    assert cli.main(["chat", "--memory", "--plan-store", "spring"], out=out) == 2 and "https" in out.getvalue()
