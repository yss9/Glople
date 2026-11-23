"""`python -m glople_agent.service [--llm fake|real] [--plan-store memory|spring] [--read-backend fixture|spring]
[--after-tools answer|loop] [--port N]`

환경 변수(service/settings.py)를 먼저 읽고 인자로 덮는다. 기본 주소는 127.0.0.1:8095(모델 서버·Spring과 같은 컴퓨터).
이 명령은 모델 서버(8090)·Spring(8080)을 띄우지 않는다(오너가 따로 띄움). --llm fake면 모델 서버 없이 돈다.
"""
import argparse
import ipaddress
import sys

from glople_agent.config import SettingsError


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m glople_agent.service", description="Glople 여행 도우미 서비스(M5)")
    parser.add_argument("--host", help="기본 127.0.0.1(GLOPLE_SERVICE_HOST)")
    parser.add_argument("--port", type=int, help="기본 8095(GLOPLE_SERVICE_PORT)")
    parser.add_argument("--llm", choices=("fake", "real"), help="기본 real(GLOPLE_SERVICE_LLM)")
    parser.add_argument("--answer", choices=("llm", "template"), help="답변 방식(GLOPLE_ANSWER_MODE, 기본 llm)")
    parser.add_argument("--after-tools", choices=("answer", "loop"),
                        help="도구 결과 뒤(GLOPLE_AFTER_TOOLS, 기본 answer). loop = 라우터가 답하거나 한 번 더(M7, 최대 2라운드)")
    parser.add_argument("--plan-store", choices=("memory", "spring"), help="일정 저장소(real 기본 spring, fake 기본 memory)")
    parser.add_argument("--read-backend", choices=("fixture", "spring"), help="읽기 도구(기본 fixture)")
    parser.add_argument("--data-dir", help="체크포인트·주인 표 폴더(기본 agent/var/service)")
    parser.add_argument("--allow-remote", action="store_true", help="127.0.0.1이 아닌 주소에 묶기(권장하지 않음)")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args(argv)

    import os
    from pathlib import Path

    from glople_agent.service.settings import ServiceSettings

    env = dict(os.environ)
    if args.llm:
        env["GLOPLE_SERVICE_LLM"] = args.llm
    try:
        settings = ServiceSettings.from_env(env)
        changes = {k: v for k, v in {"host": args.host, "port": args.port, "answer_mode": args.answer,
                                       "after_tools": args.after_tools, "plan_store": args.plan_store,
                                       "read_backend": args.read_backend}.items()
                   if v is not None}
        if args.data_dir:
            changes["data_dir"] = Path(args.data_dir).expanduser().resolve()
        settings = settings.with_(**changes)
    except SettingsError as e:
        print(f"설정 오류: {e}", file=sys.stderr)
        return 2
    if not _loopback(settings.host) and not args.allow_remote:
        print(f"127.0.0.1(루프백)에만 묶습니다: {settings.host} (정말 필요하면 --allow-remote)", file=sys.stderr)
        return 2

    import uvicorn

    from glople_agent.service.app import create_app

    print(f"Glople 서비스 http://{settings.host}:{settings.port}/ai-api/v1 · llm={settings.llm} answer={settings.answer_mode} "
          f"after_tools={settings.after_tools} plan_store={settings.plan_store} read_backend={settings.read_backend} · "
          f"데이터 {settings.data_dir}",
          file=sys.stderr)
    uvicorn.run(create_app(settings), host=settings.host, port=settings.port, log_level=args.log_level,
                proxy_headers=False, server_header=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
