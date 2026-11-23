# 여행지·여행 일정 API (개편)

> 화면과 LangGraph 에이전트(`agent/`)가 같이 쓰는 Spring API입니다. 에이전트 설계(`agent/docs/design.md` 8.4)의 S0·S1·S2 묶음과 프로필 비밀번호 확인을 다룹니다.
> 누가 쓸 수 있는지(공개·로그인·본인)는 [authorization.md](authorization.md)에 있습니다. 여기서는 요청·응답과 정렬·검사 규칙을 적습니다.

| 묶음 | 메서드 | 경로 | 접근 | 하는 일 |
| :-- | :-- | :-- | :-- | :-- |
| S0 | `POST` | `/api/search` | 공개 | 키워드 추천. 동점 정렬을 바꾸고 선택 값 `scope`·`limit`·`seed`를 더함 |
| S1 | `GET` | `/api/destinations` | 공개 | 지역·분류·검색어로 여행지 목록 |
| S1 | `GET` | `/api/destinations/lookup?name=` | 공개 | 이름으로 여행지 찾기 |
| S1 | `GET` | `/api/destinations/{id}` | 공개 | 여행지 상세(주소 포함) |
| S2 | `POST` | `/api/travel-plans` | 로그인 | 여행 일정 저장(같은 `clientRequestId`는 한 번만) |
| S2 | `GET` | `/api/travel-plans` | 본인 | 내 일정 목록 |
| S2 | `GET` | `/api/travel-plans/{id}` | 본인·관리자 | 일정 한 건 |
| S2 | `DELETE` | `/api/travel-plans/{id}` | 본인 | 일정 삭제 |
| - | `POST` | `/api/users/update-profile` | 본인 | 비밀번호를 바꿀 때 현재 비밀번호 확인 |

틀린 값은 모두 400이고, 401·403과 같은 모양의 JSON에 틀린 칸마다 `errors` 항목이 붙습니다.

```json
{"status":400,"error":"BAD_REQUEST","message":"여행지 조회 값이 올바르지 않습니다.","path":"/api/destinations",
 "errors":[{"field":"category","code":"unknown_value","message":"category는 관광지, 문화시설, 축제, 레포츠, 음식점, 숙박 중 하나여야 합니다."}]}
```

## S0 · 키워드 추천 동점 정렬 (`POST /api/search`)

예전 쿼리는 일치 키워드 수가 같으면 `id` 오름차순이었습니다. 국내 여행지는 TourAPI에서 관광 타입별·제목순으로 넣었기 때문에, 동점이면 언제나 '가'로 시작하는 국내 관광지 21곳이 나오고 해외 여행지는 나오지 않았습니다.

### 요청

`keywords`만 보내면 예전과 같은 요청(국내·해외 전체 21곳)입니다. 화면(`keyword.js`)은 바꾸지 않았습니다.

| 칸 | 값 | 기본값 |
| :-- | :-- | :-- |
| `keywords` | 키워드 목록. 서버 목록(`KeywordMask`)에 없는 키워드는 조용히 버림. 아는 키워드가 없으면 쿼리 없이 `[]` | - |
| `scope` | `all` · `domestic`(국내) · `overseas`(해외). 대소문자·앞뒤 공백 무시 | `all` |
| `limit` | 1~50 | 21 |
| `seed` | 동점 정렬에 쓰는 정수 | 오늘 날짜(Asia/Seoul) `yyyyMMdd`, 예: `20261018` |

- 국내 = `source = TOUR_API`이거나 `country = 대한민국`인 행입니다. 나머지(위키데이터 행, 출처 없는 기존 해외 행)는 해외입니다.
- `scope`가 세 값이 아니거나 `limit`가 범위 밖이면 400입니다. 고쳐 쓰지 않습니다.

### 순서

