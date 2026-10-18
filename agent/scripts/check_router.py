"""route 노드와 같은 길로 실제 모델 서버를 채점한다(design.md 13절 M2 'check_router 점수 == check_server').

training/scripts/check_server.py는 HTTP로 서버를 직접 부르고, 이 스크립트는 에이전트가 쓰는 길을 그대로 탄다:
llm/models.py의 ChatOpenAI(router_model) → bind_tools(contract.TOOLS) → [System(SYSTEM_PROMPT), Human(발화)] →
llm/normalize.py의 normalize_router. 채점은 evaluate.py와 같은 함수(eval/scoring.py)다. 두 점수가 같으면 LangChain
경로가 요청·파싱에서 아무것도 바꾸지 않았다는 뜻이다.

사용(서버를 먼저 띄움: bash training/scripts/serve.sh adapters/qwen3-4b-tool-calling-v3 8090):
  .venv/bin/python scripts/check_router.py --data v2-test --limit 60
"""
import argparse
import json
import time

from langchain_core.messages import HumanMessage, SystemMessage

from glople_agent import contract
from glople_agent.eval.scoring import same_calls
from glople_agent.llm.models import bind_router, router_model
from glople_agent.llm.normalize import normalize_router

DATA = {"v2-test": contract.TRAINING_DIR / "data" / "tool_calling_v2" / "test.jsonl", "human": contract.HUMAN_STYLE_PATH}


def expected(record):
    calls = record["messages"][-1].get("tool_calls") or []
    return [{"name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"])} for c in calls]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", default="v2-test", choices=sorted(DATA))
    p.add_argument("--limit", type=int, default=60)
    p.add_argument("--model-url", default="http://127.0.0.1:8090/v1")
    args = p.parse_args()
    router = bind_router(router_model(args.model_url))
    rows = [json.loads(line) for line in DATA[args.data].read_text(encoding="utf-8").splitlines()][:args.limit]
    decision = exact = tool_examples = 0
    kinds = {}
    t0 = time.time()
    for rec in rows:
        user = next(m["content"] for m in rec["messages"] if m["role"] == "user")
        try:
            outcome = normalize_router(router.invoke([SystemMessage(contract.SYSTEM_PROMPT), HumanMessage(user)]))
        except Exception as e:  # noqa: BLE001 - 연결 오류 등도 결과로 센다
            outcome = normalize_router(e)
        kinds[outcome.kind] = kinds.get(outcome.kind, 0) + 1
        got = [{"name": c["name"], "arguments": c["args"]} for c in outcome.tool_calls] if outcome.kind == "tool" else []
        want = expected(rec)
        decision += bool(got) == bool(want)
        if want:
            tool_examples += 1
            exact += same_calls(got, want)
    print(json.dumps({"path": "route 노드와 같은 길(ChatOpenAI + normalize_router)", "data": args.data,
                      "examples": len(rows), "decision_accuracy": round(decision / len(rows), 3),
                      "exact_match": round(exact / max(tool_examples, 1), 3), "router_kinds": kinds,
                      "seconds_per_example": round((time.time() - t0) / len(rows), 2)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
