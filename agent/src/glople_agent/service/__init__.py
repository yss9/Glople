"""M5 서비스: 그래프를 FastAPI `/ai-api/v1`(127.0.0.1:8095)로 감싼다(design.md 4·10.2·12절).

    .venv/bin/python -m glople_agent.service                # 환경 변수 설정(기본: 실제 모델 8090 + Spring 8080)
    .venv/bin/python -m glople_agent.service --llm fake     # 가짜 모델(화면 개발용, 모델 서버 없이)
    .venv/bin/uvicorn --factory glople_agent.service.app:app_factory --host 127.0.0.1 --port 8095

모듈: settings(환경 변수) · auth(my-info 확인·캐시) · threads(주인 표) · runner(astream·aget_state) · limits(잠금·자리·요청 수)
· review(일정 검토) · deps(real/fake 의존성) · app(라우트·SSE). fastapi·uvicorn은 extras 'server'(pyproject.toml).
"""
import os

# langgraph를 import하기 전에(cli.py와 같음: 트레이싱 끔, 역직렬화 허용 목록)
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