1. 일치 키워드 수(`BIT_COUNT`) 내림차순. 행의 마스크는 설정 `keyword.recommend.mask-mode`(기본 `union` = 규칙 마스크 ∪ 키워드 태거 예측)로 정합니다([keyword-tagger-serving.md](keyword-tagger-serving.md#추천에-쓰는-마스크-설정)). 태거 예측을 가져오기 전에는 예전과 같습니다.
2. 실제 설명문(`info`)이 있는 곳 먼저
3. 대표 이미지(`image_url`)가 있는 곳 먼저
4. `CRC32(CONCAT(id, ':', seed))` 오름차순 (MySQL 함수. Java 쪽 `DestinationOrdering.tieHash`가 같은 값을 계산)
5. `id` 오름차순 (해시가 같을 때만)

seed가 같으면 순서가 같습니다. 그래서 seed를 보내지 않으면 **같은 날에는 같은 결과**, 날이 바뀌면 동점 안의 순서가 바뀝니다. 에이전트는 대화마다 정한 seed를 보내 같은 대화 안에서 결과가 흔들리지 않게 할 수 있습니다.

### 지역 상한

한 지역이 자리를 모두 차지하지 못하게 합니다.

- 지역 = 국내는 시도(`location` 첫 단어, 예: `부산광역시`), 해외는 나라(`country`). 값이 없으면 `location` 첫 단어.
- 한 지역은 최대 `max(2, ⌈limit / 4⌉)`곳입니다. `limit` 21이면 6곳, 8이면 2곳, 50이면 13곳.
- 순위대로 고르다가 상한에 걸린 후보는 건너뜁니다. 다른 지역 후보가 모자라 자리가 남으면 건너뛴 후보로 **순위대로 채웁니다**. 그래서 후보가 한 지역뿐이면 그 지역으로 다 채워집니다.
- 결과는 고른 여행지를 원래 순위 순서로 돌려줍니다(채운 후보도 제자리).

### 처리 방식

1. 후보 쿼리(`findKeywordCandidates`)가 위 순서로 `limit × 4`곳(최대 200곳)을 고릅니다. 이 쿼리는 순위와 지역 판정에 필요한 열(id, 나라, location, source, 점수, 설명문·이미지 여부)만 읽고 설명문 본문은 읽지 않습니다.
2. Java가 같은 순서로 다시 정렬하고 지역 상한을 적용합니다. 순서 규칙을 DB 없이 시험하기 위해 Java에도 같은 규칙을 둡니다.
3. 고른 여행지만 `findAllById`로 읽어 응답으로 바꿉니다.

### 응답

예전 칸(`id`, `country`, `location`, `destName`, `info`, `source`, `copyrightType`, `sourceUrl`, `license`, `infoKind`)은 이름·뜻이 같습니다. `info`는 예전처럼 실제 설명문, 없으면 분류 소개(`summary`)입니다. 새로 더한 칸은 `imageUrl`, `countryCode`(위키데이터 행의 ISO 국가 코드), `contentTypeId`(TourAPI 관광 타입)입니다.

```bash
curl -s -X POST http://localhost:8080/api/search -H "Content-Type: application/json" \
  -d '{"keywords":["해변","섬"],"scope":"domestic","limit":5,"seed":7}'
```

```json
[{"id":1203,"country":"대한민국","location":"제주특별자치도 서귀포시","destName":"…","info":"…","source":"TOUR_API",
  "copyrightType":"Type3","sourceUrl":null,"license":null,"infoKind":"DESCRIPTION",
  "imageUrl":"http://tong.visitkorea.or.kr/…","countryCode":null,"contentTypeId":"12"}]
```

## S1 · 여행지 읽기 (`/api/destinations`)

### 목록 `GET /api/destinations`

| 파라미터 | 값 | 기본값 |
| :-- | :-- | :-- |
| `region` | 지역 이름 단어 1~3개, 50자까지. 글자·숫자·가운뎃점(·)·하이픈·마침표만 | 없음(전체) |
| `category` | 관광지 · 문화시설 · 축제 · 레포츠 · 음식점 · 숙박 | 없음(전체) |
| `query` | 이름·분류 소개(`summary`)에 들어 있는 글자. 50자까지 | 없음 |
| `limit` | 1~50 | 20 |
| `seed` | 동점 정렬 정수 | 오늘 날짜(Asia/Seoul) |

순서: 이름이 `query`와 같은 곳 → 이름에 `query`가 든 곳 → S0와 같은 동점 규칙(설명문 → 이미지 → `CRC32(id:seed)` → id). 지역 상한은 두지 않습니다(지역으로 고르는 API라서).

**지역 맞추기** (`RegionPatterns`, design.md 8.3의 접미사 떼기)

- 여행지 쪽 글은 `나라 location`입니다. 예: 국내 `대한민국 부산광역시 해운대구`, 위키데이터 `일본 일본 교토부`, 기존 해외 행 `일본 교토`.
- 받은 단어가 모두 이 글의 한 단어와 맞아야 합니다. 맞는다는 것은 `바탕 이름 + (행정구역 접미사 하나 또는 없음)`이 그 단어와 같다는 뜻입니다.
  - 바탕 이름: 받은 단어 그대로, 그리고 끝의 접미사를 하나 뗀 이름. 떼면 한 글자만 남는 경우는 떼지 않습니다('대구', '중구', '광주'를 지키려고).
  - 접미사: 특별자치도 · 특별자치시 · 특별시 · 광역시 · 자치도 · 자치시 · 도 · 시 · 군 · 구 · 부 · 현 · 주 · 성
  - 줄임말: 충북·충남·전북·전남·경북·경남 ↔ 충청북·충청남·전라북·전라남·경상북·경상남, 한국·국내 → 대한민국
- 예: `부산` → 부산광역시, `해운대` → 해운대구, `강원도` → 강원특별자치도, `경남` → 경상남도, `전라북도` → 전북특별자치도·전라북도, `울릉도` → 울릉군, `교토` → 교토부·교토, `캘리포니아` → 캘리포니아주.
- `광주`는 광주광역시와 경기도 광주시 둘 다 나옵니다. `경기 광주`처럼 두 단어를 주면 둘 다 맞는 곳만 남습니다. 어느 쪽인지 되묻는 것은 부르는 쪽(에이전트 지역 해석기)이 합니다.
- 쿼리는 MySQL 8 `REGEXP`(ICU)로 단어마다 `(^| )(바탕들)(접미사들)?( |$)` 패턴을 겁니다. 쓰는 문법이 단순해 Java 정규식과 뜻이 같고, 테스트는 같은 패턴을 Java로 돌려 확인합니다.

**분류**

| category | 국내(`content_type_id`) | 해외(키워드 마스크로 근사) |
| :-- | :-- | :-- |
| 관광지 | 12 | 해외 전체 |
| 문화시설 | 14 | 박물관·미술관·공연·예술 |
| 축제 | 15 | 축제 |
| 레포츠 | 28 | 스포츠·레크리에이션·야외 활동·등산·트레킹·하이킹·하이킹 코스·캠핑·서핑·다이빙·스노클링·카누·열기구 |
| 음식점 | 39 | 음식 |
| 숙박 | 32 | 없음(해외 결과 없음) |

- 국내는 지금 12·14·15·28만 수집하므로 음식점·숙박은 국내 결과가 없습니다([domestic-tour-sync.md](domestic-tour-sync.md)).
- 응답의 `category`도 같은 표로 붙입니다. 국내는 관광 타입이 표 밖(25 여행코스, 38 쇼핑)이면 `null`입니다. 해외는 축제 → 문화시설 → 레포츠 → 음식점 순으로 먼저 맞는 것, 아무것도 없으면 관광지입니다(근사값).

### 이름 찾기 `GET /api/destinations/lookup?name=`

- 이름이 같은 곳, 이름 뒤에 괄호 구분이 붙은 곳(`마니산(강화)`, `백련사 (서울)`), 공백을 뺀 이름이 같은 곳을 최대 10곳 돌려줍니다. 이름이 정확히 같은 곳이 먼저이고, 그다음 설명문·이미지가 있는 곳, id 순입니다.
- 같은 이름이 여러 곳이면 모두 돌려줍니다. 에이전트는 `location`으로 "어느 지역의 ○○인가요?"를 되묻습니다.
- `name`이 없거나 공백뿐이면 400, 100자를 넘으면 400입니다. 찾은 곳이 없으면 `[]`입니다.

### 상세 `GET /api/destinations/{id}`

목록 항목에 `address`(TourAPI 주소), `regionCode`·`sigunguCode`(법정동 코드), `summary`(분류 소개)를 더합니다. 값이 없으면 `null`입니다. 없는 id는 404 JSON입니다(공개 데이터라 존재를 숨기지 않음).

### 응답 항목

| 칸 | 뜻 |
| :-- | :-- |
| `id`, `name`, `country`, `location` | 여행지 |
| `category` | 위 분류 표의 이름 또는 `null` |
| `info` | 실제 설명문, 없으면 분류 소개(`COALESCE(info, summary)`) |
| `infoKind` | `DESCRIPTION`(실제 설명문) · `SUMMARY`(수집한 필드로 만든 소개) · `null` |
| `imageUrl` | 대표 이미지 주소 |
| `lat`, `lng` | 위도·경도 |
| `source` | `TOUR_API` · `WIKIDATA` · `null`(기존 행) |
| `copyrightType` | TourAPI 저작권 유형(Type1 출처표시, Type3 출처표시+변경금지) |
| `license`, `sourceUrl` | 위키데이터 행의 라이선스와 위키백과 문서 주소 |

**공공누리 제3유형**: `source = TOUR_API`이고 `infoKind = DESCRIPTION`인 글은 변경금지일 수 있습니다. 화면과 에이전트는 이 글을 요약·의역하지 않고 원문 그대로 출처와 함께 보여줘야 합니다(에이전트 판정표: design.md 11절, `agent/resources/text_policy_cases.csv`). 서버는 판정 결과(`textPolicy`)를 따로 내려주지 않고, 판정에 필요한 `source`·`infoKind`·`copyrightType`을 그대로 줍니다.

```bash
curl -s "http://localhost:8080/api/destinations?region=부산%20해운대&category=관광지&limit=3"
curl -s "http://localhost:8080/api/destinations/lookup?name=마니산"
curl -s "http://localhost:8080/api/destinations/1203"
```

```json
[{"id":1203,"name":"해운대해수욕장","country":"대한민국","location":"부산광역시 해운대구","category":"관광지",
  "info":"부산광역시 해운대구의 관광지입니다. 분류: …","infoKind":"SUMMARY","imageUrl":"http://tong.visitkorea.or.kr/…",
  "lat":35.1587,"lng":129.1604,"source":"TOUR_API","copyrightType":"Type3","license":null,"sourceUrl":null}]
```

## S2 · 여행 일정 (`/api/travel-plans`)

에이전트의 `create_travel_plan`(사람 확인 뒤 저장, design.md 9절)과 앞으로의 일정 화면이 씁니다. 주인은 언제나 토큰 사용자이고, 본문에는 userId 칸이 없습니다(보내도 무시). 모르는 칸도 무시합니다.

### 저장 `POST /api/travel-plans`

```json
{"clientRequestId":"call_plan_1","title":"부산 1박 2일","region":"부산","days":2,
 "places":[{"name":"해운대해수욕장","dayNo":1,"position":1,"source":"TOOL_RESULT"},
           {"name":"감천문화마을","dayNo":2,"position":2,"source":"USER"}]}
```

| 칸 | 규칙 |
| :-- | :-- |
| `clientRequestId` | 1~80자. 받은 그대로 비교(에이전트는 tool_call_id) |
| `title` | 1~100자(앞뒤 공백을 떼고 검사·저장) |
| `region` | 1~50자(〃). 서버가 해석하지 않는 글 |
| `days` | 1~30 |
| `places` | 1~30곳 |
| `places[].name` | 1~50자(〃) |
| `places[].dayNo` | 1~30이고 `days` 이하 |
| `places[].position` | 1 이상, 일정 안에서 겹치지 않음 |
| `places[].source` | `USER`(사용자가 말함) · `TOOL_RESULT`(도구 결과에서 고름) · `USER_EDIT`(확인 화면에서 고침). 에이전트의 `MODEL_SUGGESTED`는 받지 않음 |

- 글자 수는 유니코드 코드 포인트로 셉니다(이모지 하나 = 한 글자, 에이전트 파이썬 `len`과 같음).
- 숫자 칸은 Jackson 기본 규칙대로 `"3"` 같은 문자열 숫자도 받습니다.

| 경우 | 응답 |
| :-- | :-- |
| 새로 저장 | `201` + `Location: /api/travel-plans/{id}` + 일정 JSON |
| 같은 사용자가 같은 `clientRequestId`로 다시 보냄 | `200` + **처음 저장한 일정**(본문이 달라도). 새 행을 만들지 않음 |
| 값이 틀림 | `400` + `errors`(틀린 칸 모두). 값 검사가 먼저라 같은 키여도 400 |
| 읽을 수 없는 JSON | `400`(`errors[0].field = body`) |
| 토큰 없음·만료·삭제된 사용자 | `401` |

```json
{"id":42,"clientRequestId":"call_plan_1","title":"부산 1박 2일","region":"부산","days":2,
 "places":[{"name":"해운대해수욕장","dayNo":1,"position":1,"source":"TOOL_RESULT"},
           {"name":"감천문화마을","dayNo":2,"position":2,"source":"USER"}],
 "createdAt":"2026-10-18T14:03:00"}
```

`createdAt`은 서버 현지 시각(초 단위, 시간대 표시 없음)입니다. 장소는 `position` 순서입니다.

중복 저장은 표의 `UNIQUE(user_id, client_request_id)`가 막습니다. 같은 키 요청 두 개가 동시에 와서 둘째가 이 제약에 걸리면, 먼저 저장된 일정을 다시 읽어 200으로 답합니다.

### 목록·조회·삭제

| 요청 | 응답 |
| :-- | :-- |
| `GET /api/travel-plans` | `200` `[{"id","title","region","days","placeCount","createdAt"}]` 내 일정만, 최근 저장 순. 관리자도 자기 일정만 |
| `GET /api/travel-plans/{id}` | `200` 일정 JSON(주인 또는 관리자). 남의 일정·없는 일정은 둘 다 `403`(채팅 기록 C01처럼 존재 여부를 숨김) |
| `DELETE /api/travel-plans/{id}` | `204`(주인만). 남의 일정·없는 일정은 `403`. 관리자도 남의 일정은 지우지 못함 |

```bash
TOKEN=...   # POST /api/users/login 응답의 token
curl -i -X POST http://localhost:8080/api/travel-plans -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d @plan.json      # 201, 같은 파일로 다시 보내면 200
curl -s http://localhost:8080/api/travel-plans -H "Authorization: Bearer $TOKEN"
curl -i -X DELETE http://localhost:8080/api/travel-plans/42 -H "Authorization: Bearer $TOKEN"   # 204
```

### 표

`ddl-auto=update`가 서버를 띄울 때 만듭니다.

| 표 | 열 |
| :-- | :-- |
| `travel_plan` | `id`, `user_id`(→ `user.id`), `client_request_id` VARCHAR(80), `title` VARCHAR(100), `region` VARCHAR(50), `days`, `created_at`. `UNIQUE uk_travel_plan_user_client_request (user_id, client_request_id)` |
| `travel_plan_place` | `id`, `plan_id`(→ `travel_plan.id`, 인덱스), `position_no`, `day_no`, `name` VARCHAR(50), `source` VARCHAR(20) |

- `position`은 MySQL 함수 이름이라 열 이름을 `position_no`로 했습니다.
- `source`는 MySQL ENUM이 아니라 VARCHAR입니다. `ddl-auto=update`는 나중에 값을 더해도 ENUM 정의를 고치지 않기 때문입니다.

## 프로필 비밀번호 변경 (`POST /api/users/update-profile`)

토큰만으로 비밀번호를 바꾸지 못하게 했습니다(토큰이 새어 나가도 계정을 빼앗기지 않게).

- 새 비밀번호(`password`)를 보내면 현재 비밀번호(`currentPassword`)도 보내야 합니다. 없으면 `400`(`errors[0].field = currentPassword`), 틀리면 `403` `{"message":"현재 비밀번호가 맞지 않습니다."}`입니다. 이때는 닉네임 등 다른 칸도 바꾸지 않습니다.
- `password`가 없거나 빈 문자열이면 비밀번호는 그대로이고 다른 칸만 바꿉니다(예전처럼 현재 비밀번호 없이).
- 성공 응답은 예전과 같은 `{"message", "token"}`(새 토큰)입니다. `currentPassword`는 어떤 응답 JSON에도 싣지 않습니다.
- 화면(`frontend/src/pages/My/myprofilemodify.js`)에 '새 비밀번호'·'현재 비밀번호' 칸을 더했고, 새 비밀번호를 적었을 때만 두 값을 보냅니다.

## 테스트 (DB 없음)

| 테스트 | 확인하는 것 |
| :-- | :-- |
| `KeywordRecommendServiceTest` | 동점이 id 순이 아니라 CRC32(id:seed) 순, seed가 같으면 같은 순서·다르면 바뀜, 설명문·이미지 우선, 기본 seed = 서울 날짜, 옵션 400, 지역 상한과 채우기, 응답 칸 이름 |
| `DestinationInfoRepositoryQueryTest` | native query 문자열(정렬·조건·LIMIT), 모든 `@Query`의 `:이름`과 `@Param`이 같은지, 따옴표·괄호 짝 |
| `DestinationSearchServiceTest` | 지역 패턴(접미사·줄임말·한 글자 규칙·두 단어), 분류 표, 쿼리 인자, LIKE 이스케이프, 응답 항목 |
| `DestinationControllerTest` | 비로그인 공개, 응답 칸, 400·404 JSON |
| `TravelPlanControllerTest`, `TravelPlanServiceTest` | 위 S2 표의 모든 경우 |
| `JpaMappingWithoutDatabaseTest` | DB 연결 없이 Hibernate(MySQL 방언)로 엔티티 매핑과 저장소 파생 쿼리를 검사하고, `travel_plan` DDL의 UNIQUE·외래 키·열 길이 확인 |
| `AuthorizationMatrixTest`, `UserServiceAuthorizationTest` | 새 경로의 401·403·공개, 비밀번호 변경 400·403과 아무 칸도 바뀌지 않음 |

## 실제 서버에서 확인할 것 (오너)

DB 없이 시험했으므로 아래는 서버를 띄워 확인해야 합니다.

- 서버 기동 시 `travel_plan`, `travel_plan_place` 표와 UNIQUE 제약이 만들어지는지
- `/api/search`의 새 후보 쿼리, `/api/destinations`의 `REGEXP`·`ESCAPE '!'` 쿼리가 MySQL 8에서 오류 없이 도는지와 응답 시간(`EXPLAIN ANALYZE`)
- MySQL `CRC32(CONCAT(id, ':', seed))`가 Java `DestinationOrdering.tieHash`와 같은 값인지(예: `SELECT CRC32(CONCAT(17, ':', 20261018))`)
- 같은 `clientRequestId`로 두 번 저장했을 때 행이 하나인지
