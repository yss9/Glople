"""CLI(M1): tool(LLM용 JSON과 카드 JSON), grounding 표, chat --trajectory, replay --predictions."""
import io
import json

import pytest

from glople_agent import cli
from glople_agent.eval.grounding_eval import PREDICTION_SETS
from glople_agent.eval.trajectory import JsonlTrajectorySink, format_line

TYPE3_SENTINEL = "TYPE3-SENTINEL-5d1c8e2a"


def run(argv) -> tuple[int, str]:
    out = io.StringIO()
    return cli.main(argv, out=out), out.getvalue()


def _section(text: str, title: str) -> str:
    start = text.index(title)
    rest = text[start:].split("\n", 1)[1]
    return rest.split("\n\n", 1)[0]


def test_tool_shows_llm_json_and_card_json_side_by_side():
    code, text = run(["tool", "recommend_by_keywords", '{"keywords": ["바다"]}', "--fixture",
                      "--now", "2026-10-17T12:00:00+09:00"])
    assert code == 0
    assert "[HTTP] POST /api/search · body {\"keywords\": [\"바다\"]} · Authorization 없음" in text
    llm = json.loads(_section(text, "── LLM용 content"))
    card = json.loads(_section(text, "── 카드 JSON"))
    assert llm["status"] == "ok" and TYPE3_SENTINEL not in json.dumps(llm, ensure_ascii=False)   # 원문 없음
    assert any(TYPE3_SENTINEL in (i.get("text") or "") for i in card["data"]["items"])       # 카드엔 원문
    assert any(i.get("attribution", "") and "제3유형" in i["attribution"] for i in card["data"]["items"])
    assert "가거도: LLM ['name', 'note', 'where'] | 카드만" in text
    assert "20자 겹침 0건" in text


def test_tool_with_utterance_runs_grounding_first():
    code, text = run(["tool", "recommend_similar_traveler_routes", '{"age": 30, "gender": "남성", "mbti": "ENTP"}',
                      "--say", "나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임"])
    assert code == 0 and "막힘(grounding) · clarify:age" in text and "[HTTP]" not in text
    code, text = run(["tool", "search_gloplers", '{"hashtag": "나고야"}'])
    assert "query {\"hashtag\": \"#나고야\"}" in text and "query {\"country\": \"일본\"}" in text


@pytest.mark.parametrize(("argv", "needle"), [
    (["tool", "create_travel_plan", '{"region": "제주", "days": 1, "places": ["우도"]}'], "확인 카드"),
    (["tool", "book_hotel", "{}"], "모르는 도구"),
    (["tool", "search_reviews", '{"query": "바나힐", "userId": "u2"}'], "스키마에 맞지 않음"),
    (["tool", "search_reviews", "{not json"], "인자 JSON을 읽지 못했어요"),
])
def test_tool_rejects_bad_input(argv, needle):
    code, text = run(argv)
    assert code == 2 and needle in text


def test_grounding_command_prints_tables():
    code, text = run(["grounding"])
    assert code == 0 and "| human | 86 | 0 | 0 | 0 |" in text
    if PREDICTION_SETS["v1-human"].is_file():
        assert "| v1-human | 90 | 66 | 0 | 24 |" in text


def test_replay_predictions_table():
    if not PREDICTION_SETS["v1-human"].is_file():
        pytest.skip("training/results 없음")
    code, text = run(["replay", "--predictions", "v1-human"])
    assert code == 0 and "| v1-human | 90 | 66 |" in text and "| 0 | 0 |" in text
    code, text = run(["replay", "--predictions", "nope"])
    assert code == 2


def test_chat_trajectory_lines_have_no_user_token_or_text(tmp_path):
    sink = JsonlTrajectorySink(tmp_path)
    lines = ["(지어냄) 나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임", "28살이요", "바나힐 정보랑 사람들 리뷰 같이 알려줘"]
    out = io.StringIO()
    session = cli.ChatSession("rules", user=cli.CLI_USER, inp=io.StringIO("\n".join(lines) + "\n"), out=out,
                              trajectory=sink)
    assert session.run() == 0
    text = out.getvalue()
    assert "궤적: router=tool×1 | recommend_similar_traveler_routes:grounding[clarify:age]" in text
    assert "궤적: router=tool×1 이어붙임 | recommend_similar_traveler_routes:ok" in text
    [path] = list(tmp_path.glob("*.jsonl"))
    raw = path.read_text(encoding="utf-8")
    records = [json.loads(line) for line in raw.splitlines()]
    assert len(records) == 3 and all("ts" in r for r in records)
    for secret in (cli.CLI_USER, cli.CLI_TOKEN, "28살", "ENTP 남자임", "바나힐"):
        assert secret not in raw
    assert records[1]["followup"] == {"joined": True, "demonstratives": 0}


def test_trajectory_sink_refuses_forbidden_keys(tmp_path):
    sink = JsonlTrajectorySink(tmp_path)
    with pytest.raises(ValueError):
        sink({"turn_id": "t", "verdicts": [{"name": "x", "args": {"age": 28}}]})
    with pytest.raises(ValueError):
        sink({"user_id": "u1"})
    assert not list(tmp_path.glob("*.jsonl"))
    assert format_line({"router_kind": "tool", "router_attempts": 1, "verdicts": [], "results": [],
                        "reply_kind": "direct"}) == "router=tool×1 | - | - | reply=direct"
