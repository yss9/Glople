"""도구 결과 뒤 평가(M7): agent/scripts/measure_after_tools.py의 측정을 로컬 모델+어댑터(mlx generate) 또는 띄운 서버로 다시 잰다.

방식 A = 학습 때와 같은 system + 도구 8개 + [사용자, 호출, 결과] (일반 에이전트 루프, v4가 배운 것)
방식 B = 답변 전용 system(에이전트 llm/prompts.py) + 도구 없음 + <tool_call> 토큰 막기 (에이전트의 지금 답변 경로 a3)
판정은 measure_after_tools.classify와 같다(결과가 있으면 이름·숫자, 없으면 제한 알림, 도구를 또 부르면 재호출).

venv가 둘이라 세 단계로 나눈다.
  1. cases (agent/.venv: 에이전트 도구 코드로 결과를 만든다. GPU 없음)
       ../agent/.venv/bin/python scripts/evaluate_after_tools.py cases
     → data/after_tools_eval/cases.jsonl
       protocol : measure_after_tools.cases(20)을 그대로 불러 만든 문제(v2 test + human_style, 정답 호출이 하나,
                  결과 ok 전부 + 빈 결과·준비 중·미지원 20개). 에이전트 기본 fixture(학습 fixture와 이름이 겹치지 않음)
       human2   : human_style_v2에 같은 규칙(추가 세트)
       chain    : v4 test의 이어서 부르기 기록 — [결과 1] 뒤 두 번째 호출이 맞는지, [결과 1, 2] 뒤 답
  2. run (training/.venv. 로컬이면 mlx로 GPU를 쓰고, --url이면 표준 라이브러리만)
       .venv/bin/python scripts/evaluate_after_tools.py run --name after_v4_A --adapter adapters/qwen3-4b-tool-calling-v4
       .venv/bin/python scripts/evaluate_after_tools.py run --name after_v3_A --url http://127.0.0.1:8090/v1/chat/completions
       --mode B 는 답변 전용 방식(protocol 세트만). → results/<name>.after_tools.predictions.jsonl(.gitignore 대상), results/<name>.after_tools.summary.json
  3. score (agent/.venv: 에이전트 답변 가드로 다시 채점, 복사한 판정이 원본과 같은지 확인, 비교표)
       ../agent/.venv/bin/python scripts/evaluate_after_tools.py score --names after_v3_A,after_v4_A --out results/report_after_tools_v4.md

주의: 날씨처럼 이름이 없는 결과는 원래 판정이 늘 '결과 이름을 하나도 안 씀'으로 센다(M6 전에는 날씨가 늘 미지원이라 없던 경우).
그래서 표에는 원래 판정과 함께, 이름 없는 결과는 숫자만 보는 'v4 판정'도 낸다.
"""
import argparse
import json
import re
import sys
import time
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT.parent / "agent"
CASES = ROOT / "data" / "after_tools_eval" / "cases.jsonl"
RESULTS = ROOT / "results"
TOOL_CALL = re.compile(r"<tool_call>\s*(.*?)\s*</tool_call>", re.S)
LOGIT_BIAS = {151657: -100, 151658: -100}   # <tool_call>, </tool_call> (measure_after_tools.py와 같음)
NAME_FIELDS = ("name", "title", "nickname")
LIMIT_WORDS = ("없", "준비", "아직", "연결하지", "못 찾", "찾지 못")


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# --- 1. cases (agent/.venv) -------------------------------------------------------------------

