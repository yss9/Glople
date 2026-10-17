"""CLI(M0 확인 ③④⑤): graph mermaid, chat --fake rules 데모, replay 표."""
import io

from glople_agent import cli
from glople_agent.llm.fake import RULE_DEMOS, QuirkScript, tool_call_message


def run(argv, stdin_text: str | None = None) -> tuple[int, str]:
    out = io.StringIO()
    if stdin_text is None:
        return cli.main(argv, out=out), out.getvalue()
    session = cli.ChatSession(argv[0], user=argv[1], inp=io.StringIO(stdin_text), out=out)
    return session.run(), out.getvalue()


def test_graph_prints_mermaid():
    code, text = run(["graph"])
    assert code == 0 and text.startswith("---") and "graph TD;" in text
    assert "route -.-> route;" in text and "review -.-> save_plan;" in text


def test_chat_rules_runs_five_demos_with_confirm_card():
    demos = [d.text for d in RULE_DEMOS[:5]]   # 병렬, 되묻기, 직답, 일정 저장, quirk
    lines = demos[:4] + ["e", "", "2", "성산일출봉, 우도, 오설록", "", "y", demos[4], "/state", "/new"]
    code, text = run(["rules", cli.CLI_USER], "\n".join(lines) + "\n")
    assert code == 0
    assert "경로: start_turn → route → validate → tools → collect → answer_template → finish" in text
    assert "[카드] 여행 후기 검색 · ok · 3개" in text
    assert "도우미> 비슷한 여행자를 찾으려면 나이가 필요해요. 알려주시겠어요?   [clarify]" in text
    assert "경로: start_turn → route → direct_reply → finish" in text
    assert "[확인 카드] '제주 2박 3일' 일정을 이대로 저장할까요?" in text
    assert "3. [x] 2일차 오설록 (USER_EDIT)" in text and "(남은 수정 2회)" in text
    assert "도우미> '제주 1박 2일' 일정을 저장했어요.   [plan]" in text
    assert "경로: start_turn → route → route → fallback → finish" in text
    assert "저장된 일정: 1" in text and "새 대화:" in text
    assert cli.CLI_TOKEN not in text


def test_chat_edit_with_blank_places_keeps_unselected_suggestion_out(monkeypatch):
    # 모델이 지어낸 장소(MODEL_SUGGESTED, 기본 해제)는 e에서 장소 칸을 비워 두어도 저장되지 않음(9.1)
    raw = tool_call_message([("create_travel_plan", {"region": "제주", "days": 3,
                                                     "places": ["성산일출봉", "오설록", "우도"]})])
    monkeypatch.setattr(cli, "build_script", lambda name: QuirkScript([raw]))
    lines = ["제주 2박 3일 성산일출봉이랑 우도 저장해줘", "e", "", "2", "", "", "y"]
    out = io.StringIO()
    session = cli.ChatSession("rules", user=cli.CLI_USER, inp=io.StringIO("\n".join(lines) + "\n"), out=out)
    assert session.run() == 0
    text = out.getvalue()
    assert "2. [ ] 2일차 오설록 (MODEL_SUGGESTED)" in text
    assert "(해제된 제안: 오설록 · 넣으려면 장소 칸에 직접 적기)" in text
    assert "장소(쉼표로) [성산일출봉, 우도]: " in text
    assert "오설록 (USER_EDIT)" not in text
    [saved] = session.store.list_plans(cli.CLI_USER)
    assert saved["days"] == 2 and [(i["name"], i["source"]) for i in saved["items"]] == [
        ("성산일출봉", "USER"), ("우도", "USER")]


def test_chat_new_message_while_card_pending_cancels_it():
    code, text = run(["rules", cli.CLI_USER], "제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘\n안녕\n")
    assert code == 0 and "(이전 일정 저장은 취소했어요.)" in text and "[direct]" in text


def test_chat_anonymous_and_bad_edit():
    code, text = run(["rules", None], "제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘\n")
    assert code == 0 and "일정을 저장하려면 로그인이 필요해요." in text
    code, text = run(["rules", cli.CLI_USER],
                     "제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘\ne {\"region\": \"제주\"}\nn\n")
    assert "같은 카드에 다시 답해 주세요." in text and "도우미> 저장하지 않았어요." in text


def test_chat_oracle_falls_back_to_rules_for_unknown_text():
    code, text = run(["oracle", cli.CLI_USER], "안녕\n")
    assert code == 0 and "[direct]" in text


def test_replay_human_prints_table():
    code, text = run(["replay", "--data", "human"])
    assert code == 0 and "| END 도달 | 90/90 |" in text and "| 경로 | 건수 | 비율 |" in text
