"""LLM 연결: 서버 quirk 정규화(normalize), 가짜 모델(fake), 실제 ChatOpenAI 클라이언트(models.py, M1),
답변 프롬프트(prompts.py, M2), 카세트 녹화·재생(cassette.py + fake.CassetteScript, M2).

models.py는 langchain-openai를 import하므로 여기서 미리 import하지 않는다(필요한 곳에서 직접).
"""
from glople_agent.llm.cassette import Cassette, CassetteMiss, RecordingChatModel, recording_model, request_key
from glople_agent.llm.fake import (
    DEFAULT_QUIRKS,
    QUIRK_KINDS,
    RULE_DEMOS,
    CassetteScript,
    OracleCase,
    OracleMiss,
    OracleScript,
    QuirkScript,
    RequestShapeError,
    ResultEchoScript,
    RuleDemo,
    RuleScript,
    ScriptedChatModel,
    ScriptExhausted,
    check_router_request,
    fake_answer_model,
    fake_router_model,
    from_server,
    load_oracle_cases,
    message_from_record,
    quirk_message,
    sequential_ids,
    server_tool_call,
    text_message,
    tool_call_message,
)
from glople_agent.llm.normalize import (
    RETRY_OVERRIDES,
    RouterOutcome,
    is_answer_model_error,
    is_model_connection_error,
    is_model_error,
    is_model_timeout_error,
    new_call_id,
    normalize_router,
    normalize_router_error,
    parse_tool_call_text,
    retry_overrides,
)

__all__ = [
    "DEFAULT_QUIRKS", "QUIRK_KINDS", "RETRY_OVERRIDES", "RULE_DEMOS",
    "Cassette", "CassetteMiss", "CassetteScript", "OracleCase", "OracleMiss", "OracleScript", "QuirkScript",
    "RecordingChatModel", "RequestShapeError", "ResultEchoScript", "RouterOutcome", "RuleDemo",
    "RuleScript", "ScriptExhausted", "ScriptedChatModel", "message_from_record", "recording_model", "request_key",
    "check_router_request", "fake_answer_model", "fake_router_model", "from_server", "is_answer_model_error",
    "is_model_connection_error",
    "is_model_error", "is_model_timeout_error", "load_oracle_cases", "new_call_id", "normalize_router",
    "normalize_router_error", "parse_tool_call_text", "quirk_message", "retry_overrides", "sequential_ids",
    "server_tool_call", "text_message", "tool_call_message",
]
