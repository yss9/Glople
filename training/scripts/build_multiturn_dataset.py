"""Glople 도구 호출 모델 v4(M7) 데이터: 도구 결과를 받은 **뒤** 무엇을 할지(답하기 / 한 번 더 부르기) 배우는 다턴 기록.

왜 만드나 (agent/docs/m2-after-tools.md, 2026-10-18 실측)
- v1~v3 학습 데이터에는 도구 결과(role=tool) 뒤 턴이 하나도 없다. 그래서 v3 LoRA에 학습 때와 같은 system + 도구 8개로
  [사용자, 호출, 결과]를 보내면(방식 A, 일반 에이전트 루프) 결과를 무시하고 되묻거나, 같은/다른 도구를 또 부르거나,
  학습한 되묻기 문장을 그대로 냈다(결과를 근거로 답함 65/85). 답변 전용 프롬프트(방식 B)로는 80/85였다.
- v4는 방식 A에서도 결과를 읽고 끝내도록 가르친다. design.md 15.3의 'v3 다턴 데이터'가 이제 v4다.

기록 = assistant 정답 턴 하나 (mlx-lm ChatDataset은 마지막 assistant만 학습한다, mask_prompt)
  (a) first_turn   : v3 train 기록 그대로 [system, user] → 첫 판단(도구 호출 / 되묻기 / 바로 답변). 같은 SYSTEM_PROMPT·도구·train/test 분리
  (b) after_answer : [system, user, 호출, 결과…] → 결과만 근거로 한 2~4문장 답 (결과가 있는 경우)
  (c) after_call   : [system, user, 호출, 결과] → 두 번째 호출. 사용자가 '결과에 따라 이어서' 해 달라고 했을 때만(드물게)
                     예) 키워드 추천 → 맨 위 장소 후기, 키워드 추천 → 그 장소 날씨, 비슷한 루트 → 첫 여행지 후기,
                         상세 정보가 빈 결과 → '없으면 후기라도'. 최대 2라운드, 같은 서명(이름+인자) 반복 없음
      after2_answer: [system, user, 호출, 결과, 호출, 결과] → 두 결과를 합친 답 (2라운드 뒤에는 늘 답)
      chain_first  : 위 '이어서' 발화의 첫 판단 → 첫 호출만 (두 번째 인자는 결과를 봐야 알 수 있으므로)
  (d) after_limit  : 빈 결과·준비 중·미지원·오류 → 제한을 그대로 알리는 답 (같은 호출을 다시 부르지 않음)

도구 결과는 에이전트의 실제 도구 코드로 만든다
- glople_agent.tools.registry.run_tool + FakeBackend(가짜 Spring·가짜 Open-Meteo, httpx.MockTransport). 오류는
  FakeBackend(errors=…) → ToolBackendError → envelope.tool_error_to_json. 결과 JSON은 에이전트가 모델에 보내는 봉투 그대로이고
  Type3 설명문은 원래 없다. 에이전트 M6(2026-10-18 오후)부터 지역 검색·상세 정보(S1)와 날씨(Open-Meteo)도 실제 결과를 낸다
  (국내 음식점·숙박은 부르지 않음 = unsupported, 같은 이름이 여러 지역이면 후보 되묻기, 날씨는 날짜·기온·강수 확률).
- train·valid는 학습 전용 fixture(scripts/multiturn_fixtures.py → fixtures/v4_backend/, 에이전트 fixture와 이름이 겹치지 않음)의
  변형(행 일부·순서 섞기)으로, test는 에이전트 기본 fixture(평가와 같은 것)로 만든다. agent/는 고치지 않는다.
- 에이전트 도구 코드가 바뀌면 결과 모양도 바뀔 수 있다. 만들 때 쓴 에이전트 파일 해시를 data/tool_calling_v4/manifest.json에 남기고,
  check_multiturn_dataset.py가 지금 파일과 다르면 다시 만들라고 알린다(같은 시드면 같은 파일이 나온다).
- 숫자: 개수는 고유어('세 곳'), 날짜는 결과 글자 그대로(2026-10-19). measure_after_tools.classify가 답의 아라비아 숫자를 결과 글의
  숫자와 글자로 맞춰 보기 때문이다('8월 1일'의 8은 '08'과 다르게 셈).
답은 코드 템플릿 + 정해진 변주로 쓰고(LLM 호출 없음), 모든 답을 에이전트 답변 가드(glople_agent.graph.guard.check_answer)와
아래 추가 검사로 확인해 하나라도 걸리면 버린다. 두 번째 호출은 에이전트 인자 모델(ARGS_MODELS)과 근거 검사(check_call,
근거 = 발화 + 앞 결과의 이름·지역)를 통과해야 한다.

누설 검사 (새로 만든 기록에서 하나라도 걸리면 멈춤. 가져온 v3 기록이 걸리면 그 기록만 뺀다):
v2/v3 test·human_style·human_style_v2 발화가 train·valid에 없음, v2/v3 test 지역·장소·조건·해시태그·일정 이름과 사람 말투 세트에만
나오는 지역·장소가 train·valid 글(발화·호출·결과·답)에 없음, test 카테고리 말이 발화에 없음, train·valid 도구 결과 항목에
에이전트 평가 fixture의 장소 이름·후기 제목·닉네임이 없음.

실행 (agent/.venv: 에이전트 도구 코드와 가드를 import한다. mlx·GPU·서버 없음):
  cd training && ../agent/.venv/bin/python scripts/build_multiturn_dataset.py
  토큰 길이 확인은 training/.venv로: .venv/bin/python scripts/check_multiturn_dataset.py
출력: data/tool_calling_v4/{train,valid,test}.jsonl (mlx-lm chat 형식: messages + tools, 그 밖에 task·meta), fixtures/v4_backend/*.json
"""
import argparse
import importlib.util
import json
import random
import re
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT / "scripts"))

try:
    from glople_agent import contract
    from glople_agent.answer.templates import josa, pick_josa, tool_label
    from glople_agent.graph.grounding import check_call
    from glople_agent.graph.guard import answer_facts, check_answer
    from glople_agent.sanitize import clean_text
    from glople_agent.tools.args import ARGS_MODELS
    from glople_agent.tools.backend import KST, ToolContext
    from glople_agent.tools.envelope import ToolBackendError, tool_error_to_json
    from glople_agent.tools.fake_backend import DEFAULT_FIXTURES_DIR, FakeBackend
    from glople_agent.tools.registry import run_tool
    from glople_agent.tools.regions import resolve as resolve_region
except ImportError as e:  # pragma: no cover - 실행 환경 안내
    sys.exit(f"agent/.venv의 python으로 실행하세요 (../agent/.venv/bin/python scripts/build_multiturn_dataset.py): {e}")

import multiturn_fixtures as mf  # noqa: E402

TODAY = date(2026, 10, 18)
CTX = ToolContext(now=datetime(2026, 10, 18, 12, tzinfo=KST))   # agent/scripts/measure_after_tools.py와 같은 기준 시각
OUT_DIR = ROOT / "data" / "tool_calling_v4"
FIXTURE_DIR = ROOT / "fixtures" / "v4_backend"
EVAL_SETS = {
    "v2_test": ROOT / "data" / "tool_calling_v2" / "test.jsonl",
    "v3_test": ROOT / "data" / "tool_calling_v3" / "test.jsonl",
    "human_style": ROOT / "eval_sets" / "human_style.jsonl",
    "human_style_v2": ROOT / "eval_sets" / "human_style_v2.jsonl",
}
N_VARIANTS = 6
# 렌더링 토큰 = system+도구 머리 1,268 + (system 뒤 메시지 JSON 글자 수 × 최대 0.51)(check_multiturn_dataset.py로 잰 값).
# 1,500자면 2,033토큰 이하라 max_seq_length 2048 안에 든다(넘는 기록은 버림 — 잘린 채 학습하면 끝을 못 배움)
MAX_BODY_CHARS = 1500
DROP_LOG: list | None = None   # --dump-drops 경로를 주면 버린 답을 모아 쓴다(템플릿 손볼 때)
READ_TOOLS = set(contract.READ_TOOL_NAMES)
NATIVE = {1: "한", 2: "두", 3: "세", 4: "네", 5: "다섯"}
VERBATIM_NOTE = "설명은 원문 카드"
NOTE_DOMESTIC = "국내 글로플러는 아직 없어요(지금은 미국·이탈리아·일본)."
NOTE_NONE = "지금은 미국·이탈리아·일본 글로플러만 있어요."
LORA_TIC = "비슷한 여행자를 찾으려면"   # M2 실측의 LoRA 말버릇(학습한 되묻기 앞부분). 결과 뒤 답에는 쓰지 않는다
# measure_after_tools.classify의 '제한을 알림' 판정 낱말
LIMIT_WORDS = ("없", "준비", "아직", "연결하지", "못 찾", "찾지 못")


