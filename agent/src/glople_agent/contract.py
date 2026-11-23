"""학습 계약: 라우터 요청을 학습 때와 바이트까지 같게 만드는 값들.

training/ 파일은 경로로 읽기만 한다. SYSTEM_PROMPT는 build_tool_dataset.py를 실행하지 않고
ast로 그 대입문만 읽는다(모듈을 import하면 학습 스크립트 코드가 돈다).
"""
import ast
import hashlib
import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

from glople_agent.config import load_settings

TRAINING_DIR = load_settings().training_dir
TOOLS_PATH = TRAINING_DIR / "tools" / "glople_tools.json"
KEYWORDS_PATH = TRAINING_DIR / "keyword_tagger" / "keywords.json"
DATASET_SCRIPT_PATH = TRAINING_DIR / "scripts" / "build_tool_dataset.py"
LABELING_RULES_PATH = TRAINING_DIR / "eval_sets" / "labeling_rules.md"
HUMAN_STYLE_PATH = TRAINING_DIR / "eval_sets" / "human_style.jsonl"  # 커밋돼 있음
V2_DATA_DIR = TRAINING_DIR / "data" / "tool_calling_v2"             # gitignore, 시드로 다시 만듦
V2_TRAIN_PATH = V2_DATA_DIR / "train.jsonl"
V2_TEST_PATH = V2_DATA_DIR / "test.jsonl"

PLAN_TOOL_NAME = "create_travel_plan"


def read_assigned_literal(path: Path, name: str) -> Any:
    """파일을 실행하지 않고 최상위 `name = <리터럴>` 대입문의 값만 읽는다."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            found.append(value)
    if len(found) != 1:
        raise RuntimeError(f"{path.name}에서 {name} 대입문을 하나로 찾지 못했습니다({len(found)}개)")
    return ast.literal_eval(found[0])


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


TOOLS_BYTES: bytes = TOOLS_PATH.read_bytes()
TOOLS_SHA256: str = hashlib.sha256(TOOLS_BYTES).hexdigest()
TOOLS: list[dict] = json.loads(TOOLS_BYTES)  # 파일 순서 그대로. bind_tools에 이 dict를 넘김
TOOL_NAMES: tuple[str, ...] = tuple(t["function"]["name"] for t in TOOLS)
READ_TOOL_NAMES: tuple[str, ...] = tuple(n for n in TOOL_NAMES if n != PLAN_TOOL_NAME)

SYSTEM_PROMPT: str = read_assigned_literal(DATASET_SCRIPT_PATH, "SYSTEM_PROMPT")
KEYWORDS: tuple[str, ...] = tuple(json.loads(KEYWORDS_PATH.read_text(encoding="utf-8")))


@lru_cache(maxsize=4)
def _canned_texts(path: Path) -> tuple[str, ...] | None:
    if not path.is_file():
        return None
    seen: dict[str, None] = {}
    for record in read_jsonl(path):
        answer = record["messages"][-1]
        if answer.get("role") == "assistant" and not answer.get("tool_calls"):
            seen.setdefault(answer["content"], None)
    return tuple(seen)


def canned_texts(train_path: Path | None = None) -> list[str] | None:
    """학습 텍스트 정답(인사·거절·되묻기 정형 문장) 목록. train.jsonl이 없으면 None."""
    texts = _canned_texts(V2_TRAIN_PATH if train_path is None else train_path)
    return None if texts is None else list(texts)


def _human_style_system_prompts() -> set[str] | None:
    if not HUMAN_STYLE_PATH.is_file():
        return None
    return {r["messages"][0]["content"] for r in read_jsonl(HUMAN_STYLE_PATH)}


def summary() -> tuple[str, bool]:
    """(요약 한 줄, 계약이 맞는지)."""
    systems = _human_style_system_prompts()
    if systems is None:
        prompt_part, prompt_ok = f"SYSTEM_PROMPT 확인 못 함(human_style 없음, {len(SYSTEM_PROMPT)}자)", True
    elif systems == {SYSTEM_PROMPT}:
        prompt_part, prompt_ok = f"SYSTEM_PROMPT 일치({len(SYSTEM_PROMPT)}자)", True
    else:
        prompt_part, prompt_ok = f"SYSTEM_PROMPT 불일치({len(SYSTEM_PROMPT)}자)", False
    texts = canned_texts()
    canned_part = "정형 문장 데이터 없음(skip)" if texts is None else f"정형 문장 {len(texts)}개"
    line = (f"도구 {len(TOOLS)}개, sha256={TOOLS_SHA256}, {prompt_part}, "
            f"키워드 {len(KEYWORDS)}개, {canned_part}")
    return line, prompt_ok


def main() -> int:
    line, ok = summary()
    print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
