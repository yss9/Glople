"""템플릿 스냅샷(design.md 13절 M1, 15.1): 에이전트가 보낸 라우터 요청을 mlx_lm.server와 같은 채팅 템플릿으로 렌더링해
학습 레코드 앞부분과 바이트까지 같은지, 몇 토큰인지, 특수 토큰이 늘지 않았는지 본다.

training/.venv로 실행한다(jinja2·tokenizers만 import. transformers·mlx·torch는 import하지 않음, CPU만):

    training/.venv/bin/python -I agent/scripts/render_prompt.py --request req.json --record training/eval_sets/human_style.jsonl

- --request: 에이전트 쪽에서 가짜 OpenAI 서버가 받은 요청 본문(JSON). tests/test_template_snapshot.py가 만든다.
- --record: 같은 발화의 학습 레코드가 든 jsonl(system·user·assistant + tools). 발화로 찾는다.
  (M2) 생략할 수 있다: 답변(a3) 요청처럼 대조할 학습 레코드가 없는 요청은 토큰 수·특수 토큰 수만 본다.
  (M7) 도구 결과 뒤 요청(메시지 n개)은 발화가 같고 앞 n개의 역할이 같으며 그보다 긴 레코드(v4 after_call·after2_answer)를 찾아
  레코드의 앞 n개를 렌더링해 비교한다. 라우터 요청(n=2)은 지금까지와 같다.
- 렌더링 환경은 transformers의 apply_chat_template과 같게 맞췄다(utils/chat_template_utils.py를 읽고 옮김):
  ImmutableSandboxedEnvironment(trim_blocks, lstrip_blocks, loopcontrols), tojson = json.dumps(ensure_ascii=False).
- 요청 쪽은 mlx_lm.server process_message_content처럼 content None → "", 텍스트 조각 목록 → 이어 붙임, tool_calls의
  arguments 문자열 → dict(json.loads), add_generation_prompt=True. 레코드 쪽은 학습(mlx-lm ChatDataset) 때처럼 그대로 렌더링한다.
출력은 JSON 한 줄: same_bytes, prefix_of_record, tokens, special_counts, sha256.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

import jinja2
import jinja2.ext
from jinja2.sandbox import ImmutableSandboxedEnvironment
from tokenizers import Tokenizer

DEFAULT_MODEL_DIR = Path(__file__).resolve().parents[2] / "training" / "models" / "qwen3-4b-instruct-2507-4bit"
WATCH = ("<|im_start|>", "<|im_end|>", "<tool_call>", "</tool_call>", "<tool_response>", "</tool_response>",
         "<|endoftext|>", "<think>", "</think>")


def environment() -> ImmutableSandboxedEnvironment:
    def tojson(x, ensure_ascii=False, indent=None, separators=None, sort_keys=False):
        return json.dumps(x, ensure_ascii=ensure_ascii, indent=indent, separators=separators, sort_keys=sort_keys)

    def raise_exception(message):
        raise jinja2.exceptions.TemplateError(message)

    env = ImmutableSandboxedEnvironment(trim_blocks=True, lstrip_blocks=True, extensions=[jinja2.ext.loopcontrols])
    env.filters["tojson"] = tojson
    env.globals["raise_exception"] = raise_exception
    return env


def server_messages(messages: list[dict]) -> list[dict]:
    """mlx_lm.server process_message_content와 같은 정리(arguments 문자열은 dict로, server.py 238-243행)."""
    out = []
    for m in messages:
        m = dict(m)
        content = m.get("content")
        if isinstance(content, list):
            m["content"] = "".join(part["text"] for part in content if part.get("type") == "text")
        elif content is None:
            m["content"] = ""
        if m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                tc = dict(tc)
                func = dict(tc.get("function") or {})
                if isinstance(func.get("arguments"), str) and func["arguments"]:
                    func["arguments"] = json.loads(func["arguments"])
                tc["function"] = func
                calls.append(tc)
            m["tool_calls"] = calls
        out.append(m)
    return out


def render(template, messages, tools, *, add_generation_prompt: bool, server: bool = True) -> str:
    """server=True면 서버가 받은 요청처럼 정리한 뒤, False면 학습 레코드 그대로 렌더링한다."""
    msgs = server_messages(messages) if server else messages
    return template.render(messages=msgs, tools=tools or None, documents=None,
                           add_generation_prompt=add_generation_prompt)


def find_record(path: Path, request_messages: list[dict]) -> dict | None:
    """발화가 같고 앞 n개(요청 메시지 수)의 역할이 같으며 n개보다 긴 첫 레코드."""
    user = next(m["content"] for m in request_messages if m["role"] == "user")
    roles = [m["role"] for m in request_messages]
    with path.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            msgs = r["messages"]
            same_roles = [m["role"] for m in msgs[:len(roles)]] == roles
            if len(msgs) > len(roles) and msgs[1]["content"] == user and same_roles:
                return r
    return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--request", required=True, type=Path)
    p.add_argument("--record", default=None, type=Path)
    p.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    args = p.parse_args(argv)

    request = json.loads(args.request.read_text(encoding="utf-8"))
    record = find_record(args.record, request["messages"]) if args.record is not None else None
    n = len(request["messages"])
    template = environment().from_string((args.model_dir / "chat_template.jinja").read_text(encoding="utf-8"))
    tokenizer = Tokenizer.from_file(str(args.model_dir / "tokenizer.json"))
    text = render(template, request["messages"], request.get("tools"), add_generation_prompt=True)
    ids = tokenizer.encode(text, add_special_tokens=False).ids
    special = {t: ids.count(tokenizer.token_to_id(t)) for t in WATCH if tokenizer.token_to_id(t) is not None}
    out = {"tokens": len(ids), "chars": len(text), "special_counts": special,
           "sha256": hashlib.sha256(text.encode()).hexdigest(), "record_found": record is not None}
    if record is not None:
        prefix = render(template, record["messages"][:n], record["tools"], add_generation_prompt=True, server=False)
        full = render(template, record["messages"], record["tools"], add_generation_prompt=False, server=False)
        out["same_bytes"] = prefix.encode() == text.encode()
        out["prefix_of_record"] = full.startswith(text)
        out["record_special_counts"] = {t: tokenizer.encode(prefix, add_special_tokens=False).ids.count(
            tokenizer.token_to_id(t)) for t in special}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
