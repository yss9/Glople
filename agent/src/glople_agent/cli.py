"""CLI: 그래프를 직접 부른다(서비스는 M5).

    python -m glople_agent.cli graph                       # draw_mermaid() 텍스트
    python -m glople_agent.cli chat --fake rules           # REPL: 노드 진행 줄·카드 요약·답·확인 카드 y/n/e
    python -m glople_agent.cli chat --fake oracle --anon   # 정답 재생 라우터, 비로그인
    python -m glople_agent.cli chat --trajectory           # 턴마다 궤적 한 줄(var/trajectories/, 토큰·사용자 id·원문 없음)
    python -m glople_agent.cli replay --data v2-test,human # 정답 재생 표(v2-train도 가능)
    python -m glople_agent.cli tool recommend_by_keywords '{"keywords": ["바다"]}' --fixture   # LLM용 JSON과 카드 JSON
    python -m glople_agent.cli grounding                   # 근거 검사 측정(정답 막힘·예측 오답 잡음)

user_id·토큰은 고정 테스트 값이다(실제 JWT를 받지 않음). 모델 서버·Spring에 접속하지 않는다
(tool --live만 GLOPLE_SPRING_BASE_URL로 공개 API를 부른다. 오너가 IDOR 점검 뒤 직접 실행).
"""
import os

# langgraph를 import하기 전에(STRICT는 import 때 한 번 읽힘, api-notes)
os.environ["LANGSMITH_TRACING"] = "false"
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")

import argparse  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
import uuid  # noqa: E402
from collections.abc import Sequence  # noqa: E402
from datetime import datetime  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import TextIO  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

from langgraph.checkpoint.memory import InMemorySaver  # noqa: E402

from glople_agent.answer.templates import PLAN_PREVIOUS_CANCELLED, tool_label  # noqa: E402
from glople_agent.config import load_settings  # noqa: E402
from glople_agent.context import AgentContext, BearerToken  # noqa: E402
from glople_agent.eval.trajectory import JsonlTrajectorySink, format_line  # noqa: E402
from glople_agent.graph import (InterruptNotPending, InvalidResume, TurnResult, build_graph,  # noqa: E402
                                cancel_pending, fake_deps, mermaid, new_thread_id, resume, run_config, run_turn)
from glople_agent.llm.fake import OracleScript, RuleScript  # noqa: E402
from glople_agent.plan import InMemoryPlanStore  # noqa: E402
from glople_agent.tools.fake_backend import FakeBackend  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
CLI_USER = "cli-user"
CLI_TOKEN = "cli-fake-token-not-a-jwt"   # 고정 테스트 값
APPROVE, REJECT, EDIT = {"y", "yes", "예", "ㅇ"}, {"n", "no", "아니오", "ㄴ"}, "e"
HELP = "명령: /state 현재 상태, /new 새 대화, /help 도움말, /quit 끝. 확인 카드: y=저장 n=취소 e=고치기"


def build_script(name: str):
    if name == "rules":
        return RuleScript()
    if name == "oracle":   # 정답에 없는 발화는 규칙으로
        return OracleScript(skip_missing=True, fallback=RuleScript())
    raise ValueError(f"모르는 --fake: {name!r}")


def card_summary(card: dict) -> str:
    tool, status, data = card.get("tool") or "", card.get("status"), card.get("data") or {}
    if card.get("kind") == "plan_confirm":
        return f"[카드] 일정 확인 · {status}" + (" (취소됨)" if data.get("cancelled") else "")
    if card.get("kind") == "plan_saved":
        return f"[카드] 일정 저장됨 · plan_id={data.get('plan_id')}"
    items = data.get("items") or []
    names = [str(i.get("name") or i.get("title") or i.get("nickname") or i.get("ageBand") or "")
             for i in items if isinstance(i, dict)]
    head = f"[카드] {tool_label(tool)} · {status}"
    if status == "ok":
        shown = ", ".join(n for n in names[:3] if n)
        return f"{head} · {len(items)}개" + (f": {shown}" + (" …" if len(names) > 3 else "") if shown else "")
    notes = data.get("notes") or []
    return f"{head} · {notes[0]}" if notes else head


