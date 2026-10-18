"""학습 평가(training/scripts/evaluate.py)와 같은 채점 함수.

evaluate.py는 16줄에서 mlx를 import하므로 모듈을 import하지 않는다. 파일을 ast로 읽어 채점에 쓰는 함수 정의
(`parse_output`, `_sort_key`, `normalize`, `canonical`)와 `TOOL_CALL` 대입문만 골라 컴파일한다(design.md 15.2).
그래서 에이전트 쪽 '정답' 판정이 학습 평가와 글자 하나까지 같다. evaluate.py가 바뀌면 여기도 그대로 따라간다.
"""
import ast
import json
import re
from functools import cache
from typing import Any

from glople_agent import contract

EVALUATE_PATH = contract.TRAINING_DIR / "scripts" / "evaluate.py"
_FUNCTIONS = ("parse_output", "_sort_key", "normalize", "canonical")
_ASSIGNS = ("TOOL_CALL",)


@cache
def _namespace() -> dict[str, Any]:
    tree = ast.parse(EVALUATE_PATH.read_text(encoding="utf-8"), filename=str(EVALUATE_PATH))
    picked: list[ast.stmt] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in _FUNCTIONS:
            picked.append(node)
        elif isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in _ASSIGNS for t in node.targets):
            picked.append(node)
    names = {n.name for n in picked if isinstance(n, ast.FunctionDef)}
    if names != set(_FUNCTIONS):
        raise RuntimeError(f"evaluate.py에서 채점 함수를 찾지 못함: {sorted(set(_FUNCTIONS) - names)}")
    module = ast.Module(body=picked, type_ignores=[])
    namespace: dict[str, Any] = {"json": json, "re": re, "__name__": "evaluate_scoring"}
    exec(compile(module, str(EVALUATE_PATH), "exec"), namespace)   # 함수 정의·정규식 대입만(부작용 없음)
    return namespace


def parse_output(text: str) -> tuple[list[dict], int]:
    """모델 출력의 <tool_call> 블록 → ([{"name", "arguments"}], 깨진 블록 수)."""
    return _namespace()["parse_output"](text)


def canonical(calls: list[dict]) -> list[tuple[str, str]]:
    """호출 목록의 비교용 꼴(이름 + 정렬한 인자 JSON). 순서·목록 순서·숫자 문자열 차이를 무시한다."""
    return _namespace()["canonical"](calls)


def same_calls(a: list[dict], b: list[dict]) -> bool:
    return canonical(a) == canonical(b)


__all__ = ["EVALUATE_PATH", "canonical", "parse_output", "same_calls"]
