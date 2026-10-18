"""CLI: 그래프를 직접 부른다(서비스는 M5).

    python -m glople_agent.cli graph                       # draw_mermaid() 텍스트
    python -m glople_agent.cli chat --fake rules           # REPL: 노드 진행 줄·카드 요약·답·확인 카드 y/n/e
    python -m glople_agent.cli chat --fake oracle --anon   # 정답 재생 라우터, 비로그인
    python -m glople_agent.cli chat --fake rules --answer llm   # 가짜 답변 모델(ResultEchoScript)로 answer_llm 경로
    python -m glople_agent.cli chat --llm real [--model-url http://127.0.0.1:8090/v1] [--answer llm|template] [--record PATH]
    python -m glople_agent.cli chat --trajectory           # 턴마다 궤적 한 줄(var/trajectories/, 토큰·사용자 id·원문 없음)
    python -m glople_agent.cli chat --db var/checkpoints.sqlite --resume   # M3: 껐다 켠 뒤 가장 최근 대화를 이어서(--thread ID)
    python -m glople_agent.cli chat --plan-graph subgraph  # M3: 일정 노드를 서브그래프로(평평한 판과 같은 동작)
    python -m glople_agent.cli chat --answer-prompt v2     # M4: 답변 system 문구 v2(llm/prompts.py)
    python -m glople_agent.cli replay --data v2-test,human # 정답 재생 표(v2-train도 가능, 늘 가짜 라우터)
    python -m glople_agent.cli tool recommend_by_keywords '{"keywords": ["바다"]}' --fixture   # LLM용 JSON과 카드 JSON
    python -m glople_agent.cli grounding                   # 근거 검사 측정(정답 막힘·예측 오답 잡음)
    python -m glople_agent.cli chat --weather live         # M6: 날씨를 실제 Open-Meteo로(--llm real의 기본). fixture·off
    python -m glople_agent.cli tool get_weather '{"region": "부산", "when": "내일"}' --fixture   # M6: 가짜 Open-Meteo
    python -m glople_agent.cli chat --fake v4 --after-tools loop   # M7: 도구 결과 뒤 루프(v4 test 정답 재생, 최대 2라운드)
    python -m glople_agent.cli chat --llm real --after-tools loop  # M7: 실제 모델(v4 서버)로 루프(기본 GLOPLE_AFTER_TOOLS)
    python -m glople_agent.cli replay --after-tools loop           # M7: 정답 재생을 루프 판으로(결과 뒤는 가짜 LoopAnswerScript)
    python -m glople_agent.cli graph --after-tools loop            # M7: 루프 판 그래프

user_id·토큰은 고정 테스트 값이다(실제 JWT를 받지 않음). 예외는 --plan-store spring(M3, 오너가 S2 확인할 때만)으로, 이때만
환경 변수 GLOPLE_SPRING_TOKEN의 본인 토큰을 BearerToken으로 감싸 일정 저장 요청에만 붙인다(화면·로그·체크포인트에 안 남김).
기본(--llm fake)은 모델 서버·Spring에 접속하지 않는다. chat은 기본으로 var/checkpoints.sqlite(gitignore)에 대화를 남긴다
(--memory면 메모리만). 명령 /history(체크포인트 목록)·/fork N(그 체크포인트에서 새 갈래)은 M3.
--llm real만 모델 서버(--model-url, 기본 GLOPLE_MODEL_BASE_URL = 127.0.0.1:8090)에 붙는다. 서버는 오너가
`bash training/scripts/serve.sh <어댑터> 8090`으로 띄우고, 이 CLI는 띄우지 않는다. Spring은 --llm real에서도 가짜(fixture)다
(tool --live만 GLOPLE_SPRING_BASE_URL로 공개 API를 부른다. 오너가 IDOR 점검 뒤 직접 실행).
M6 날씨(Open-Meteo, 오너 승인): --weather live|fixture|off. 기본은 --llm real·tool --live가 GLOPLE_WEATHER(없으면 live),
--llm fake·tool --fixture가 fixture(가짜 Open-Meteo, 네트워크 없음). 보내는 것은 좌표·일수(+ 지역 표 이름 지오코딩)뿐, 토큰 없음.
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

from glople_agent import checkpoints  # noqa: E402
from glople_agent.answer.templates import PLAN_PREVIOUS_CANCELLED, tool_label  # noqa: E402
from glople_agent.config import SettingsError, load_settings  # noqa: E402
from glople_agent.context import AgentContext, BearerToken, token_chars_ok  # noqa: E402
from glople_agent.eval.trajectory import JsonlTrajectorySink, format_line  # noqa: E402
from glople_agent.graph import (InterruptNotPending, InvalidResume, TurnResult, build_graph,  # noqa: E402
                                cancel_pending, fake_deps, fork, make_plan_store, mermaid, new_thread_id,
                                plan_state, real_deps, resume, run_config, run_turn)
from glople_agent.graph.build import PLAN_GRAPH_META  # noqa: E402
from glople_agent.llm.fake import (AfterToolsScript, LoopAnswerScript, OracleScript, ResultEchoScript,  # noqa: E402
                                   RuleScript, V4OracleScript, fake_answer_model)
from glople_agent.plan import InMemoryPlanStore, PlanStore  # noqa: E402
from glople_agent.tools.fake_backend import FakeBackend  # noqa: E402

KST = ZoneInfo("Asia/Seoul")
CLI_USER = "cli-user"
CLI_TOKEN = "cli-fake-token-not-a-jwt"   # 고정 테스트 값
ENV_SPRING_TOKEN = "GLOPLE_SPRING_TOKEN"  # --plan-store spring에서만 읽는 본인 토큰(출력하지 않음)
APPROVE, REJECT, EDIT = {"y", "yes", "예", "ㅇ"}, {"n", "no", "아니오", "ㄴ"}, "e"
HELP = ("명령: /state 현재 상태, /history [N|all] 체크포인트 목록, /fork N 그 체크포인트에서 새 갈래, /new 새 대화, "
        "/help 도움말, /quit 끝. 확인 카드: y=저장 n=취소 e=고치기")
HISTORY_LIMIT = 20
WEATHER_LABELS = {"live": "Open-Meteo(실제, 좌표만 보냄)", "fixture": "가짜 Open-Meteo(fixture, 네트워크 없음)",
                  "off": "끔(미지원 안내)"}


def build_script(name: str):
    if name == "rules":
        return RuleScript()
    if name == "oracle":   # 정답에 없는 발화는 규칙으로
        return OracleScript(skip_missing=True, fallback=RuleScript())
    if name == "v4":   # M7: v4 test 정답(첫 판단·두 번째 호출·결과 뒤 답). 없는 발화는 v2 정답 → 규칙, 결과 뒤는 LoopAnswerScript
        try:
            first = V4OracleScript(fallback=OracleScript(skip_missing=True, fallback=RuleScript()))
            after = V4OracleScript(fallback=LoopAnswerScript())
        except OSError:
            raise ValueError("--fake v4에는 training/data/tool_calling_v4/test.jsonl이 필요해요"
                             "(training/scripts/build_multiturn_dataset.py로 만듦).") from None
        return AfterToolsScript(first, after)
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
    if status == "ok" and card.get("kind") == "weather":   # M6: 날짜별 요약 + 출처(Open-Meteo 조건)
        days = [f"{i.get('label') or i.get('date')} {i.get('summary') or ''} {i.get('tmin')}~{i.get('tmax')}도".strip()
                for i in items[:3] if isinstance(i, dict)]
        place = (data.get("location") or {}).get("name")
        return (f"{head} · {place} · " if place else f"{head} · ") + ", ".join(days) + \
            (" …" if len(items) > 3 else "") + f" · {data.get('attribution')}"
    if status == "ok" and data.get("needsClarification"):   # M6 동명 장소 후보
        return f"{head} · 후보 {len(items)}곳(되묻기): " + ", ".join(
            f"{i.get('name')}({i.get('location')})" for i in items[:3] if isinstance(i, dict))
    if status == "ok":
        shown = ", ".join(n for n in names[:3] if n)
        return f"{head} · {len(items)}개" + (f": {shown}" + (" …" if len(names) > 3 else "") if shown else "")
    notes = data.get("notes") or []
    return f"{head} · {notes[0]}" if notes else head


def answer_label(result: TurnResult) -> str:
    """답 뒤에 붙는 [종류]. llm 모드에서 가드가 바꿨으면 이유를 함께(예: [template · 가드 unknown_number · 교체])."""
    kind = result.reply_kind or "?"
    trace = result.answer_trace
    if trace.get("guard") == "fail" and kind == "template":
        reason = trace.get("reason") or "?"
        return f"[template · 가드 {reason}" + (" · 교체]" if result.replaced else "]")
    return f"[{kind}]"


class ChatSession:
    """표준입력 한 줄 = 한 마디. 확인 카드가 떠 있으면 y/n/e로 답한다(다른 말을 하면 카드를 닫고 새 턴).

    llm="fake"(기본): 가짜 라우터(fake 스크립트). answer="llm"이면 가짜 답변 모델(ResultEchoScript)로 answer_llm 경로.
    llm="real": real_deps(model_url) — 라우터·답변 모두 실제 모델 서버(ChatOpenAI), Spring은 가짜(fixture).
    record: 카세트 경로(llm="real"만). 모델 요청·응답을 녹화한다(llm/cassette.py).
    M3: db(체크포인트 파일, None이면 메모리), thread(이 id로 시작·이어 감), resume_latest(파일의 가장 최근 대화를 이어 감),
    plan_graph("flat"|"subgraph"), store(일정 저장소, 기본 메모리). M4: answer_prompt("v1"|"v2"), answer_url(a1 답변 서버).
    M7: after_tools("answer"|"loop"). fake + loop면 결과 뒤 요청은 LoopAnswerScript가 받는다(--fake v4는 v4 정답)."""

    def __init__(self, fake: str = "rules", *, user: str | None = CLI_USER, inp: TextIO | None = None,
                 out: TextIO | None = None, trajectory: JsonlTrajectorySink | None = None, llm: str = "fake",
                 model_url: str | None = None, answer: str | None = None, record: str | Path | None = None,
                 db: str | Path | None = None, thread: str | None = None, resume_latest: bool = False,
                 plan_graph: str = "flat", answer_prompt: str = "v1", answer_url: str | None = None,
                 store: PlanStore | None = None, bearer: BearerToken | None = None, weather: str | None = None,
                 after_tools: str = "answer"):
        self.inp = inp or sys.stdin
        self.out = out or sys.stdout
        self.store = store if store is not None else InMemoryPlanStore()
        self.trajectory = trajectory   # 옵트인(--trajectory). 없으면 궤적을 파일에 쓰지 않음
        self.llm = llm
        self._bearer = bearer          # --plan-store spring의 본인 토큰(없으면 고정 테스트 값)
        # M6 날씨: real은 GLOPLE_WEATHER(기본 live, 오너 승인), fake는 fixture(네트워크 없음). --weather가 이김
        self.weather = weather or (load_settings().weather if llm == "real" else "fixture")
        if llm == "real":
            self.model_url = model_url or load_settings().model_base_url
            self.deps = real_deps(self.model_url, answer_mode=answer or "llm",
                                  backend=FakeBackend(weather=self.weather),
                                  store=self.store, sink=trajectory, retry_initial_interval=0.5, record=record,
                                  answer_url=answer_url, answer_prompt=answer_prompt, plan_graph=plan_graph,
                                  after_tools=after_tools)
        elif llm == "fake":
            if record is not None:
                raise ValueError("--record는 --llm real에서만 씁니다(가짜 모델은 녹화할 응답이 없음)")
            self.model_url = None
            mode = answer or "template"
            script = build_script(fake)
            if after_tools == "loop" and not isinstance(script, AfterToolsScript):
                script = AfterToolsScript(script)   # 결과 뒤 요청은 LoopAnswerScript(첫 판단 규칙이 같은 호출을 또 내지 않게)
            self.deps = fake_deps(script, backend=FakeBackend(weather=self.weather), store=self.store,
                                  retry_initial_interval=0.5, sink=trajectory, answer_mode=mode,
                                  answer_llm=fake_answer_model(ResultEchoScript()) if mode == "llm" else None,
                                  plan_graph=plan_graph, answer_prompt=answer_prompt, after_tools=after_tools)
        else:
            raise ValueError(f"모르는 --llm: {llm!r}")
        self.db = Path(db) if db is not None else None
        self._conn = None
        if self.db is not None:
            self.saver, self._conn = checkpoints.open_saver(self.db)
        else:
            self.saver = InMemorySaver()
        try:
            self.thread, self.continued = self._pick_thread(thread, resume_latest)
        except ValueError:
            self.close()
            raise
        self.graph = build_graph(self.deps, self.saver)
        if self.continued:
            try:
                self._check_plan_graph()
            except ValueError:
                self.close()
                raise
        self.user = user
        self.shown_card: str | None = None   # 화면에 띄운 확인 카드의 interrupt id(재개 때 그대로 보냄, 9.2)
        self.shown_value: dict | None = None  # 그 카드의 내용(e 고치기의 기본값)
        self.fork_from: str | None = None     # /fork N이 턴 끝 체크포인트면 다음 말을 그 뒤에 붙임
        self._rows: list[checkpoints.HistoryRow] = []   # 마지막 /history 목록(/fork N의 N)
        self.interactive = self.inp.isatty()
        self._previewing = False             # 답변 미리보기 줄을 쓰는 중(토큰이 오는 동안)

    def _pick_thread(self, thread: str | None, resume_latest: bool) -> tuple[str, bool]:
        if resume_latest and thread is None:
            latest = checkpoints.latest_thread(self.saver)
            if latest is None:
                raise ValueError("이어 갈 대화가 없어요(--db 파일에 체크포인트 없음).")
            return latest, True
        if thread is not None:
            exists = checkpoints.thread_exists(self.saver, thread)
            if resume_latest and not exists:
                raise ValueError(f"그 thread의 대화가 없어요: {thread}")
            return thread, exists
        return new_thread_id(), False

    def _check_plan_graph(self) -> None:
        """다른 --plan-graph로 만든 대화면 멈춘다: 대기 노드(plan ↔ review)가 이 그래프에 없어 확인 카드를 잃기 때문.
        판은 _stream이 체크포인트 metadata(plan_graph)에 남긴다."""
        tup = self.saver.get_tuple({"configurable": {"thread_id": self.thread}})
        made_with = ((tup.metadata or {}) if tup is not None else {}).get(PLAN_GRAPH_META)
        if made_with and made_with != self.deps.plan_graph:
            raise ValueError(f"이 대화는 --plan-graph {made_with}로 만든 대화예요. --plan-graph {made_with}로 이어 주세요.")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()   # WAL이 본 파일로 합쳐진다
            self._conn = None

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
        bearer = (self._bearer or BearerToken(CLI_TOKEN)) if self.user else None
        return AgentContext(user_id=self.user, bearer=bearer, request_id=str(uuid.uuid4()),
                            now=datetime.now(KST), seed=0)

    def on_event(self, kind: str, value) -> None:
        if kind == "card":
            self.say("  " + card_summary(value))
        elif kind == "token":   # M2 answer_llm 미리보기(가드 전). 끝나면 show()가 줄을 닫고 최종 답을 따로 찍음
            if not self._previewing:
                self.out.write("  미리보기> ")
                self._previewing = True
            self.out.write(value)
            self.out.flush()

    # --- 한 턴 ---
    def show(self, result: TurnResult) -> None:
        if self._previewing:
            self.say()
            self._previewing = False
        self.shown_card = result.pending.id if result.pending is not None else None
        self.shown_value = result.pending.value if result.pending is not None else None
        self._rows = []   # 턴·재개가 지나면 /history 번호가 밀리므로 다음 /fork는 새 목록 기준
        path = " → ".join(result.qualified_path or result.path)
        if result.pending is not None:
            path = (path + " → " if path else "") + f"{(result.waiting or ('review',))[0]}(확인 대기)"
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
            self.say(f"도우미> {result.final_text}   {answer_label(result)}")
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
        if self.fork_from is not None:   # /fork N(턴 끝 체크포인트): 머리 대신 그 뒤에 붙여 새 갈래
            checkpoint_id, self.fork_from = self.fork_from, None
            self.show(run_turn(self.graph, self.thread, text, self.context(), on_event=self.on_event,
                               checkpoint_id=checkpoint_id))
            return
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
        # 화면에 띄운 카드의 초안(interrupt 값). 서브그래프 판은 plan_draft가 부모 state에 없어서 카드 값을 쓴다
        d = (self.shown_value or {}).get("draft") or plan_state(self.graph, self.thread).get("plan_draft")
        if d is None:
            self.say("  고칠 확인 카드가 없어요.")
            return None
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
        plan = plan_state(self.graph, self.thread)   # 서브그래프 판은 대기 중인 서브그래프의 일정 키
        kinds = [type(m).__name__.replace("Message", "") for m in v.get("messages") or []]
        self.say(f"  thread {self.thread} · next {snap.next or '()'} · 대기 카드 {len(snap.interrupts)}")
        for key in ("turn_id", "router_input", "router_kind", "router_attempts", "reply_kind", "final_text"):
            self.say(f"  {key}: {v.get(key)!r}")
        for key in ("plan_edits", "plan_notice"):
            self.say(f"  {key}: {plan.get(key)!r}")
        self.say(f"  verdicts: {[(x['name'], x['ok'], x['reason']) for x in v.get('verdicts') or []]}")
        self.say(f"  results: {[(r['tool'], r['status']) for r in v.get('results') or []]}")
        self.say(f"  plan_draft: {(plan.get('plan_draft') or {}).get('title')!r}")
        self.say(f"  messages({len(kinds)}): {' '.join(kinds)}")
        self.say(f"  cards: {len(v.get('cards') or [])}장 · memory: {v.get('memory') or {}}")
        self.say(f"  trace 키: {sorted((v.get('trace') or {}).keys())} · 저장된 일정: "
                 f"{getattr(self.store, 'save_count', '?')}")

    # --- M3: 기록·시간 여행 ---
    def print_history(self, arg: str = "") -> None:
        limit = None if arg == "all" else (int(arg) if arg.isascii() and arg.isdecimal() else HISTORY_LIMIT)
        self._rows = checkpoints.history(self.graph, self.thread, limit=limit)
        self.say(checkpoints.format_history(self._rows))

    def fork_at(self, arg: str) -> None:
        self.fork_from = None   # 앞선 /fork(턴 끝)의 '다음 말 붙이기'는 새 /fork로 취소
        if not (arg.isascii() and arg.isdecimal()):
            self.say("  /fork N 꼴로 적어 주세요(N은 /history의 #번호).")
            return
        index = int(arg)   # 마지막 /history 목록의 번호. 그 목록에 없으면(줄여 봤거나 안 봤으면) 지금 전체 목록에서
        row = next((r for r in self._rows if r.index == index), None)
        if row is None:
            row = next((r for r in checkpoints.history(self.graph, self.thread) if r.index == index), None)
        if row is None:
            self.say(f"  #{index} 체크포인트가 없어요(/history로 확인).")
            return
        if not row.next:   # 턴이 끝난 자리: 다음 말을 그 뒤에 붙인다
            self.fork_from = row.checkpoint_id
            self.say(f"  #{index}(step {row.step}) 뒤에서 이어요. 다음 말이 새 갈래가 됩니다.")
            return
        self.say(f"  #{index}(step {row.step})에서 다시 실행: {', '.join(row.next)}부터")
        self.show(fork(self.graph, row.config, self.context(), on_event=self.on_event))

    def greet(self) -> None:
        who = f"user={self.user}" if self.user else "비로그인"
        answer = "LLM 답변" if self.deps.answer_mode == "llm" else "템플릿 답변"
        if self.deps.after_tools == "loop":   # M7: 결과 뒤 답은 라우터 모델이 쓰고 가드가 봄(실패·막힘은 템플릿)
            answer = "결과 뒤 라우터 답변(loop)"
        model = f"실제 모델 {self.model_url}" if self.llm == "real" else "가짜 모델"
        self.say(f"Glople 도우미 M2 ({model}·{answer}·가짜 Spring, {who}). {HELP}")
        extras = []
        if self.db is not None:
            extras.append(f"체크포인트 {self.db}")
        if self.deps.plan_graph != "flat":
            extras.append("일정 서브그래프")
        if self.deps.answer_prompt != "v1":
            extras.append(f"답변 문구 {self.deps.answer_prompt}")
        if type(self.store).__name__ != "InMemoryPlanStore":
            extras.append(f"일정 저장 {type(self.store).__name__}")
        if extras:
            self.say("  M3·M4: " + " · ".join(extras))
        if self.deps.after_tools == "loop":
            self.say("  M7: 도구 결과 뒤 루프(라우터가 결과를 보고 답하거나 한 번 더 부름, 최대 2라운드)")
        self.say("  M6: 날씨 " + WEATHER_LABELS.get(self.weather, self.weather))
        if self.continued:
            snap = self.graph.get_state(run_config(self.thread))
            msgs = snap.values.get("messages") or []
            last = snap.values.get("final_text")
            self.say(f"  이어서: thread {self.thread} · 메시지 {len(msgs)}개 · 마지막 답 " + (f"“{last}”" if last else "없음"))
            if snap.interrupts:   # 카드가 떠 있는 채로 껐으면 그 카드를 다시 띄워 바로 답하게
                intr = snap.interrupts[0]
                self.shown_card, self.shown_value = intr.id, intr.value
                self.say("  (끄기 전에 떠 있던 확인 카드)")
                self.show_confirm(intr.value)
        elif self.db is not None:
            self.say(f"  새 대화: thread {self.thread} (다시 켤 때 --resume 또는 --thread {self.thread})")

    def run(self) -> int:
        try:
            self.greet()
            while True:
                line = self.read("나> ")
                if line is None:
                    return 0
                line = line.strip()
                if not line:
                    continue
                if line in ("/quit", "/exit"):
                    return 0
                command, _, arg = line.partition(" ")
                if line == "/help":
                    self.say(f"  {HELP}")
                elif line == "/state":
                    self.print_state()
                elif command == "/history":
                    self.print_history(arg.strip())
                elif command == "/fork":
                    self.fork_at(arg.strip())
                elif line == "/new":
                    cancel_pending(self.graph, self.thread, self.context())
                    self.thread, self.shown_card, self.shown_value, self.fork_from = new_thread_id(), None, None, None
                    self._rows = []
                    self.say(f"  새 대화: {self.thread}")
                elif (self.fork_from is None and self.graph.get_state(run_config(self.thread)).interrupts
                      and self.answer_card(line)):   # /fork로 턴 끝을 골랐으면 다음 말은 카드 답이 아니라 새 갈래
                    continue
                else:
                    self.turn(line)
        finally:
            self.close()


def cmd_graph(args, out: TextIO) -> int:
    deps = fake_deps(RuleScript(), plan_graph=args.plan_graph, after_tools=args.after_tools)
    print(mermaid(build_graph(deps), xray=args.xray), file=out)
    return 0


def spring_bearer(environ=None) -> BearerToken | None:
    """--plan-store spring의 본인 토큰(GLOPLE_SPRING_TOKEN). 값은 BearerToken 밖으로 꺼내지 않는다.
    헤더에 넣을 수 없는 글자(공백·제어 문자·ASCII 밖)가 있으면 None(값은 어디에도 찍지 않음)."""
    raw = (os.environ if environ is None else environ).get(ENV_SPRING_TOKEN, "").strip()
    return BearerToken(raw) if raw and token_chars_ok(raw) else None


def cmd_chat(args, out: TextIO) -> int:
    if args.record and args.llm != "real":
        print("--record는 --llm real에서만 씁니다.", file=out)
        return 2
    settings = load_settings()   # 잘못된 환경 변수 값은 main()이 SettingsError로 받아 종료 코드 2
    store = None
    bearer = None
    plan_store = args.plan_store or settings.plan_store   # 기본 메모리(GLOPLE_PLAN_STORE로 바꿀 수 있음)
    if plan_store == "spring":   # 오너가 S2(/api/travel-plans)를 확인할 때만. 읽기 도구는 그대로 가짜 Spring
        bearer = spring_bearer()
        if bearer is None or args.anon:
            print(f"--plan-store spring에는 로그인 사용자와 {ENV_SPRING_TOKEN}(본인 토큰, 헤더에 넣을 수 있는 글자)이 "
                  "필요해요.", file=out)
            return 2
        try:
            store = make_plan_store("spring", spring_base_url=settings.spring_base_url)
        except ValueError as e:   # 토큰을 평문으로 보낼 원격 http 주소 등
            print(str(e), file=out)
            return 2
    sink = None
    if args.trajectory is not None:
        sink = JsonlTrajectorySink(Path(args.trajectory) if args.trajectory else None)
    try:
        session = ChatSession(args.fake, user=None if args.anon else args.user, out=out, trajectory=sink,
                              llm=args.llm, model_url=args.model_url, answer=args.answer, record=args.record,
                              db=None if args.memory else args.db, thread=args.thread, resume_latest=args.resume,
                              plan_graph=args.plan_graph, answer_prompt=args.answer_prompt or settings.answer_prompt,
                              answer_url=args.answer_url, store=store, bearer=bearer, weather=args.weather,
                              after_tools=args.after_tools or settings.after_tools)
    except ValueError as e:
        print(str(e), file=out)
        return 2
    return session.run()


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
    report = replay(args.data.split(","), check_request=not args.no_check_request, after_tools=args.after_tools)
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
    settings = load_settings()
    base_url = settings.spring_base_url if args.live else None
    weather = args.weather or (settings.weather if args.live else "fixture")
    report = inspect_tool(args.name, raw_args, utterance=args.say, now=now, live_base_url=base_url, weather=weather)
    print(report.format(), file=out)
    return report.exit_code


def cmd_grounding(args, out: TextIO) -> int:
    from glople_agent.eval.grounding_eval import format_report, run

    golds, preds, missing = run()
    print(format_report(golds, preds, missing, details=args.details), file=out)
    bad = sum(g.blocked + g.changed for g in golds) + sum(len(p.correct_flagged) + p.broken() for p in preds)
    return 1 if bad else 0


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m glople_agent.cli", description="Glople 에이전트 CLI(M2)")
    sub = p.add_subparsers(dest="command", required=True)
    graph = sub.add_parser("graph", help="그래프 mermaid 텍스트")
    graph.add_argument("--plan-graph", choices=("flat", "subgraph"), default="flat",
                       help="일정 노드를 평평하게(기본) / 서브그래프로(M3)")
    graph.add_argument("--xray", action="store_true", help="서브그래프 안까지 그림")
    graph.add_argument("--after-tools", choices=("answer", "loop"), default="answer",
                       help="M7: 도구 결과 뒤 answer(기본) / loop(route_after_tools·validate_after_tools)")
    chat = sub.add_parser("chat", help="대화(REPL)")
    chat.add_argument("--llm", choices=("fake", "real"), default="fake",
                      help="fake=가짜 라우터(기본, 네트워크 없음), real=실제 모델 서버(mlx_lm.server, 오너가 띄움)")
    chat.add_argument("--fake", choices=("rules", "oracle", "v4"), default="rules",
                      help="가짜 라우터 스크립트(--llm fake). v4 = v4 test 정답 재생(M7 루프 데모)")
    chat.add_argument("--model-url", default=None,
                      help="--llm real의 OpenAI 호환 주소(기본 GLOPLE_MODEL_BASE_URL, http://127.0.0.1:8090/v1)")
    chat.add_argument("--answer", choices=("llm", "template"), default=None,
                      help="답변 경로(기본: real은 llm, fake는 template). fake+llm은 가짜 답변 모델")
    chat.add_argument("--record", default=None, metavar="PATH",
                      help="--llm real에서 모델 요청·응답을 카세트(JSON)로 녹화(재생은 llm.fake.CassetteScript)")
    chat.add_argument("--user", default=CLI_USER, help="고정 테스트 user_id")
    chat.add_argument("--anon", action="store_true", help="비로그인(일정 저장 불가)")
    chat.add_argument("--trajectory", nargs="?", const="", default=None, metavar="DIR",
                      help="턴마다 궤적 한 줄을 남김(기본 agent/var/trajectories/, 토큰·사용자 id·원문 없음)")
    chat.add_argument("--db", default=str(checkpoints.DEFAULT_DB), metavar="PATH",
                      help="M3: 체크포인트 sqlite 파일(기본 agent/var/checkpoints.sqlite, gitignore)")
    chat.add_argument("--memory", action="store_true", help="M3: 체크포인트를 파일에 남기지 않음(메모리만)")
    chat.add_argument("--thread", default=None, metavar="ID", help="M3: 이 thread id로 시작하거나 이어 감")
    chat.add_argument("--resume", action="store_true",
                      help="M3: --db의 가장 최근 대화를 이어 감(--thread와 함께면 그 thread가 있어야 함)")
    chat.add_argument("--plan-graph", choices=("flat", "subgraph"), default="flat",
                      help="M3: 일정 노드를 평평하게(기본) / 서브그래프로")
    chat.add_argument("--plan-store", choices=("memory", "spring"), default=None,
                      help=f"M3: 일정 저장소(기본 GLOPLE_PLAN_STORE 또는 memory. spring은 S2 /api/travel-plans + "
                           f"{ENV_SPRING_TOKEN}, 오너 확인용)")
    chat.add_argument("--answer-prompt", choices=("v1", "v2"), default=None,
                      help="M4: 답변 system 문구(기본 GLOPLE_ANSWER_PROMPT 또는 v1 = design.md 6.4, v2 = 2~4문장 소개)")
    chat.add_argument("--answer-url", default=None,
                      help="M4: --llm real에서 답변만 다른 서버로(a1, 예: 기본 모델 http://127.0.0.1:8091/v1)")
    chat.add_argument("--weather", choices=("live", "fixture", "off"), default=None,
                      help="M6: 날씨(기본: --llm real은 GLOPLE_WEATHER 또는 live, fake는 fixture). live = Open-Meteo, "
                           "fixture = 가짜(네트워크 없음), off = 부르지 않음")
    chat.add_argument("--after-tools", choices=("answer", "loop"), default=None,
                      help="M7: 도구 결과 뒤(기본 GLOPLE_AFTER_TOOLS 또는 answer). loop = 라우터가 결과를 보고 답하거나 "
                           "한 번 더 부름(최대 2라운드, 가드 실패·막힘은 템플릿)")
    rep = sub.add_parser("replay", help="정답 재생 표")
    rep.add_argument("--data", default="v2-test,human", help="쉼표로: v2-train, v2-valid, v2-test, human")
    rep.add_argument("--no-check-request", action="store_true", help="라우터 요청 모양 검사 끄기")
    rep.add_argument("--after-tools", choices=("answer", "loop"), default="answer",
                     help="M7: 도구 결과 뒤 answer(기본) / loop(결과 뒤 요청은 가짜 LoopAnswerScript)")
    rep.add_argument("--predictions", default=None,
                     help="모델 예측 재생(쉼표로: v1-human, v2-human, v1-v2test, v2-v2test). 주면 --data는 무시")
    tool = sub.add_parser("tool", help="도구 하나 실행: LLM용 JSON과 카드 JSON을 나란히")
    tool.add_argument("name", help="도구 이름(읽기 도구 7개)")
    tool.add_argument("args", help="인자 JSON, 예: '{\"keywords\": [\"바다\"]}'")
    where = tool.add_mutually_exclusive_group()
    where.add_argument("--fixture", action="store_true", default=True, help="가짜 Spring(기본, 네트워크 없음)")
    where.add_argument("--live", action="store_true", help="GLOPLE_SPRING_BASE_URL의 공개 API(오너가 직접)")
    tool.add_argument("--say", default=None, help="발화를 주면 validate와 같은 근거 검사를 먼저 보여 줌")
    tool.add_argument("--now", default=None, help="기준 시각 ISO(나이 → 생년월일, 날씨 날짜), 기본 지금")
    tool.add_argument("--weather", choices=("live", "fixture", "off"), default=None,
                      help="M6: get_weather의 날씨(기본: --fixture면 fixture, --live면 GLOPLE_WEATHER 또는 live)")
    gr = sub.add_parser("grounding", help="근거 검사 측정 표(정답 막힘·예측 오답 잡음)")
    gr.add_argument("--details", action="store_true", help="걸린 발화 목록도")
    return p


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    args = parser().parse_args(argv)
    out = out or sys.stdout
    commands = {"graph": cmd_graph, "chat": cmd_chat, "replay": cmd_replay, "tool": cmd_tool,
                "grounding": cmd_grounding}
    try:
        return commands[args.command](args, out)
    except SettingsError as e:   # 환경 변수 값이 틀림(GLOPLE_PLAN_STORE=mysql 등)
        print(f"설정 오류: {e}", file=out)
        return 2


if __name__ == "__main__":
    sys.exit(main())
