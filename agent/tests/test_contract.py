"""학습 계약: 라우터 요청이 학습 레코드와 같은 system·도구 JSON을 쓰는지."""
import hashlib
import json
import subprocess
import sys

import pytest

from glople_agent import contract

# glople_tools.json이 바뀌면 다시 학습해야 하므로 해시를 여기 기록해 둔다
EXPECTED_TOOLS_SHA256 = "4296f8936c5ebc7cf0fd73184fc603641bc324d82f455e9f8a1b066f6fd53ec6"
EXPECTED_TOOL_NAMES = (
    "recommend_by_keywords",
    "search_destinations",
    "recommend_similar_traveler_routes",
    "get_destination_detail",
    "search_reviews",
    "search_gloplers",
    "get_weather",
    "create_travel_plan",
)


def _ordered(obj) -> str:
    # 문자열 비교로 키 순서까지 본다
    return json.dumps(obj, ensure_ascii=False)


def test_tools_are_eight_in_file_order():
    assert contract.TOOL_NAMES == EXPECTED_TOOL_NAMES
    assert [t["type"] for t in contract.TOOLS] == ["function"] * 8


def test_read_tools_exclude_only_plan():
    assert contract.READ_TOOL_NAMES == EXPECTED_TOOL_NAMES[:-1]
    assert len(contract.READ_TOOL_NAMES) == 7
    assert contract.PLAN_TOOL_NAME not in contract.READ_TOOL_NAMES


def test_tools_bytes_and_sha256():
    raw = contract.TOOLS_PATH.read_bytes()
    assert contract.TOOLS_BYTES == raw
    assert contract.TOOLS_SHA256 == hashlib.sha256(raw).hexdigest() == EXPECTED_TOOLS_SHA256


def test_tools_not_mutated_and_keep_key_order():
    # 다른 테스트·코드가 TOOLS를 고쳐 쓰지 않았는지, 파일의 키 순서를 그대로 갖는지
    assert _ordered(contract.TOOLS) == _ordered(json.loads(contract.TOOLS_BYTES))
    assert list(contract.TOOLS[1]["function"]["parameters"]["properties"]) == ["region", "category", "query", "limit"]


def test_human_style_tools_equal_file_json():
    records = contract.read_jsonl(contract.HUMAN_STYLE_PATH)
    assert len(records) == 90
    for r in records:
        assert _ordered(r["tools"]) == _ordered(contract.TOOLS)


@pytest.mark.needs_training_data("tool_calling_v2/test.jsonl")
def test_v2_test_tools_and_system_equal_contract():
    records = contract.read_jsonl(contract.V2_TEST_PATH)
    assert len(records) == 300
    for r in records:
        assert _ordered(r["tools"]) == _ordered(contract.TOOLS)
        assert r["messages"][0] == {"role": "system", "content": contract.SYSTEM_PROMPT}


def test_system_prompt_equals_human_style_system():
    systems = {r["messages"][0]["content"] for r in contract.read_jsonl(contract.HUMAN_STYLE_PATH)}
    assert systems == {contract.SYSTEM_PROMPT}
    assert len(contract.SYSTEM_PROMPT) == 127


def test_system_prompt_read_without_running_script():
    # ast로만 읽으므로 학습 스크립트 모듈이 import되지 않는다
    assert "build_tool_dataset" not in sys.modules
    assert contract.read_assigned_literal(contract.DATASET_SCRIPT_PATH, "SYSTEM_PROMPT") == contract.SYSTEM_PROMPT


def test_read_assigned_literal_refuses_non_literal(tmp_path):
    src = tmp_path / "mod.py"
    src.write_text("X = open('nope')\nY = 1\nY = 2\n", encoding="utf-8")
    with pytest.raises(ValueError):
        contract.read_assigned_literal(src, "X")   # 호출식은 실행하지 않고 거부
    with pytest.raises(RuntimeError):
        contract.read_assigned_literal(src, "Y")   # 대입문이 둘이면 모호
    with pytest.raises(RuntimeError):
        contract.read_assigned_literal(src, "Z")


def test_keywords_94_unique_and_same_as_dataset_script():
    assert len(contract.KEYWORDS) == 94
    assert len(set(contract.KEYWORDS)) == 94
    assert all(isinstance(k, str) and k == k.strip() and k for k in contract.KEYWORDS)
    # 데이터 생성기가 쓴 목록과 순서까지 같다
    assert list(contract.KEYWORDS) == contract.read_assigned_literal(contract.DATASET_SCRIPT_PATH, "KEYWORDS")


@pytest.mark.needs_training_data("tool_calling_v2/train.jsonl")
def test_canned_texts_from_train():
    texts = contract.canned_texts()
    assert texts is not None and len(texts) == 30
    assert len(set(texts)) == 30
    assert "도움이 되어서 기뻐요! 여행 계획이 더 필요하면 언제든 말씀해 주세요." in texts
    texts.append("x")  # 돌려받은 목록을 고쳐도 다음 호출에는 영향 없음
    assert len(contract.canned_texts()) == 30


def test_canned_texts_none_without_data(tmp_path):
    assert contract.canned_texts(tmp_path / "missing" / "train.jsonl") is None


def test_canned_texts_from_given_file(tmp_path):
    path = tmp_path / "train.jsonl"
    rows = [
        {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u1"},
                      {"role": "assistant", "content": "안녕하세요!"}]},
        {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u2"},
                      {"role": "assistant", "content": "", "tool_calls": [
                          {"type": "function", "function": {"name": "get_weather", "arguments": "{}"}}]}]},
        {"messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u3"},
                      {"role": "assistant", "content": "안녕하세요!"}]},
    ]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    assert contract.canned_texts(path) == ["안녕하세요!"]


def test_module_prints_summary_line():
    proc = subprocess.run([sys.executable, "-m", "glople_agent.contract"],
                          capture_output=True, text=True, timeout=60, check=False)
    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.strip().splitlines()
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith(f"도구 8개, sha256={EXPECTED_TOOLS_SHA256}, SYSTEM_PROMPT 일치(127자), 키워드 94개, ")
    expected_tail = "정형 문장 30개" if contract.V2_TRAIN_PATH.is_file() else "정형 문장 데이터 없음(skip)"
    assert line.endswith(expected_tail)
