"""v4 다턴 데이터(data/tool_calling_v4) 확인: 형식, 토큰 길이, 누설, 에이전트 코드가 바뀌었는지. CPU만 쓴다.

training/.venv로 실행한다(jinja2·tokenizers만 import. mlx·transformers·torch는 import하지 않음):
  .venv/bin/python scripts/check_multiturn_dataset.py                 # 기본: data/tool_calling_v4, configs/qwen3-4b-lora-v4.yaml
  .venv/bin/python scripts/check_multiturn_dataset.py --json data/tool_calling_v4/token_stats.json

- 렌더링은 mlx-lm ChatDataset과 같다: tokenizer.apply_chat_template(messages, tools=tools)와 같은 Qwen 채팅 템플릿
  (models/…/chat_template.jsonl을 transformers처럼 ImmutableSandboxedEnvironment로, agent/scripts/render_prompt.py와 같은 설정).
  mask_prompt: 마지막 assistant 앞(생성 프롬프트 포함)까지가 프롬프트이고, 그 뒤만 학습한다. 렌더링한 전체가 프롬프트로
  시작하는지(offset이 맞는지)도 본다.
- 형식: system(SYSTEM_PROMPT) → user → [assistant 호출 → tool 결과 × 호출 수]… → assistant 정답, 도구 정의 = tools/glople_tools.json,
  arguments는 JSON 문자열, 렌더링한 <tool_call>·<tool_response> 수 = 호출·결과 수(결과 글에 특수 토큰 문자열이 없음).
- 누설(생성기와 따로 다시 확인): train·valid 발화가 v2/v3 test·human_style·human_style_v2에 없음, v2/v3 test 지역·장소·조건·
  해시태그·일정 이름과 사람 말투 세트에만 나오는 지역·장소가 train·valid 글에 없음.
- manifest.json의 에이전트 코드 해시와 지금 파일을 비교해, 데이터를 만든 뒤 도구 코드·fixture가 바뀌었으면 알린다.
"""
import argparse
import ast
import hashlib
import importlib.util
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import jinja2
import jinja2.ext
from jinja2.sandbox import ImmutableSandboxedEnvironment
from tokenizers import Tokenizer

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT.parent / "agent"
MODEL_DIR = ROOT / "models" / "qwen3-4b-instruct-2507-4bit"
EVAL_SETS = [ROOT / "data" / "tool_calling_v2" / "test.jsonl", ROOT / "data" / "tool_calling_v3" / "test.jsonl",
             ROOT / "eval_sets" / "human_style.jsonl", ROOT / "eval_sets" / "human_style_v2.jsonl"]
# v3 학습 실측(logs/train_v3.log): 500단계 × 배치 4, 기록 평균 1,328토큰(32 단위로 채우면 약 1,345)에 학습 시간 약 9,150초
# (전체 2시간 39분에서 검증 손실 4번·모델 읽기를 뺌) → 초당 약 294토큰(전체 시퀀스 기준, M4 Pro 24GB, 4bit QLoRA, grad checkpoint)
V3_TOKENS_PER_SECOND = 294
V3_EVAL_SECONDS = 92   # 검증 손실 한 번(val_batches 10)


def environment() -> ImmutableSandboxedEnvironment:
    def tojson(x, ensure_ascii=False, indent=None, separators=None, sort_keys=False):
        return json.dumps(x, ensure_ascii=ensure_ascii, indent=indent, separators=separators, sort_keys=sort_keys)

    def raise_exception(message):
        raise jinja2.exceptions.TemplateError(message)

    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True, extensions=[jinja2.ext.loopcontrols])
    env.filters["tojson"] = tojson
    env.globals["raise_exception"] = raise_exception
    return env


def render(template, messages, tools, add_generation_prompt):
    msgs = [{**m, "content": m.get("content") or ""} for m in messages]
    return template.render(messages=msgs, tools=tools or None, documents=None, add_generation_prompt=add_generation_prompt)


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def assigned_literal(path: Path, name: str):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise KeyError(name)


