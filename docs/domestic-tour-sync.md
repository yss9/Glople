# 국내 여행지 데이터 수집 (개편)

> 해외 여행지만 다루던 Glople에 국내 여행지를 더하는 개인 개편 작업의 1단계입니다.
> 한국관광공사 국문 관광정보 서비스(TourAPI `KorService2`)에서 여행지를 받아 기존 `destination_info` 테이블에 함께 저장합니다.

## 동작 방식

| 단계 | API | 하는 일 | 호출 수 |
| :-- | :-- | :-- | :-- |
| 목록 동기화 | `areaBasedList2` | 관광 타입별로 여행지 목록을 1,000건씩 받아 새 여행지는 추가하고, `modifiedtime`이 바뀐 여행지는 갱신 | 타입별 (총 건수 ÷ 1,000)회 |
| 설명문 동기화 | `detailCommon2` | 설명문(`overview`)이 아직 없는 여행지를 골라 HTML을 걷어낸 뒤 `info`에 저장. 수집에 실패한 적 없는 여행지를 id 순으로 먼저, 실패했던 여행지는 그 뒤에 오래전에 실패한 순으로 고름 | 여행지 1곳당 1회 |
| 소개 재계산 | 없음 | 저장된 국내 여행지 전체의 분류 소개(`summary`)와 키워드 마스크를 1,000건씩 다시 계산. [분류 소개](#분류-소개-summary) 참고 | 0회 |

- 국내 여행지는 `source = TOUR_API`, `country = 대한민국`, `location = 시도 시군구`(주소 앞부분)로 저장합니다. 기존 해외 여행지 행은 바뀌지 않습니다.
- 설명문이 저장되면 기존 `@PreUpdate`가 키워드 마스크를 다시 계산하므로, 국내 여행지도 키워드 추천(`/api/search`) 결과에 바로 나옵니다. 설명문이 아직 없는 여행지도 분류 코드에 정해 둔 키워드로 추천됩니다([분류 소개](#분류-소개-summary)).
- 국내 설명문의 마스크는 `KeywordMask.fromKoreanDescription`으로 계산합니다. 두 글자 이상 키워드는 기존처럼 부분 문자열로 찾지만, 한 글자 키워드(`산`·`섬`·`성`·`탑`·`강`)는 조사만 붙은 독립된 단어일 때만 인정합니다. 그래서 '부산·울산'의 `산`, '강원·강릉'의 `강`, '조성·구성'의 `성`, '문화유산'의 `산`은 켜지지 않습니다. 대신 '한라산', '한강'처럼 이름에 붙은 경우도 잡지 못하므로, 이런 여행지는 설명문에 들어 있는 다른 키워드(`등산`, `국립공원` 등)로 찾힙니다. 해외 여행지는 기존 방식(부분 문자열) 그대로입니다.
- 원본의 `modifiedtime`이 바뀌면 설명문 수집 기록(`overview_synced_at`, `overview_failed_at`)을 비워 다음 설명문 동기화 때 다시 받습니다. `modifiedtime`이 비어 있는 여행지는 저장된 값(null)과 같은 것으로 보고 갱신하지 않습니다.
- 여행지 한 곳의 설명문 수집이 실패하면(TourAPI 오류 응답, 해석할 수 없는 본문, 저장 오류 등) 그 시각을 `overview_failed_at`에 남깁니다. 계속 실패하는 여행지가 대기열 앞을 막지 않도록, 다음 실행은 아직 시도하지 않은 여행지부터 받습니다. 한 실행에서 5회 연속 실패하면 키 오류 같은 전체 장애로 보고 그 실행을 멈춥니다.
- TourAPI에서 삭제 예정인 `areacode`·`sigungucode`·`cat1~3` 대신 법정동 코드(`lDongRegnCd`, `lDongSignguCd`)와 분류체계 코드(`lclsSystm3`)를 저장합니다.
- 저작권 유형(`cpyrhtDivCd`: Type1 출처표시 / Type3 출처표시+변경금지)을 `copyright_type`에 함께 저장합니다. 데이터를 화면에 보여주거나 외부에 공개할 때 이 값을 기준으로 출처를 표시합니다.

### 호출 한도

개발계정은 일일 호출 한도가 있어서 설명문을 한 번에 모두 받을 수 없습니다. 설명문 동기화는 기본 900건씩 실행하고, 한도에 걸리면 그때까지 받은 결과를 저장한 채 멈춥니다(`stoppedByQuota: true`). 다음 날 같은 요청을 다시 보내면 남은 여행지부터 이어 받습니다. 매일 자동으로 이어 받으려면 아래 `tour.sync.overview-cron`을 설정합니다.

## 분류 소개 (summary)

설명문(`overview`)은 하루 1,000건 한도 때문에 2026-10-17 기준 19,949곳 중 950곳에만 있습니다. 나머지 여행지도 추천에 쓰고 결과에 무언가 보여줄 수 있도록, 목록 동기화 때 받은 TourAPI 필드만으로 짧은 소개를 만들어 `summary` 컬럼(`VARCHAR(500)`)에 저장합니다.

```text
부산광역시 해운대구의 관광지입니다. 분류: 자연관광 > 자연경관(하천‧해양) > 해변. 해수욕장
```

- **TourAPI 값만 씁니다.** `location`(주소 앞부분), `content_type_id`(12 관광지 · 14 문화시설 · 15 축제공연행사 · 28 레포츠, 그 밖은 '여행지'), `category`(`lclsSystm3`)와 그 코드의 분류명(대 > 중 > 소)을 정해진 틀에 넣을 뿐이고, AI가 쓴 문장이나 지어낸 설명은 들어가지 않습니다.
- 앞말에 따라 형태가 바뀌는 조사(은/는, 이/가 등)를 피해 `의`와 `입니다`만 씁니다. 지역이 없으면 지역 부분을, 코드표에 없는 분류면 `분류:` 부분을 뺍니다. 담을 사실이 하나도 없으면 `null`입니다.
- 실제 설명문은 지금처럼 `info`에만 들어가고, 소개는 따로 저장되어 설명문을 덮어쓰지 않습니다. 키워드 추천 결과의 `info`는 `COALESCE(info, summary)`라서 **실제 설명문이 있으면 설명문이**, 없을 때만 소개가 나옵니다.
- 목록 동기화에서 새로 추가되거나 `modifiedtime`이 바뀐 여행지는 그때 소개를 만듭니다. 이미 저장된 여행지는 아래 [소개 재계산](#실행)을 한 번 실행합니다.

### 키워드 마스크

국내 여행지(`source = TOUR_API`)의 키워드 마스크는 **설명문 키워드(`fromKoreanDescription(info)`) ∪ 분류 코드표의 키워드**입니다. 해외 여행지는 기존 규칙(`fromDescription(info)`) 그대로입니다.

소개 문장 자체는 다시 훑지 않습니다. 소개에는 분류명과 지명이 그대로 들어가서, 훑으면 코드표에서 일부러 뺀 키워드가 켜집니다. 예를 들어 '문화관광'의 `문화`는 시민공원·다리·공항 같은 문화관광 분류 전체에, '역사관광'의 `역사`는 기독교 성지에, '자연경관(산)'의 `산`은 계곡·폭포에, '축제/공연/행사'의 `축제`·`공연`은 박람회·스포츠경기에, '부산광역시 수영구'의 `수영`은 수영구의 모든 여행지에 켜집니다. 그래서 분류에서 나오는 키워드는 코드표 한 곳에서만 정합니다.

2026-10-17 데이터 기준으로 19,949곳 중 15,783곳은 분류만으로 키워드가 생기고, 4,166곳은 키워드를 비워 둔 분류(시민공원 605 · 기타체험관광 539 · 전시관 529 · 주제공원 486 · 골목길·문화거리 311 등)라서 설명문을 받기 전까지 추천에 나오지 않습니다.

### 분류 → 키워드 코드표

[`src/main/resources/tour/category-keywords.csv`](../src/main/resources/tour/category-keywords.csv)

| 열 | 내용 |
| :-- | :-- |
| `code` | 분류체계 3Depth 코드 (`lclsSystm3`) |
| `depth1`·`depth2`·`depth3` | TourAPI 분류명 그대로 (쉼표가 들어간 이름은 큰따옴표로 감쌈) |
| `keywords` | 이 분류에 붙는 추천 키워드를 `\|`로 이어 적음. 없으면 빈 칸 |

- 원본은 TourAPI `lclsSystmCode2`(분류체계 코드 조회) 2026-10-17 응답의 246개 코드 전체입니다. 181개 코드에 키워드가 있고, 65개는 맞는 키워드가 없어 비워 두었습니다(호텔·카페·박람회·시민공원 등).
- 키워드는 `KeywordMask`의 키워드 목록에 있는 값만 쓸 수 있습니다. 목록에 없는 키워드, 열 수가 틀린 행, 중복 코드가 있으면 조용히 건너뛰지 않고 `TourCategoriesTest`가 실패하며, 서버도 시작 단계(시작 시 마스크 보정이 이 파일을 읽음)에서 몇 번째 줄이 틀렸는지 알리는 예외로 멈춥니다.
- **만든 방법**: 분류명을 보고 AI가 대분류 묶음별로 키워드 초안을 만들고, 다른 AI 검토자가 묶음마다 교차 확인해 고친 결과입니다. 사람이 하나씩 검토하지는 않았으므로, 추천 결과가 이상하면 이 파일부터 확인하고 사람이 한 번 훑어보는 것을 권장합니다.
- 파일을 고친 뒤에는 서버를 다시 띄우고 [소개 재계산](#실행)을 실행해야 저장된 마스크에 반영됩니다.

### TourAPI 분류 코드가 바뀌었을 때

1. `lclsSystmCode2`를 전체 목록 조회(`lclsSystmListYn=Y`, `numOfRows`는 코드 수보다 크게)로 한 번 불러 코드 목록을 받습니다(호출 1회, 목록 동기화와 같은 인증키).
2. 새로 생긴 코드는 행을 추가하고 키워드를 정합니다. 없어진 코드는 지우고, 이름이 바뀐 코드는 `depth1~3`을 고칩니다.
3. `TourCategoriesTest`의 전체 개수(246)와 대분류별 개수를 새 목록에 맞게 고치고 테스트를 돌립니다.
4. 서버를 다시 띄운 뒤 `POST /api/admin/tour-sync/summaries`로 소개와 마스크를 다시 계산합니다. 코드표에 없는 분류 코드를 가진 여행지는 `분류:` 부분과 분류 키워드 없이 저장됩니다.

## 설정

`src/main/resources/application.properties`(gitignore 대상)에 추가합니다.

```properties
# 공공데이터포털에서 발급받은 일반 인증키. Encoding/Decoding 키 모두 사용 가능
tour.api.service-key=<TourAPI 인증키>

# --- 아래는 선택 항목 (적힌 값이 기본값) ---
# 수집할 관광 타입: 관광지, 문화시설, 축제공연행사, 레포츠 (32 숙박, 39 음식점 등 추가 가능)
tour.sync.content-type-ids=12,14,15,28
# 예약 실행 시 하루에 받을 설명문 수
tour.sync.overview-daily-limit=900
# 설명문 예약 실행 시각. "-"는 꺼짐, 예: 0 0 4 * * * (매일 04:00)
tour.sync.overview-cron=-
```

인증키는 [공공데이터포털 - 한국관광공사_국문 관광정보 서비스_GW](https://www.data.go.kr/data/15101578/openapi.do)에서 활용신청 후 발급받습니다. 키가 없어도 서버는 정상적으로 뜨고, 동기화 API만 `503`을 돌려줍니다.

### 기존 DB를 쓰는 경우

`ddl-auto=update`는 새 컬럼은 추가하지만 기존 컬럼의 타입은 바꾸지 않습니다. 이전에 만들어진 DB라면 설명문이 255자를 넘을 수 있도록 한 번만 바꿔 둡니다.

```sql
ALTER TABLE destination_info MODIFY info TEXT;
```

`summary` 컬럼은 서버를 띄울 때 `ddl-auto=update`가 추가합니다(기존 행은 `NULL`). 이어서 시작 시 마스크 보정(`DestinationKeywordMaskBackfill`)이 마스크가 0인 행 중 설명문이 있거나 키워드가 있는 분류 코드를 가진 행의 마스크를 채웁니다. 설명문도 없고 키워드도 없는 분류인 행은 계산해도 0이므로 매번 다시 읽지 않습니다. 소개는 시작할 때 만들지 않으니, 컬럼이 생긴 뒤 아래 `summaries`를 한 번 실행합니다. 실행 전까지는 분류 키워드로 새로 추천되는 국내 여행지의 추천 결과 `info`가 비어(`null`) 나옵니다.

## 실행

모두 관리자 전용 API(`/api/admin/**`)입니다. 관리자 계정 `admin`으로 로그인해 받은 토큰을 사용합니다. 관리자 비밀번호는 `.env`의 `GLOPLE_ADMIN_PASSWORD`이고, 서버가 시작할 때 `admin` 계정을 이 값에 맞춥니다(값이 없으면 계정을 만들지 않음). 아래 명령은 프로젝트 루트에서 `.env`의 값을 읽어 씁니다.

```bash
ADMIN_PASSWORD=$(sed -n 's/^GLOPLE_ADMIN_PASSWORD=//p' .env)
TOKEN=$(curl -s -X POST http://localhost:8080/api/users/login \
  -H "Content-Type: application/json" \
  -d "{\"userId\":\"admin\",\"password\":\"$ADMIN_PASSWORD\"}" | sed -E 's/.*"token":"([^"]+)".*/\1/')

# 1) 목록 동기화 (백그라운드 실행, 202 Accepted)
curl -X POST http://localhost:8080/api/admin/tour-sync/destinations -H "Authorization: Bearer $TOKEN"

# 2) 진행 상태 확인
curl http://localhost:8080/api/admin/tour-sync/status -H "Authorization: Bearer $TOKEN"

# 3) 설명문 동기화 (limit 기본 900)
curl -X POST "http://localhost:8080/api/admin/tour-sync/overviews?limit=900" -H "Authorization: Bearer $TOKEN"

# 4) 소개 재계산 (TourAPI 호출 없음, 인증키 없어도 실행)
curl -X POST http://localhost:8080/api/admin/tour-sync/summaries -H "Authorization: Bearer $TOKEN"
```

| 응답 | 의미 |
| :-- | :-- |
| `202` | 동기화를 시작함. 본문은 현재 상태 |
| `400` | `limit`이 1~10,000 범위를 벗어남 |
| `401` | 토큰이 없거나 만료·위조됨 (응답 JSON은 [authorization.md](authorization.md#오류-응답)) |
| `403` | 로그인했지만 관리자(`ROLE_ADMIN`)가 아님 |
| `409` | 다른 동기화가 이미 진행 중 (소개 재계산도 같은 행을 저장하므로 다른 작업과 동시에 돌지 않음) |
| `503` | `tour.api.service-key`가 설정되지 않음 (`summaries`는 TourAPI를 부르지 않아 해당 없음) |

상태 응답의 `lastResult`는 `processed`(처리 건수), `inserted`, `updated`, `failed`, `stoppedByQuota`를, 실패했다면 `lastError`에 원인을 담습니다. 소개 재계산(`task: SUMMARIES`)의 `processed`는 읽은 국내 여행지 수, `updated`는 소개나 마스크가 바뀌어 저장한 수입니다. 바뀐 것이 없는 행은 저장하지 않으므로 두 번째 실행부터는 `updated`가 0에 가깝습니다.

## 로컬 수집 환경 (2026-10-17 구성)

실제 TourAPI 데이터는 로컬 MySQL에 쌓고, 학습 데이터는 이 DB에서 꺼내 씁니다.

| 항목 | 값 |
| :-- | :-- |
| MySQL | 도커 컨테이너 `glople-mysql` (mysql 8.4, 포트 3307, DB `travien`). 데이터는 볼륨 `glople-mysql-data`에 남아서 컨테이너를 멈췄다 켜도 유지됩니다 |
| 비밀값 | 프로젝트 루트 `.env` (gitignore, 키 목록은 [`.env.example`](../.env.example)): `TOUR_API_SERVICE_KEY`, `GLOPLE_DB_PASSWORD`, `GLOPLE_JWT_SECRET`, `GLOPLE_ADMIN_PASSWORD` |
| 관리자 계정 | `admin`. 비밀번호는 `.env`의 `GLOPLE_ADMIN_PASSWORD`이며, 서버를 시작할 때 저장된 비밀번호가 이 값과 다르면 바꿉니다 |
| 서버 설정 | `src/main/resources/application.properties` (gitignore). `spring.config.import=optional:file:.env[.properties]`로 `.env` 값을 읽습니다 |

인증키는 포털의 64자리 키를 씁니다. 같은 화면의 예전 88자리 키(Encoding/Decoding)는 2026-10-17 기준 `SERVICE_KEY_IS_NOT_REGISTERED_ERROR`(30)로 거절됐습니다.

### 날마다 설명문 이어 받기

`detailCommon2`는 하루 1,000건 한도라서 남은 설명문은 매일 나눠 받습니다. 한도를 늘리려면 포털에서 운영계정을 신청합니다.

```bash
docker start glople-mysql
sh gradlew bootRun        # 프로젝트 루트에서, 다른 터미널
```

```bash
ADMIN_PASSWORD=$(sed -n 's/^GLOPLE_ADMIN_PASSWORD=//p' .env)   # 프로젝트 루트에서
TOKEN=$(curl -s -X POST http://localhost:8080/api/users/login -H "Content-Type: application/json" \
  -d "{\"userId\":\"admin\",\"password\":\"$ADMIN_PASSWORD\"}" | sed -E 's/.*"token":"([^"]+)".*/\1/')
curl -X POST "http://localhost:8080/api/admin/tour-sync/overviews?limit=950" -H "Authorization: Bearer $TOKEN"
curl http://localhost:8080/api/admin/tour-sync/status -H "Authorization: Bearer $TOKEN"
```

진행률은 DB에서 확인합니다 (`docker exec -it glople-mysql mysql --default-character-set=utf8mb4 -uroot -p travien`). 문자셋 옵션을 빼면 한글이 `???`로 보이지만 저장된 값은 정상입니다.

```sql
SELECT COUNT(*) total, SUM(overview_synced_at IS NOT NULL) synced, SUM(info IS NOT NULL) with_info
FROM destination_info WHERE source = 'TOUR_API';
```

### 수집 기록

| 날짜 | 목록 동기화 | 설명문 | 누적 설명문 |
| :-- | :-- | :-- | :-- |
| 2026-10-17 | 19,949건 추가 (관광지 12,554 · 문화시설 2,768 · 축제 890 · 레포츠 3,737, 12초) | 950건 (실패 0, 평균 408자, 1분 34초) | 950 / 19,949 |

같은 날 `POST /api/admin/tour-sync/summaries`로 19,949건 전부에 분류 기반 소개를 만들었습니다(7초). 키워드 마스크가 있는 국내 여행지는 802건에서 15,954건으로 늘었습니다.

### 알아 둘 점

- 설명문은 대상 행을 id 순으로 고르기 때문에 첫날 950건은 모두 관광지이고, 이름이 'ㄱ'으로 시작하는 곳에 몰려 있습니다. 학습 데이터로 쓸 때는 유형·지역 분포를 확인합니다.

- `location`은 주소 앞부분이라 일부(약 100건)는 `경남 창원시`처럼 줄임말로 들어옵니다. 지역으로 묶을 때는 `region_code`(법정동 시도 코드)를 씁니다.
- 2026년 행정구역 개편으로 `전남광주통합특별시`처럼 새 시도명이 주소에 나옵니다.

## 관련 코드

- [`TourApiClient.java`](../src/main/java/proj/travien/service/TourApiClient.java): TourAPI 호출, 인증키 인코딩, 응답·오류 해석. 게이트웨이 오류는 XML 또는 `OpenAPI_ServiceResponse` JSON으로 오고, 미등록 키는 HTTP 403 + JSON(코드 30)으로 오는 것을 2026-10-17 실제 호출로 확인
- [`TourDestinationSyncService.java`](../src/main/java/proj/travien/service/TourDestinationSyncService.java): 목록 추가·갱신, 설명문 수집, 분류 소개 생성(`summaryOf`)과 재계산(`rebuildSummaries`)
- [`TourCategories.java`](../src/main/java/proj/travien/service/TourCategories.java): 분류 코드표([`category-keywords.csv`](../src/main/resources/tour/category-keywords.csv)) 읽기, 코드별 분류명·키워드 마스크
- [`DestinationKeywordMaskBackfill.java`](../src/main/java/proj/travien/service/DestinationKeywordMaskBackfill.java): 서버 시작 시 비어 있는 키워드 마스크 보정
- [`TourSyncRunner.java`](../src/main/java/proj/travien/service/TourSyncRunner.java): 백그라운드 단일 실행, 상태 보관, 예약 실행
- [`TourSyncController.java`](../src/main/java/proj/travien/controller/TourSyncController.java): 관리자 API
- [`KeywordMask.java`](../src/main/java/proj/travien/service/KeywordMask.java): 국내 설명문용 키워드 마스크(`fromKoreanDescription`), 마스크 합치기(`Mask.union`)
- [`JwtTokenProvider.java`](../src/main/java/proj/travien/jwt/JwtTokenProvider.java): 관리자 권한 판별. role claim(`ROLE_ADMIN`)에 접두사를 다시 붙여 `ROLE_ROLE_ADMIN`이 되던 문제와, 만료 토큰이 500을 내던 문제를 고쳤습니다.
- 테스트: [`TourApiClientTest`](../src/test/java/proj/travien/service/TourApiClientTest.java), [`TourDestinationSyncServiceTest`](../src/test/java/proj/travien/service/TourDestinationSyncServiceTest.java), [`TourSyncRunnerTest`](../src/test/java/proj/travien/service/TourSyncRunnerTest.java), [`KeywordMaskTest`](../src/test/java/proj/travien/service/KeywordMaskTest.java), [`TourCategoriesTest`](../src/test/java/proj/travien/service/TourCategoriesTest.java), [`DestinationKeywordMaskTest`](../src/test/java/proj/travien/service/DestinationKeywordMaskTest.java), [`TourSyncControllerTest`](../src/test/java/proj/travien/controller/TourSyncControllerTest.java), [`DestinationInfoRepositoryQueryTest`](../src/test/java/proj/travien/repository/DestinationInfoRepositoryQueryTest.java), [`JwtTokenProviderTest`](../src/test/java/proj/travien/jwt/JwtTokenProviderTest.java) (모두 DB 없이 실행)

```bash
sh gradlew test --tests "proj.travien.service.TourApiClientTest" \
               --tests "proj.travien.service.TourDestinationSyncServiceTest" \
               --tests "proj.travien.service.TourSyncRunnerTest" \
               --tests "proj.travien.service.KeywordMaskTest" \
               --tests "proj.travien.service.TourCategoriesTest" \
               --tests "proj.travien.service.DestinationKeywordMaskTest" \
               --tests "proj.travien.controller.TourSyncControllerTest" \
               --tests "proj.travien.repository.DestinationInfoRepositoryQueryTest" \
               --tests "proj.travien.jwt.JwtTokenProviderTest"
```
