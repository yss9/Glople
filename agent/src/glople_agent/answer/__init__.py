"""답변: 코드 템플릿(M0~, 기준선이자 대체 경로). LLM 답변(M2)은 graph 쪽에서 붙인다."""
from glople_agent.answer.templates import (CLARIFY, FALLBACK, answer_for_card, answer_for_cards, clarify_for,
                                           fallback_text, join_answers, josa)

__all__ = ["CLARIFY", "FALLBACK", "answer_for_card", "answer_for_cards", "clarify_for", "fallback_text",
           "join_answers", "josa"]
