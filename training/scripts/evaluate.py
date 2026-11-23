"""test 세트로 모델의 도구 호출 정확도를 잰다 (기본 모델 / LoRA 어댑터 비교용).

지표
- decision_accuracy: 도구를 불러야 할 때 부르고, 되묻거나 바로 답해야 할 때 부르지 않은 비율
- tool_name_accuracy: 도구 호출 정답 중 부른 도구 이름(집합)이 맞은 비율
- exact_match: 도구 호출 정답 중 도구 이름과 인자가 모두 맞은 비율
- json_error_rate: <tool_call> 블록 중 JSON으로 읽히지 않은 비율
"""
import argparse
import json
import re
import time
from collections import defaultdict
from pathlib import Path

from mlx_lm import generate, load

ROOT = Path(__file__).resolve().parents[1]
TOOL_CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)


def parse_output(text):
    calls, invalid = [], 0
    for block in TOOL_CALL.findall(text):
        try:
            obj = json.loads(block)
            if not isinstance(obj, dict):
                raise ValueError("tool call is not an object")
            arguments = obj.get("arguments") or {}
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            name = obj.get("name")
            # 이름이 문자열이 아니거나 인자가 객체가 아니면 형식이 깨진 호출로 센다.
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise ValueError("malformed tool call")
            calls.append({"name": name, "arguments": arguments})
        except (ValueError, TypeError):  # json.JSONDecodeError는 ValueError의 하위 클래스
            invalid += 1
    # 닫는 태그 없이 끝난 경우도 깨진 호출로 센다.
    if text.count("<tool_call>") > text.count("</tool_call>"):
        invalid += 1
    return calls, invalid


def _sort_key(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def normalize(value):
    # 모델 출력에는 숫자·문자열·객체가 섞인 리스트도 나올 수 있어 정렬 키를 JSON 문자열로 통일한다.
    if isinstance(value, list):
        return sorted((normalize(v) for v in value), key=_sort_key)
    if isinstance(value, dict):
        return {str(k): normalize(v) for k, v in value.items()}
    if isinstance(value, str):
        stripped = value.strip()
        return int(stripped) if stripped.isdigit() else stripped
    return value


def canonical(calls):
    return sorted(
        (c["name"], json.dumps(normalize(c["arguments"]), ensure_ascii=False, sort_keys=True))
        for c in calls
    )


def _relative_to_root(path):
    if path is None:
        return None
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(ROOT))
    except ValueError:
        return str(path)  # training/ 밖의 경로나 허브 모델 이름은 그대로 둔다.


def expected_calls(record):
    assistant = record["messages"][-1]
    return [
        {"name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"])}
        for c in assistant.get("tool_calls", [])
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", default=str(ROOT / "models" / "qwen3-4b-instruct-2507-4bit"))
    parser.add_argument("--adapter", default=None)
    parser.add_argument("--data", default=str(ROOT / "data" / "tool_calling" / "test.jsonl"))
    parser.add_argument("--name", required=True, help="결과 파일 이름 (예: base, finetuned)")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-tokens", type=int, default=256)
    args = parser.parse_args()

    records = [json.loads(line) for line in Path(args.data).read_text(encoding="utf-8").splitlines()]
    if args.limit:
        records = records[:args.limit]

    model, tokenizer = load(args.model, adapter_path=args.adapter)
    out_dir = ROOT / "results"
    out_dir.mkdir(exist_ok=True)

    totals = defaultdict(int)
    by_task = defaultdict(lambda: defaultdict(int))
    started = time.time()
    with (out_dir / f"{args.name}.predictions.jsonl").open("w", encoding="utf-8") as f:
        for i, record in enumerate(records, 1):
            prompt = tokenizer.apply_chat_template(
                record["messages"][:-1], tools=record["tools"], add_generation_prompt=True, tokenize=False)
            try:
                output = generate(model, tokenizer, prompt=prompt, max_tokens=args.max_tokens, verbose=False)
            except Exception as e:  # 한 예시의 생성 실패로 전체 평가가 멈추지 않도록 오답으로 기록한다.
                output = f"<generation error: {e}>"
                totals["generation_errors"] += 1

            expected = expected_calls(record)
            predicted, invalid = parse_output(output)
            expects_tool = bool(expected)
            predicted_tool = bool(predicted) or invalid > 0

            task = by_task[record["task"]]
            totals["examples"] += 1
            task["examples"] += 1
            totals["tool_call_blocks"] += len(predicted) + invalid
            totals["invalid_json"] += invalid
            if expects_tool == predicted_tool:
                totals["decision_correct"] += 1
                task["decision_correct"] += 1
            if expects_tool:
                totals["tool_examples"] += 1
                names_ok = sorted(c["name"] for c in predicted) == sorted(c["name"] for c in expected)
                exact_ok = invalid == 0 and canonical(predicted) == canonical(expected)
                totals["names_correct"] += names_ok
                totals["exact_correct"] += exact_ok
                task["exact_correct"] += exact_ok
            else:
                exact_ok = not predicted_tool
                task["exact_correct"] += exact_ok

            f.write(json.dumps({
                "task": record["task"], "user": record["messages"][1]["content"],
                "expected": expected or record["messages"][-1]["content"],
                "output": output, "correct": exact_ok,
            }, ensure_ascii=False) + "\n")
            if i % 25 == 0:
                print(f"[{args.name}] {i}/{len(records)} exact so far={totals['exact_correct']}/{totals['tool_examples']}",
                      flush=True)

    elapsed = time.time() - started
    summary = {
        "name": args.name,
        # 커밋되는 결과 파일에 로컬 절대경로가 남지 않도록 training/ 기준 상대경로로 적는다.
        "model": _relative_to_root(args.model),
        "adapter": _relative_to_root(args.adapter),
        "examples": totals["examples"],
        "decision_accuracy": totals["decision_correct"] / totals["examples"],
        "tool_name_accuracy": totals["names_correct"] / max(totals["tool_examples"], 1),
        "exact_match": totals["exact_correct"] / max(totals["tool_examples"], 1),
        "json_error_rate": totals["invalid_json"] / max(totals["tool_call_blocks"], 1),
        "generation_errors": totals["generation_errors"],
        "seconds_per_example": elapsed / totals["examples"],
        "by_task_accuracy": {
            name: round(t["exact_correct"] / t["examples"], 3) for name, t in sorted(by_task.items())
        },
    }
    (out_dir / f"{args.name}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
