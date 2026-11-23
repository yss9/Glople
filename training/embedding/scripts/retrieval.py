"""검색기 세 가지: 키워드 마스크 검색(지금 서버), BM25(글자 2-gram), 임베딩(코사인 유사도).

모두 '질의 목록 -> 질의마다 여행지 id 순위 목록'을 돌려주고, 채점은 emb_common.evaluate_rankings 한 곳에서 합니다.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Dict, List, Sequence

import emb_common as ec

# ---------------------------------------------------------------------------
# (a) 키워드 마스크 검색: 서버 DestinationInfoRepository.findTop21ByKeywordMask 를 파이썬으로
# ---------------------------------------------------------------------------
SERVER_LIMIT = 21


def keyword_mask_rank(query_keywords: Sequence[str], corpus: Sequence[str], dests: Dict[str, dict],
                      mask_field: str = "server_mask_keywords") -> List[str]:
    """겹치는 키워드 수(BIT_COUNT(mask & 질의 마스크)) 내림차순, 같으면 DB id 오름차순, 1개 이상 겹치는 행만, 최대 21개.

    corpus 는 db_order 순서여야 합니다(emb_common.corpus_ids 가 그렇게 돌려줌). 정렬이 안정 정렬이라 동점은 그 순서를 따릅니다.
    mask_field='keywords' 로 주면 정답 라벨을 마스크로 쓴 참고용 상한(태거가 완벽할 때)이 됩니다.
    """
    q = set(query_keywords)
    if not q:
        return []  # 서버도 빈 마스크면 빈 목록 (KeywordRecommendService.searchByKeywords)
    scored = [(len(q & set(dests[c][mask_field])), i, c) for i, c in enumerate(corpus)]
    scored = [s for s in scored if s[0] > 0]
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [c for _, _, c in scored[:SERVER_LIMIT]]


def literal_query_keywords(text: str, keywords: Sequence[str]) -> List[str]:
    """질의 문장에 키워드 글자가 그대로 들어 있으면 그 키워드 (서버 국내 규칙 KeywordMask.fromKoreanDescription 과 같음)."""
    return ec.korean_rule_predict(text, keywords)


# ---------------------------------------------------------------------------
# (b) BM25 (형태소 분석기 없이 글자 2-gram)
# ---------------------------------------------------------------------------
_NON_WORD = re.compile(r"[^0-9a-zA-Z가-힣\s]")


def char_bigrams(text: str) -> List[str]:
    """'조용한 바닷가' -> ['조용', '용한', '바닷', '닷가']. 한 글자 낱말은 그대로 둡니다.

    한국어는 조사·어미가 붙어 낱말 모양이 바뀌므로('바다가', '바다를') 공백 단위 토큰보다 2-gram 이 잘 맞습니다.
    """
    out = []
    for word in _NON_WORD.sub(" ", text.lower()).split():
        if len(word) == 1:
            out.append(word)
        else:
            out.extend(word[i:i + 2] for i in range(len(word) - 1))
    return out


class BM25:
    """Okapi BM25 (k1=1.2, b=0.75, idf = log(1 + (N - df + 0.5) / (df + 0.5)))."""

    def __init__(self, docs: Sequence[str], k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(char_bigrams(d)) for d in docs]
        self.len = [sum(tf.values()) for tf in self.tf]
        self.avg_len = sum(self.len) / max(1, len(self.len))
        n = len(docs)
        df = Counter(t for tf in self.tf for t in tf)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: str) -> List[float]:
        terms = set(char_bigrams(query))
        out = []
        for tf, dl in zip(self.tf, self.len):
            s = 0.0
            norm = self.k1 * (1 - self.b + self.b * dl / self.avg_len)
            for t in terms:
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + norm)
            out.append(s)
        return out


def bm25_rankings(queries: Sequence[dict], corpus: Sequence[str], dests: Dict[str, dict], policy: str) -> List[List[str]]:
    index = BM25([ec.passage_text(dests[c], policy) for c in corpus])
    return [ec.rank_by_scores(index.scores(q["text"]), corpus) for q in queries]


# ---------------------------------------------------------------------------
# (c) 임베딩 검색
# ---------------------------------------------------------------------------
def embedding_rankings(model, queries: Sequence[dict], corpus: Sequence[str], dests: Dict[str, dict], *,
                       policy: str, query_prefix: str, passage_prefix: str, max_query_len: int,
                       max_passage_len: int, batch_size: int = 32) -> List[List[str]]:
    """질의·여행지를 정규화 임베딩으로 바꾸고 내적(= 코사인 유사도) 내림차순으로 줄 세웁니다."""
    import numpy as np

    passages = ec.encode_texts(model, [ec.passage_text(dests[c], policy) for c in corpus], passage_prefix,
                               max_passage_len, batch_size)
    qvec = ec.encode_texts(model, [q["text"] for q in queries], query_prefix, max_query_len, batch_size)
    scores = qvec @ passages.T
    order = np.argsort(-scores, axis=1, kind="stable")  # 동점이면 corpus 순서
    return [[corpus[j] for j in row] for row in order]


def passage_token_stats(model, texts: Sequence[str], prefix: str, limit: int) -> dict:
    """토크나이저 기준 길이 통계 (자르기 전). max_passage_len 을 정할 때 봅니다."""
    lengths = sorted(len(ids) for ids in model.tokenizer([prefix + t for t in texts], add_special_tokens=True, verbose=False,
                                                          truncation=False)["input_ids"])
    n = len(lengths)
    return {
        "count": n, "mean": round(sum(lengths) / n, 1), "p50": lengths[n // 2], "p90": lengths[int(n * 0.9)],
        "max": lengths[-1], f"over_{limit}": sum(1 for x in lengths if x > limit),
    }
