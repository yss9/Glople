"""임베딩 학습·평가 데이터 만들기: (합성 선호 질의, 정답 여행지, 어려운 음성) 쌍과 평가 질의 세트.

입력 (모두 읽기만 함)
- keyword_tagger/labels/gold_labels.jsonl : 여행지 id -> 정답 키워드 (AI silver 라벨, 1,934개)
- keyword_tagger/labels/splits.json       : 태거 분할 train/valid/test/test_new (여행지 id 기준)
- keyword_tagger/data/db_descriptions.jsonl : 로컬 DB 추출본 (TOUR_API 이름·지역·분류·저작권 표시·설명문)
- keyword_tagger/data/descriptions.jsonl    : 1차 배치 덤프 (DB 에 없는 1차 여행지 106개의 이름·주소·설명문)
- src/main/resources/tour/category-keywords.csv : 분류 코드 이름과 분류별 키워드 (서버와 같은 표)

출력 (data/ 는 gitignore: 설명문 원문이 들어감)
- data/destinations.jsonl  : 여행지 1,934개 (id, 이름, 지역, 분류, 저작권 표시, 설명문, 정답 키워드, 서버 마스크 키워드, 분할)
- data/train_pairs.jsonl   : 학습 쌍 (질의, 정답 여행지 id, 어려운 음성 id). 태거 train 여행지만 씀
- data/queries_valid.jsonl, queries_test.jsonl, queries_test_iid.jsonl : 평가 질의
- results/data_stats.json  : 개수·분포·합성 질의 예시 (설명문 없음, 커밋함)

질의는 모두 **합성**입니다. 실제 사용자 질의가 아니라, 정답 키워드 1~3개(+ 가끔 지역·분류)를 골라
query_templates.py 의 표현·문장 틀로 문장을 만든 것입니다. 정답(관련도)도 같은 키워드로 계산합니다(emb_common.graded_relevance).

분할 규칙: 태거 분할을 여행지 id 그대로 따릅니다. 학습 쌍의 정답·음성은 train 여행지에서만 나오고,
valid·test·test_new 여행지는 학습에 한 번도 나오지 않습니다(마지막에 assert 로 확인).

사용: python3 -I scripts/prepare_data.py   (표준 라이브러리만 사용)
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))  # python -I 로 돌려도 같은 폴더 모듈을 찾도록

import emb_common as ec  # noqa: E402
import query_templates as qt  # noqa: E402

QUERIES_PER_TRAIN_DEST = 4
P_REGION_SIDO = 0.25
P_REGION_SIGUNGU = 0.12
P_CATEGORY = 0.15
P_FLUFF = 0.35
K_WEIGHTS = {1: 0.3, 2: 0.5, 3: 0.2}   # 질의에 넣는 키워드 개수 분포
CONTENT_TYPE_NAMES = {"12": "관광지", "14": "문화시설", "15": "축제공연행사", "28": "레포츠"}


# ---------------------------------------------------------------------------
# 1. 여행지 표 만들기
# ---------------------------------------------------------------------------
DOMESTIC_SPLITS = ("train", "valid", "test", "test_new")


def build_destinations(keywords: List[str]):
    """국내(TourAPI) 여행지만 만듭니다. 돌려주는 값: (여행지 목록, 건너뛴 해외 id 수).

    2026-10-18 에 keyword_tagger 에 3차 배치(해외 WIKIDATA 800개, 분할 test_overseas)가 더해졌습니다.
    지역·분류 조건, 서버 마스크 규칙(국내 규칙 ∪ 분류 코드표), 질의 문장 틀이 모두 국내용이라 이번 데이터에서는 뺍니다.
    """
    gold = ec.read_jsonl(ec.GOLD_LABELS_PATH)
    splits = ec.read_json(ec.SPLITS_PATH)
    category_table = ec.load_category_table()
    keyword_set = set(keywords)

    split_of: Dict[str, str] = {}
    for name, ids in splits.items():
        if not isinstance(ids, list):
            continue
        for i in ids:
            if i in split_of:
                raise SystemExit(f"splits.json: id {i} 가 {split_of[i]} 와 {name} 에 모두 있습니다")
            split_of[i] = name

    if not ec.DB_EXPORT_PATH.exists():
        raise SystemExit(f"{ec.DB_EXPORT_PATH} 가 없습니다. keyword_tagger/scripts/export_from_db.py 로 만든 로컬 추출본이 필요합니다.")
    export = ec.read_jsonl(ec.DB_EXPORT_PATH)
    overseas_ids = {r["id"] for r in export if r.get("source") == "WIKIDATA"}
    db_rows = [r for r in export if r.get("source") == "TOUR_API"]
    # 추출 쿼리가 ORDER BY source, id 이므로 이 순서가 DB 행 id 순서 = 서버 키워드 검색의 동점 순서입니다.
    db = {r["id"]: (i, r) for i, r in enumerate(db_rows)}
    dump = {r["id"]: r for r in ec.read_jsonl(ec.DUMP_PATH)} if ec.DUMP_PATH.exists() else {}

    dests = []
    seen = set()
    skipped_overseas = 0
    for row in gold:
        i = row["id"]
        if i in seen:
            raise SystemExit(f"gold_labels.jsonl: id {i} 중복")
        seen.add(i)
        if i in overseas_ids and i not in db:
            skipped_overseas += 1
            continue
        bad = [k for k in row["keywords"] if k not in keyword_set]
        if bad:
            raise SystemExit(f"gold_labels.jsonl: id {i} 에 목록 밖 키워드 {bad}")
        if split_of.get(i) not in DOMESTIC_SPLITS:
            raise SystemExit(f"splits.json: 국내 여행지 id {i} 의 분할이 {split_of.get(i)} 입니다 ({DOMESTIC_SPLITS} 이어야 함)")

        if i in db:
            order, r = db[i]
            source = "db"
            title, location = r["title"], r.get("location")
            category, content_type = r.get("category"), r.get("content_type_id")
            copyright_type, description = r.get("copyright_type"), r.get("overview")
        elif i in dump:
            r = dump[i]
            order = len(db_rows) + 0  # 아래에서 id 순으로 다시 매김
            source = "dump"
            title, location = r["title"], ec.region_of_address(r.get("address"))
            category, content_type = None, r.get("content_type_id")
            copyright_type, description = None, r.get("overview")
        else:
            raise SystemExit(f"id {i} 의 이름·설명문을 찾을 수 없습니다 (DB 추출본에도 1차 덤프에도 없음)")

        cat = category_table.get((category or "").strip())
        description = ec.clean_description(description)
        server_kw = set(ec.korean_rule_predict(description, keywords))
        if cat:
            server_kw |= set(cat["keywords"])
        region = ec.parse_region(location)
        dests.append({
            "id": i,
            "title": title.strip(),
            "location": (location or "").strip() or None,
            "sido": region["sido"],
            "sigungu": region["sigungu"],
            "category": (category or "").strip() or None,
            "category_path": f"{cat['depth1']} > {cat['depth2']} > {cat['depth3']}" if cat else None,
            "content_type_id": content_type,
            "content_type_name": CONTENT_TYPE_NAMES.get((content_type or "").strip(), "여행지"),
            "copyright_type": copyright_type,
            "description": description or None,
            "keywords": row["keywords"],
            # 서버가 지금 TOUR_API 행에 저장하는 마스크: 국내 규칙(설명문) ∪ 분류 코드표 키워드 (DestinationInfo.computeKeywordMask)
            "server_mask_keywords": [k for k in keywords if k in server_kw],
            "split": split_of[i],
            "text_source": source,
            "db_order": order,
        })

    # DB 에 없는 1차 여행지는 DB 행 뒤에 id 숫자 순으로 둡니다 (서버에는 이 행이 없으므로 동점 순서는 임의로 정함).
    dump_only = sorted((d for d in dests if d["text_source"] == "dump"), key=lambda d: int(d["id"]))
    for j, d in enumerate(dump_only):
        d["db_order"] = len(db_rows) + j
    dests.sort(key=lambda d: d["db_order"])
    return dests, skipped_overseas


def sigungu_query_names(dests: List[dict]) -> Dict[tuple, List[str]]:
    """(시도, 시군구) -> 질의에 쓸 이름들. '경주시' -> ['경주', '경주시'].

    줄기('경주')가 두 글자 이상이고, 여러 시도에 같은 이름이 없고, 시도 이름과 겹치지 않을 때만 씁니다.
    ('중구'·'북구'·'고성군'처럼 헷갈리는 이름은 지역 조건에 쓰지 않음)
    """
    stems = defaultdict(set)
    for d in dests:
        sg = d.get("sigungu")
        if d.get("sido") and sg and sg[-1] in "시군구" and len(sg) >= 3:
            stems[sg[:-1]].add((d["sido"], sg))
    sido_aliases = {a for names in qt.SIDO_QUERY_NAMES.values() for a in names} | set(ec.SIDO_CANON)
    out = {}
    for stem, pairs in stems.items():
        if len(pairs) != 1 or stem in sido_aliases or len(stem) < 2:
            continue
        (sido, sg), = pairs
        # '경주시' -> '경주'·'경주시' 둘 다, 구는 줄기만 쓰면 어색해서('남동에 있는') 전체 이름만
        out[(sido, sg)] = [sg] if sg.endswith("구") else [stem, sg]
    return out


# ---------------------------------------------------------------------------
# 2. 질의 합성
# ---------------------------------------------------------------------------
class QueryMaker:
    def __init__(self, keyword_freq: Counter, sigungu_names: Dict[tuple, List[str]]):
        self.weight = {k: 1.0 / math.sqrt(max(1, n)) for k, n in keyword_freq.items()}
        self.sigungu_names = sigungu_names
        self.category_groups = qt.CATEGORY_HEADS

    def category_group_of(self, dest: dict):
        code = dest.get("category") or ""
        for name, heads, prefixes in self.category_groups:
            if any(code.startswith(p) for p in prefixes):
                return name, heads, prefixes
        return None

    def _sample_keywords(self, gold: List[str], k: int, rng: random.Random, exclude: set) -> List[str]:
        pool = [g for g in gold if g not in exclude]
        chosen = []
        for _ in range(min(k, len(pool))):
            weights = [self.weight.get(g, 1.0) for g in pool]
            pick = rng.choices(pool, weights=weights, k=1)[0]
            chosen.append(pick)
            pool.remove(pick)
        return chosen

    def make(self, dest: dict, rng: random.Random, style: str) -> Optional[dict]:
        """여행지 하나에서 질의 하나. style='train' 은 학습용 재료, 'eval' 은 평가 전용 재료로 만듭니다."""
        gold = dest["keywords"]
        if not gold:
            return None
        templates = qt.TRAIN_TEMPLATES if style == "train" else qt.EVAL_TEMPLATES
        fluffs = qt.TRAIN_FLUFF if style == "train" else qt.EVAL_FLUFF
        generic_heads = qt.TRAIN_GENERIC_HEADS if style == "train" else qt.EVAL_GENERIC_HEADS

        # 지역 조건
        region = None
        r = rng.random()
        sg_names = self.sigungu_names.get((dest.get("sido"), dest.get("sigungu")))
        if dest.get("sido") and r < P_REGION_SIDO:
            surface = rng.choice(qt.SIDO_QUERY_NAMES[dest["sido"]])
            region = {"level": "sido", "value": dest["sido"], "sido": dest["sido"], "surface": surface}
        elif sg_names and r < P_REGION_SIDO + P_REGION_SIGUNGU:
            surface = rng.choice(sg_names)
            region = {"level": "sigungu", "value": dest["sigungu"], "sido": dest["sido"], "surface": surface}

        # 분류 조건 (머리 명사)
        category = None
        group = self.category_group_of(dest)
        if group and rng.random() < P_CATEGORY:
            name, heads, prefixes = group
            category = {"group": name, "head": rng.choice(heads), "code_prefixes": prefixes}

        # 키워드 고르기 (드문 키워드가 조금 더 자주 뽑히도록 빈도^-0.5 가중치)
        k = rng.choices(list(K_WEIGHTS), weights=list(K_WEIGHTS.values()), k=1)[0]
        if category:
            k = min(k, 2)
        exclude = set()
        if category:  # 머리 명사와 같은 말을 키워드 표현으로 또 쓰지 않음 ('절 좋아하는데 ... 절')
            exclude = {kw for kw in gold if category["head"] in qt.KEYWORD_PHRASES[kw]}
        chosen = self._sample_keywords(gold, k, rng, exclude)
        if not chosen:
            return None

        # 키워드 -> 표현
        phrases = []
        for kw in chosen:
            train_forms, eval_forms = qt.split_phrases(kw)
            if style == "train":
                others = train_forms[1:]
                phrases.append(rng.choice(others) if others and rng.random() >= 0.4 else train_forms[0])
            else:
                phrases.append(eval_forms[-1] if len(eval_forms) > 1 and rng.random() < 0.5 else eval_forms[0])

        # 문장 틀
        n_activity = sum(kw in qt.ACTIVITY_KEYWORDS for kw in chosen)
        kinds = {"any"} | ({"activity"} if n_activity == len(chosen) else {"sight"} if n_activity == 0 else set())
        candidates = [(i, t) for i, (kind, t) in enumerate(templates) if kind in kinds]
        if category:
            candidates = [(i, t) for i, t in candidates if "{H" in t]
        tid, template = rng.choice(candidates)
        head = category["head"] if category else rng.choice(generic_heads)
        fluff = rng.choice(fluffs) + " " if rng.random() < P_FLUFF else ""
        text = qt.render(
            template, phrases, rng.randrange(3),
            region_loc=f"{region['surface']}에서 " if region else "",
            region_adn=f"{region['surface']}에 있는 " if region else "",
            fluff=fluff, head=head,
        )
        qtype = "kw" + ("+region" if region else "") + ("+category" if category else "")
        return {
            "text": text,
            "keywords": chosen,
            "phrases": phrases,
            "region": region,
            "category": category,
            "type": qtype,
            "origin_id": dest["id"],
            "origin_split": dest["split"],
            "style": style,
            "template": f"{style}:{tid}",
        }


# ---------------------------------------------------------------------------
# 3. 어려운 음성 (hard negative)
# ---------------------------------------------------------------------------
def pick_hard_negative(query: dict, pos: dict, pool: List[dict], rng: random.Random):
    """학습 쌍마다 음성 여행지 하나 (pool = train 여행지만).

    A. 조건만 다른 음성: 질의에 지역·분류 조건이 있을 때, 키워드는 모두 맞지만 지역·분류가 다른 여행지 (50%)
    B. 키워드가 조금 겹치는 음성: 정답 여행지와 키워드가 1개 ~ 절반 겹치지만 이 질의에는 완전 관련이 아닌 여행지
    C. 둘 다 없으면 질의와 상관없는 아무 여행지
    """
    kq = set(query["keywords"])
    if query["region"] or query["category"]:
        cand_a = [x for x in pool if x["id"] != pos["id"] and kq <= set(x["keywords"]) and not ec.constraint_ok(query, x)]
        if cand_a and rng.random() < 0.5:
            return rng.choice(cand_a)["id"], "constraint"
    gpos = set(pos["keywords"])
    limit = max(1, len(gpos) // 2)
    cand_b = [x for x in pool if x["id"] != pos["id"] and 1 <= len(gpos & set(x["keywords"])) <= limit
              and not ec.is_fully_relevant(query, x)]
    if cand_b:
        return rng.choice(cand_b)["id"], "few_shared"
    cand_c = [x for x in pool if x["id"] != pos["id"] and not ec.is_fully_relevant(query, x)]
    return rng.choice(cand_c)["id"], "random"


# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--queries-per-dest", type=int, default=QUERIES_PER_TRAIN_DEST)
    parser.add_argument("--seed", type=int, default=ec.SEED)
    args = parser.parse_args()

    qt.all_template_slots_ok()
    missing = [k for k in ec.load_keywords() if k not in qt.KEYWORD_PHRASES]
    if missing:
        raise SystemExit(f"query_templates.KEYWORD_PHRASES 에 없는 키워드: {missing}")
    keywords = ec.load_keywords()
    dests, skipped_overseas = build_destinations(keywords)
    by_split = defaultdict(list)
    for d in dests:
        by_split[d["split"]].append(d)
    sg_names = sigungu_query_names(dests)

    # 키워드 가중치는 train 여행지 빈도로만 계산 (평가 여행지 정보는 쓰지 않음)
    freq = Counter(k for d in by_split["train"] for k in d["keywords"])
    for k in keywords:
        freq.setdefault(k, 0)
    maker = QueryMaker(freq, sg_names)

    # 학습 쌍 -------------------------------------------------------------
    rng = random.Random(args.seed)
    train_pool = by_split["train"]
    pairs, seen_text = [], set()
    for d in train_pool:
        made = 0
        for _attempt in range(args.queries_per_dest * 10):
            if made >= args.queries_per_dest:
                break
            q = maker.make(d, rng, "train")
            if q is None:
                break
            key = (q["text"], d["id"])
            if key in seen_text:
                continue
            seen_text.add(key)
            neg_id, neg_kind = pick_hard_negative(q, d, train_pool, rng)
            q.update({"qid": f"train-{len(pairs):05d}", "positive_id": d["id"], "negative_id": neg_id,
                      "negative_kind": neg_kind})
            pairs.append(q)
            made += 1

    # 평가 질의 -----------------------------------------------------------
    query_sets = {}
    for name, style, seed_offset in (("valid", "train", 101), ("test", "eval", 202), ("test_iid", "train", 303)):
        rng_eval = random.Random(args.seed + seed_offset)
        rows = []
        for split in ec.CORPUS_SPLITS[name]:
            for d in by_split[split]:
                q = maker.make(d, rng_eval, style)
                if q is None:
                    continue
                q["qid"] = f"{name}-{len(rows):04d}"
                rows.append(q)
        query_sets[name] = rows

    # 누수 검사 -----------------------------------------------------------
    train_ids = {d["id"] for d in train_pool}
    used = {p["positive_id"] for p in pairs} | {p["negative_id"] for p in pairs}
    assert used <= train_ids, "학습 쌍에 train 밖 여행지가 들어갔습니다"
    for name in ("valid", "test", "test_iid"):
        corpus_split = set(ec.CORPUS_SPLITS[name])
        eval_ids = {d["id"] for d in dests if d["split"] in corpus_split}
        assert not (eval_ids & used), f"{name} 여행지가 학습 쌍에 들어갔습니다"
    train_templates = {p["template"] for p in pairs}
    assert not any(q["template"] in train_templates for q in query_sets["test"]), "test 가 학습 문장 틀을 씁니다"
    train_texts = {p["text"] for p in pairs}
    overlap = {name: sum(q["text"] in train_texts for q in rows) for name, rows in query_sets.items()}

    # 저장 ----------------------------------------------------------------
    ec.write_jsonl(ec.DESTINATIONS_PATH, dests)
    ec.write_jsonl(ec.TRAIN_PAIRS_PATH, pairs)
    for name, rows in query_sets.items():
        ec.write_jsonl(ec.QUERY_SET_PATHS[name], rows)

    # 통계 (설명문 없음) ------------------------------------------------------
    dest_by_id = {d["id"]: d for d in dests}

    def relevant_counts(name):
        corpus = ec.corpus_ids(dest_by_id, name)
        counts = [sum(ec.is_fully_relevant(q, dest_by_id[c]) for c in corpus) for q in query_sets[name]]
        buckets = Counter("1" if n == 1 else "2-5" if n <= 5 else "6-20" if n <= 20 else "21+" for n in counts)
        return {"corpus": len(corpus), "mean": round(sum(counts) / len(counts), 2), "buckets": dict(sorted(buckets.items()))}

    def describe(rows):
        return {
            "queries": len(rows),
            "types": dict(Counter(q["type"] for q in rows).most_common()),
            "keywords_per_query": dict(sorted(Counter(len(q["keywords"]) for q in rows).items())),
            "paraphrased_keyword_ratio": round(
                sum(p != k for q in rows for p, k in zip(q["phrases"], q["keywords"]))
                / max(1, sum(len(q["keywords"]) for q in rows)), 3),
            "templates_used": len({q["template"] for q in rows}),
        }

    stats = {
        "note": "질의는 모두 합성(AI가 쓴 표현·문장 틀로 생성). 정답 키워드는 keyword_tagger 의 AI silver 라벨. 설명문 원문은 넣지 않음.",
        "seed": args.seed,
        "destinations": {
            "total": len(dests),
            "skipped_overseas_labels": skipped_overseas,
            "by_split": {s: len(v) for s, v in sorted(by_split.items())},
            "text_source": dict(Counter(d["text_source"] for d in dests)),
            "with_description": sum(bool(d["description"]) for d in dests),
            "copyright_type": dict(Counter(d["copyright_type"] or "없음" for d in dests)),
            "with_category_path": sum(bool(d["category_path"]) for d in dests),
            "empty_keywords": sum(not d["keywords"] for d in dests),
            "sido": dict(Counter(d["sido"] or "모름" for d in dests).most_common()),
            "sigungu_names_usable": len(sg_names),
        },
        "train_pairs": {
            **describe(pairs),
            "train_destinations_used": len({p["positive_id"] for p in pairs}),
            "negative_kind": dict(Counter(p["negative_kind"] for p in pairs)),
        },
        "query_sets": {name: {**describe(rows), "relevant_per_query": relevant_counts(name)}
                       for name, rows in query_sets.items()},
        "exact_text_overlap_with_train": overlap,
        "files_sha256": {p.name: ec.sha256_file(p) for p in [ec.DESTINATIONS_PATH, ec.TRAIN_PAIRS_PATH,
                                                              *ec.QUERY_SET_PATHS.values()]},
        "examples": {
            "train": [{"text": p["text"], "keywords": p["keywords"], "type": p["type"],
                       "positive": dest_by_id[p["positive_id"]]["title"],
                       "negative": dest_by_id[p["negative_id"]]["title"], "negative_kind": p["negative_kind"]}
                      for p in random.Random(1).sample(pairs, 12)],
            "test": [{"text": q["text"], "keywords": q["keywords"], "type": q["type"],
                      "origin": dest_by_id[q["origin_id"]]["title"]}
                     for q in random.Random(2).sample(query_sets["test"], 12)],
        },
    }
    ec.write_json(ec.RESULTS_DIR / "data_stats.json", stats)

    print(f"여행지 {len(dests)}개 (국내만, 해외 라벨 {skipped_overseas}개는 뺌): "
          + ", ".join(f"{s} {len(v)}" for s, v in sorted(by_split.items())))
    print(f"학습 쌍 {len(pairs)}개 (train 여행지 {stats['train_pairs']['train_destinations_used']}개), "
          f"음성 종류 {stats['train_pairs']['negative_kind']}")
    for name, rows in query_sets.items():
        rc = stats["query_sets"][name]["relevant_per_query"]
        print(f"{name:8} 질의 {len(rows)}개, 검색 대상 {rc['corpus']}개, 질의당 완전 관련 평균 {rc['mean']} {rc['buckets']}, "
              f"학습 질의와 같은 문장 {overlap[name]}개")
    print("예시 (test):")
    for ex in stats["examples"]["test"][:6]:
        print(f"  {ex['text']}  <- {ex['keywords']} ({ex['origin']})")
    print(f"저장: {ec.DATA_DIR.relative_to(ec.ROOT)}/, results/data_stats.json")


if __name__ == "__main__":
    main()
