"""평가: 정답 재생(replay, M0), 근거 검사 측정(grounding_eval, M1), 채점(scoring)·궤적(trajectory),
M4 A/B 하네스(abtest: `python -m glople_agent.eval.abtest`, langgraph import 전에 환경 변수를 맞추므로 여기서 import하지 않음)."""
from glople_agent.eval.replay import (DATASETS, CaseResult, ReplayReport, dataset_paths, format_report, replay,
                                      replay_cases)

__all__ = ["DATASETS", "CaseResult", "ReplayReport", "dataset_paths", "format_report", "replay", "replay_cases"]