class ChatSession:
    """표준입력 한 줄 = 한 마디. 확인 카드가 떠 있으면 y/n/e로 답한다(다른 말을 하면 카드를 닫고 새 턴)."""

    def __init__(self, fake: str = "rules", *, user: str | None = CLI_USER, inp: TextIO | None = None,
                 out: TextIO | None = None, trajectory: JsonlTrajectorySink | None = None):
        self.inp = inp or sys.stdin
        self.out = out or sys.stdout
        self.store = InMemoryPlanStore()
        self.trajectory = trajectory   # 옵트인(--trajectory). 없으면 궤적을 파일에 쓰지 않음
        self.deps = fake_deps(build_script(fake), backend=FakeBackend(), store=self.store,
                              retry_initial_interval=0.5, sink=trajectory)
        self.graph = build_graph(self.deps, InMemorySaver())
        self.user = user
        self.thread = new_thread_id()
        self.shown_card: str | None = None   # 화면에 띄운 확인 카드의 interrupt id(재개 때 그대로 보냄, 9.2)
        self.interactive = self.inp.isatty()

    # --- 입출력 ---
    def say(self, text: str = "") -> None:
        print(text, file=self.out, flush=True)

    def read(self, prompt: str) -> str | None:
        if self.interactive:
            try:
                return input(prompt)
            except EOFError:
                return None
        line = self.inp.readline()
        if not line:
            return None
        line = line.rstrip("\n")
        self.say(f"{prompt}{line}")   # 파이프 입력도 대화 기록처럼 보이게
        return line

    def context(self) -> AgentContext:
        # 턴·재개마다 새로 만든다(context는 체크포인트에 저장되지 않음)
        bearer = BearerToken(CLI_TOKEN) if self.user else None
        return AgentContext(user_id=self.user, bearer=bearer, request_id=str(uuid.uuid4()),
                            now=datetime.now(KST), seed=0)

    def on_event(self, kind: str, value) -> None:
        if kind == "card":
            self.say("  " + card_summary(value))

    # --- 한 턴 ---
    def show(self, result: TurnResult) -> None:
        self.shown_card = result.pending.id if result.pending is not None else None
        path = " → ".join(result.path)
        if result.pending is not None:
            path += " → review(확인 대기)"
        values = result.values or {}
        if values.get("router_input") and values.get("router_input") != values.get("user_text") \
                and result.path[:1] == ["start_turn"]:
            # M1 후속 발화: 되묻기 답 이어 붙이기·지시어 풀이가 라우터에 넣은 한 마디를 바꿈(원문 메시지는 그대로)
            self.say(f"  라우터 입력: {values['router_input']}")
        self.say(f"  경로: {path}")
        if result.error == "recursion":
            self.say("  (GraphRecursionError: recursion_limit 25를 넘음)")
        if result.pending is not None:
            self.show_confirm(result.pending.value)
        elif result.final_text:
            self.say(f"도우미> {result.final_text}   [{result.reply_kind}]")
            if self.trajectory is not None and self.trajectory.last is not None:
                self.say(f"  궤적: {format_line(self.trajectory.last)}")

    def show_confirm(self, value: dict) -> None:
        d = value["draft"]
        self.say(f"  [확인 카드] {value['message']}")
        if value.get("notice_text"):
            self.say(f"    ! {value['notice_text']}")
        self.say(f"    지역 {d['region']} · {d['days']}일 · 제목 '{d['title']}'")
        for it in d["items"]:
            mark = "x" if it["selected"] else " "
            self.say(f"    {it['position']}. [{mark}] {it['dayNo']}일차 {it['name']} ({it['source']})")
        for p in d.get("problems") or []:
            self.say(f"    - {p}")
        self.say(f"    y=저장 · n=취소 · e=고치기 (남은 수정 {value['edits_left']}회)")

    def turn(self, text: str) -> None:
        if cancel_pending(self.graph, self.thread, self.context()) is not None:
            self.say(f"  ({PLAN_PREVIOUS_CANCELLED})")
        self.show(run_turn(self.graph, self.thread, text, self.context(), on_event=self.on_event))

    def answer_card(self, line: str) -> bool:
        """확인 카드 답이면 처리하고 True."""
        word = line.strip()
        lowered = word.lower()
        if lowered in APPROVE:
            decision = {"decision": "approve"}
        elif lowered in REJECT:
            decision = {"decision": "reject"}
        elif lowered == EDIT or lowered.startswith(EDIT + " "):
            decision = self.ask_edit(word[1:].strip())
            if decision is None:
                return True
        else:
            return False
        try:
            result = resume(self.graph, self.thread, decision, self.context(), interrupt_id=self.shown_card or "",
                            on_event=self.on_event)
        except InvalidResume as e:
            self.say(f"  {e} → 같은 카드에 다시 답해 주세요.")
            return True
        except InterruptNotPending as e:
            self.say(f"  {e}")
            return True
        self.show(result)
        return True

    def ask_edit(self, inline: str) -> dict | None:
        state = self.graph.get_state(run_config(self.thread)).values
        d = state["plan_draft"]
        if inline:   # e {"region": ..., "days": ..., "places": [...]}
            try:
                return {"decision": "edit", "draft": json.loads(inline)}
            except ValueError:
                self.say("  JSON을 읽지 못했어요. 예: e {\"region\": \"제주\", \"days\": 2, \"places\": [\"우도\"]}")
                return None
        # 고친 places는 '저장할 장소 목록'이라 빈 줄의 기본값은 선택된 장소만(해제된 제안은 적어야 들어감)
        places = [it["name"] for it in d["items"] if it["selected"]]
        unselected = [it["name"] for it in d["items"] if not it["selected"]]
        region = self.read(f"  지역 [{d['region']}]: ")
        days = self.read(f"  일수 [{d['days']}]: ")
        if unselected:
            self.say(f"  (해제된 제안: {', '.join(unselected)} · 넣으려면 장소 칸에 직접 적기)")
        names = self.read(f"  장소(쉼표로) [{', '.join(places)}]: ")
        title = self.read(f"  제목 [{d['title']}]: ")
        if title is None:
            return None
        draft = {"region": region or d["region"],
                 "days": (int(days) if days and days.strip().lstrip("-").isdigit() else (days or d["days"])),
                 "places": [p.strip() for p in names.split(",")] if names else places}
        if title:
            draft["title"] = title
        return {"decision": "edit", "draft": draft}

    def print_state(self) -> None:
        snap = self.graph.get_state(run_config(self.thread))
        v = snap.values
        kinds = [type(m).__name__.replace("Message", "") for m in v.get("messages") or []]
        self.say(f"  thread {self.thread} · next {snap.next or '()'} · 대기 카드 {len(snap.interrupts)}")
        for key in ("turn_id", "router_input", "router_kind", "router_attempts", "reply_kind", "final_text",
                    "plan_edits", "plan_notice"):
            self.say(f"  {key}: {v.get(key)!r}")
        self.say(f"  verdicts: {[(x['name'], x['ok'], x['reason']) for x in v.get('verdicts') or []]}")
        self.say(f"  results: {[(r['tool'], r['status']) for r in v.get('results') or []]}")
        self.say(f"  plan_draft: {(v.get('plan_draft') or {}).get('title')!r}")
        self.say(f"  messages({len(kinds)}): {' '.join(kinds)}")
        self.say(f"  cards: {len(v.get('cards') or [])}장 · memory: {v.get('memory') or {}}")
        self.say(f"  trace 키: {sorted((v.get('trace') or {}).keys())} · 저장된 일정: {self.store.save_count}")

    def run(self) -> int:
        who = f"user={self.user}" if self.user else "비로그인"
        self.say(f"Glople 도우미 M1 (가짜 모델·가짜 Spring, {who}). {HELP}")
        while True:
            line = self.read("나> ")
            if line is None:
                return 0
            line = line.strip()
            if not line:
                continue
            if line in ("/quit", "/exit"):
                return 0
            if line == "/help":
                self.say(f"  {HELP}")
            elif line == "/state":
                self.print_state()
            elif line == "/new":
                cancel_pending(self.graph, self.thread, self.context())
                self.thread, self.shown_card = new_thread_id(), None
                self.say(f"  새 대화: {self.thread}")
            elif self.graph.get_state(run_config(self.thread)).interrupts and self.answer_card(line):
                continue
            else:
                self.turn(line)


