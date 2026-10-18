"""정답 재생(design.md 14.2 test_replay): OracleScript로 v2 train·test + human_style을 그래프에 흘린다.

M1부터 validate에 근거 검사가 붙었다. 그래도 정답 호출은 하나도 막히지 않고(validate 통과, 인자도 그대로),
텍스트 정답은 direct_reply로 간다(설계 13절 M1: 정답 3,289호출 막힘 0).
human_style은 저장소에 커밋돼 있어 늘 돌고, v2는 training/data가 있을 때만 돈다.
"""
import pytest

from glople_agent import contract
from glople_agent.eval.replay import CATEGORIES, dataset_paths, format_report, replay, replay_cases
from glople_agent.graph import RECURSION_LIMIT
from glople_agent.llm.fake import load_oracle_cases


def _check(report, expected_total: int) -> None:
    assert report.total == expected_total
    assert report.errors == [], [(r.case.utterance, r.error) for r in report.errors[:5]]
    assert report.reached_end == expected_total          # 모두 END(확인 카드는 approve로 재개)
    assert report.unexpected == [], [(r.case.utterance, r.category) for r in report.unexpected[:5]]
    for r in report.results:
        if r.case.is_call:
            assert r.verdicts_ok and r.calls == len(r.case.expected_calls)   # validate가 하나도 막지 않음
        else:
            assert r.category == "direct" and r.reply_kind == "direct"
    assert report.max_invoke_steps < RECURSION_LIMIT
    paths = report.path_counts()
    assert sum(paths.values()) == expected_total and set(paths) <= set(CATEGORIES)
    assert paths["fallback"] == paths["clarify"] == paths["unfinished"] == 0
    assert report.saved_plans == paths["plan"]   # 일정 정답은 승인 한 번에 1건씩


def test_human_style_replay():
    report = replay(["human"])
    _check(report, 90)
    assert report.path_counts()["plan"] > 0 and report.path_counts()["tool(병렬)"] > 0
    table = format_report(report)
    assert "| 예외 | 0 |" in table and "| END 도달 | 90/90 |" in table


@pytest.mark.needs_training_data("tool_calling_v2/test.jsonl")
def test_v2_test_and_human_style_replay():
    report = replay(["v2-test", "human"])
    _check(report, 390)
    assert report.reply_counts()["direct"] == sum(not r.case.is_call for r in report.results)


@pytest.mark.needs_training_data("tool_calling_v2/train.jsonl")
def test_v2_train_replay_with_grounding_blocks_nothing():
    # train 3,000건(호출 2,913) 전부: 근거 검사를 거쳐도 막힘·인자 변경 0(13~15초)
    report = replay(["v2-train"])
    _check(report, 3000)
    assert sum(r.calls for r in report.results) == 2913


def test_replay_matches_oracle_answers_exactly():
    # 라우터 메시지에 남은 호출이 정답 호출과 이름·인자까지 같은지(정규화 뒤에도 바뀌지 않음)
    cases = load_oracle_cases([contract.HUMAN_STYLE_PATH])[:40]
    results, _ = replay_cases(cases)
    for r in results:
        assert r.as_expected, r.case.utterance
        assert r.executed == r.case.expected_calls   # 텍스트 정답이면 둘 다 []


def test_dataset_names():
    paths, missing = dataset_paths(["human", " "])
    assert paths == [contract.HUMAN_STYLE_PATH] and missing == []
    with pytest.raises(ValueError):
        dataset_paths(["nope"])
