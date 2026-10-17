"""serve.sh로 띄운 OpenAI 호환 서버가 evaluate.py와 같은 도구 호출을 내는지 확인한다.

에이전트는 이 서버를 OpenAI API처럼 부르므로, 서버 응답(tool_calls)을 evaluate.py와 같은 기준으로 채점해
로컬 평가 결과와 서버 결과가 어긋나지 않는지 본다.
사용: .venv/bin/python scripts/check_server.py --data data/tool_calling/test.jsonl --limit 30
"""
import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from evaluate import canonical, expected_calls, parse_output  # noqa: E402  (mlx_lm을 불러오지만 GPU는 쓰지 않는다)


def server_calls(message):
    """OpenAI 형식 응답에서 도구 호출을 꺼낸다. 파서가 못 읽은 경우는 본문의 <tool_call>을 직접 읽는다."""
    calls, invalid = [], 0
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments") or {}
        try:
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(function.get("name"), str) or not isinstance(arguments, dict):
                raise ValueError
            calls.append({"name": function["name"], "arguments": arguments})
        except ValueError:
            invalid += 1
    if not calls and not invalid:
        calls, invalid = parse_output(message.get("content") or "")
    return calls, invalid


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8090/v1/chat/completions")
    parser.add_argument("--data", default=str(ROOT / "data" / "tool_calling" / "test.jsonl"))
    parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args()

    records = [json.loads(line) for line in Path(args.data).read_text(encoding="utf-8").splitlines()][:args.limit]
    exact = tool_examples = decision = 0
    started = time.time()
    for record in records:
        body = json.dumps({
            "model": "default_model",
            "messages": record["messages"][:-1],
            "tools": record["tools"],
            "temperature": 0.0,
            "max_tokens": 256,
        }).encode("utf-8")
        request = urllib.request.Request(args.url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            message = json.loads(response.read())["choices"][0]["message"]

        expected = expected_calls(record)
        predicted, invalid = server_calls(message)
        decision += bool(expected) == (bool(predicted) or invalid > 0)
        if expected:
            tool_examples += 1
            exact += invalid == 0 and canonical(predicted) == canonical(expected)

    print(json.dumps({
        "examples": len(records),
        "decision_accuracy": round(decision / len(records), 3),
        "exact_match": round(exact / max(tool_examples, 1), 3),
        "seconds_per_example": round((time.time() - started) / len(records), 2),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
