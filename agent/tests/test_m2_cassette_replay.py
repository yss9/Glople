"""M2 데모 카세트 재생: 2026-10-18에 실제 v3 서버(serve.sh adapters/qwen3-4b-tool-calling-v3)로 녹화한 대화를
GPU·서버 없이 그대로 다시 돌린다. 그래프·답변 입력·가드가 바뀌어 모델 요청이 달라지면 CassetteMiss로 실패한다
(그럴 때는 서버를 띄우고 `chat --llm real --answer llm --record cassettes/m2_demo.json`으로 다시 녹화).
"""
from datetime import datetime
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from glople_agent.context import AgentContext, BearerToken
from glople_agent.graph.build import build_graph, fake_deps, resume, run_turn
from glople_agent.llm.fake import CassetteScript, fake_answer_model
from glople_agent.tools.backend import KST

CASSETTE = Path(__file__).resolve().parents[1] / "cassettes" / "m2_demo.json"

# (발화, 기대 reply_kind, 가드가 LLM 답을 템플릿으로 바꿨는지)
DEMO = [
    ("박물관 하나만 골라줘", "llm", False),
    ("바다랑 야경 좋아하는데 여행지 추천해줘", "llm", False),
    ("22살 남자 INFP인데 비슷한 사람들 여행 루트 보여줘", "llm", False),
    ("도동항 다녀온 분들 평가 좀 알려 주세요", "template", True),     # 좋아요 수를 후기 개수로 읽음 → 가드
    ("#카페투어 글로플러 찾아줘", "llm", False),
    ("바나힐 정보랑 사람들 리뷰 같이 알려줘", "template", True),       # 결과에 없는 내용을 지어냄 → 가드
    ("부산 내일 날씨 어때?", "template", False),                      # 미지원은 처음부터 템플릿
    ("나랑 비슷한 여행자 루트 보고 싶은데 ENTP 남자임", "direct", False),  # 나이 되묻기
    ("28살이요", "llm", False),                                       # 앞 발화에 이어 붙여 라우터로
    ("안녕", "direct", False),
    ("파이썬 코드 짜줘", "direct", False),
    ("제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘", "plan", False),
]

pytestmark = pytest.mark.skipif(not CASSETTE.exists(), reason="M2 데모 카세트 없음")


def test_m2_demo_replays_from_cassette():
    script = CassetteScript(CASSETTE)
    deps = fake_deps(script, answer_mode="llm", answer_llm=fake_answer_model(script))
    graph = build_graph(deps, InMemorySaver())
    ctx = AgentContext(user_id="cli-user", bearer=BearerToken("SENTINEL-TOKEN-m2-cassette"), request_id="m2",
                       now=datetime(2026, 10, 18, 10, tzinfo=KST), seed=0)
    for text, kind, replaced in DEMO:
        turn = run_turn(graph, "m2-demo", text, ctx)
        assert turn.error is None, text
        if turn.interrupts:   # 일정 확인 카드 → 승인
            turn = resume(graph, "m2-demo", {"decision": "approve"}, ctx, interrupt_id=turn.interrupts[0].id)
        assert turn.values["reply_kind"] == kind, text
        assert bool(turn.values.get("trace", {}).get("answer_llm", {}).get("replaced")) == replaced, text
    assert script.hits == len(script.cassette)   # 녹화한 요청을 모두 썼다