def build_cases(args) -> int:
    import importlib.util
    from datetime import date

    from glople_agent import contract
    from glople_agent.llm.prompts import answer_system_prompt
    from glople_agent.tools.fake_backend import FakeBackend
    from glople_agent.tools.registry import run_tool

    spec = importlib.util.spec_from_file_location("measure_after_tools", AGENT / "scripts" / "measure_after_tools.py")
    measure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(measure)
    answer_system = answer_system_prompt(args.answer_prompt)
    out = []

    def mode_messages(case):
        call = {"id": "call_0", "type": "function",
                "function": {"name": case["tool"], "arguments": json.dumps(case["args"], ensure_ascii=False)}}
        tail = [{"role": "user", "content": case["user"]}, {"role": "assistant", "content": "", "tool_calls": [call]},
                {"role": "tool", "tool_call_id": "call_0", "content": case["content"]}]
        return ([{"role": "system", "content": contract.SYSTEM_PROMPT}] + tail,
                [{"role": "system", "content": answer_system}] + tail)

    import functools
    be = FakeBackend(today=date(2026, 10, 18))
    # protocol: 측정 스크립트의 cases()를 그대로 쓴다(문제·순서·결과가 같음). 카드(가드의 원문 겹침 검사용)만 다시 만든다.
    # 측정 스크립트의 FakeBackend()는 오늘 날짜(실제 시계)로 가짜 날씨 예보를 만들어, 다른 날 돌리면 날씨 결과가 바뀐다
    # (CTX는 2026-10-18 고정). 그래서 가짜 백엔드의 '오늘'만 측정일(2026-10-18)로 고정한다. 그날 돌린 것과 같은 문제다.
    measure.FakeBackend = functools.partial(FakeBackend, today=date(2026, 10, 18))
    for c in measure.cases(args.others):
        _, card = run_tool(c["tool"], be, c["args"], "call_0", measure.CTX)
        a, b = mode_messages(c)
        out.append({"set": "protocol", "source": c["set"], "user": c["user"], "results": [
            {"tool": c["tool"], "args": c["args"], "content": c["content"], "status": c["status"], "card": card}],
            "messages_A": a, "messages_B": b, "expect": "answer"})
    # human2: human_style_v2에 같은 규칙
    others = 0
    for rec in read_jsonl(ROOT / "eval_sets" / "human_style_v2.jsonl"):
        calls = rec["messages"][-1].get("tool_calls") or []
        if len(calls) != 1 or calls[0]["function"]["name"] == "create_travel_plan":
            continue
        name, cargs = calls[0]["function"]["name"], json.loads(calls[0]["function"]["arguments"])
        content, card = run_tool(name, be, cargs, "call_0", measure.CTX)
        status = json.loads(content)["status"]
        if status != "ok":
            if others >= args.others:
                continue
            others += 1
        case = {"tool": name, "args": cargs, "content": content, "user": rec["messages"][1]["content"]}
        a, b = mode_messages(case)
        out.append({"set": "human2", "source": "human_style_v2", "user": case["user"], "results": [
            {"tool": name, "args": cargs, "content": content, "status": status, "card": card}],
            "messages_A": a, "messages_B": b, "expect": "answer"})
    # chain: v4 test의 이어서 부르기(두 번째 호출 정답)와 두 결과 뒤 답
    v4_test = ROOT / "data" / "tool_calling_v4" / "test.jsonl"
    if v4_test.is_file():
        for rec in read_jsonl(v4_test):
            if rec["task"] not in ("after_call", "after2_answer"):
                continue
            msgs = rec["messages"]
            results = []
            for i, m in enumerate(msgs[:-1]):
                if m["role"] == "assistant" and m.get("tool_calls"):
                    for j, tc in enumerate(m["tool_calls"]):
                        content = msgs[i + 1 + j]["content"]
                        results.append({"tool": tc["function"]["name"], "args": json.loads(tc["function"]["arguments"]),
                                        "content": content, "status": json.loads(content)["status"], "card": None})
            target = msgs[-1]
            out.append({"set": "chain", "source": rec["task"], "user": msgs[1]["content"], "results": results,
                        "messages_A": msgs[:-1], "messages_B": None,
                        "expect": "call" if target.get("tool_calls") else "answer",
                        "expected_calls": [{"name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"])}
                                           for c in target.get("tool_calls") or []],
                        "reference": target.get("content") or ""})
    CASES.parent.mkdir(parents=True, exist_ok=True)
    with CASES.open("w", encoding="utf-8") as f:
        for c in out:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    counts = Counter((c["set"], "ok" if any(r["status"] == "ok" for r in c["results"]) else "other", c["expect"]) for c in out)
    log(f"문제 {len(out)}개 → {CASES.relative_to(ROOT)}: " + ", ".join(f"{s}/{k}/{e} {n}" for (s, k, e), n in sorted(counts.items())))
    (CASES.parent / "meta.json").write_text(json.dumps({
        "made": datetime.now().isoformat(timespec="seconds"), "answer_prompt": args.answer_prompt, "others": args.others,
        "tools": contract.TOOLS}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return 0


# --- 판정 (measure_after_tools.classify를 옮긴 것 + 이름 없는 결과용 v4 판정) ----------------------------------

def item_names(results: list[dict]) -> list[str]:
    names = []
    for r in results:
        for item in json.loads(r["content"]).get("items") or []:
            names += [str(item[f]) for f in NAME_FIELDS if item.get(f)]
            names += [str(d) for d in item.get("destinations", [])]
    return names


def parse_calls(text: str) -> tuple[list[dict], int]:
    """evaluate.py parse_output과 같다(mlx를 import하지 않으려고 옮김)."""
    calls, invalid = [], 0
    for block in TOOL_CALL.findall(text):
        try:
            obj = json.loads(block)
            arguments = obj.get("arguments") or {}
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(obj.get("name"), str) or not isinstance(arguments, dict):
                raise ValueError
            calls.append({"name": obj["name"], "arguments": arguments})
        except (ValueError, TypeError, AttributeError):
            invalid += 1
    if text.count("<tool_call>") > text.count("</tool_call>"):
        invalid += 1
    return calls, invalid


def classify(case: dict, message: dict, canned: set[str], *, nameless_ok: bool) -> str:
    """measure_after_tools.classify와 같은 판정. nameless_ok면 이름이 없는 결과(날씨)는 이름 검사를 건너뛴다(v4 판정)."""
    text = (message.get("content") or "").strip()
    if message.get("tool_calls") or "<tool_call>" in text:
        names = [c["name"] for c in message.get("tool_calls") or []]
        same = names == [r["tool"] for r in case["results"]][:1] if len(case["results"]) == 1 else False
        return "도구를 또 부름(같은 도구)" if same else "도구를 또 부름(다른 도구)"
    if not text:
        return "빈 답"
    if text in canned:
        return "학습한 정형 문장"
    names = item_names(case["results"])
    contents = " ".join(r["content"] for r in case["results"])
    if any(r["status"] == "ok" for r in case["results"]):
        if not any(n in text for n in names) and not (nameless_ok and not names):
            return "결과 이름을 하나도 안 씀"
        body = re.sub(r"(?m)^\s*\d+[.)]\s", "", text)
        nums = set(re.findall(r"\d+(?:\.\d+)?", body)) - set(re.findall(r"\d+(?:\.\d+)?", contents + case["user"]))
        if nums:
            return "결과에 없는 숫자"
        return "결과를 근거로 답함"
    if any(w in text for w in LIMIT_WORDS):
        return "제한을 알림"
    return "제한을 안 알림(지어냈을 수 있음)"


def canonical(calls: list[dict]) -> list:
    def norm(v):
        if isinstance(v, list):
            return sorted((norm(x) for x in v), key=lambda x: json.dumps(x, ensure_ascii=False, sort_keys=True))
        if isinstance(v, dict):
            return {str(k): norm(x) for k, x in v.items()}
        if isinstance(v, str):
            return int(v.strip()) if v.strip().isdigit() else v.strip()
        return v
    return sorted((c["name"], json.dumps(norm(c["arguments"]), ensure_ascii=False, sort_keys=True)) for c in calls)


def classify_chain(case: dict, message: dict, canned: set[str]) -> str:
    text = message.get("content") or ""
    calls = message.get("tool_calls") or []
    if case["expect"] == "call":
        if not calls and "<tool_call>" not in text:
            return "부르지 않고 답함"
        if canonical(calls) == canonical(case["expected_calls"]):
            return "맞는 두 번째 호출"
        prev = canonical([{"name": r["tool"], "arguments": r["args"]} for r in case["results"]])
        if any(c in prev for c in canonical(calls)):
            return "같은 호출을 또 부름"
        return "다른 호출"
    return classify(case, message, canned, nameless_ok=True)


def canned_texts() -> set[str]:
    """에이전트 contract.canned_texts()와 같은 것: v2 train의 텍스트 정답(인사·거절·되묻기 정형 문장)."""
    path = ROOT / "data" / "tool_calling_v2" / "train.jsonl"
    out = set()
    if path.is_file():
        for r in read_jsonl(path):
            a = r["messages"][-1]
            if a.get("role") == "assistant" and not a.get("tool_calls"):
                out.add(a["content"])
    return out


# --- 2. run (training/.venv) --------------------------------------------------------------------

class LocalModel:
    def __init__(self, model: str, adapter: str | None, max_tokens: int):
        from mlx_lm import generate, load   # GPU(MLX). run 단계에서만 import
        from mlx_lm.sample_utils import make_logits_processors

        self.generate = generate
        self.model, self.tokenizer = load(model, adapter_path=adapter)
        self.block = make_logits_processors(logit_bias=LOGIT_BIAS)
        self.max_tokens = max_tokens

    def __call__(self, messages: list[dict], tools: list | None, mode: str) -> dict:
        prompt = self.tokenizer.apply_chat_template(messages, tools=tools, add_generation_prompt=True, tokenize=False)
        kwargs = {"logits_processors": self.block} if mode == "B" else {}
        text = self.generate(self.model, self.tokenizer, prompt=prompt, max_tokens=self.max_tokens, verbose=False, **kwargs)
        calls, invalid = parse_calls(text)
        content = TOOL_CALL.sub("", text).strip() if calls else text
        return {"content": content, "tool_calls": calls, "raw": text, "invalid": invalid}


class ServerModel:
    def __init__(self, url: str, max_tokens: int):
        self.url, self.max_tokens = url, max_tokens

    def __call__(self, messages: list[dict], tools: list | None, mode: str) -> dict:
        body = {"model": "default_model", "temperature": 0, "max_tokens": self.max_tokens, "messages": messages}
        if tools:
            body["tools"] = tools
        if mode == "B":
            body["logit_bias"] = {str(k): v for k, v in LOGIT_BIAS.items()}
        req = urllib.request.Request(self.url, data=json.dumps(body, ensure_ascii=False).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            msg = json.loads(r.read())["choices"][0]["message"]
        calls = [{"name": c["function"]["name"], "arguments": json.loads(c["function"]["arguments"] or "{}")}
                 for c in msg.get("tool_calls") or []]
        text = msg.get("content") or ""
        if not calls and "<tool_call>" in text:
            calls, _ = parse_calls(text)
        return {"content": text, "tool_calls": calls, "raw": text, "invalid": 0}


def run(args) -> int:
    cases = read_jsonl(Path(args.cases))
    sets = set(args.sets.split(","))
    cases = [c for c in cases if c["set"] in sets and (args.mode == "A" or c["messages_B"] is not None)]
    if args.limit:
        cases = cases[:args.limit]
    tools = json.loads((Path(args.cases).parent / "meta.json").read_text(encoding="utf-8"))["tools"]
    model = (ServerModel(args.url, args.max_tokens) if args.url
             else LocalModel(args.model, args.adapter, args.max_tokens))
    canned = canned_texts()
    RESULTS.mkdir(exist_ok=True)
    out_path = RESULTS / f"{args.name}.after_tools.predictions.jsonl"
    rows = []
    started = time.time()
    log(f"{args.name}: 방식 {args.mode}, 문제 {len(cases)}개, {'서버 ' + args.url if args.url else '로컬 ' + str(args.adapter or '기본 모델')}")
    with out_path.open("w", encoding="utf-8") as f:
        for i, case in enumerate(cases, 1):
            msgs = case["messages_A"] if args.mode == "A" else case["messages_B"]
            t = time.time()
            try:
                message = model(msgs, tools if args.mode == "A" else None, args.mode)
            except Exception as e:  # noqa: BLE001 - 한 문제 실패로 전체가 멈추지 않게
                message = {"content": "", "tool_calls": [], "raw": f"<generation error: {e}>", "invalid": 0}
            sec = time.time() - t
            kind = (classify_chain(case, message, canned) if case["set"] == "chain"
                    else classify(case, message, canned, nameless_ok=False))
            kind_v4 = (kind if case["set"] == "chain" else classify(case, message, canned, nameless_ok=True))
            row = {"set": case["set"], "source": case["source"], "expect": case["expect"],
                   "ok": any(r["status"] == "ok" for r in case["results"]), "tools": [r["tool"] for r in case["results"]],
                   "user": case["user"], "kind": kind, "kind_v4": kind_v4, "sec": round(sec, 2),
                   "text": message["content"], "calls": message["tool_calls"], "raw": message["raw"][:1500]}
            rows.append(row)
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            if i % 10 == 0 or i == len(cases):
                good = sum(r["kind_v4"] in ("결과를 근거로 답함", "제한을 알림", "맞는 두 번째 호출") for r in rows)
                log(f"[{args.name}] {i}/{len(cases)} 바람직한 행동 {good}/{i} (평균 {(time.time() - started) / i:.2f}초)")
    summary = {"name": args.name, "mode": args.mode, "adapter": args.adapter, "url": args.url, "cases": len(rows),
               "by_set": {}}
    for (s, ok), group in sorted(group_rows(rows).items()):
        summary["by_set"][f"{s}/{'ok' if ok else 'other'}"] = {
            "n": len(group), "kind": dict(Counter(r["kind"] for r in group)),
            "kind_v4": dict(Counter(r["kind_v4"] for r in group)),
            "seconds": round(sum(r["sec"] for r in group) / len(group), 2)}
    (RESULTS / f"{args.name}.after_tools.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1) + "\n",
                                                                   encoding="utf-8")
    log(f"저장: {out_path.relative_to(ROOT)}")
    return 0


def group_rows(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["set"], r["ok"])].append(r)
    return groups


# --- 3. score (agent/.venv) -------------------------------------------------------------------------

def score(args) -> int:
    import importlib.util

    from glople_agent.graph.guard import answer_facts, check_answer

    spec = importlib.util.spec_from_file_location("measure_after_tools", AGENT / "scripts" / "measure_after_tools.py")
    measure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(measure)
    from glople_agent import contract
    agent_canned = set(contract.canned_texts() or [])

    cases = {(c["set"], c["user"], json.dumps([r["args"] for r in c["results"]], ensure_ascii=False)): c
             for c in read_jsonl(Path(args.cases))}
    names = []
    for n in (x for x in args.names.split(",") if x):
        if (RESULTS / f"{n}.after_tools.predictions.jsonl").is_file():
            names.append(n)
        else:
            log(f"{n}: 결과 파일이 없어 뺍니다(run 단계가 실패했나요?)")
    if not names:
        return 1
    tables, mismatches, guard = {}, Counter(), {}
    for name in names:
        rows = read_jsonl(RESULTS / f"{name}.after_tools.predictions.jsonl")
        tables[name] = rows
        g = Counter()
        for row in rows:
            case = next((c for (s, u, _), c in cases.items() if s == row["set"] and u == row["user"]
                         and [r["tool"] for r in c["results"]] == row["tools"]), None)
            if case is None:
                continue
            if row["set"] != "chain" and len(case["results"]) == 1:   # 옮긴 판정이 원본과 같은지
                r0 = case["results"][0]
                original = measure.classify({"tool": r0["tool"], "status": r0["status"], "content": r0["content"],
                                             "user": case["user"]},
                                            {"message": {"content": row["text"] if not row["calls"] else row["raw"],
                                                         "tool_calls": [{"function": {"name": c["name"]}} for c in row["calls"]]}},
                                            agent_canned)[0]
                if original != row["kind"]:
                    mismatches[name] += 1
            if row["calls"] or not row["text"].strip() or row["expect"] == "call":
                continue
            facts = answer_facts([r["content"] for r in case["results"]], [r["card"] for r in case["results"] if r["card"]],
                                 case["user"])
            verdict = check_answer(row["text"], facts)
            g["pass" if verdict.ok else "fail"] += 1
            for reason in verdict.reasons:
                g[f"fail:{reason}"] += 1
            row["guard"] = list(verdict.reasons)
        guard[name] = g

    lines = ["# 도구 결과 뒤 평가 (v3 vs v4)", "",
             f"- 만든 때: {datetime.now().strftime('%Y-%m-%d %H:%M')}, `scripts/evaluate_after_tools.py` "
             "(agent/scripts/measure_after_tools.py와 같은 문제·판정, 에이전트 기본 fixture)",
             "- 방식 A = 학습 때 system + 도구 8개(일반 에이전트 루프), 방식 B = 답변 전용 system + 도구 없음 + `<tool_call>` 막기",
             "- '원래 판정'은 측정 스크립트 그대로, 'v4 판정'은 이름이 없는 결과(날씨)를 숫자로만 본 것. "
             f"옮긴 판정과 원본의 차이: {dict(mismatches) or '0건'}", ""]
    for set_name, title in (("protocol", "v2 test + human_style (측정 스크립트 문제)"), ("human2", "human_style_v2"),
                            ("chain", "v4 test 이어서 부르기")):
        for ok in (True, False):
            sub = {n: [r for r in rows if r["set"] == set_name and r["ok"] == ok] for n, rows in tables.items()}
            if not any(sub.values()):
                continue
            label = "결과가 있는 경우" if ok else "빈 결과·준비 중·미지원·오류"
            for key, judge in (("kind", "원래 판정"), ("kind_v4", "v4 판정")):
                if set_name == "chain" and key == "kind":
                    continue
                kinds = sorted({r[key] for rows in sub.values() for r in rows})
                lines += [f"## {title} · {label} · {judge}", "", "| 행동 | " + " | ".join(names) + " |",
                          "| :-- |" + " --: |" * len(names)]
                for k in kinds:
                    lines.append(f"| {k} | " + " | ".join(f"{sum(r[key] == k for r in sub[n])}/{len(sub[n])}" for n in names) + " |")
                secs = [f"{sum(r['sec'] for r in sub[n]) / max(1, len(sub[n])):.2f}" for n in names]
                lines += ["| 평균 응답 시간(초) | " + " | ".join(secs) + " |", ""]
    lines += ["## 에이전트 답변 가드(check_answer) — 글로 답한 것만", "", "| 결과 | " + " | ".join(names) + " |",
              "| :-- |" + " --: |" * len(names)]
    keys = sorted({k for g in guard.values() for k in g})
    for k in keys:
        lines.append(f"| {k} | " + " | ".join(str(guard[n].get(k, 0)) for n in names) + " |")
    lines += ["", "## 예시", ""]
    shown = Counter()
    for n, rows in tables.items():
        for r in rows:
            key = (n, r["set"], r["kind_v4"])
            if shown[key] >= 1:
                continue
            shown[key] += 1
            out = r["text"][:200] or ("호출: " + json.dumps(r["calls"], ensure_ascii=False)[:200] if r["calls"] else "(빈 답)")
            lines.append(f"- **{n} · {r['set']} · {r['kind_v4']}** “{r['user']}” → {out}")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:80]), flush=True)
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cases", help="문제 만들기 (agent/.venv)")
    c.add_argument("--others", type=int, default=20, help="빈 결과·준비 중 문제 수(측정 스크립트 기본과 같음)")
    c.add_argument("--answer-prompt", default="v1", help="방식 B의 답변 system 문구(에이전트 llm/prompts.py)")
    r = sub.add_parser("run", help="모델로 풀기 (training/.venv)")
    r.add_argument("--name", required=True)
    r.add_argument("--mode", choices=("A", "B"), default="A")
    r.add_argument("--model", default=str(ROOT / "models" / "qwen3-4b-instruct-2507-4bit"))
    r.add_argument("--adapter", default=None)
    r.add_argument("--url", default=None, help="띄운 서버로 풀 때(예: http://127.0.0.1:8090/v1/chat/completions)")
    r.add_argument("--sets", default="protocol,human2,chain")
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--max-tokens", type=int, default=300)
    r.add_argument("--cases", default=str(CASES))
    s = sub.add_parser("score", help="가드로 다시 채점하고 비교표 (agent/.venv)")
    s.add_argument("--names", required=True)
    s.add_argument("--out", default=str(RESULTS / "report_after_tools_v4.md"))
    s.add_argument("--cases", default=str(CASES))
    args = p.parse_args()
    return {"cases": build_cases, "run": run, "score": score}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
