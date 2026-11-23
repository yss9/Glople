"""llm/models.py 팩토리(네트워크 없이): 설계 6.2의 값이 ChatOpenAI에 그대로 들어가는지. HTTP 왕복은 test_pins_openai_http."""
from glople_agent import contract
from glople_agent.config import Settings
from glople_agent.llm.models import (ANSWER_LOGIT_BIAS, DUMMY_API_KEY, MODEL_NAME, answer_model, bind_router,
                                     models_from_settings, router_model)

URL = "http://127.0.0.1:1/v1"   # 부르지 않음(만들기만)


def test_router_model_settings():
    m = router_model(URL)
    assert (m.model_name, m.openai_api_base, m.use_responses_api, m.max_retries) == (MODEL_NAME, URL, False, 0)
    assert m.openai_api_key.get_secret_value() == DUMMY_API_KEY
    assert (m.temperature, m.max_tokens, m.disable_streaming, m.tags) == (0, 384, True, ["nostream"])
    assert m.request_timeout == 20.0 and not m.streaming
    bound = bind_router(m)
    assert bound.kwargs["tools"] == contract.TOOLS and "tool_choice" not in bound.kwargs
    payload = m._get_request_payload([("user", "x")], tools=contract.TOOLS)
    assert payload["tools"] == contract.TOOLS and payload["max_completion_tokens"] == 384


def test_answer_model_settings_and_overrides():
    a = answer_model(URL, timeout=5)
    assert (a.temperature, a.top_p, a.max_tokens, a.streaming) == (0.3, 0.8, 400, True)
    assert a.extra_body == {"top_k": 20} and a.logit_bias == ANSWER_LOGIT_BIAS and a.request_timeout == 5
    assert router_model(URL, max_tokens=768).max_tokens == 768


def test_models_from_settings_share_one_base_url():
    r, a = models_from_settings(Settings(model_base_url=URL))
    assert r.openai_api_base == a.openai_api_base == URL
