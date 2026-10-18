"""템플릿 스냅샷(design.md 13절 M1, 15.1): 에이전트가 보낸 라우터 요청을 mlx_lm.server와 같은 채팅 템플릿으로 렌더링해
학습 레코드 앞부분과 바이트까지 같은지, 몇 토큰인지, 특수 토큰이 늘지 않았는지 본다.

training/.venv로 실행한다(jinja2·tokenizers만 import. transformers·mlx·torch는 import하지 않음, CPU만):

    training/.venv/bin/python -I agent/scripts/render_prompt.py --request req.json --record training/eval_sets/human_style.jsonl

- --request: 에이전트 쪽에서 가짜 OpenAI 서버가 받은 요청 본문(JSON). tests/test_template_snapshot.py가 만든다.
- --record: 같은 발화의 학습 레코드가 든 jsonl(system·user·assistant + tools). 발화로 찾는다.
- 렌더링 환경은 transformers의 apply_chat_template과 같게 맞췄다(utils/chat_template_utils.py를 읽고 옮김):
  ImmutableSandboxedEnvironment(trim_blocks, lstrip_blocks, loopcontrols), tojson = json.dumps(ensure_ascii=False).
- mlx_lm.server처럼 content None → "", 텍스트 조각 목록 → 이어 붙임, add_generation_prompt=True.
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
    """mlx_lm.server process_message_content와 같은 정리."""
    out = []
    for m in messages:
        m = dict(m)
        content = m.get("content")
        if isinstance(content, list):
            m["content"] = "".join(part["text"] for part in content if part.get("type") == "text")
        elif content is None:
            m["content"] = ""
        out.append(m)
    return out


def render(template, messages, tools, *, add_generation_prompt: bool) -> str:
    return template.render(messages=server_messages(messages), tools=tools or None, documents=None,
                           add_generation_prompt=add_generation_prompt)


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--request", required=True, type=Path)
    p.add_argument("--record", required=True, type=Path)
    p.add_argument("--model-dir", type=Path, default=DEFAULT_MODEL_DIR)
    args = p.parse_args(argv)

    request = json.loads(args.request.read_text(encoding="utf-8"))
    user = next(m["content"] for m in request["messages"] if m["role"] == "user")
    record = None
    with args.record.open(encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if r["messages"][1]["content"] == user:
                record = r
                break
    template = environment().from_string((args.model_dir / "chat_template.jinja").read_text(encoding="utf-8"))
    tokenizer = Tokenizer.from_file(str(args.model_dir / "tokenizer.json"))
    text = render(template, request["messages"], request.get("tools"), add_generation_prompt=True)
    ids = tokenizer.encode(text, add_special_tokens=False).ids
    special = {t: ids.count(tokenizer.token_to_id(t)) for t in WATCH if tokenizer.token_to_id(t) is not None}
    out = {"tokens": len(ids), "chars": len(text), "special_counts": special,
           "sha256": hashlib.sha256(text.encode()).hexdigest(), "record_found": record is not None}
    if record is not None:
        prefix = render(template, record["messages"][:2], record["tools"], add_generation_prompt=True)
        full = render(template, record["messages"], record["tools"], add_generation_prompt=False)
        out["same_bytes"] = prefix.encode() == text.encode()
        out["prefix_of_record"] = full.startswith(text)
        out["record_special_counts"] = {t: tokenizer.encode(prefix, add_special_tokens=False).ids.count(
            tokenizer.token_to_id(t)) for t in special}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
