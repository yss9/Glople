# 키워드 태거 예측을 추천에 쓰기 (개편 2단계 서버 연동)

학습한 키워드 태거([`training/keyword_tagger`](../training/keyword_tagger/README.md))의 예측을 서버의 키워드 추천(`POST /api/search`)에 반영하는 방법입니다. 모델은 서버 안에서 돌리지 않습니다. 오너가 배치로 예측 파일을 만들고, 관리자 API로 DB에 가져옵니다.

```
data/db_descriptions.jsonl ──predict_all.py──▶ data/predicted_keywords_<run>.jsonl ──POST /api/admin/keyword-masks/import──▶ destination_info.tagger_*
                                                                                                                                  │
POST /api/search ◀── 행마다 (규칙 마스크, 태거 마스크)를 keyword.recommend.mask-mode(기본 tagger)로 합쳐 일치 수를 셈 ◀───────────────┘
```

## 한눈에

- **규칙 마스크**(`keyword_mask_low/high`)는 예전 그대로입니다. 저장할 때마다(`@PrePersist`·`@PreUpdate`) 설명문·분류 코드표·유형 코드표로 다시 계산합니다.
- **태거 마스크**(`tagger_mask_low/high`)는 새 열입니다. 가져오기 API만 씁니다. 규칙 계산이 덮어쓰지 않으므로, 예전처럼 SQL로 넣은 값이 다음 저장 때 되돌아가는 문제가 없습니다.
- **추천**은 쿼리 안에서 둘을 합칩니다. 기본은 `규칙 ∪ 태거`입니다. 설정만 바꾸고 다시 띄우면 `태거만`이나 `규칙만`으로 바뀌고, 저장된 값은 바뀌지 않습니다.
- **설명문이 바뀌면** 그 행의 태거 예측은 저장할 때 지워집니다(예측에 쓴 설명문의 CRC32를 같이 저장해 비교). 다시 예측해 가져오기 전까지 그 행은 규칙 마스크만 씁니다.
- **규칙 판**(`keyword_mask_version`)을 함께 저장합니다. 서버를 시작할 때 판이 다른 국내·위키데이터 행만 다시 계산합니다. 그래서 키워드 목록이나 코드표를 고친 뒤에는 서버를 다시 띄우기만 하면 마스크가 맞춰집니다([규칙 판](#규칙-판과-시작-시-다시-계산)).
- 응답 모양과 S0 동점 정렬([travel-api.md](travel-api.md#s0--키워드-추천-동점-정렬-post-apisearch))은 그대로입니다. 바뀌는 것은 "일치 키워드 수"를 셀 때 쓰는 행의 마스크뿐입니다.

## 새 열 (`ddl-auto=update`가 추가)

| 열 | 형 | 뜻 |
| :-- | :-- | :-- |
| `keyword_mask_version` | `VARCHAR(40)` NULL | 규칙 마스크를 계산한 규칙의 판. 예: `r1-k1a2b3c4d-t5e6f7a8b`. 기존 행은 NULL이라 첫 시작 때 다시 계산 |
| `tagger_mask_low`, `tagger_mask_high` | `BIGINT DEFAULT 0 NOT NULL` | 태거 예측 키워드 마스크. 비트 순서는 `KeywordMask`(= `keywords.json`)와 같음. 기존 행은 0 |
| `tagger_model` | `VARCHAR(100)` NULL | 예측을 만든 모델(예: `roberta-base-v3`). NULL = 예측 없음 |
| `tagger_info_crc32` | `BIGINT` NULL | 예측에 쓴 설명문(`info`)의 CRC32(UTF-8). 지금 설명문과 다르면 저장할 때 예측을 지움 |
| `tagger_updated_at` | `DATETIME(6)` NULL | 예측을 가져온 시각 |

규칙 마스크 열(`keyword_mask_low/high`)과 다른 열은 바뀌지 않습니다.

## 추천에 쓰는 마스크 (설정)

`keyword.recommend.mask-mode` (기본 `tagger`, 2026-10-18에 union에서 바꿈. 아래 '어떤 마스크를 쓸까'). 값은 대소문자를 가리지 않고, 모르는 값이면 서버가 시작 단계에서 멈춥니다.

| 값 | 태거 예측이 있는 행 | 예측이 없는 행 | 쓰임 |
| :-- | :-- | :-- | :-- |
| `union` (기본) | 규칙 ∪ 태거 | 규칙 | 규칙이 놓치던 키워드(`역사적인 장소`, `경치`, '한강'의 `강` 등)를 더합니다. 규칙의 오탐(예: 부르즈 할리파의 `모스크`)은 남습니다. |
| `tagger` | 태거만 | 규칙 | 규칙 오탐이 빠집니다. 대신 모델이 못 내는 키워드(v3는 학습 예시가 없는 열기구·호랑이·낙타)와 모델이 놓친 코드표 키워드(예: v3가 '국립 자동차 박물관'에 `박물관`을 붙이지 않음)도 빠집니다. |
| `rule` | 규칙 | 규칙 | 가져오기 전과 같습니다. 되돌릴 때 씁니다. |

설정하는 곳(하나만):

```properties
# src/main/resources/application.properties (gitignore 대상)
keyword.recommend.mask-mode=tagger
```

또는 서버를 띄우는 명령에 `--keyword.recommend.mask-mode=rule`을 붙입니다. 설정을 바꾸면 서버를 다시 띄워야 하고, DB는 고치지 않습니다. 가져오기 API의 응답 `recommendMaskMode`로 지금 값을 확인할 수 있습니다.

- 쿼리: [`DestinationInfoRepository.RECOMMEND_MASK_LOW_SQL`](../src/main/java/proj/travien/repository/DestinationInfoRepository.java)·`_HIGH_SQL`이 행마다 `CASE`로 마스크를 고르고, 필터(`(마스크 & :lowMask) <> 0 …`)와 점수(`BIT_COUNT`)가 그 마스크를 씁니다. 마스크 값은 비트 0~62만 써서 부호가 없는 정수로 바꿔도 값이 같습니다.
- `GET /api/destinations`의 해외 분류 근사(`DestinationCategory`)는 **규칙 마스크만** 씁니다(바꾸지 않음).

## 실행 순서 (오너)

아래는 프로젝트 루트(`Glople/`)에서 실행합니다. 서버는 8080, DB는 로컬 `glople-mysql`입니다.

### 0) 새 코드로 서버 다시 띄우기 (한 번)

평소처럼 다시 빌드해 띄웁니다. 첫 시작 때 일어나는 일:

1. `ddl-auto=update`가 위 열 6개를 추가합니다.
2. 시작 시 마스크 보정(`DestinationKeywordMaskBackfill`)이 판이 없는 국내·위키데이터 행 전부(2026-10-17 수집 기준 19,949 + 7,583행)를 id 순으로 500행씩 읽어 규칙 마스크를 다시 계산하고 판을 기록합니다. 마스크가 같은 행은 판만 씁니다. 로그 한 줄이 출처마다 남습니다.

   ```
   Keyword mask recompute source=TOUR_API version=r1-k........-t........ read=19949 maskChanged=…
   Keyword mask recompute source=WIKIDATA version=r1-k........-w........ read=7583 maskChanged=…
   ```

   `maskChanged`가 0이 아니면 코드표를 고친 뒤 소개 재계산을 돌리지 않았던 행입니다. 두 번째 시작부터는 읽는 행이 0이라 로그가 없습니다. 실제 MySQL에서 걸리는 시간은 재지 않았습니다(어림 수 초).

### 1) (선택) DB 설명문 다시 꺼내기

새로 받은 설명문까지 예측하려면 추출본을 다시 만듭니다. `docker exec`로 DB를 읽고 `.env`의 DB 비밀번호를 씁니다. 추출본을 바꾸면 라벨 검증(`prepare_data.py`)이 본문 차이로 멈출 수 있으니 학습 데이터와 따로 생각합니다([README](../training/keyword_tagger/README.md#실행) 0)).

```bash
cd training/keyword_tagger && python3 -I scripts/export_from_db.py && cd ../..
```

### 2) 예측 파일 만들기

```bash
cd training/keyword_tagger
# CPU (기본). 모델 폴더 이름이 그대로 tagger_model 이 된다
.venv/bin/python -I scripts/predict_all.py --model-dir outputs/roberta-base-v3
# GPU가 비어 있으면
.venv/bin/python -I scripts/predict_all.py --model-dir outputs/roberta-base-v3 --device mps
cd ../..
```

- 결과: `training/keyword_tagger/data/predicted_keywords_roberta-base-v3.jsonl`(한 줄에 `source`, `id`, `keywords`, `scores`, `model`, `info_crc32`)과 `…summary.json`. `data/`는 커밋되지 않습니다. 두 파일 모두 설명문 원문은 담지 않습니다.
- 화면에 출처별 요약이 나옵니다: `union 으로 바뀌는 행`(태거가 규칙에 없는 키워드를 하나 이상 더하는 행), `tagger 만 쓰면 다른 행`, 평균 키워드 수(규칙 / 태거 / union). `summary.json`에는 키워드별로 태거가 더하는 수(`added_by_tagger`)와 규칙에만 있는 수(`rule_only_keywords`), 출처별 예시(id·이름·키워드)가 있습니다.
- 비교 기준(규칙)은 서버 `computeKeywordMask`의 파이썬 포트입니다. 2026-10-18 추출본 9,407행 전부에서 Java 계산과 마스크·CRC32가 모두 같았습니다(세계유산 플래그 없이). 추출본에 세계유산 플래그가 없어서 요약의 위키데이터 규칙에는 '유네스코' 규칙이 빠져 있습니다.
- 시간: CPU 2스레드로 40행에 약 2초였습니다. 전체(9,407행)는 어림으로 수 분입니다.
- 시험: `--limit 40`(출처별로 번갈아 40행), `--source TOUR_API`, `--no-scores`.

### 3) 관리자 토큰

```bash
ADMIN_PASSWORD=$(sed -n 's/^GLOPLE_ADMIN_PASSWORD=//p' .env)
TOKEN=$(curl -s -X POST http://localhost:8080/api/users/login -H "Content-Type: application/json" \
  -d "{\"userId\":\"admin\",\"password\":\"$ADMIN_PASSWORD\"}" | sed -E 's/.*"token":"([^"]+)".*/\1/')
```

### 4) 미리 세기 (dryRun) → 가져오기

```bash
PRED=training/keyword_tagger/data/predicted_keywords_roberta-base-v3.jsonl

# 쓰지 않고 세기만 한다
curl -s -X POST "http://localhost:8080/api/admin/keyword-masks/import?dryRun=true" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/x-ndjson" --data-binary @"$PRED"

# 실제로 가져온다 (한 트랜잭션, 수 초)
curl -s -X POST "http://localhost:8080/api/admin/keyword-masks/import" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/x-ndjson" --data-binary @"$PRED"
```

`-H "Content-Type: application/x-ndjson"`을 꼭 붙입니다(`--data-binary`의 기본 형식인 form은 415). `staleDescription`이 크면 추출본을 꺼낸 뒤 설명문 수집이 돌아 설명문이 바뀐 것이므로 1)부터 다시 합니다. `notFound`는 추출본 뒤에 지워졌거나 외부 id가 바뀐 여행지입니다.

### 5) 확인

```bash
# 같은 요청을 가져오기 전후로 저장해 비교한다 (seed를 고정해야 동점 순서가 같다)
curl -s -X POST http://localhost:8080/api/search -H "Content-Type: application/json" \
  -d '{"keywords":["역사적인 장소","경치"],"scope":"all","limit":20,"seed":7}' > /tmp/search_after.json
```

```sql
-- docker exec -it glople-mysql mysql -uroot -p travien
SELECT source, tagger_model, COUNT(*) AS rows_with_prediction, MAX(tagger_updated_at) AS imported_at
FROM destination_info WHERE tagger_model IS NOT NULL GROUP BY source, tagger_model;

SELECT source, keyword_mask_version, COUNT(*) FROM destination_info GROUP BY source, keyword_mask_version;
```

- 가져오기 전 결과는 같은 요청을 `--keyword.recommend.mask-mode=rule`로 띄운 서버에서 받아 비교할 수도 있습니다.
- 응답 칸 이름·순서 규칙은 그대로이므로 화면(`keyword.js`)은 고칠 것이 없습니다.

### 되돌리기

- 추천만 되돌리기: `keyword.recommend.mask-mode=rule`로 다시 띄웁니다. DB는 그대로입니다.
- 예측 지우기(오너가 DB에서 직접): `UPDATE destination_info SET tagger_mask_low=0, tagger_mask_high=0, tagger_model=NULL, tagger_info_crc32=NULL, tagger_updated_at=NULL WHERE tagger_model='roberta-base-v3';`
- 다른 모델로 바꾸기: 그 모델로 2)·4)를 다시 하면 같은 행의 예측을 덮어씁니다. 새 파일에 없는 행의 예전 예측은 남습니다.

## `POST /api/admin/keyword-masks/import`

관리자(`ROLE_ADMIN`) 전용. [authorization.md](authorization.md)의 K01입니다.

| 항목 | 값 |
| :-- | :-- |
| 본문 형식 | `application/x-ndjson`(JSON Lines, 한 줄에 객체 하나), `application/json`(같은 객체의 배열), `text/plain`(JSON Lines). 다른 형식은 415 |
| 파라미터 | `dryRun`(기본 `false`, `true`면 세기만), `model`(항목에 `model`이 없을 때 쓸 이름) |
| 한 번에 | 20,000항목까지 |

항목:

| 칸 | 필수 | 뜻 |
| :-- | :-- | :-- |
| `source` | O | `TOUR_API` 또는 `WIKIDATA` |
| `externalId` (또는 `id`, `external_id`) | O | `destination_info.external_id` (TourAPI contentid, 위키데이터 QID). 숫자로 써도 됨 |
| `keywords` | O | `KeywordMask` 목록의 키워드(철자 그대로) 배열. `[]`는 "맞는 키워드 없음"이라는 예측으로 저장 |
| `model` | 항목이나 파라미터 중 하나 | 영문·숫자·`.`·`_`·`-` 100자 이내 |
| `infoCrc32` (또는 `info_crc32`) | - | 예측에 쓴 설명문의 CRC32. 지금 설명문과 다르면 그 행은 가져오지 않음. 없으면 지금 설명문 기준으로 저장 |

`scores` 같은 다른 칸은 무시합니다. 그래서 `predict_all.py` 출력을 고치지 않고 보냅니다.

처리 규칙:

1. 먼저 모든 항목을 검사합니다. 하나라도 틀리면 **아무것도 쓰지 않고** 400입니다: JSON이 아님, 객체가 아님, `source` 모름, id 없음, 목록에 없는 키워드, `model` 없음·형식 틀림, `infoCrc32`가 0~4294967295 정수가 아님, 같은 `(source, externalId)`가 두 번.
2. `(source, external_id)`가 같은 여행지 행을 500개씩 찾아 태거 열만 씁니다(같은 외부 id 행이 여럿이면 모두). 새 여행지 행은 만들지 않고, 규칙 마스크는 바꾸지 않습니다.
3. 행의 설명문이 없거나 CRC32가 다르면 `staleDescription`으로 세고 건너뜁니다. 같은 모델·키워드·설명문으로 이미 저장된 행은 `unchanged`입니다.
4. 국내·해외 동기화와 같은 잠금을 씁니다(`TourSyncRunner.runExclusively`). 동기화가 도는 중이면 409이고, 가져오는 동안에는 동기화를 시작할 수 없습니다(동기화는 읽어 둔 행을 통째로 다시 저장하므로 동시에 돌면 한쪽 값이 덮일 수 있음).

응답 (200):

```json
{"dryRun":false,"received":9407,"updated":9380,"unchanged":0,"notFound":20,"staleDescription":7,
 "models":["roberta-base-v3"],"notFoundSamples":["TOUR_API:2861234"],"staleSamples":["WIKIDATA:Q42"],"recommendMaskMode":"union"}
```

(숫자는 모양을 보이기 위한 예입니다.) `updated`·`unchanged`·`staleDescription`은 여행지 행 수, `received`·`notFound`는 항목 수입니다. 예시 목록은 10개까지입니다.

오류:

| 상태 | 언제 |
| :-- | :-- |
| `400` | 항목 형식 오류. 본문은 [authorization.md](authorization.md#오류-응답)와 같은 JSON에 `errors`(칸별 사유, 앞의 20건)를 더한 것. 칸 이름은 JSON Lines면 `lines[3].keywords[0]`(줄 번호, 1부터), 배열이면 `rows[0].source`(0부터). `dryRun=maybe`처럼 파라미터 형식이 틀려도 같은 JSON |
| `401` / `403` | 토큰 없음·만료 / 관리자가 아님 |
| `409` | 다른 동기화가 진행 중 (같은 JSON 모양) |
| `415` | 본문 형식(Content-Type)이 위 셋이 아님 |

```json
{"status":400,"error":"BAD_REQUEST","message":"태거 예측 1곳이 올바르지 않아 아무것도 가져오지 않았습니다.",
 "path":"/api/admin/keyword-masks/import",
 "errors":[{"field":"lines[3].keywords[0]","code":"unknown_keyword","message":"서버 키워드 목록(KeywordMask)에 없는 키워드입니다: '바닷가'"}]}
```

## 규칙 판과 시작 시 다시 계산

[revamp-todo](revamp-todo.md)의 "키워드 마스크 재계산 범위 결함(0인 마스크만 백필)"을 고친 부분입니다.

- 판 = `r<RULE_REVISION>-k<키워드·조사 지문>` + 국내는 `-t<분류 코드표 지문>`, 위키데이터는 `-w<유형 코드표 지문>`([`KeywordMaskVersion`](../src/main/java/proj/travien/service/KeywordMaskVersion.java)). 지문은 CRC32 16진수 8자리입니다.
  - 키워드 목록(순서 포함)·조사 목록·코드표의 코드별 키워드가 바뀌면 지문이 바뀌어 자동으로 다시 계산됩니다. 코드표의 분류명만 바꾸면 판은 그대로입니다.
  - 계산 코드(`KeywordMask`의 매칭 방식, `DestinationInfo.computeKeywordMask`)를 바꾸면 `KeywordMaskVersion.RULE_REVISION`을 올립니다.
- 시작 시 마스크 보정(`DestinationKeywordMaskBackfill`)
  1. 국내·위키데이터 행: 판이 NULL이거나 지금 판과 다른 행만 id 순 500행씩 읽어 다시 계산합니다. 엔티티를 통째로 저장하지 않고 마스크·판 열만 바꾸는 UPDATE를 씁니다. 그사이 다른 저장이 이미 지금 판을 기록했으면 쓰지 않습니다. 태거 열은 건드리지 않습니다.
  2. 출처가 없는 기존 해외 행: 예전처럼 마스크가 0이고 설명문이 있는 행만 채웁니다. 원래 데이터의 마스크를 덮어쓰지 않으려고 판으로 다시 계산하지 않습니다.
- 코드표를 고친 뒤 소개(`summary`) 글까지 바꾸려면 예전처럼 `POST /api/admin/tour-sync/summaries`·`/wiki-sync/summaries`를 실행합니다. 마스크만이면 다시 띄우는 것으로 충분합니다.
- 코드표 형식이 틀리면 판을 계산할 때 예외가 나서 서버가 시작 단계에서 멈춥니다(예전에는 분류 코드표만 시작 때 읽었고, 이제 유형 코드표도 읽습니다).

## 알아 둘 점

- 예측은 설명문 단위입니다. 가져온 뒤 새로 설명문을 받은 행, 설명문이 바뀐 행은 다음 예측·가져오기 전까지 규칙 마스크만 씁니다. 제목만 바뀐 것은 감지하지 않습니다.
- 2026-10-17 추출본에서 국내 1,828행은 전부, 해외는 800행이 AI(silver) 라벨이 있는 행입니다(1차 977 + 2차 851 + 3차 800). 이 행에도 모델 예측을 씁니다(train·valid에 쓴 행은 예측이 라벨에 가깝습니다). 라벨을 대신 쓰는 선택지는 만들지 않았습니다.
- 모델 입력에는 위키데이터 유형(`category`)이 들어가지 않습니다. `union`에서는 유형 코드표 키워드가 규칙 쪽에 남아 있지만, `tagger`에서는 빠집니다.
- 추천 쿼리(native SQL)는 DB 없는 테스트에서 문자열만 확인했습니다. 실제 MySQL에서 결과가 맞는지는 위 5)로 오너가 확인해야 합니다.
- 예측 파일과 DB의 태거 키워드는 TourAPI·한국어 위키백과(CC BY-SA 4.0) 설명문으로 만든 것입니다. 공개 범위는 [README](../training/keyword_tagger/README.md#사람이-확인해야-할-것) 9번과 같이 판단해야 합니다.

## 관련 코드·테스트

- [`DestinationInfo.java`](../src/main/java/proj/travien/domain/DestinationInfo.java): 새 열, `refreshKeywordMask`(판 기록, 설명문이 바뀐 행의 예측 삭제), `applyTaggerPrediction`, `infoCrc32`
- [`KeywordMaskMode.java`](../src/main/java/proj/travien/service/KeywordMaskMode.java), [`KeywordRecommendService.java`](../src/main/java/proj/travien/service/KeywordRecommendService.java), [`DestinationInfoRepository.java`](../src/main/java/proj/travien/repository/DestinationInfoRepository.java)(`findKeywordCandidates`, `RECOMMEND_MASK_*_SQL`)
- [`KeywordTaggerImportService.java`](../src/main/java/proj/travien/service/KeywordTaggerImportService.java), [`KeywordMaskController.java`](../src/main/java/proj/travien/controller/KeywordMaskController.java), [`TourSyncRunner.runExclusively`](../src/main/java/proj/travien/service/TourSyncRunner.java)
- [`KeywordMaskVersion.java`](../src/main/java/proj/travien/service/KeywordMaskVersion.java), [`DestinationKeywordMaskBackfill.java`](../src/main/java/proj/travien/service/DestinationKeywordMaskBackfill.java)
- [`predict_all.py`](../training/keyword_tagger/scripts/predict_all.py), [`predict.py`](../training/keyword_tagger/scripts/predict.py)(`load_tagger`, `predict_probs`), [`kt_common.py`](../training/keyword_tagger/scripts/kt_common.py)(`load_tour_category_table`, `domestic_rule_predict`)
- 테스트(모두 DB 없이): `KeywordTaggerImportServiceTest`, `KeywordMaskControllerTest`(실제 SecurityConfig), `DestinationKeywordMaskTest`(판·재계산·태거 열·CRC32), `KeywordRecommendServiceTest`(모드), `DestinationInfoRepositoryQueryTest`(쿼리 문자열·파라미터), `JpaMappingWithoutDatabaseTest`(새 열 DDL), `TourSyncRunnerTest`(잠금), `AuthorizationMatrixTest`

```bash
sh gradlew test --offline --console=plain \
  --tests "proj.travien.service.Keyword*" \
  --tests "proj.travien.service.DestinationKeywordMaskTest" \
  --tests "proj.travien.service.TourSyncRunnerTest" \
  --tests "proj.travien.controller.KeywordMaskControllerTest" \
  --tests "proj.travien.controller.AuthorizationMatrixTest" \
  --tests "proj.travien.repository.DestinationInfoRepositoryQueryTest" \
  --tests "proj.travien.repository.JpaMappingWithoutDatabaseTest"
```

> `proj.travien.repository.*`처럼 패키지 전체를 고르면 실제 DB에 붙는 `UserRepositoryTest`가 같이 돕니다. 위처럼 클래스를 지정합니다.

## 어떤 마스크를 쓸까 (2026-10-18, roberta-base-v3)

학습에 쓰지 않은 시험지에서 서버의 실제 규칙(국내: 설명문 규칙 ∪ 분류 코드표, 해외: 한국어 규칙 ∪ 유형 코드표)과 비교했습니다.

| 시험지 | rule F1 (정밀도) | tagger F1 (정밀도) | union F1 (정밀도) |
| :-- | --: | --: | --: |
| 국내 test 146 | 0.580 (0.589) | **0.744 (0.723)** | 0.703 (0.584) |
| 국내 test_new 127 | 0.639 (0.655) | **0.742 (0.762)** | 0.729 (0.630) |
| 해외 test_overseas 120 | 0.701 (0.697) | 0.728 **(0.732)** | **0.739** (0.636) |

- `tagger`가 국내 F1과 세 시험지 모두의 정밀도에서 가장 높습니다.
- `union`은 재현율이 가장 높지만(0.867~0.883), 규칙의 오탐을 그대로 남깁니다. 실제로 '모스크'를 고르면 러시아의 차르의 종, 차르 캐넌이 나왔습니다.
- 추천은 상위 21개만 보여 주므로 정밀도를 앞에 두어 기본값을 `tagger`로 정했습니다.
- 실제 반영 결과: 9,407행을 가져왔습니다(국내 1,781행, 해외 7,390행에 태거 마스크). 반영 전 DB 백업은 `../glople-backups/travien-20261018-1447-before-tagger.sql.gz`입니다.

## 실제로 적용한 것: 키워드별 섞어 쓰기 (2026-10-18)

`tagger`만 쓰면 태거가 거의 예측하지 않는 드문 키워드(예: 모스크)는 추천 결과가 0개가 됐습니다. 그래서 `scripts/hybrid_masks.py`로 키워드마다 규칙·태거·합집합 중 하나를 골라 그 결과를 '태거 마스크'로 가져왔습니다. 서버는 `mask-mode=tagger`(기본) 그대로 이 마스크를 씁니다.

- **고르는 기준:** valid 분할의 키워드별 F1입니다. 정답이 3개 미만이거나 태거가 전체에서 한 번도 예측하지 않은 키워드는 규칙을 씁니다.
- **고른 결과:** 태거 29개, 규칙 61개, 합집합 4개(`results/roberta-base-v3.hybrid_choice.json`).
- **학습에 쓰지 않은 시험지 결과:**

| 시험지 | rule | tagger | union | **hybrid** (정밀도 / 재현율) |
| :-- | --: | --: | --: | --: |
| 국내 test 146 | 0.580 | 0.744 | 0.703 | **0.745** (0.670 / 0.837) |
| 국내 test_new 127 | 0.639 | 0.742 | 0.729 | **0.777** (0.720 / 0.844) |
| 해외 test_overseas 120 | 0.701 | 0.728 | 0.739 | **0.774** (0.710 / 0.850) |

- **같이 고친 규칙 오탐:** 위키데이터 설명문의 '모스크바' 속 '모스크' 때문에 크렘린의 차르의 종·차르 캐넌이 모스크로 추천됐습니다. `DestinationInfo.MISLEADING_WORDS`에 '모스크바'를 더했고(파이썬 `kt_common.MISLEADING_WORDS`도 같음), 규칙 판을 `r2`로 올렸습니다. 서버를 다시 띄우면 모든 행의 규칙 마스크가 다시 계산됩니다.
- **실행 순서:**
  1. `predict_all.py --model-dir outputs/roberta-base-v3`
  2. `hybrid_masks.py --run roberta-base-v3`
  3. `data/predicted_keywords_roberta-base-v3-hybrid.jsonl`을 가져오기 API로 보냅니다.
- **반영 결과:** 국내 1,797행, 해외 7,473행에 태거 마스크가 있습니다(모델 이름 `roberta-base-v3-rule-hybrid`).
- **남은 오탐:** 파르테논 신전, 예수승천기념경당처럼 설명문에 실제로 '모스크'가 나오는 곳(역사 중에 모스크로 쓰임)은 아직 '모스크'로 나옵니다.
