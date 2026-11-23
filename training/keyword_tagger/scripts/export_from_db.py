"""로컬 MySQL(도커 glople-mysql)의 destination_info에서 설명문이 있는 여행지를 학습용 jsonl로 꺼낸다.

- 국내(TOUR_API)와 해외(WIKIDATA)를 모두 꺼내고, 이미 정답 라벨이 있는 id(labels/gold_labels.jsonl)를 표시한다.
- DB 비밀번호는 Glople 루트의 .env(GLOPLE_DB_PASSWORD)에서 읽어 docker exec의 환경변수로만 넘긴다. 출력하지 않는다.
- 결과 파일(data/db_descriptions.jsonl)에는 설명문 원문이 들어가므로 git에 올리지 않는다(data/는 gitignore).
사용: python3 -I scripts/export_from_db.py
"""
import json
import os
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GLOPLE = ROOT.parents[1]
OUT = ROOT / "data" / "db_descriptions.jsonl"
GOLD = ROOT / "labels" / "gold_labels.jsonl"

QUERY = """
SELECT JSON_OBJECT(
  'id', external_id, 'source', source, 'title', dest_name, 'overview', info,
  'category', category, 'content_type_id', content_type_id, 'country', country,
  'country_code', country_code, 'location', location, 'copyright_type', copyright_type,
  'license', license, 'source_url', source_url)
FROM destination_info
WHERE source IN ('TOUR_API', 'WIKIDATA') AND info IS NOT NULL
ORDER BY source, id
"""


def db_password():
    for line in (GLOPLE / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("GLOPLE_DB_PASSWORD="):
            return line.split("=", 1)[1].strip()
    sys.exit(".env에 GLOPLE_DB_PASSWORD가 없습니다.")


def main():
    env = dict(os.environ, MYSQL_PWD=db_password())
    result = subprocess.run(
        ["docker", "exec", "-i", "-e", "MYSQL_PWD", "glople-mysql", "mysql", "--default-character-set=utf8mb4",
         "-uroot", "-N", "-B", "--raw", "travien", "-e", QUERY],
        capture_output=True, text=True, env=env)
    if result.returncode != 0:
        sys.exit("DB 조회 실패: " + result.stderr.strip()[:300])

    labeled = {json.loads(line)["id"] for line in GOLD.read_text(encoding="utf-8").splitlines()}
    rows = []
    for line in result.stdout.splitlines():
        row = json.loads(line)
        row["already_labeled"] = row["id"] in labeled
        rows.append(row)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    counts = Counter((r["source"], r["already_labeled"]) for r in rows)
    print(f"저장: {OUT.relative_to(ROOT)} ({len(rows)}건)")
    for (source, done), n in sorted(counts.items()):
        print(f"  {source:9} {'라벨 있음' if done else '라벨 없음'}: {n}")


if __name__ == "__main__":
    main()
