"""CLI(M2): chat --answer llm(가짜 답변 모델), chat --llm real(가짜 OpenAI 서버에 붙여 시험), --record 카세트.

실제 모델 서버(8090)에는 접속하지 않는다. --llm real은 테스트 안에서 띄운 가짜 서버의 빈 포트 주소로만 돌린다.
"""
import importlib.util
import io
import json
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from glople_agent import cli
from glople_agent.graph import build_graph, fake_deps, run_turn
from glople_agent.llm.fake import CassetteScript, RuleScript, fake_answer_model
from glople_agent.tools.fake_backend import FakeBackend

AGENT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("fake_openai_server", AGENT / "scripts" / "fake_openai_server.py")
fos = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fos)

SEA = "바다 보이는 곳 추천해줘"


def session(lines: list[str], **kw) -> tuple[int, str, cli.ChatSession]:
    out = io.StringIO()
    s = cli.ChatSession("rules", user=cli.CLI_USER, inp=io.StringIO("\n".join(lines) + "\n"), out=out, **kw)
    return s.run(), out.getvalue(), s


def server(answer_text=None):
    rules = RuleScript()

    def script(body: dict):
        if "tools" in body:
            msg = rules.respond(body["messages"][-1]["content"])
            if msg.tool_calls:
                return fos.Reply.calls([(tc["name"], tc["args"]) for tc in msg.tool_calls])
            return fos.Reply.text(msg.content)
        if answer_text is not None:
            return fos.Reply.text(answer_text)
        tool = next(m for m in body["messages"] if m["role"] == "tool")
        return fos.Reply.text(f"{json.loads(tool['content'])['items'][0]['name']} 같은 곳을 찾았어요.")

    return fos.serve(port=0, script=script)


def test_parser_defaults_keep_fake_router():
    args = cli.parser().parse_args(["chat"])
    assert (args.llm, args.fake, args.answer, args.model_url, args.record) == ("fake", "rules", None, None, None)
    args = cli.parser().parse_args(["chat", "--llm", "real", "--model-url", "http://127.0.0.1:1/v1", "--answer",
                                    "template"])
    assert (args.llm, args.model_url, args.answer) == ("real", "http://127.0.0.1:1/v1", "template")


def test_fake_router_with_fake_llm_answer_shows_preview_and_llm_kind():
    code, text, s = session([SEA, "안녕"], answer="llm")
    assert code == 0 and s.deps.answer_mode == "llm"
    assert text.startswith("Glople 도우미 M2 (가짜 모델·LLM 답변·가짜 Spring")
    assert "경로: start_turn → route → validate → tools → collect → answer_llm → finish" in text
    assert "  미리보기> 찾은 결과 중에는 산토리니, 가거도, 가천 다랭이마을이 있어요." in text
    assert "도우미> 찾은 결과 중에는 산토리니" in text and "[llm]" in text and "[direct]" in text


def test_default_fake_chat_stays_template():
    code, text, s = session([SEA])
    assert code == 0 and s.deps.answer_mode == "template" and s.deps.answer_llm is None
    assert "미리보기>" not in text and "[template]" in text and "M2 (가짜 모델·템플릿 답변" in text


def test_llm_real_against_fake_server(sentinel_raw):
    srv = server()
    try:
        code, text, s = session([SEA, "안녕"], llm="real", model_url=srv.base_url)
        requests = list(srv.requests)
    finally:
        srv.shutdown()
        srv.server_close()
    assert code == 0 and f"실제 모델 {srv.base_url}·LLM 답변" in text
    assert "  미리보기> 산토리니 같은 곳을 찾았어요." in text and "도우미> 산토리니 같은 곳을 찾았어요.   [llm]" in text
    assert "[direct]" in text
    assert len(requests) == 3 and [("tools" in r["body"]) for r in requests] == [True, False, True]
    for r in requests:   # CLI 고정 토큰·사용자 id는 모델 서버에 0건
        raw = r["raw"].decode()
        assert cli.CLI_TOKEN not in raw and cli.CLI_USER not in raw and sentinel_raw not in raw


def test_llm_real_guard_failure_label():
    srv = server(answer_text="산토리니 등 7곳을 찾았어요.")
    try:
        code, text, _ = session([SEA], llm="real", model_url=srv.base_url)
    finally:
        srv.shutdown()
        srv.server_close()
    assert code == 0 and "  미리보기> 산토리니 등 7곳을 찾았어요." in text
    assert "도우미> 바다 취향으로 여행지 5곳을 찾았어요" in text and "[template · 가드 unknown_number · 교체]" in text


def test_llm_real_with_template_answer_uses_router_only():
    srv = server()
    try:
        code, text, s = session([SEA], llm="real", model_url=srv.base_url, answer="template")
        n = len(srv.requests)
    finally:
        srv.shutdown()
        srv.server_close()
    assert code == 0 and s.deps.answer_llm is None and n == 1
    assert "[template]" in text and "미리보기>" not in text


def test_record_then_replay_offline(tmp_path, agent_context):
    path = tmp_path / "demo.json"
    srv = server()
    try:
        code, text, _ = session([SEA, "안녕"], llm="real", model_url=srv.base_url, record=path)
    finally:
        srv.shutdown()
        srv.server_close()
    assert code == 0 and path.is_file()
    script = CassetteScript(path)
    deps = fake_deps(script, backend=FakeBackend(), answer_mode="llm", answer_llm=fake_answer_model(script))
    graph = build_graph(deps, InMemorySaver())
    r = run_turn(graph, "t-replay", SEA, agent_context)
    assert r.reply_kind == "llm" and r.final_text == "산토리니 같은 곳을 찾았어요." and f"도우미> {r.final_text}" in text


def test_record_requires_real_llm():
    out = io.StringIO()
    assert cli.main(["chat", "--record", "x.json"], out=out) == 2 and "--llm real" in out.getvalue()
