"""build_index.py --corpus db 의 서비스 검색 대상 만들기 시험 (모델 없이, 표준 unittest).

실행: cd training/embedding && .venv/bin/python -B -I -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import build_index as bi  # noqa: E402
import emb_common as ec  # noqa: E402

TYPE_LABELS = [("Q23442", "섬"), ("Q16970", ""), ("Q8502", "산"), ("Q33506", "박물관")]
CATEGORY_TABLE = {"NA020500": {"depth1": "자연관광", "depth2": "자연경관(하천‧해양)", "depth3": "섬", "keywords": ["섬"]}}

EXPORT = [
    {"id": "127480", "source": "TOUR_API", "title": " 가거도 ", "country": "대한민국", "category": "NA020500",
     "content_type_id": "12", "location": "전남광주통합특별시 신안군", "copyright_type": "Type1",
     "overview": "가거도는<br>섬이다."},
    {"id": "999", "source": "TOUR_API", "title": "분류 없는 곳", "country": "대한민국", "category": None,
     "content_type_id": "14", "location": None, "copyright_type": "Type3", "overview": "글"},
    {"id": "Q6059", "source": "WIKIDATA", "title": "크르크섬", "country": "크로아티아", "category": "Q8502|Q23442",
     "content_type_id": None, "location": "크로아티아 프리모레고르스키코타르주", "copyright_type": None, "overview": "섬이다"},
    {"id": "Q1", "source": "WIKIDATA", "title": "유형 이름 없음", "country": "프랑스", "category": "Q16970",
     "content_type_id": None, "location": "프랑스", "copyright_type": None, "overview": None},
]


class ServiceCorpusTest(unittest.TestCase):
    def setUp(self):
        self.rows = bi.service_destinations(EXPORT, CATEGORY_TABLE, TYPE_LABELS, {"127480": "train", "Q6059": "train"})

    def test_wiki_primary_label_follows_table_order(self):
        self.assertEqual(bi.wiki_primary_label("Q8502|Q23442", TYPE_LABELS), "섬")   # 코드표에서 섬이 산보다 앞
        self.assertEqual(bi.wiki_primary_label("Q16970|Q33506", TYPE_LABELS), "박물관")  # 한국어 이름 없는 유형은 건너뜀
        self.assertIsNone(bi.wiki_primary_label("Q16970", TYPE_LABELS))
        self.assertIsNone(bi.wiki_primary_label(None, TYPE_LABELS))

    def test_scope_matches_server_rule(self):
        self.assertEqual(bi.scope_of_row("TOUR_API", None), "domestic")
        self.assertEqual(bi.scope_of_row(None, "대한민국"), "domestic")
        self.assertEqual(bi.scope_of_row("WIKIDATA", "일본"), "overseas")
        self.assertEqual([r["scope"] for r in self.rows], ["domestic", "domestic", "overseas", "overseas"])

    def test_rows_and_passages(self):
        dom, no_cat, wiki, no_label = self.rows
        self.assertEqual((dom["title"], dom["category"], dom["split"]), ("가거도", "섬", "train"))
        self.assertEqual(ec.passage_text(dom, "all"),
                         "가거도 | 전남광주통합특별시 신안군 | 자연관광 > 자연경관(하천‧해양) > 섬 | 가거도는 섬이다.")
        self.assertEqual((no_cat["category"], no_cat["location"]), ("문화시설", None))
        self.assertEqual(ec.passage_text(no_cat, "none"), "분류 없는 곳 | 문화시설")
        self.assertEqual(ec.passage_text(wiki, "all"), "크르크섬 | 크로아티아 프리모레고르스키코타르주 | 섬 | 섬이다")
        self.assertIsNone(wiki["split"])   # 임베딩 학습에는 국내만 썼으므로 해외는 분할 없음
        self.assertEqual((no_label["category"], ec.passage_text(no_label, "all")), ("여행지", "유형 이름 없음 | 프랑스 | 여행지"))

    def test_duplicate_rows_rejected(self):
        with self.assertRaises(SystemExit):
            bi.service_destinations(EXPORT + EXPORT[:1], CATEGORY_TABLE, TYPE_LABELS, {})

    def test_real_type_table_loads(self):
        labels = bi.load_wiki_type_labels()
        self.assertGreater(len(labels), 100)
        self.assertEqual(labels[0], ("Q23442", "섬"))


if __name__ == "__main__":
    unittest.main()