def load_v3():
    spec = importlib.util.spec_from_file_location("build_tool_dataset_v3", ROOT / "scripts" / "build_tool_dataset.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_HANGUL = re.compile(r"[가-힣ㄱ-ㅎ]")


def boundary_hits(text: str, entities) -> list[str]:
    found = []
    for e in entities:
        i = text.find(e)
        while i >= 0:
            if i == 0 or not _HANGUL.match(text[i - 1]):
                found.append(e)
                break
            i = text.find(e, i + 1)
    return found


def user_text(r: dict) -> str:
    return next(m["content"] for m in r["messages"] if m["role"] == "user")


def format_problems(r: dict, system_prompt: str, tools: list) -> list[str]:
    msgs = r["messages"]
    out = []
    if r.get("tools") != tools:
        out.append("도구 정의가 tools/glople_tools.json과 다름")
    if [m["role"] for m in msgs[:2]] != ["system", "user"] or msgs[0]["content"] != system_prompt:
        out.append("system·user 머리가 다름")
    if msgs[-1]["role"] != "assistant":
        out.append("마지막이 assistant가 아님")
    i = 2
    while i < len(msgs) - 1:
        m = msgs[i]
        calls = m.get("tool_calls") or []
        if m["role"] != "assistant" or not calls:
            out.append(f"{i}번째 메시지가 호출이 아님")
            break
        results = msgs[i + 1:i + 1 + len(calls)]
        if len(results) != len(calls) or any(x["role"] != "tool" for x in results):
            out.append("호출 수와 결과 수가 다름")
            break
        for c, t in zip(calls, results):
            if c.get("id") and t.get("tool_call_id") != c["id"]:
                out.append("tool_call_id가 호출 id와 다름")
            try:
                obj = json.loads(t["content"])
                if obj.get("tool") != c["function"]["name"]:
                    out.append("결과의 tool이 호출 이름과 다름")
            except (ValueError, KeyError):
                out.append("결과가 JSON 봉투가 아님")
        i += 1 + len(calls)
    for m in msgs:
        for c in m.get("tool_calls") or []:
            try:
                if not isinstance(json.loads(c["function"]["arguments"]), dict):
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                out.append("arguments가 JSON 객체 문자열이 아님")
    rounds = sum(1 for m in msgs if m["role"] == "assistant" and m.get("tool_calls"))
    if rounds > 2:
        out.append("2라운드를 넘음")
    return out


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--data", default=str(ROOT / "data" / "tool_calling_v4"))
    p.add_argument("--config", default=str(ROOT / "configs" / "qwen3-4b-lora-v4.yaml"))
    p.add_argument("--max-seq", type=int, default=None, help="기본: 설정 파일의 max_seq_length")
    p.add_argument("--json", default=None, help="토큰 통계를 쓸 경로(선택)")
    args = p.parse_args()

    data = Path(args.data)
    config = Path(args.config)
    conf = {}
    if config.is_file():
        for line in config.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^(\w+):\s*([^#\s][^#]*?)\s*(#.*)?$", line)
            if m:
                conf[m.group(1)] = m.group(2)
    max_seq = args.max_seq or int(conf.get("max_seq_length", 2048))
    iters, batch = int(conf.get("iters", 0) or 0), int(conf.get("batch_size", 4) or 4)
    steps_per_eval = int(conf.get("steps_per_eval", 0) or 0)

    system_prompt = assigned_literal(ROOT / "scripts" / "build_tool_dataset.py", "SYSTEM_PROMPT")
    tools = json.loads((ROOT / "tools" / "glople_tools.json").read_text(encoding="utf-8"))
    template = environment().from_string((MODEL_DIR / "chat_template.jinja").read_text(encoding="utf-8"))
    tok = Tokenizer.from_file(str(MODEL_DIR / "tokenizer.json"))
    ids = {t: tok.token_to_id(t) for t in ("<tool_call>", "<tool_response>", "<|im_start|>")}
    # 도구 머리(system + <tools>)의 안내문에도 <tool_call>이 두 번 나온다. 그만큼은 기본값
    head_ids = tok.encode(render(template, [{"role": "system", "content": system_prompt}], tools, False),
                          add_special_tokens=False).ids
    base = {t: head_ids.count(i) for t, i in ids.items() if t != "<|im_start|>"}

    errors: list[str] = []
    stats: dict = {"max_seq_length": max_seq, "splits": {}}
    for split in ("train", "valid", "test"):
        records = read_jsonl(data / f"{split}.jsonl")
        full_lens, trained, by_kind = [], [], defaultdict(list)
        too_long = 0
        for n, r in enumerate(records):
            for prob in format_problems(r, system_prompt, tools):
                errors.append(f"{split}:{n + 1}: {prob}")
            full = render(template, r["messages"], r["tools"], False)
            prompt = render(template, r["messages"][:-1], r["tools"], True)
            if not full.startswith(prompt):
                errors.append(f"{split}:{n + 1}: 렌더링한 전체가 프롬프트로 시작하지 않음(mask offset 어긋남)")
            f_ids = tok.encode(full, add_special_tokens=False).ids
            p_ids = tok.encode(prompt, add_special_tokens=False).ids
            n_calls = sum(len(m.get("tool_calls") or []) for m in r["messages"])
            n_tools = sum(m["role"] == "tool" for m in r["messages"])
            if (f_ids.count(ids["<tool_call>"]) != base["<tool_call>"] + n_calls
                    or f_ids.count(ids["<tool_response>"]) != base["<tool_response>"] + n_tools):
                errors.append(f"{split}:{n + 1}: 특수 토큰 수가 호출·결과 수와 다름(결과 글에 특수 토큰 문자열?)")
            if f_ids.count(ids["<|im_start|>"]) != 3 + sum(m["role"] == "assistant" for m in r["messages"][2:-1]) \
                    + sum(1 for i, m in enumerate(r["messages"]) if m["role"] == "tool" and r["messages"][i - 1]["role"] != "tool"):
                errors.append(f"{split}:{n + 1}: <|im_start|> 수가 메시지 구조와 다름")
            full_lens.append(len(f_ids))
            trained.append(len(f_ids) - len(p_ids))
            kind = r.get("meta", {}).get("kind", r.get("task"))
            kind = "first_turn" if kind == "first_turn" else r["task"]
            by_kind[kind].append(len(f_ids))
            too_long += len(f_ids) > max_seq
        padded = [1 + 32 * ((x + 31) // 32) for x in full_lens]
        stats["splits"][split] = {
            "records": len(records), "mean": round(statistics.mean(full_lens), 1), "p50": pct(full_lens, 0.5),
            "p90": pct(full_lens, 0.9), "p99": pct(full_lens, 0.99), "max": max(full_lens), "min": min(full_lens),
            "mean_padded": round(statistics.mean(padded), 1), "trained_mean": round(statistics.mean(trained), 1),
            "trained_max": max(trained), "over_max_seq": too_long,
            "by_kind": {k: {"n": len(v), "mean": round(statistics.mean(v), 1), "max": max(v)} for k, v in sorted(by_kind.items())},
        }
        if too_long:
            errors.append(f"{split}: max_seq_length {max_seq}을 넘는 기록 {too_long}개(잘려서 학습됨)")

    # 누설 다시 확인 ------------------------------------------------------------------------
    v3 = load_v3()
    held = {}
    for path in EVAL_SETS:
        for r in read_jsonl(path):
            held.setdefault(user_text(r), path.name)
    test_values = sorted({v for pool in (v3.REGIONS["test"], v3.PLACES["test"], list(v3.REGION_PLACES["test"]),
                                         list(v3.DESCRIPTORS["test"]), v3.HASHTAGS["test"], v3.PLAN_TITLES["test"])
                          for v in pool})
    train_pool = (set(v3.REGIONS["train"]) | set(v3.PLACES["train"]) | set(v3.HASHTAGS["train"])
                  | set(v3.DESCRIPTORS["train"]) | set(v3.KEYWORDS) | set(v3.PLAN_TITLES["train"]))
    human = set()
    for path in EVAL_SETS[2:]:
        for r in read_jsonl(path):
            for c in r["messages"][-1].get("tool_calls") or []:
                a = json.loads(c["function"]["arguments"])
                for k in ("region", "name", "query", "hashtag", "places", "title"):
                    v = a.get(k)
                    human.update(x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and x)
    human_only = sorted(human - train_pool)
    leak = Counter()
    texts = {}
    for split in ("train", "valid", "test"):
        records = read_jsonl(data / f"{split}.jsonl")
        texts[split] = {user_text(r) for r in records}
        if split == "test":
            continue
        for r in records:
            if user_text(r) in held:
                leak[f"{split}: 평가 발화"] += 1
            body = json.dumps(r["messages"][1:], ensure_ascii=False)
            for e in boundary_hits(body, test_values):
                leak[f"{split}: test 값 {e}"] += 1
            for e in boundary_hits(body, human_only):
                leak[f"{split}: 사람 말투 세트 값 {e}"] += 1
    overlap = {"train∩valid": len(texts["train"] & texts["valid"]), "train∩test": len(texts["train"] & texts["test"]),
               "valid∩test": len(texts["valid"] & texts["test"])}
    for k, v in overlap.items():
        if v:
            errors.append(f"발화 겹침 {k}: {v}")
    for k, v in leak.items():
        errors.append(f"누설 {k}: {v}건")
    stats["leakage"] = {"held_out_utterances": len(held), "test_values": len(test_values),
                        "human_only_values": len(human_only), "hits": dict(leak), "split_overlap": overlap}

    # 에이전트 코드가 바뀌었는지 -------------------------------------------------------------------
    warnings = []
    manifest_path = data / "manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        changed = []
        for rel, digest in manifest.get("agent_sources", {}).items():
            f = AGENT / rel
            if not f.is_file() or hashlib.sha256(f.read_bytes()).hexdigest() != digest:
                changed.append(rel)
        for split, digest in manifest.get("files", {}).items():
            if hashlib.sha256((data / split).read_bytes()).hexdigest() != digest:
                warnings.append(f"{split}가 manifest와 다름(생성 뒤 손으로 고침?)")
        if changed:
            warnings.append(f"데이터를 만든 뒤 에이전트 도구 코드·fixture가 바뀜({len(changed)}개, 예: {', '.join(changed[:4])}). "
                            "도구 결과 모양이 바뀌었을 수 있으니 ../agent/.venv/bin/python scripts/build_multiturn_dataset.py로 다시 만드세요")
        stats["agent_changed_files"] = changed
    else:
        warnings.append("manifest.json 없음(에이전트 코드 변경 여부를 확인하지 못함)")

    # 출력 ---------------------------------------------------------------------------------
    print(f"토큰 길이 (Qwen3 채팅 템플릿 + 도구 8개, max_seq_length {max_seq})")
    print(f"{'split':<6}{'기록':>6}{'평균':>8}{'p50':>6}{'p90':>6}{'p99':>6}{'최대':>6}{'학습 토큰 평균':>14}{'초과':>5}")
    for split, st in stats["splits"].items():
        print(f"{split:<6}{st['records']:>6}{st['mean']:>8}{st['p50']:>6}{st['p90']:>6}{st['p99']:>6}{st['max']:>6}"
              f"{st['trained_mean']:>14}{st['over_max_seq']:>5}")
    print("\ntrain 종류별 길이 (기록 수 / 평균 / 최대)")
    for k, v in stats["splits"]["train"]["by_kind"].items():
        print(f"  {k:<14}{v['n']:>6}{v['mean']:>9}{v['max']:>7}")
    if iters:
        tr = stats["splits"]["train"]
        seconds = iters * batch * tr["mean_padded"] / V3_TOKENS_PER_SECOND
        evals = (iters // steps_per_eval + 1) if steps_per_eval else 1
        total = seconds + evals * V3_EVAL_SECONDS * stats["splits"]["valid"]["mean_padded"] / 1345
        stats["estimate"] = {"iters": iters, "batch_size": batch, "epochs": round(iters * batch / tr["records"], 2),
                             "train_seconds": round(seconds), "with_eval_seconds": round(total)}
        print(f"\n학습 시간 어림 (v3 실측 초당 {V3_TOKENS_PER_SECOND}토큰 기준): {iters}단계 × 배치 {batch} = "
              f"{iters * batch}개({iters * batch / tr['records']:.2f} epoch), 약 {seconds / 3600:.1f}시간 "
              f"(검증 {evals}번 포함 약 {total / 3600:.1f}시간)")
    print(f"\n누설 검사: 평가 발화 {len(held)}개, test 값 {len(test_values)}개, 사람 말투 세트에만 있는 값 {len(human_only)}개 → "
          + ("걸린 것 없음" if not leak else ", ".join(f"{k} {v}" for k, v in leak.items())))
    print("split 사이 발화 겹침: " + ", ".join(f"{k} {v}" for k, v in overlap.items()))
    for w in warnings:
        print(f"경고: {w}")
    if args.json:
        Path(args.json).write_text(json.dumps(stats, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if errors:
        print(f"\n문제 {len(errors)}건:")
        for e in errors[:30]:
            print(f"  - {e}")
        return 1
    print("\n형식·토큰·누설 검사 통과")
    return 0


if __name__ == "__main__":
    sys.exit(main())