def cmd_graph(args, out: TextIO) -> int:
    deps = fake_deps(RuleScript())
    print(mermaid(build_graph(deps)), file=out)
    return 0


def cmd_chat(args, out: TextIO) -> int:
    sink = None
    if args.trajectory is not None:
        sink = JsonlTrajectorySink(Path(args.trajectory) if args.trajectory else None)
    return ChatSession(args.fake, user=None if args.anon else args.user, out=out, trajectory=sink).run()


def cmd_replay(args, out: TextIO) -> int:
    from glople_agent.eval.replay import format_prediction_replays, format_report, replay, replay_predictions

    if args.predictions:
        from glople_agent.eval.grounding_eval import PREDICTION_SETS

        names = [n.strip() for n in args.predictions.split(",") if n.strip()]
        unknown = [n for n in names if n not in PREDICTION_SETS]
        if unknown:
            print(f"모르는 예측 이름: {unknown} (가능: {', '.join(PREDICTION_SETS)})", file=out)
            return 2
        reps = [replay_predictions(n, PREDICTION_SETS[n]) for n in names if PREDICTION_SETS[n].is_file()]
        print(format_prediction_replays(reps), file=out)
        return 1 if any(r.errors or r.correct_changed for r in reps) else 0
    report = replay(args.data.split(","), check_request=not args.no_check_request)
    print(format_report(report), file=out)
    if not report.total:
        print("재생할 데이터가 없어요(training/data는 build_tool_dataset.py로 다시 만들 수 있음).", file=out)
    return 1 if report.errors or report.unexpected else 0