def load_v3():
    """v3 생성기(scripts/build_tool_dataset.py)를 모듈로 읽는다. 파일은 고치지 않고 정의만 가져다 쓴다(main은 돌지 않음)."""
    spec = importlib.util.spec_from_file_location("build_tool_dataset_v3", ROOT / "scripts" / "build_tool_dataset.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


V3 = load_v3()
SYSTEM_PROMPT = V3.SYSTEM_PROMPT
TOOLS = V3.TOOLS
assert SYSTEM_PROMPT == contract.SYSTEM_PROMPT, "v3 생성기와 에이전트 계약의 SYSTEM_PROMPT가 다름"
assert TOOLS == contract.TOOLS, "v3 생성기와 에이전트 계약의 도구 정의가 다름"


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def user_text(record: dict) -> str:
    return next(m["content"] for m in record["messages"] if m["role"] == "user")


def calls_of(message: dict) -> list[tuple[str, dict]]:
    return [(c["function"]["name"], json.loads(c["function"]["arguments"])) for c in message.get("tool_calls") or []]


def signature(name: str, args: dict) -> str:
    return name + " " + json.dumps(args, ensure_ascii=False, sort_keys=True)


# --- 평가 쪽 값 (누설 검사) ----------------------------------------------------------------

_HANGUL = re.compile(r"[가-힣ㄱ-ㅎ]")


def boundary_hits(text: str, entities) -> list[str]:
    """entities 중 text에 단어 시작 경계(앞 글자가 한글이 아님)로 들어 있는 것. 뒤에 붙는 글자는 보지 않는다(보수적)."""
    found = []
    for e in entities:
        i = text.find(e)
        while i >= 0:
            if i == 0 or not _HANGUL.match(text[i - 1]):
                found.append(e)
                break
            i = text.find(e, i + 1)
    return found


class HeldOut:
    """학습(train·valid)에 들어가면 안 되는 평가 쪽 발화·값."""

    def __init__(self):
        self.utterances: dict[str, str] = {}
        for name, path in EVAL_SETS.items():
            for r in read_jsonl(path):
                self.utterances.setdefault(user_text(r), name)
        test_pools = [V3.REGIONS["test"], V3.PLACES["test"], list(V3.REGION_PLACES["test"]), list(V3.DESCRIPTORS["test"]),
                      V3.HASHTAGS["test"], V3.PLAN_TITLES["test"]]
        self.test_entities = sorted({v for pool in test_pools for v in pool})
        self.test_category_words = sorted({w for words in V3.CATEGORY_WORDS_TEST.values() for w in words})
        train_pool = (set(V3.REGIONS["train"]) | set(V3.PLACES["train"]) | set(V3.HASHTAGS["train"])
                      | set(V3.DESCRIPTORS["train"]) | set(V3.KEYWORDS) | set(V3.PLAN_TITLES["train"]))
        human = set()
        for name in ("human_style", "human_style_v2"):
            for r in read_jsonl(EVAL_SETS[name]):
                for _, a in calls_of(r["messages"][-1]):
                    for k in ("region", "name", "query", "hashtag", "places", "title"):
                        v = a.get(k)
                        human.update(x for x in (v if isinstance(v, list) else [v]) if isinstance(x, str) and x)
        # v3 train 풀에도 있는 값(우도·전주처럼 사람 말투 세트를 쓸 때 생성기를 보지 않아 겹친 것)은 v3부터 이미 train에 있어 뺀다
        self.human_only_entities = sorted(human - train_pool)
        self.eval_fixture_names = sorted(self._agent_fixture_names())

    @staticmethod
    def _agent_fixture_names() -> set[str]:
        names = set()
        kw = json.loads((DEFAULT_FIXTURES_DIR / "recommend_by_keywords.json").read_text(encoding="utf-8"))
        names |= {r["destName"] for r in kw["rows"]}
        rv = json.loads((DEFAULT_FIXTURES_DIR / "search_reviews.json").read_text(encoding="utf-8"))
        names |= {clean_text(r["title"], 40) for r in rv["rows"]}
        gl = json.loads((DEFAULT_FIXTURES_DIR / "search_gloplers.json").read_text(encoding="utf-8"))
        names |= {r["nickname"] for r in gl["rows"]}
        dest = DEFAULT_FIXTURES_DIR / "destinations.json"   # M6 S1 여행지(에이전트에 2026-10-18 오후 추가)
        if dest.is_file():
            names |= {r["name"] for r in json.loads(dest.read_text(encoding="utf-8"))["rows"]}
        return names

    def record_problems(self, record: dict, *, new_content: bool) -> list[str]:
        """train·valid 기록 하나의 누설 문제. new_content면 도구 결과·답까지(v4가 새로 만든 글), 아니면 v3 기록."""
        problems = []
        user = user_text(record)
        if user in self.utterances:
            problems.append(f"평가 발화({self.utterances[user]}): {user}")
        body = json.dumps(record["messages"][1:], ensure_ascii=False)
        for e in boundary_hits(body, self.test_entities):
            problems.append(f"test 값 '{e}'")
        for e in boundary_hits(body, self.human_only_entities):
            problems.append(f"사람 말투 세트 값 '{e}'")
        # test 카테고리 말(밥집·펜션 …)은 발화 문장 틀의 분리 규칙이라 사용자 발화만 본다. 답에는 에이전트의 정해진 안내
        # ('음식점·숙소 정보는 아직 모으지 않았어요')가 그대로 들어갈 수 있다(enum 값 '음식점'과 같은 말)
        for e in boundary_hits(user, self.test_category_words):
            problems.append(f"test 카테고리 말 '{e}'")
        if new_content:
            results = [m["content"] for m in record["messages"] if m["role"] == "tool"]
            problems += [f"평가 fixture 이름 '{e}'(도구 결과)" for e in self.eval_fixture_hits(results)]
        return problems

    def eval_fixture_hits(self, contents: list[str]) -> list[str]:
        """도구 결과 항목 이름(장소·후기 제목·닉네임)이 평가 fixture 이름과 같은지. 5자 이상 이름은 글 속 포함도 본다
        ('곤돌라'·'캐니언' 같은 짧은 닉네임은 다른 이름의 일부일 수 있어 같은 이름만 센다)."""
        own, texts = set(), []
        for c in contents:   # notes는 사용자가 말한 검색어를 되풀이하므로('아직 ○○ 후기가 없어요') 항목만 본다
            for item in json.loads(c).get("items") or []:
                own.update(str(item[k]) for k in ("name", "title", "nickname") if item.get(k))
                texts.append(json.dumps(item, ensure_ascii=False))
        joined = "\n".join(texts)
        return sorted({e for e in self.eval_fixture_names if e in own or (len(e) >= 5 and e in joined)})


# --- 학습 fixture ------------------------------------------------------------------------------

def fixture_sources():
    """학습 fixture를 만들 재료: 루트 목적지, 후기 주제. 에이전트 fixture 이름과 평가 쪽 값은 뺀다."""
    held = HeldOut()
    banned = set(held.eval_fixture_names) | set(held.test_entities) | set(held.human_only_entities)

    def ok(value: str) -> bool:
        return not boundary_hits(value, banned) and value not in banned

    places = {}
    for name, where, _ in mf.all_places():
        region = next((r for r in V3.REGIONS["train"] if r in where or r in name), where.split()[-1])
        places[name] = region
    for region, names in V3.REGION_PLACES["train"].items():
        for name in names:
            places.setdefault(name, region)
    places = {n: r for n, r in places.items() if ok(n)}
    regions = [r for r in V3.REGIONS["train"] if ok(r)]
    destinations = regions + [n for n in V3.PLACES["train"] if ok(n)]
    centers = {}
    for _, where, _ in mf.all_places():   # 해외 도시의 도심 좌표(지역 해석기 표) → 학습 여행지 행 좌표
        city = where.split()[-1]
        reg = resolve_region(city).region
        if reg is not None and reg.kind == "OVERSEAS" and reg.lat is not None and reg.lng is not None:
            centers[city] = (reg.lat, reg.lng)
    geocoding = json.loads((DEFAULT_FIXTURES_DIR / "weather.json").read_text(encoding="utf-8"))["geocoding"]
    region_wheres = []   # train 지역 → S1 행 위치(국내는 해석기 이름, 해외는 '나라 도시')
    for r in regions:
        reg = resolve_region(r).region
        if reg is None:
            continue
        if reg.kind == "DOMESTIC":
            region_wheres.append((r, reg.label if reg.level == "SIGUNGU" else f"{reg.label} 중구"
                                  if reg.label.endswith(("특별시", "광역시")) else reg.label, True))
        else:
            region_wheres.append((r, f"{reg.country} {r}" if reg.country and reg.country != r else r, False))
    return held, places, regions, destinations, {"centers": centers, "agent_geocoding": geocoding, "keep_geocoding": ok,
                                                 "region_wheres": region_wheres, "keep_name": ok}


class ToolEnv:
    """도구 실행기. fixtures_dirs 중 하나를 기록마다 고른다(None = 에이전트 기본 fixture)."""

    def __init__(self, fixtures_dirs: list[Path | None]):
        self.dirs = fixtures_dirs
        self._backends: dict[Path | None, FakeBackend] = {}

    def backend(self, d: Path | None, errors=None) -> FakeBackend:
        if errors:
            return FakeBackend(d, today=TODAY, errors=errors)
        if d not in self._backends:
            self._backends[d] = FakeBackend(d, today=TODAY)
        return self._backends[d]

    def run(self, calls: list[tuple[str, dict]], rng: random.Random, *, error_index: int | None = None,
            fixture: Path | None = None, call_offset: int = 0) -> tuple[list[dict], Path | None]:
        """호출들을 실행해 [{name, args, call_id, content, card}]를 돌려준다. error_index번째 호출은 오류를 흉내 낸다."""
        d = fixture if fixture is not None else rng.choice(self.dirs)
        out = []
        for i, (name, args) in enumerate(calls):
            call_id = f"call_{call_offset + i}"
            errors = None
            if error_index == i:
                errors = {name: rng.choice([TimeoutError("timeout"), ConnectionError("down")])}
            try:
                content, card = run_tool(name, self.backend(d, errors), args, call_id, CTX)
            except ToolBackendError as e:
                content, card = tool_error_to_json(e), None
            out.append({"name": name, "args": args, "call_id": call_id, "content": content, "card": card,
                        "obj": json.loads(content)})
        return out, d


# --- 메시지 ------------------------------------------------------------------------------------

def assistant_call(calls: list[tuple[str, dict]], ids: list[str] | None = None) -> dict:
    """assistant 호출 메시지. 맥락 속 호출은 id를 붙이고(에이전트 요청과 같은 모양), 정답 호출은 v3 기록처럼 id 없이 둔다."""
    tool_calls = []
    for i, (name, args) in enumerate(calls):
        call = {"type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}
        if ids is not None:
            call = {"id": ids[i], **call}
        tool_calls.append(call)
    return {"role": "assistant", "content": "", "tool_calls": tool_calls}


def tool_messages(results: list[dict]) -> list[dict]:
    return [{"role": "tool", "tool_call_id": r["call_id"], "content": r["content"]} for r in results]


def context(user: str, rounds: list[list[dict]]) -> list[dict]:
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
    for results in rounds:
        msgs.append(assistant_call([(r["name"], r["args"]) for r in results], [r["call_id"] for r in results]))
        msgs += tool_messages(results)
    return msgs


def make_record(messages: list[dict], target: dict, task: str, meta: dict) -> dict:
    return {"messages": messages + [target], "tools": TOOLS, "task": task, "meta": meta}


# --- 답 문장 ------------------------------------------------------------------------------------

def count_word(n: int, unit: str, rng: random.Random) -> str:
    """개수는 고유어로 쓴다('세 곳'). measure_after_tools.classify는 답의 아라비아 숫자를 결과 글의 숫자와 글자로 맞춰 보므로
    결과에 없는 개수 '3'을 쓰면 '결과에 없는 숫자'가 된다(가드는 개수를 허용하지만 측정 기준에 맞춘다)."""
    return f"{NATIVE[n]} {unit}" if n in NATIVE else f"여러 {unit}"


def join_and(words: list[str]) -> str:
    """'A', 'A와 B', 'A, B와 C'(조사는 앞말 받침)."""
    if len(words) == 1:
        return words[0]
    return ", ".join(words[:-2] + [josa(words[-2], "과/와") + " " + words[-1]])


def kw_phrase(keywords: list[str], rng: random.Random) -> str:
    if len(keywords) <= 1:
        return "".join(keywords)
    return "·".join(keywords) if rng.random() < 0.5 else join_and(keywords)


def paren(name: str, where: str, particle: str | None = None) -> str:
    """'해운대해수욕장(부산광역시 해운대구)을' — 조사는 괄호 앞 이름을 따른다."""
    tail = pick_josa(name, particle) if particle else ""
    return f"{name}({where}){tail}" if where and where != name else name + tail


def ko_date(iso: str) -> str:
    """날짜는 결과 글자 그대로(2024-08-01). '8월 1일'로 바꾸면 측정 스크립트가 '08'과 '8'을 다른 숫자로 본다."""
    return iso


def quoted(title: str) -> str:
    return f"'{title}'"


def unknown_keywords(obj: dict) -> list[str]:
    for n in obj.get("notes") or []:
        if n.startswith("목록에 없어 반영하지 못한 키워드: "):
            return [w.strip() for w in n.split(":", 1)[1].split(",") if w.strip()]
    return []


def category_word(user: str, category: str | None) -> str:
    """사용자가 쓴 카테고리 말(맛집·숙소 …). 없으면 '여행지'."""
    if not category:
        return "여행지"
    words = list(V3.CATEGORY_WORDS.get(category, [])) + list(V3.CATEGORY_WORDS_TEST.get(category, []))
    found = [w for w in words if w in user]
    return max(found, key=len) if found else "여행지"


WHEN_LABEL = {"오늘": "오늘", "내일": "내일", "이번 주말": "이번 주말", "다음 주": "다음 주"}


def join_paren(pairs: list[tuple[str, str]], particle: str | None = None) -> str:
    """[(이름, 지역)…] → '가(지역), 나(지역)와 다(지역)' — 조사는 늘 괄호 앞 이름을 따른다. particle은 끝에 붙일 조사."""
    parts = [paren(n, w) for n, w in pairs]
    if len(parts) >= 2:
        n_prev = pairs[-2][0]
        parts[-2] = paren(pairs[-2][0], pairs[-2][1], "과/와") if pairs[-2][1] and pairs[-2][1] != n_prev \
            else josa(n_prev, "과/와")
        head = ", ".join(parts[:-2] + [parts[-2]]) if len(parts) > 2 else parts[-2]
        text = head + " " + parts[-1]
    else:
        text = parts[0]
    if particle:
        text += pick_josa(pairs[-1][0], particle)
    return text


class Composer:
    """도구 결과 → 답 문장 조각. 문장마다 결과에 있는 이름·지역·숫자만 쓰고, 설명(note)은 옮기지 않는다."""

    def __init__(self, rng: random.Random, user: str):
        self.rng = rng
        self.user = user

    def pick(self, options):
        return self.rng.choice([o for o in options if o])

    # 결과가 있는 경우 ---------------------------------------------------------------------
    def ok_keywords(self, r: dict, max_sentences: int = 4, lead: str | None = None) -> list[str]:
        items, rng = r["obj"]["items"], self.rng
        unknown = unknown_keywords(r["obj"])
        kws = [k for k in r["args"].get("keywords", []) if k not in unknown]
        phrase = kw_phrase(kws, rng)
        n = len(items)
        k = n if n <= 3 else rng.choice([3, n])
        pairs = [(i["name"], i.get("where") or "") for i in items[:k]]
        heads = [f"{phrase} 취향으로 찾은 여행지예요.",
                 f"말씀하신 {phrase} 키워드로 여행지 {josa(count_word(n, '곳', rng), '을/를')} 찾았어요.",
                 f"{josa(phrase, '을/를')} 좋아하신다면 이런 곳{'들이' if n > 1 else '이'} 있어요."]
        if lead:
            heads = [lead]
        name0, where0 = pairs[0]
        if n == 1:
            lists = [f"{where0}의 {josa(name0, '이/가')} 나왔어요." if where0 else f"{josa(name0, '이/가')} 나왔어요.",
                     f"{where0}에 있는 {josa(name0, '을/를')} 추천드려요." if where0 else f"{josa(name0, '을/를')} 추천드려요."]
        else:
            of_pairs = [f"{w}의 {nm}" if w else nm for nm, w in pairs]
            if len(of_pairs) == 2:
                l1 = f"{josa(of_pairs[0], '과/와')} {josa(of_pairs[1], '이/가')} 나왔어요."
            else:
                l1 = ", ".join(of_pairs[:-1]) + ", " + josa(of_pairs[-1], "이/가") + " 나왔어요."
            l2 = (f"가장 먼저 나온 곳은 {where0}에 있는 {name0}{pick_josa(name0, '이고')}, "
                  f"{join_paren(pairs[1:])}도 함께 추천드려요.") if where0 else None
            l3 = f"{join_paren(pairs, '을/를')} 추천드려요."
            lists = [l1, l2, l3]
        out = [self.pick(heads), self.pick(lists)]
        must, optional = [], []
        if unknown:
            must.append(f"{josa(join_and(unknown), '은/는')} 키워드 목록에 없어서 이번 추천에는 반영하지 못했어요.")
        must += [note for note in r["obj"].get("notes") or [] if note.startswith("길이 제한으로")]
        if k < n:
            optional.append(self.pick(["나머지 곳도 화면 카드에서 볼 수 있어요.", "다른 곳은 화면 카드에 함께 있어요."]))
        elif any(i.get("note") == VERBATIM_NOTE for i in items) and rng.random() < 0.6:
            optional.append(self.pick(["장소 소개는 화면 카드의 원문으로 확인해 주세요.",
                                       "각 장소의 자세한 설명은 화면 카드에서 볼 수 있어요."]))
        if len(out) + len(must) > max_sentences:   # 머리 문장을 빼서라도 notes의 제한은 알린다
            out = out[1:]
        out += must
        out += optional[:max(0, max_sentences - len(out))]
        return out

    def ok_routes(self, r: dict, max_sentences: int = 4) -> list[str]:
        items, rng = r["obj"]["items"], self.rng
        n = len(items)
        heads = [f"나이·성별·MBTI가 비슷한 여행자 {count_word(n, '명', rng)}의 루트를 찾았어요.",
                 f"말씀하신 조건과 비슷한 {'여행자들이' if n > 1 else '여행자가'} 다녀온 곳을 찾았어요.",
                 f"성향이 비슷한 여행자 {josa(count_word(n, '명', rng), '이/가')} 다녀온 루트예요."]

        def who(i):
            return " ".join(str(x) for x in (i.get("ageBand"), i.get("gender"), i.get("mbti")) if x)

        persons = []
        for i in items:
            ds = i.get("destinations") or []
            if ds:
                persons.append((who(i), self.pick(["·", ", "]).join(ds), ds[-1]))
        sentences = []
        if persons:
            w, d, last = persons[0]
            label = f"{w} 여행자" if w else "비슷한 여행자"
            sentences.append(self.pick([f"{label}는 {d}{pick_josa(last, '을/를')} 다녀왔어요.",
                                        f"가장 먼저 나온 {label}의 루트는 {d}{pick_josa(last, '이에요')}."]))
        if len(persons) >= 2:
            parts = [f"{w + ' ' if w else ''}여행자는 {d}" for w, d, _ in persons[1:]]
            sentences.append(", ".join(parts) + pick_josa(persons[-1][2], "을/를") + " 다녀왔어요.")
        out = [self.pick(heads)] + sentences
        if len(out) < max_sentences and rng.random() < 0.5:
            out.append(self.pick(["여행지 전체 목록은 화면 카드에서 볼 수 있어요.",
                                  "루트에 담긴 다른 여행지는 화면 카드에서 확인해 주세요."]))
        return out[:max_sentences]

    def review_list(self, items: list[dict], max_sentences: int) -> list[str]:
        titles = [quoted(i["title"]) for i in items if i.get("title")]
        plain = [", ".join(titles[:-1] + [josa(titles[-1], "이/가")]) + " 있어요."]
        options = [plain]
        likes = [i.get("likes") or 0 for i in items]
        if likes and likes[0] > 0 and likes[0] >= max(likes):
            rest = titles[1:]
            if rest and max_sentences < 3:   # 한 문장에 다 넣는다(제목을 하나만 말하지 않게)
                options.append([f"좋아요가 가장 많은 글은 좋아요 {likes[0]}개를 받은 {titles[0]}{pick_josa(titles[0], '이고')}, "
                                + ", ".join(rest) + "도 있어요."])
            else:
                s = f"좋아요가 가장 많은 글은 {titles[0]}{pick_josa(titles[0], '으로/로')}, 좋아요 {likes[0]}개를 받았어요."
                options.append([s] + ([", ".join(rest) + "도 있어요."] if rest else []))
        dated = [(i["date"], quoted(i["title"])) for i in items if i.get("date") and i.get("title")]
        if len(dated) >= 2:
            d, t = max(dated)
            options.append(plain + [f"가장 최근 글은 {ko_date(d)}에 올라온 {t}{pick_josa(t, '이에요')}."])
        return self.pick(options)

    def ok_reviews(self, r: dict, max_sentences: int = 4) -> list[str]:
        items, rng = r["obj"]["items"], self.rng
        q = r["args"].get("query", "")
        n = len(items)
        heads = [f"{q} 관련 Glople 후기 {josa(count_word(n, '개', rng), '을/를')} 찾았어요.",
                 f"Glople에 올라온 {q} 후기예요.", f"{q} 후기를 찾아봤어요."]
        out = [self.pick(heads)] + self.review_list(items, max_sentences - 1)
        if len(out) < max_sentences and rng.random() < 0.6:
            out.append(self.pick(["글 내용은 화면 카드에서 볼 수 있어요.", "자세한 내용은 화면 카드에서 글을 열어 확인해 주세요."]))
        return out[:max_sentences]

    def ok_gloplers(self, r: dict, max_sentences: int = 4) -> list[str]:
        items, rng = r["obj"]["items"], self.rng
        word = r["args"].get("hashtag", "")
        n = len(items)
        notes = [x for x in r["obj"].get("notes") or [] if x]
        heads = [f"{word} 쪽으로 활동하는 글로플러를 찾았어요.",
                 f"{word} 관련 글로플러 {josa(count_word(n, '명', rng), '을/를')} 찾았어요."]
        people = []
        for i in items[:3]:
            info = ", ".join(x for x in (i.get("country"), f"평점 {i['rating']}" if i.get("rating") else None) if x)
            people.append((i["nickname"] + "님", info))
        first = items[0]
        if len(people) == 1 or (rng.random() < 0.4 and max_sentences >= 3):
            nick = people[0][0]
            s = josa(nick, "은/는")
            if first.get("country"):
                s += f" {first['country']}에서 활동하고"
            tags = "·".join("#" + t for t in (first.get("hashtags") or [])[:2])
            if tags:
                s += f" {tags} 해시태그를 쓰며"
            s += (f" 평점은 {first['rating']}{pick_josa(str(first['rating']), '이에요')}." if first.get("rating")
                  else " 활동하고 있어요.")
            body = [s]
            if len(people) >= 2:
                body.append(join_paren(people[1:]) + "도 있어요.")
        else:
            body = [join_paren(people, "이/가") + " 있어요."]
        out = [notes[0] if notes else self.pick(heads)] + body
        if len(out) < max_sentences and rng.random() < 0.5:
            out.append(self.pick(["소개글과 프로필은 화면 카드에서 볼 수 있어요.", "자세한 소개는 화면 카드에서 확인해 주세요."]))
        return out[:max_sentences]

    def place_list(self, pairs: list[tuple[str, str]]) -> str:
        """장소 나열 한 문장(키워드 추천·지역 검색 공통)."""
        name0, where0 = pairs[0]
        if len(pairs) == 1:
            return self.pick([f"{where0}의 {josa(name0, '이/가')} 나왔어요." if where0 else f"{josa(name0, '이/가')} 나왔어요.",
                              f"{where0}에 있는 {josa(name0, '을/를')} 추천드려요." if where0 else f"{josa(name0, '을/를')} 추천드려요."])
        of_pairs = [f"{w}의 {nm}" if w else nm for nm, w in pairs]
        l1 = (f"{josa(of_pairs[0], '과/와')} {josa(of_pairs[1], '이/가')} 나왔어요." if len(of_pairs) == 2
              else ", ".join(of_pairs[:-1]) + ", " + josa(of_pairs[-1], "이/가") + " 나왔어요.")
        l2 = (f"가장 먼저 나온 곳은 {where0}에 있는 {name0}{pick_josa(name0, '이고')}, "
              f"{join_paren(pairs[1:])}도 함께 추천드려요.") if where0 else None
        l3 = f"{join_paren(pairs, '을/를')} 추천드려요."
        return self.pick([l1, l2, l3])

    def ok_search(self, r: dict, max_sentences: int = 4) -> list[str]:
        items, rng, args = r["obj"]["items"], self.rng, r["args"]
        region = args.get("region", "")
        cw = category_word(self.user, args.get("category"))
        n = len(items)
        k = n if n <= 3 else rng.choice([3, n])
        notes = [x for x in r["obj"].get("notes") or [] if x]
        relaxed = any("조건 없이 골랐어요" in x for x in notes)
        cond = f"'{args['query']}' 조건으로 " if args.get("query") and not relaxed else ""
        heads = [f"{cond}{region} {cw} {josa(count_word(n, '곳', rng), '을/를')} 찾았어요.",
                 f"{cond}{region}에서 찾은 {cw}예요." if pick_josa(cw, "이/가") == "가" else f"{cond}{region}에서 찾은 {cw}이에요.",
                 f"{cond}{region} {josa(cw, '으로/로')}는 이런 곳이 있어요."]
        out = [self.pick(heads), self.place_list([(i["name"], i.get("where") or "") for i in items[:k]])]
        optional = []
        if k < n:
            optional.append(self.pick(["나머지 곳도 화면 카드에서 볼 수 있어요.", "다른 곳은 화면 카드에 함께 있어요."]))
        elif any(i.get("note") == VERBATIM_NOTE for i in items) and rng.random() < 0.5:
            optional.append("각 장소의 자세한 설명은 화면 카드에서 볼 수 있어요.")
        if len(out) + len(notes) > max_sentences:
            out = out[1:]
        out += notes
        out += optional[:max(0, max_sentences - len(out))]
        return out

    def ok_detail(self, r: dict, max_sentences: int = 4) -> list[str]:
        items, notes = r["obj"]["items"], [x for x in r["obj"].get("notes") or [] if x]
        card = r.get("card") or {}
        if (card.get("data") or {}).get("needsClarification") or len(items) > 1:
            # 같은 이름이 여러 지역에 있음 → 후보와 함께 되묻는 문장(어댑터가 만든 notes)을 그대로
            return [x.strip() + ("" if x.strip().endswith(("?", ".")) else ".")
                    for x in re.split(r"(?<=[.?])\s+", notes[0]) if x.strip()] if notes else []
        i = items[0]
        name, where, kind = i["name"], i.get("where") or "", i.get("type")
        first = (f"{josa(name, '은/는')} {where}에 있는 {kind}{pick_josa(kind, '이에요')}." if kind and where
                 else f"{josa(name, '은/는')} {where}에 있어요." if where else f"{name} 정보를 찾았어요.")
        rest = []
        if i.get("address") and i["address"] != where:
            rest.append(f"주소는 {i['address']}{pick_josa(i['address'], '이에요')}.")
        if i.get("note") == VERBATIM_NOTE:
            rest.append(self.pick(["자세한 소개는 화면 카드의 원문으로 확인해 주세요.", "소개 글은 화면 카드에서 원문 그대로 볼 수 있어요."]))
        else:
            rest.append(self.pick(["사진과 자세한 소개는 화면 카드에서 볼 수 있어요.", "더 자세한 내용은 화면 카드에서 확인해 주세요."]))
        if self.rng.random() < 0.4:
            rest.append(f"원하시면 {name} 후기도 찾아 드릴게요.")
        return ([first] + rest + notes)[:max(max_sentences, 1 + len(notes))]

    def ok_weather(self, r: dict, max_sentences: int = 4) -> list[str]:
        items, args = r["obj"]["items"], r["args"]
        notes = [x for x in r["obj"].get("notes") or [] if x]
        region = args.get("region", "")
        when = WHEN_LABEL.get(args.get("when") or "", "오늘")

        def md(iso):   # 결과 글자 그대로(측정 스크립트가 '08'과 '8'을 다르게 봄)
            return iso

        def temps(i):
            return f"최저 {i['tmin']}도·최고 {i['tmax']}도" if i.get("tmin") is not None and i.get("tmax") is not None else ""

        out = []
        if len(items) == 1:
            i = items[0]
            summary = i.get("summary")
            head = f"{region} {when}({md(i['date'])}) 날씨는 "
            if summary and temps(i):
                out.append(head + f"{summary}{pick_josa(summary, '이고')} {temps(i)}예요.")
            elif summary:
                out.append(head + f"{summary}{pick_josa(summary, '이에요')}.")
            else:
                out.append(head + f"{temps(i)}예요.")
            if i.get("pop") is not None:
                out.append(f"강수 확률은 {i['pop']}%예요.")
        else:
            parts = []
            for i in items:
                bits = (i.get("summary") or "") + (f"({temps(i)})" if temps(i) else "")
                parts.append(f"{md(i['date'])} {bits}".strip())
            out.append(f"{region} {when} 날씨는 " + ", ".join(parts) + "이에요.")
            pops = [(i["pop"], i["date"]) for i in items if i.get("pop") is not None]
            if pops:
                top, day = max(pops)
                out.append(f"강수 확률이 가장 높은 날은 {md(day)}({top}%)이에요.")
        if len(out) + len(notes) > max_sentences:
            out = out[:max(1, max_sentences - len(notes))]
        return out + notes

    def ok(self, r: dict, max_sentences: int = 4) -> list[str]:
        fn = {"recommend_by_keywords": self.ok_keywords, "recommend_similar_traveler_routes": self.ok_routes,
              "search_reviews": self.ok_reviews, "search_gloplers": self.ok_gloplers,
              "search_destinations": self.ok_search, "get_destination_detail": self.ok_detail,
              "get_weather": self.ok_weather}[r["name"]]
        return fn(r, max_sentences=max_sentences)

    # 빈 결과·준비 중·미지원·오류 -----------------------------------------------------------
    def limit(self, r: dict, max_sentences: int = 2, offer: bool = True) -> list[str]:
        """제한을 알리는 문장들. 첫 묶음(제한 사실)은 늘 남기고, offer면 할 수 있는 다른 일을 한 문장 덧붙인다."""
        name, args, obj, rng = r["name"], r["args"], r["obj"], self.rng
        status = obj.get("status")
        if status == "error":
            label = tool_label(obj.get("tool") or name)
            if obj.get("error") == "timeout":
                fact = [self.pick([f"{label} 응답이 늦어 이번에는 결과를 찾지 못했어요.",
                                   f"죄송해요, {label} 응답이 늦어서 아직 결과가 없어요."])]
            else:
                fact = [self.pick([f"지금은 {label} 결과를 가져오지 못해 보여드릴 결과가 없어요.",
                                   f"죄송해요, {label} 결과를 지금은 찾지 못했어요."])]
            offers = ["잠시 뒤에 다시 시도해 주세요.", "조금 뒤에 다시 물어봐 주시면 그때 다시 찾아볼게요."]
        elif name == "search_destinations":
            region = args.get("region", "")
            cw = category_word(self.user, args.get("category"))
            target = f"{args['query']} {cw}" if args.get("query") else cw
            notes = [x for x in obj.get("notes") or [] if x]
            if status == "pending_feature":
                fact = [self.pick([f"지역별 여행지 검색은 아직 준비 중이라 {region} {josa(target, '을/를')} 지금 찾아드릴 수 없어요.",
                                   f"{region}에서 {josa(target, '을/를')} 찾는 기능은 아직 준비 중이에요."])]
            elif status == "unsupported":   # 음식점(국내)·숙박은 수집하지 않아 부르지 않음
                fact = self.pick([[f"아쉽지만 {region} {cw} 정보는 아직 없어요.", *split_sentences(notes[0])] if notes else
                                  [f"아쉽지만 {region} {cw} 정보는 아직 없어요."],
                                  split_sentences(notes[0]) if notes else [f"{region} {cw} 정보는 아직 없어요."]])
                if max_sentences <= 2 and notes:   # 다른 결과와 함께 답할 때는 안내 문장만
                    fact = split_sentences(notes[0])
            else:   # empty: 첫 note는 상태 문장('○○에서 찾은 곳이 없어요.'), 그 뒤는 이유
                fact = [self.pick([f"{region}에서 {josa(target, '을/를')} 찾지 못했어요.",
                                   f"{region} {target} 검색 결과가 없어요."])] + notes[1:]
            offers = [f"대신 바다·야경 같은 취향 키워드로 여행지를 추천받거나 {region} 후기를 찾아볼 수 있어요.",
                      f"{region} 여행 후기를 먼저 찾아볼까요?"]
            if status == "unsupported":
                offers = [f"{region} 관광지나 축제를 대신 찾아볼까요?", f"대신 {region} 후기를 찾아볼 수 있어요."]
        elif name == "get_destination_detail":
            place = args.get("name", "")
            if status == "pending_feature":
                fact = [self.pick([f"{place} 상세 정보는 아직 준비 중이라 지금은 보여드릴 수 없어요.",
                                   f"여행지 상세 정보 기능이 아직 준비 중이라 {place} 정보를 찾아드리기 어려워요."])]
            else:
                fact = [self.pick([f"Glople에는 아직 {place} 정보가 없어요.", f"{place} 정보를 찾아봤지만 Glople에는 아직 없어요."])]
            offers = [f"대신 {place} 후기를 찾아 드릴까요?", f"{place}에 다녀온 사람들의 후기는 찾아볼 수 있어요."]
        elif name == "get_weather":
            target = " ".join(x for x in (args.get("region", ""), WHEN_LABEL.get(args.get("when") or "", "")) if x)
            notes = [x for x in obj.get("notes") or [] if x]
            if status == "unsupported":
                fact = self.pick([[f"{target} 날씨는 아직 알려드릴 수 없어요.", "Glople 도우미에는 날씨 정보가 아직 연결되지 않았어요."],
                                  [f"날씨 정보는 아직 연결하지 않아서 {target} 날씨를 확인해 드릴 수 없어요."]])
            else:   # empty: 첫 note는 '날씨 정보를 찾지 못했어요.', 그 뒤가 이유(위치 못 찾음·도 전체·후보 되묻기·예보 없음)
                fact = [self.pick([f"{target} 날씨 정보를 찾지 못했어요.", f"죄송해요, {target} 날씨는 아직 알려드릴 수 없어요."])]
                fact += [x for n in notes[1:] for x in split_sentences(n)]
            offers = ["떠나기 전에 일기 예보를 따로 확인해 보세요.", "다른 도시 이름으로 다시 물어봐 주세요."]
            if any(n.endswith("?") for n in notes):
                offers = []   # 후보를 묻는 문장으로 끝낸다
        elif name == "search_reviews":
            q = args.get("query", "")
            fact = [self.pick([f"아직 Glople에 {q} 후기가 없어요.", f"{q} 후기를 찾아봤지만 아직 올라온 글이 없어요."])]
            offers = ["다른 장소나 지역 이름으로 다시 찾아볼까요?", "다른 여행지 이름을 알려주시면 그 후기를 찾아볼게요."]
        elif name == "recommend_similar_traveler_routes":
            fact = [self.pick(["말씀하신 나이·성별·MBTI와 비슷한 여행자를 찾지 못했어요.", "조건이 비슷한 여행자 루트가 아직 없어요."])]
            offers = ["대신 좋아하는 여행 키워드를 알려주시면 여행지를 추천해 드릴게요.", "취향 키워드로 여행지를 추천받아 보시겠어요?"]
        elif name == "search_gloplers":
            word = args.get("hashtag", "")
            notes = obj.get("notes") or []
            fact = [f"{word} 글로플러는 찾지 못했어요."]
            if NOTE_DOMESTIC in notes:
                fact.append(self.pick([NOTE_DOMESTIC, "국내 글로플러는 아직 없고, 지금은 미국·이탈리아·일본 글로플러만 있어요."]))
            elif NOTE_NONE in notes:
                fact.append(NOTE_NONE)
            offers = [f"{word} 여행 후기는 찾아볼 수 있어요.", "다른 지역이나 관심사로 글로플러를 찾아볼까요?"]
        elif name == "recommend_by_keywords":
            unknown = unknown_keywords(obj)
            kws = [k for k in args.get("keywords", []) if k not in unknown]
            fact = [self.pick([f"{kw_phrase(kws, rng)} 취향으로 찾은 여행지가 아직 없어요.", "그 취향으로 찾은 곳이 아직 없어요."])
                    if kws else "말씀하신 키워드로 찾은 여행지가 없어요."]
            if unknown:
                fact.append(f"{josa(join_and(unknown), '은/는')} 키워드 목록에 없어서 반영하지 못했어요.")
            offers = ["바다, 산, 야경처럼 다른 키워드로 말씀해 주시면 다시 찾아볼게요.", "다른 취향 키워드를 알려주시면 다시 찾아볼게요."]
        else:
            raise ValueError(f"제한 답을 만들 수 없는 결과: {name} {status}")
        out = list(fact)
        if offer and offers and len(out) < max(max_sentences, 2):
            out.append(self.pick(offers))
        return out[:max(max_sentences, len(fact))]


def split_sentences(text: str) -> list[str]:
    return [x.strip() for x in re.split(r"(?<=[.?!])\s+", text or "") if x.strip()]


def finish(sentences: list[str]) -> str:
    text = " ".join(s.strip() for s in sentences if s and s.strip())
    return " ".join(text.split())


def compose_single_round(user: str, results: list[dict], rng: random.Random) -> str:
    """한 라운드 결과(병렬이면 여러 개) → 답. 결과가 둘이면 결과마다 1~2문장, 전체 4문장 이하."""
    c = Composer(rng, user)
    if len(results) == 1:
        r = results[0]
        if r["obj"]["status"] == "ok":
            return finish(c.ok(r, max_sentences=rng.choice([2, 3, 3, 4])))
        return finish(c.limit(r, max_sentences=rng.choice([2, 2, 3]), offer=True))
    for budget in (2, 1):   # 결과마다 2문장 → 넘치면 1문장(notes의 제한 문장은 줄이지 않음). 그래도 넘치면 검사에서 버린다
        parts = []
        for r in results:
            if r["obj"]["status"] == "ok":
                parts.append(c.ok(r, max_sentences=budget))
            else:
                parts.append(c.limit(r, max_sentences=budget, offer=False))
        sentences = [x for p in parts for x in p]
        if len(sentences) <= 4:
            break
    # 되묻는 문장('어느 곳을 말씀하시는 건가요?')은 다른 결과를 다 말한 뒤 맨 끝에
    return finish([x for x in sentences if not x.endswith("?")] + [x for x in sentences if x.endswith("?")])


# --- 이어서 부르기(체인) -----------------------------------------------------------------------

CHAIN_TEMPLATES = {
    "train": {
        "kw_reviews": [
            "{kws} 좋아하는데 여행지 추천해 주고 제일 잘 맞는 곳 후기도 보여줘",
            "{kws} 테마로 갈 만한 곳 골라 주고, 맨 위에 나온 곳은 다녀온 사람들 후기도 알려줘",
            "{kws} 위주로 여행지 찾아 주고 첫 번째로 나온 데 리뷰까지 찾아줘",
            "{kws} 즐길 여행지 추천해 주세요. 가장 먼저 나온 곳 후기도 같이 보고 싶어요",
            "요즘 {kws}에 빠졌는데 추천 여행지 중 1순위 후기도 보여줘",
        ],
        "kw_weather": [
            "{kws} 좋아하는데 갈 만한 곳 추천해 주고 제일 먼저 나온 곳 {w} 날씨도 알려줘",
            "{kws} 여행지 골라 주고 제일 먼저 나온 곳 {w} 날씨도 봐 줘",
            "{kws} 취향으로 여행지 찾아 주고, 맨 위에 나온 곳 {w} 날씨도 확인해 줄래?",
        ],
        "routes_reviews": [
            "{age} {gw} {mbti}인데 비슷한 사람들 루트 보여 주고, 제일 비슷한 사람이 간 첫 여행지 후기도 찾아줘",
            "나 {mbti} {gw} {age}. 성향 비슷한 여행자 루트 보고 거기 첫 번째 여행지 리뷰도 보여줘",
            "MBTI {mbti}, {age} {gw_ieyo}. 비슷한 여행자 코스 찾아 주고 맨 처음 나온 여행지 후기도 알려 주세요",
        ],
        "detail_reviews": [
            "{p} 어떤 곳인지 알려줘. 정보가 없으면 후기라도 보여줘",
            "{p} 정보 찾아 주고 없으면 다녀온 사람들 리뷰라도 찾아줘",
            "{p} 소개해 줄 수 있어? 소개가 안 되면 후기라도 괜찮아",
        ],
    },
    "test": {
        "kw_reviews": ["{kws} 좋아하는 사람한테 맞는 여행지 알려 주고 그중 첫 번째 곳 후기도 같이 보여 줄래?"],
        "kw_weather": ["{kws} 쪽으로 여행지 찾아 주고 가장 먼저 나온 곳 {w} 날씨도 알려 줄래요?"],
        "routes_reviews": ["{gw}이고 {age}, 엠비티아이는 {mbti_ya}. 나랑 비슷한 사람 루트 보고 첫 여행지 후기까지 보여 줘"],
        "detail_reviews": ["{p}에 대해 알려 줘. 정보가 없으면 후기라도 대신 찾아 줘"],
    },
}
CHAIN_WHEN = {"오늘": ["오늘"], "내일": ["내일"], "이번 주말": ["이번 주말"], "다음 주": ["다음 주"]}


def chain_utterance(kind: str, split: str, rng: random.Random, places: list[str]) -> tuple[str, tuple[str, dict], dict]:
    """(발화, 첫 호출, 이어서 쓸 정보). 첫 호출은 발화만으로 정할 수 있는 것이고, 두 번째 인자는 결과를 봐야 정해진다."""
    template = rng.choice(CHAIN_TEMPLATES[split][kind])
    if kind in ("kw_reviews", "kw_weather"):
        pool = [k for k in V3.KEYWORDS if k not in mf.HELD_OUT_KEYWORDS] if rng.random() < 0.9 else list(mf.HELD_OUT_KEYWORDS)
        keywords = rng.sample(pool, rng.choice([1, 1, 2]))
        slots = V3.josa_slots("kws", V3.join_words(rng, keywords))
        info = {}
        if kind == "kw_weather":
            when = rng.choice(list(CHAIN_WHEN))
            slots["w"] = rng.choice(CHAIN_WHEN[when])
            info["when"] = when
        return V3.tidy(template.format(**slots)), ("recommend_by_keywords", {"keywords": keywords}), info
    if kind == "routes_reviews":
        age = rng.randint(19, 65)
        gender = rng.choice(["남성", "여성"])
        slots, mbti = V3.route_slots(rng, age, gender)
        return template.format(**slots), ("recommend_similar_traveler_routes",
                                          {"age": age, "gender": gender, "mbti": mbti}), {}
    place = rng.choice(places)
    return template.format(p=place), ("get_destination_detail", {"name": place}), {}


def chain_next(kind: str, first: dict, info: dict) -> tuple[str, dict] | None:
    """첫 결과에서 두 번째 호출을 정한다. 이어갈 근거(결과 항목)가 없으면 None(답으로 끝냄)."""
    obj = first["obj"]
    if kind == "detail_reviews":   # '정보가 없으면 후기라도': 상세가 없을 때(빈 결과·준비 중)만 이어서 부른다
        if obj["status"] in ("pending_feature", "empty"):
            return "search_reviews", {"query": first["args"]["name"]}
        return None
    if obj["status"] != "ok" or not obj["items"]:
        return None
    top = obj["items"][0]
    if kind == "kw_reviews":
        return "search_reviews", {"query": top["name"]}
    if kind == "kw_weather":
        # 날씨 지역 = 그 장소의 where. 자치구까지 적힌 국내 where('부산광역시 해운대구')는 시 단위('부산광역시')로 줄인다
        where = top.get("where") or top["name"]
        words = where.split()
        if len(words) >= 2 and words[-1].endswith("구"):
            where = " ".join(words[:-1])
        args = {"region": where}
        if info.get("when"):
            args["when"] = info["when"]
        return "get_weather", args
    if kind == "routes_reviews":
        dests = top.get("destinations") or []
        return ("search_reviews", {"query": dests[0]}) if dests else None
    raise ValueError(kind)


def compose_chain_final(kind: str, user: str, first: dict, second: dict, rng: random.Random) -> str:
    c = Composer(rng, user)
    if kind == "detail_reviews":
        place = first["args"]["name"]
        why = ("상세 정보는 아직 준비 중이라" if first["obj"]["status"] == "pending_feature"
               else "정보는 Glople에 아직 없어서")
        head = c.pick([f"{place} {why} 대신 후기를 찾아봤어요.", f"{place} {why} 다녀온 사람들의 후기를 찾아봤어요."])
        if second["obj"]["status"] == "ok":
            return finish([head] + c.review_list(second["obj"]["items"], 3)[:2])
        if second["obj"]["status"] == "error":
            return finish([head] + c.limit(second, max_sentences=2, offer=False))
        return finish([f"{place} {why} 후기도 찾아봤지만, Glople에 올라온 {place} 후기도 아직 없어요.",
                       c.pick(["다른 장소 이름으로 다시 찾아볼까요?", "다른 여행지를 알려주시면 그 후기를 찾아볼게요."])])
    top = first["obj"]["items"][0]
    if kind == "kw_weather":
        pairs = [(i["name"], i.get("where") or "") for i in first["obj"]["items"][:3]]
        part1 = [c.place_list(pairs)]
        st = second["obj"]["status"]
        if st == "ok":
            w = c.ok_weather(second)
            # 첫 문장 '○○ 내일(10월 19일) 날씨는 …'에 어느 장소 날씨인지 붙인다
            tail = [f"그중 {josa(top['name'], '이/가')} 있는 " + w[0]] + w[1:]
            if len(part1) + len(tail) > 4:   # 강수 확률 문장을 빼서 4문장 안에(notes는 남김)
                notes = [x for x in second["obj"].get("notes") or [] if x]
                tail = tail[:1] + notes
        else:
            tail = [f"그중 {top['name']} 쪽 날씨는 확인하지 못했어요."] + c.limit(second, max_sentences=2, offer=False)[1:]
            if st == "unsupported":
                tail = [c.pick([f"그중 {josa(top['name'], '이/가')} 있는 {second['args']['region']} 날씨는 "
                                "날씨 정보가 아직 연결되지 않아 알려드릴 수 없어요.",
                                f"다만 날씨 정보는 아직 연결하지 않아서 {top['name']} 쪽 날씨는 확인해 드릴 수 없어요."])]
        return finish(part1 + tail)
    part1 = c.ok(first, max_sentences=2)
    subject = second["args"]["query"]
    lead = (f"그중 {subject} 후기로는" if kind == "kw_reviews" else f"가장 비슷한 여행자가 다녀온 {subject} 후기로는")
    if second["obj"]["status"] == "ok":
        titles = [quoted(i["title"]) for i in second["obj"]["items"] if i.get("title")]
        tail = [f"{lead} " + ", ".join(titles[:-1] + [josa(titles[-1], "이/가")]) + " 있어요."]
        if rng.random() < 0.5:
            tail.append("글 내용은 화면 카드에서 볼 수 있어요.")
    elif second["obj"]["status"] == "error":
        tail = c.limit(second, max_sentences=1, offer=False)
    else:
        tail = [c.pick([f"{subject} 후기는 아직 Glople에 없어요.", f"다만 {subject} 후기는 아직 올라온 글이 없어요."])]
    return finish((part1 + tail)[:4])


def compose_chain_stop(kind: str, user: str, first: dict, rng: random.Random) -> str:
    """첫 결과가 비어 이어갈 수 없을 때: 제한을 알리고 두 번째 단계도 못 했다고 말한다(지어내지 않음)."""
    c = Composer(rng, user)
    base = c.limit(first, max_sentences=2, offer=False)
    follow = {"kw_reviews": "그래서 이어서 볼 후기도 찾지 못했어요.", "kw_weather": "그래서 날씨를 확인할 장소도 정하지 못했어요.",
              "routes_reviews": "그래서 이어서 볼 여행지 후기도 찾지 못했어요."}[kind]
    return finish(base + [follow])


# --- 검사 ------------------------------------------------------------------------------------

def sentence_count(text: str) -> int:
    bare = re.sub(r"'[^']*'", "''", text)
    return len([s for s in re.split(r"(?<=[.?!])\s+", bare.strip()) if s.strip()])


def protocol_kind(text: str, results: list[dict], user: str) -> str:
    """agent/scripts/measure_after_tools.py classify()와 같은 판정(결과가 있으면 이름·숫자, 없으면 제한 알림)."""
    names = []
    for r in results:
        for item in r["obj"].get("items") or []:
            names += [str(item[f]) for f in ("name", "title", "nickname") if item.get(f)]
            names += [str(d) for d in item.get("destinations", [])]
    if any(r["obj"]["status"] == "ok" for r in results):
        if names and not any(n in text for n in names):   # 날씨처럼 이름이 없는 결과는 숫자만 본다
            return "결과 이름을 하나도 안 씀"
        body = re.sub(r"(?m)^\s*\d+[.)]\s", "", text)
        allowed = set(re.findall(r"\d+(?:\.\d+)?", " ".join(r["content"] for r in results) + user))
        if set(re.findall(r"\d+(?:\.\d+)?", body)) - allowed:
            return "결과에 없는 숫자"
        return "결과를 근거로 답함"
    if any(w in text for w in LIMIT_WORDS):
        return "제한을 알림"
    return "제한을 안 알림"


def note_overlap(text: str, results: list[dict], n: int = 10) -> bool:
    """항목 소개(note·summary)를 옮겼는지: 이름·지역(where)·주소를 지운 소개에서 n자 조각이 답에 그대로 있을 때.
    ('부산광역시 해운대구의 관광지입니다'의 지역 부분은 답에 써도 된다. 남는 '의 관광지입니다.'는 n자보다 짧아 안 걸림)"""
    def strip(s, item):
        for k in ("name", "where", "address"):
            if item.get(k):
                s = s.replace(str(item[k]), "")
        return " ".join(s.split())

    for r in results:
        for item in r["obj"].get("items") or []:
            for key in ("note", "summary"):
                note = item.get(key)
                if not note or note == VERBATIM_NOTE:
                    continue
                rest, answer = strip(note, item), strip(text, item)
                if any(rest[i:i + n] in answer for i in range(0, max(len(rest) - n + 1, 0))):
                    return True
    return False


def validate_answer(text: str, user: str, results: list[dict]) -> list[str]:
    """답 하나의 문제 목록(빈 목록이면 통과): 에이전트 가드 + 이 데이터의 규칙."""
    problems = []
    contents = [r["content"] for r in results]
    cards = [r["card"] for r in results if r["card"] is not None]
    verdict = check_answer(text, answer_facts(contents, cards, user))
    problems += [f"guard:{x}" for x in verdict.reasons]
    n = sentence_count(text)
    if not 2 <= n <= 4:
        problems.append(f"sentences:{n}")
    if LORA_TIC in text:
        problems.append("lora_tic")
    if note_overlap(text, results):
        problems.append("note_overlap")
    kind = protocol_kind(text, results, user)
    if kind not in ("결과를 근거로 답함", "제한을 알림"):
        problems.append(f"protocol:{kind}")
    for r in results:
        st, tool = r["obj"]["status"], r["name"]
        notes = [x for x in r["obj"].get("notes") or [] if x]
        if st == "ok":
            names = [i.get("name") or i.get("nickname") or i.get("title") for i in r["obj"]["items"]]
            if tool == "recommend_similar_traveler_routes":
                names = [d for i in r["obj"]["items"] for d in i.get("destinations") or []]
            names = [x for x in names if x]
            said = [x for x in names if x in text]
            if names and (not said or (len(set(names)) >= 2 and len(set(said)) < 2 and len(results) == 1)):
                problems.append("too_few_names")   # 결과가 둘 이상이면 이름도 둘 이상(가드는 하나만 봄)
            reason_notes = notes
        else:
            if st == "pending_feature" and "준비 중" not in text:
                problems.append("pending_not_stated")
            if st == "unsupported" and tool == "get_weather" and "연결" not in text:
                problems.append("unsupported_not_stated")
            if st == "unsupported" and tool == "search_destinations" and "모으지 않" not in text and "아직 없" not in text:
                problems.append("unsupported_not_stated")
            if st == "error" and "못" not in text and "없" not in text:
                problems.append("error_not_stated")
            if st == "empty" and not any(w in text for w in ("없", "못")):
                problems.append("empty_not_stated")
            reason_notes = notes[1:]   # 첫 note는 registry가 넣은 상태 문장(위에서 뜻으로 확인)
            if st == "unsupported":
                reason_notes = []
        for note in reason_notes:      # notes의 제한은 그대로 알린다
            if note.startswith("목록에 없어 반영하지 못한 키워드: "):
                if any(w not in text for w in unknown_keywords(r["obj"])):
                    problems.append("unknown_keyword_not_stated")
            elif note == NOTE_DOMESTIC:
                if "국내 글로플러" not in text:
                    problems.append("domestic_note_not_stated")
            elif note == NOTE_NONE:
                if "미국·이탈리아·일본" not in text:
                    problems.append("agent_country_note_not_stated")
            elif note not in text:
                problems.append("note_not_stated")
    if "{" in text or "}" in text or "  " in text:
        problems.append("format")
    return problems


def validate_second_call(user: str, first: list[dict], call: tuple[str, dict]) -> list[str]:
    """두 번째 호출: 인자 모델 통과, 근거(발화 + 앞 결과의 이름·지역·목적지) 검사 통과, 같은 서명 반복 없음."""
    name, args = call
    problems = []
    try:
        ARGS_MODELS[name].model_validate(args)
    except Exception as e:  # noqa: BLE001
        problems.append(f"schema:{type(e).__name__}")
    evidence = [user]
    for r in first:
        for item in r["obj"].get("items") or []:
            evidence += [str(item.get(k)) for k in ("name", "where", "title", "nickname") if item.get(k)]
            evidence += [str(d) for d in item.get("destinations") or []]
        evidence += [str(v) for v in r["args"].values() if isinstance(v, str)]
    g = check_call(name, args, "\n".join(evidence))
    problems += [i for i in g.issues if i.startswith(("clarify:", "drop:", "fix:"))]
    if signature(name, args) in {signature(r["name"], r["args"]) for r in first}:
        problems.append("same_signature")
    return problems


# --- 만들기 ------------------------------------------------------------------------------------

AFTER_QUOTAS = {   # v3 기록에서 도구 결과 뒤 기록으로 쓸 개수(작업별, train 기준). valid·test는 비율대로 줄인다
    "keywords": 280, "routes": 170, "reviews": 200, "gloplers": 150, "search": 200, "detail": 120, "weather": 110,
    "parallel": 130,
}
CHAIN_QUOTAS = {"kw_reviews": 55, "kw_weather": 30, "routes_reviews": 45, "detail_reviews": 35}   # train 시도 수
ERROR_RATE = 0.05
CHAIN_FIRST_RATE = 0.85   # 이어서 부르기 발화 중 첫 판단 기록도 함께 넣는 비율


class Builder:
    def __init__(self, held: HeldOut, env: ToolEnv, split: str, rng: random.Random, stats: Counter,
                 drops: Counter, examples: dict):
        self.held, self.env, self.split, self.rng = held, env, split, rng
        self.stats, self.drops, self.examples = stats, drops, examples
        self.records: list[dict] = []

    def add(self, record: dict, *, new_content: bool) -> bool:
        body = len(json.dumps(record["messages"][1:], ensure_ascii=False))
        if new_content and body > MAX_BODY_CHARS:
            self.drops[f"{self.split}:{record['task']}:2048토큰_넘을_수_있음"] += 1
            return False
        if self.split != "test":
            problems = self.held.record_problems(record, new_content=new_content)
            if problems:
                if new_content:
                    raise AssertionError(f"새로 만든 {self.split} 기록에 평가 쪽 값이 있습니다: {problems[:5]}\n"
                                         f"{user_text(record)}")
                self.drops[f"{self.split}:v3기록_누설로_뺌"] += 1
                return False
        self.records.append(record)
        self.stats[f"{self.split}:{record['task']}"] += 1
        self.examples.setdefault((self.split, record["task"], record["meta"].get("status_key", "")), record)
        return True

    def answer_record(self, user: str, rounds: list[list[dict]], text: str, task: str, meta: dict) -> bool:
        results = [r for rnd in rounds for r in rnd]
        problems = validate_answer(text, user, results)
        if problems:
            for p in problems:
                self.drops[f"{self.split}:{task}:{p}"] += 1
            if DROP_LOG is not None:
                DROP_LOG.append({"split": self.split, "task": task, "problems": problems, "user": user, "answer": text,
                                 "results": [r["content"] for r in results]})
            return False
        meta = {**meta, "statuses": [r["obj"]["status"] for r in results], "tools": [r["name"] for r in results],
                "status_key": "+".join(f"{r['name']}:{r['obj']['status']}" for r in results)}
        return self.add(make_record(context(user, rounds), {"role": "assistant", "content": text}, task, meta),
                        new_content=True)

    # (a) ------------------------------------------------------------------------------------
    def first_turn(self, v3_records: list[dict], n: int):
        by_task = defaultdict(list)
        for r in v3_records:
            by_task[r["task"]].append(r)
        total = len(v3_records)
        picked = []
        for task, group in sorted(by_task.items()):
            k = round(n * len(group) / total)
            picked += self.rng.sample(group, min(k, len(group)))
        for r in picked:
            record = {"messages": r["messages"], "tools": TOOLS, "task": r["task"],
                      "meta": {"kind": "first_turn", "source": f"v3_{self.split}"}}
            self.add(record, new_content=False)

    # (b)(d) -------------------------------------------------------------------------------------
    def after_tools(self, v3_records: list[dict], quotas: dict[str, int]):
        by_task = defaultdict(list)
        for r in v3_records:
            calls = calls_of(r["messages"][-1])
            if not calls or not all(name in READ_TOOLS for name, _ in calls):
                continue
            if self.split != "test" and self.held.record_problems(r, new_content=False):
                self.drops[f"{self.split}:v3발화_누설로_안씀"] += 1   # 예: v3 train '보성 녹차밭'(human_style의 '보성')
                continue
            by_task[r["task"]].append(r)
        for task, quota in quotas.items():
            group = list(by_task.get(task, []))
            self.rng.shuffle(group)
            made = 0
            for r in group:
                if made >= quota:
                    break
                user = user_text(r)
                calls = calls_of(r["messages"][-1])
                error_index = (self.rng.randrange(len(calls)) if self.rng.random() < ERROR_RATE else None)
                results, _ = self.env.run(calls, self.rng, error_index=error_index)
                text = compose_single_round(user, results, self.rng)
                kind = ("after_answer" if any(x["obj"]["status"] == "ok" for x in results) else "after_limit")
                if self.answer_record(user, [results], text, kind, {"kind": kind, "source": f"v3_{self.split}",
                                                                    "v3_task": task}):
                    made += 1
            self.stats[f"{self.split}:after_from_{task}"] += made

    # (c) ------------------------------------------------------------------------------------
    def chains(self, quotas: dict[str, int], places: list[str], seen: set[str]):
        split_key = "test" if self.split == "test" else "train"
        for kind, attempts in quotas.items():
            for _ in range(attempts):
                for _retry in range(50):
                    user, call1, info = chain_utterance(kind, split_key, self.rng, places)
                    if user not in seen and user not in self.held.utterances:
                        break
                else:
                    raise RuntimeError(f"{self.split}/{kind}: 새 발화를 만들지 못했습니다")
                seen.add(user)
                fixture = self.rng.choice(self.env.dirs)
                first, _ = self.env.run([call1], self.rng, fixture=fixture)
                if self.rng.random() < CHAIN_FIRST_RATE:
                    target = assistant_call([call1])
                    self.add(make_record([{"role": "system", "content": SYSTEM_PROMPT},
                                          {"role": "user", "content": user}], target, "chain_first",
                                         {"kind": "chain_first", "chain": kind}), new_content=True)
                nxt = chain_next(kind, first[0], info)
                if nxt is None:
                    if first[0]["obj"]["status"] == "ok":   # 상세가 있음(또는 후보 되묻기) → '없으면'이 아니므로 그 결과로 답
                        text = compose_single_round(user, first, self.rng)
                        self.answer_record(user, [first], text, "after_answer", {"kind": "chain_satisfied", "chain": kind})
                    else:
                        text = compose_chain_stop(kind, user, first[0], self.rng)
                        self.answer_record(user, [first], text, "after_limit", {"kind": "chain_stop", "chain": kind})
                    continue
                problems = validate_second_call(user, first, nxt)
                if problems:
                    for p in problems:
                        self.drops[f"{self.split}:after_call:{p}"] += 1
                    continue
                self.add(make_record(context(user, [first]), assistant_call([nxt]), "after_call",
                                     {"kind": "after_call", "chain": kind, "first_status": first[0]["obj"]["status"],
                                      "status_key": f"{kind}:{first[0]['obj']['status']}"}), new_content=True)
                error_index = 0 if self.rng.random() < ERROR_RATE else None
                second, _ = self.env.run([nxt], self.rng, fixture=fixture, error_index=error_index, call_offset=1)
                text = compose_chain_final(kind, user, first[0], second[0], self.rng)
                self.answer_record(user, [first, second], text, "after2_answer",
                                   {"kind": "after2_answer", "chain": kind})


def scale(quotas: dict[str, int], factor: float) -> dict[str, int]:
    return {k: max(1, round(v * factor)) for k, v in quotas.items()}


def check_fixture(held: HeldOut, fixtures: dict[str, dict]):
    """학습 fixture 글에 평가 쪽 값·에이전트 fixture 이름이 없는지(만들 때 한 번)."""
    text = json.dumps(fixtures, ensure_ascii=False)
    problems = boundary_hits(text, held.test_entities) + boundary_hits(text, held.human_only_entities)
    own = {r["destName"] for r in fixtures["recommend_by_keywords"]["rows"]}
    own |= {clean_text(r["title"], 40) for r in fixtures["search_reviews"]["rows"]}
    own |= {r["nickname"] for r in fixtures["search_gloplers"]["rows"]}
    own |= {d for r in fixtures["recommend_similar_traveler_routes"]["rows"] for d in r["travelDestinations"]}
    problems += [n for n in held.eval_fixture_names if n in own or (len(n) >= 5 and n in text)]
    tags = {t for row in fixtures["search_gloplers"]["rows"] for t in row["hashtags"]}
    problems += [t for t in tags if t.lstrip("#") in V3.HASHTAGS["test"]]
    if problems:
        raise AssertionError(f"학습 fixture에 평가 쪽 값이 있습니다: {sorted(set(problems))}")


def check_splits(splits: dict[str, list[dict]], held: HeldOut):
    texts = {s: {user_text(r) for r in rs} for s, rs in splits.items()}
    if texts["train"] & texts["test"] or texts["valid"] & texts["test"]:
        raise AssertionError("train·valid와 test에 같은 발화가 있습니다")
    if texts["train"] & texts["valid"]:
        # v3에서 이미 분리됐고, 체인 발화는 seen으로 막는다
        raise AssertionError(f"train과 valid에 같은 발화가 있습니다: {sorted(texts['train'] & texts['valid'])[:5]}")
    for s in ("train", "valid"):
        bad = texts[s] & set(held.utterances)
        if bad:
            raise AssertionError(f"{s}에 평가 세트 발화가 있습니다: {sorted(bad)[:5]}")
    for s, rs in splits.items():
        for r in rs:
            for m in r["messages"]:
                if m["role"] == "assistant" and m is r["messages"][-1] and m.get("tool_calls"):
                    for name, args in calls_of(m):
                        if name not in contract.TOOL_NAMES:
                            raise AssertionError(f"모르는 도구: {name}")
            if r["messages"][0]["content"] != SYSTEM_PROMPT or r["tools"] != TOOLS:
                raise AssertionError("SYSTEM_PROMPT 또는 도구 정의가 v3와 다른 기록이 있습니다")
            # 같은 서명 반복 금지(맥락 + 정답)
            sigs = [signature(n, a) for m in r["messages"] if m["role"] == "assistant" for n, a in calls_of(m)]
            if len(sigs) != len(set(sigs)):
                raise AssertionError(f"같은 호출을 다시 부르는 기록: {user_text(r)}")
            rounds = sum(1 for m in r["messages"] if m["role"] == "assistant" and m.get("tool_calls"))
            if rounds > 2:
                raise AssertionError("2라운드를 넘는 기록")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument("--seed", type=int, default=41)
    parser.add_argument("--first-turn", type=int, default=2100, help="train에 넣을 v3 첫 판단 기록 수(작업 비율 유지)")
    parser.add_argument("--valid-first-turn", type=int, default=110)
    parser.add_argument("--test-first-turn", type=int, default=150)
    parser.add_argument("--show", type=int, default=0, help="종류별 예시를 몇 개 보여 줄지(0이면 하나씩)")
    parser.add_argument("--dump-drops", default=None, help="버린 답을 jsonl로 쓸 경로(선택)")
    args = parser.parse_args()
    global DROP_LOG
    if args.dump_drops:
        DROP_LOG = []

    held, review_places, review_regions, destinations, geo = fixture_sources()
    print(f"평가 쪽 값: 발화 {len(held.utterances)}개, test 값 {len(held.test_entities)}개, "
          f"test 카테고리 말 {len(held.test_category_words)}개, 사람 말투 세트에만 있는 값 {len(held.human_only_entities)}개, "
          f"평가 fixture 이름 {len(held.eval_fixture_names)}개", flush=True)

    base = mf.build_base(set(V3.KEYWORDS), destinations, review_places, review_regions, args.seed, TODAY, **geo)
    check_fixture(held, base)
    mf.write_fixtures(base, FIXTURE_DIR)
    print(f"학습 fixture: 키워드 장소 {len(base['recommend_by_keywords']['rows'])}, S1 여행지 {len(base['destinations']['rows'])}, "
          f"여행자 {len(base['recommend_similar_traveler_routes']['rows'])}, 후기 {len(base['search_reviews']['rows'])}, "
          f"글로플러 {len(base['search_gloplers']['rows'])}, 날씨 지오코딩 {len(base['weather']['geocoding'])} "
          f"→ {FIXTURE_DIR.relative_to(ROOT)}", flush=True)

    v3 = {s: read_jsonl(ROOT / "data" / "tool_calling_v3" / f"{s}.jsonl") for s in ("train", "valid", "test")}
    # '정보가 없으면 후기라도' 발화의 장소: v3 train 장소(일부는 학습 fixture에 상세가 없음) + 학습 fixture 장소
    # 학습 S1 fixture에 상세가 없는 장소를 더 자주 고른다(그래야 '없으면 후기라도'가 이어서 부르기로 이어짐)
    detail_names = {r["name"] for r in base["destinations"]["rows"]}
    train_places = sorted(n for n in set(V3.PLACES["train"]) | {n for n, _, _ in mf.all_places()}
                          if not boundary_hits(n, held.human_only_entities))
    missing = [n for n in train_places if n not in detail_names]
    chain_places = {"train": missing * 3 + train_places, "test": sorted(V3.PLACES["test"])}
    stats, drops, examples = Counter(), Counter(), {}
    seen_chain: set[str] = {user_text(r) for s in v3.values() for r in s}
    rng = random.Random(args.seed)
    splits = {}
    with tempfile.TemporaryDirectory(prefix="glople_v4_fixture_") as tmp:
        dirs = []
        for k in range(N_VARIANTS):
            d = Path(tmp) / f"v{k}"
            mf.write_fixtures(mf.make_variant(base, random.Random(args.seed * 100 + k)), d)
            dirs.append(d)
        env_train = ToolEnv(dirs)
        env_test = ToolEnv([None])   # 에이전트 기본 fixture(평가와 같은 것)

        b = Builder(held, env_train, "train", rng, stats, drops, examples)
        b.first_turn(v3["train"], args.first_turn)
        b.after_tools(v3["train"], AFTER_QUOTAS)
        b.chains(CHAIN_QUOTAS, chain_places["train"], seen_chain)
        splits["train"] = b.records

        b = Builder(held, env_train, "valid", rng, stats, drops, examples)
        b.first_turn(v3["valid"], args.valid_first_turn)
        b.after_tools(v3["valid"], scale(AFTER_QUOTAS, 0.06))
        b.chains(scale(CHAIN_QUOTAS, 0.12), chain_places["train"], seen_chain)
        splits["valid"] = b.records

        b = Builder(held, env_test, "test", rng, stats, drops, examples)
        b.first_turn(v3["test"], args.test_first_turn)
        b.after_tools(v3["test"], scale(AFTER_QUOTAS, 0.1))
        b.chains(scale(CHAIN_QUOTAS, 0.25), chain_places["test"], seen_chain)
        splits["test"] = b.records

    check_splits(splits, held)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, records in splits.items():
        rng.shuffle(records)
        with (out / f"{name}.jsonl").open("w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
    write_manifest(out, splits, args)
    print_report(splits, stats, drops, examples, args.show)
    if DROP_LOG is not None:
        Path(args.dump_drops).write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in DROP_LOG),
                                         encoding="utf-8")
    print(f"\n저장: {out.relative_to(ROOT) if out.is_relative_to(ROOT) else out}/{{train,valid,test}}.jsonl", flush=True)


AGENT_SOURCES = ["src/glople_agent/tools", "src/glople_agent/answer/templates.py", "src/glople_agent/graph/guard.py",
                 "src/glople_agent/graph/grounding.py", "src/glople_agent/sanitize.py", "tests/fixtures/backend",
                 "resources/regions", "scripts/measure_after_tools.py"]


def source_hashes() -> dict[str, str]:
    """데이터를 만들 때 쓴 에이전트 도구 코드·fixture의 sha256. check_multiturn_dataset.py가 지금 것과 비교해
    에이전트가 바뀌었으면 다시 만들라고 알린다(도구 결과 모양이 바뀌면 학습 데이터와 실제 요청이 어긋남)."""
    import hashlib
    agent = REPO / "agent"
    out = {}
    for rel in AGENT_SOURCES:
        path = agent / rel
        files = sorted(p for p in path.rglob("*") if p.is_file() and "__pycache__" not in p.parts) if path.is_dir() else [path]
        for f in files:
            if f.suffix in (".py", ".json", ".csv"):
                out[str(f.relative_to(agent))] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def write_manifest(out: Path, splits: dict, args) -> None:
    import hashlib
    manifest = {
        "generator": "scripts/build_multiturn_dataset.py", "seed": args.seed, "today": TODAY.isoformat(),
        "counts": {s: dict(Counter(r["task"] if r["meta"]["kind"] != "first_turn" else "first_turn" for r in rs))
                   for s, rs in splits.items()},
        "files": {f"{s}.jsonl": hashlib.sha256((out / f"{s}.jsonl").read_bytes()).hexdigest() for s in splits},
        "training_fixture": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(FIXTURE_DIR.glob("*.json"))},
        "agent_sources": source_hashes(),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def print_report(splits, stats, drops, examples, show):
    print("\n기록 수 (정답 턴 종류별)")
    tasks = ["first_turn", "chain_first", "after_answer", "after_limit", "after_call", "after2_answer"]
    print(f"{'종류':<14}" + "".join(f"{s:>8}" for s in splits))
    for t in tasks:
        cells = []
        for s, rs in splits.items():
            if t == "first_turn":
                n = sum(r["meta"]["kind"] == "first_turn" for r in rs)
            else:
                n = sum(r["task"] == t for r in rs)
            cells.append(f"{n:>8}")
        print(f"{t:<14}" + "".join(cells))
    print(f"{'합계':<14}" + "".join(f"{len(rs):>8}" for rs in splits.values()))

    print("\ntrain 도구 결과 상태 (도구 결과 뒤 기록, 결과 하나하나)")
    status = Counter()
    for r in splits["train"]:
        for t, st in zip(r["meta"].get("tools", []), r["meta"].get("statuses", [])):
            status[f"{t}:{st}"] += 1
    for k, v in sorted(status.items()):
        print(f"  {k:<48}{v:>5}")
    chain = Counter((r["task"], r["meta"].get("chain")) for r in splits["train"] if r["meta"].get("chain"))
    print("\ntrain 이어서 부르기 (종류, 체인): " + ", ".join(f"{t}/{c} {n}" for (t, c), n in sorted(chain.items())))
    print("\n버린 답·호출 (가드·검사 이유별)")
    for k, v in sorted(drops.items()):
        print(f"  {k:<60}{v:>5}")
    if not drops:
        print("  없음")
    print("\n예시")
    shown = Counter()
    for (split, task, key), record in sorted(examples.items(), key=lambda x: (x[0][0] != "train", x[0])):
        if split != "train" or task == "first_turn" or shown[task] >= max(1, show):
            continue
        shown[task] += 1
        msgs = record["messages"]
        print(f"- [{task}] {key}\n  사용자: {user_text(record)}")
        for m in msgs[2:-1]:
            if m["role"] == "assistant":
                print("  호출: " + "; ".join(f"{n} {json.dumps(a, ensure_ascii=False)}" for n, a in calls_of(m)))
            elif m["role"] == "tool":
                print(f"  결과: {m['content'][:220]}")
        target = msgs[-1]
        if target.get("tool_calls"):
            print("  정답(호출): " + "; ".join(f"{n} {json.dumps(a, ensure_ascii=False)}" for n, a in calls_of(target)))
        else:
            print(f"  정답(답): {target['content']}")


if __name__ == "__main__":
    main()