def cmd_tool(args, out: TextIO) -> int:
    from glople_agent.tools.tool_report import inspect_tool

    try:
        raw_args = json.loads(args.args)
    except ValueError as e:
        print(f"인자 JSON을 읽지 못했어요: {e}", file=out)
        return 2
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(KST)
    if now.tzinfo is None:
        now = now.replace(tzinfo=KST)
    base_url = load_settings().spring_base_url if args.live else None
    report = inspect_tool(args.name, raw_args, utterance=args.say, now=now, live_base_url=base_url)
    print(report.format(), file=out)
    return report.exit_code


def cmd_grounding(args, out: TextIO) -> int:
    from glople_agent.eval.grounding_eval import format_report, run

    golds, preds, missing = run()
    print(format_report(golds, preds, missing, details=args.details), file=out)
    bad = sum(g.blocked + g.changed for g in golds) + sum(len(p.correct_flagged) + p.broken() for p in preds)
    return 1 if bad else 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m glople_agent.cli", description="Glople 에이전트 CLI(M1)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("graph", help="그래프 mermaid 텍스트")
    chat = sub.add_parser("chat", help="대화(REPL)")
    chat.add_argument("--fake", choices=("rules", "oracle"), default="rules", help="가짜 라우터 스크립트")
    chat.add_argument("--user", default=CLI_USER, help="고정 테스트 user_id")
    chat.add_argument("--anon", action="store_true", help="비로그인(일정 저장 불가)")
    chat.add_argument("--trajectory", nargs="?", const="", default=None, metavar="DIR",
                      help="턴마다 궤적 한 줄을 남김(기본 agent/var/trajectories/, 토큰·사용자 id·원문 없음)")
    rep = sub.add_parser("replay", help="정답 재생 표")
    rep.add_argument("--data", default="v2-test,human", help="쉼표로: v2-train, v2-valid, v2-test, human")
    rep.add_argument("--no-check-request", action="store_true", help="라우터 요청 모양 검사 끄기")
    rep.add_argument("--predictions", default=None,
                     help="모델 예측 재생(쉼표로: v1-human, v2-human, v1-v2test, v2-v2test). 주면 --data는 무시")
    tool = sub.add_parser("tool", help="도구 하나 실행: LLM용 JSON과 카드 JSON을 나란히")
    tool.add_argument("name", help="도구 이름(읽기 도구 7개)")
    tool.add_argument("args", help="인자 JSON, 예: '{\"keywords\": [\"바다\"]}'")
    where = tool.add_mutually_exclusive_group()
    where.add_argument("--fixture", action="store_true", default=True, help="가짜 Spring(기본, 네트워크 없음)")
    where.add_argument("--live", action="store_true", help="GLOPLE_SPRING_BASE_URL의 공개 API(오너가 직접)")
    tool.add_argument("--say", default=None, help="발화를 주면 validate와 같은 근거 검사를 먼저 보여 줌")
    tool.add_argument("--now", default=None, help="기준 시각 ISO(나이 → 생년월일), 기본 지금")
    gr = sub.add_parser("grounding", help="근거 검사 측정 표(정답 막힘·예측 오답 잡음)")
    gr.add_argument("--details", action="store_true", help="걸린 발화 목록도")
    return p


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    args = parser().parse_args(argv)
    out = out or sys.stdout
    commands = {"graph": cmd_graph, "chat": cmd_chat, "replay": cmd_replay, "tool": cmd_tool,
                "grounding": cmd_grounding}
    return commands[args.command](args, out)


if __name__ == "__main__":
    sys.exit(main())
