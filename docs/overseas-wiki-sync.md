# 해외 여행지 데이터 수집 (위키데이터 · 한국어 위키백과)

> 국내 여행지 수집([domestic-tour-sync.md](domestic-tour-sync.md))과 짝을 이루는 해외 여행지 수집기입니다.
> 여행지 목록·좌표·유형은 **위키데이터**에서, 한국어 설명문은 **한국어 위키백과** 문서 도입부에서 받아 기존 `destination_info` 테이블에 `source = WIKIDATA`로 저장합니다. 두 API 모두 인증키가 필요 없습니다.

## 데이터 출처와 라이선스

| 데이터 | 출처 | 라이선스 | 저장 컬럼 |
| :-- | :-- | :-- | :-- |
| 여행지 목록, 이름, 좌표, 유형(P31), 국가, 행정구역, 세계유산 여부 | [위키데이터 쿼리 서비스](https://query.wikidata.org/) (SPARQL) | [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/) | `dest_name`, `latitude`, `longitude`, `category`, `country`, `country_code`, `location`, `world_heritage` |
| 설명문 (문서 도입부 평문) | [한국어 위키백과 Action API](https://ko.wikipedia.org/w/api.php) (`prop=extracts`) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/deed.ko) | `info`, `source_url`, `source_revision` |

모든 위키데이터 행의 `license`에는 `CC BY-SA 4.0 (한국어 위키백과), CC0 (Wikidata)`를 저장합니다.

### 출처 표시 의무 (설명문을 보여주거나 공개할 때 반드시)

위키백과 설명문(`info`)은 CC BY-SA 4.0 저작물입니다. 위키데이터 값(CC0)은 출처 표시 의무가 없지만, 위키백과 글은 아래를 지켜야 합니다.

1. **화면에 설명문을 보여줄 때마다 출처와 라이선스를 함께 표시합니다.** 문서 제목과 링크(`source_url`), "한국어 위키백과", 라이선스 이름과 링크(CC BY-SA 4.0)를 설명문 바로 아래에 둡니다. 문서 링크가 저작자 표시(편집 역사)를 대신합니다.
2. **변경 사실을 알립니다.** 저장하는 글은 도입부만 잘라 공백과 `[*]` 표시 등을 정리한 것이므로 "도입부 발췌" 같은 문구를 붙입니다.
3. **같은 조건으로 공유합니다(share-alike).** 설명문이나 설명문을 고쳐 쓴 결과(번역·요약한 글, 설명문이 들어간 학습 데이터셋, 내보낸 CSV 등)를 저장소·웹에 공개하거나 배포하면 CC BY-SA 4.0(또는 호환 라이선스)으로 공개하고 출처 표시를 유지합니다. 위키데이터 값만 쓴 데이터(`summary` 컬럼의 소개, 유형·좌표 등)는 이 조건이 없습니다.

표시 예:

```text
출처: 한국어 위키백과 「구라바엔」 문서 도입부 발췌 (https://ko.wikipedia.org/wiki/구라바엔), CC BY-SA 4.0
```

> **화면 표시**: 키워드 추천 API(`/api/search`)는 `source`·`copyrightType`·`sourceUrl`·`license`·`infoKind`를 함께 돌려주고, 여행지 상세 화면(`information.js`)이 설명문 바로 아래에 `SourceAttribution` 컴포넌트로 위 형식의 출처를 표시합니다. 설명문을 보여주는 화면을 새로 만들면 같은 컴포넌트를 붙입니다.

### 저장하지 않는 데이터

Google Places, Tripadvisor 같은 상업 API의 데이터는 `destination_info`나 학습 데이터에 **저장하지 않습니다.** Google Maps Platform 약관은 place ID를 빼고는 Places 콘텐츠(이름·리뷰·사진 등)의 저장·캐시를 막고, Tripadvisor 콘텐츠도 약관상 저장·재배포가 허용되지 않습니다. 필요하면 화면에서 그때그때 불러 보여 주기만 합니다.

테스트 픽스처(`src/test/resources/wiki/*.json`)는 2026-10-17 실제 응답을 줄인 것이고, 그 안의 설명문 발췌는 한국어 위키백과 「구라바엔」·「에펠탑」·「고대 교토의 역사 기념물」·「기이 산지의 영지와 참배길」 문서(CC BY-SA 4.0)에서 가져왔습니다. `DestinationKeywordMaskTest`의 한 문장은 「센강」 문서 도입부(CC BY-SA 4.0)입니다.

## 동작 방식

| 단계 | 호출 | 하는 일 | 요청 수 (2026-10-17 기준) |
| :-- | :-- | :-- | :-- |
| 1. 국가 목록 | SPARQL 1회 | 수집 대상 국가와 ISO 코드, 한국어 이름 | 1 |
| 2. 후보 확인 | SPARQL ([후보 목록](#후보-목록) 2,000개마다 + 세계유산 1회) | 후보 목록의 항목이 지금도 선정 조건에 맞는지 확인하고 사이트링크 수·국가 코드를 받음. 세계유산은 전 세계를 따로 물어 목록에 없는 새 지정도 잡음 | ⌈14,418 ÷ 2,000⌉ + 1 = 9 |
| 3. 국가별 상세 | SPARQL 국가마다 1회 | 국가별 상위 `limit`곳의 이름·좌표·유형·세계유산·행정구역·문서 주소를 받아 추가·갱신 | 후보가 있는 국가 수 (2026-10-17 기준 193) |
| 설명문 | 위키백과 API (20건당 1회) | 설명문이 없는 행 → 받은 지 30일 지난 행 순으로 문서 도입부를 받아 `info`에 저장 | ⌈건수 ÷ 20⌉ |
| 소개 재계산 | 없음 | 저장된 위키데이터 행의 `summary`와 키워드 마스크를 유형 코드표로 다시 계산 | 0 |

### 선정 규칙

- **국가**: 주권국가(`P31 = Q3624078`) 중 ISO 3166-1 alpha-2 코드(`P297`)가 있는 나라. 덴마크처럼 코드가 주권국가(덴마크 왕국 Q756617)가 아니라 구성국(덴마크 Q35)에 붙은 경우는 구성국을 씁니다(그래서 페로 제도·그린란드도 들어옵니다). 해체된 나라(`P576`), 대한민국(`Q884`, 국내 수집 담당)과 북한(`Q423`)은 뺍니다. 2026-10-17 기준 197개국입니다.
- **여행지**: 다음을 모두 만족하는 항목입니다.
  - 한국어 위키백과 문서가 연결되어 있음 (`schema:isPartOf <https://ko.wikipedia.org/>`)
  - 좌표(`P625`)와 국가(`P17`)가 있음
  - 유형(`P31`)이 [유형 코드표](#유형--키워드-코드표)에 있거나, **유네스코 세계유산**(`P1435 = Q9259`)임. 세계유산은 종료 시점(`P582`)이 붙은 지정(등재 해제)과 폐기 등급(deprecated) 진술을 세지 않습니다. 유형 조건은 [후보 목록](#후보-목록)을 만들 때 걸러 두고, 동기화 때는 상세 값의 `P31`로 다시 확인합니다(코드표 유형이 없어졌고 세계유산도 아니면 저장하지 않음).
  - **대한민국·북한과 얽히지 않음**: 국가(`P17`)에 대한민국(`Q884`)·북한(`Q423`)이 하나라도 있으면 다른 나라가 함께 적혀 있어도 넣지 않습니다. 소재 행정구역(`P131`)이 대한민국·북한에 속해도 국가 값과 상관없이 뺍니다. 예: 독도(`Q20317`, P17 = 일본·대한민국), 독도 천연보호구역(`Q21709690`, P17 = 일본으로 잘못 적힘, P131 = 울릉읍), 백두산(`Q107635`, P17 = 중국·북한). 국내 여행지는 TourAPI로 따로 수집합니다.
- **국가 코드**: 항목의 `P17` 값에 붙은 `P297`. 그 값에 코드가 없으면 한 단계 위(`P17`의 `P17`)의 코드를 씁니다. 예를 들어 네덜란드 항목은 `P17 = 네덜란드(Q55)`인데 Q55에는 코드가 없어 네덜란드 왕국(Q29999)의 `NL`로 들어갑니다. 해체된 나라(`P576`, 예: 일본 제국 `Q188712`)인 `P17` 값은 세지 않습니다. 그러지 않으면 일제강점기 서울의 경성신사(`Q10855232`, P17 = 일본 제국)가 한 단계 위 규칙으로 일본(`JP`) 여행지가 됩니다.
- **순서와 개수**: 국가마다 사이트링크 수(여러 언어 위키백과에 문서가 있을수록 큼) 내림차순, 같으면 QID 번호 순으로 상위 `limit`곳(기본 150)을 고릅니다.
- **여러 나라에 걸친 항목**(국경의 산, 여러 나라가 함께 등재한 세계유산 등)은 한 나라에만 넣습니다. 이미 저장된 나라가 아직 그 항목의 국가 중 하나면 그대로 두고, 아니면 ISO 코드 순으로 첫 나라입니다. 그래서 일부 국가만 골라 실행해도 나라를 오가지 않습니다. 대한민국·북한이 함께 걸린 항목은 위 규칙대로 넣지 않습니다.
- 순위에서 밀리거나 위키데이터에서 조건이 바뀐 항목도 **지우지 않습니다.** 저장된 행은 다음 실행에서 다시 후보로 잡힐 때만 갱신됩니다.

### 저장하는 값

| 컬럼 | 값 |
| :-- | :-- |
| `source` / `external_id` | `WIKIDATA` / 항목 QID (예: `Q63465`). 이 둘로 기존 행을 찾아 갱신합니다 |
| `dest_name` | 한국어 레이블. 없으면 한국어 위키백과 문서 제목(끝의 `(동음이의 구분)` 괄호 제외), 그것도 못 쓰면 영어 레이블 |
| `country` / `country_code` | 국가의 한국어 이름(없으면 영어) / ISO 3166-1 alpha-2 (새 컬럼, `VARCHAR(2)`) |
| `location` | `{국가} {행정구역}` 예: `일본 나가사키시`. 행정구역은 소재 행정구역(`P131`) 중 한국어 이름이 있고 고른 나라에 속한(국가 코드를 모르면 받아들임) 것의 QID 번호가 가장 작은 것. 없거나 국가와 같으면 국가만. 국경의 몽블랑산은 이탈리아 쪽 쿠르마유르가 QID 번호가 앞서도 프랑스 쪽 행정구역이 들어갑니다 |
| `latitude` / `longitude` | `P625`. 여러 개면 정렬상 첫 값 (실행할 때마다 같은 값) |
| `category` | `P31` 중 유형 코드표에 있는 QID를 코드표 순서로 `\|`로 이은 값 (예: 구라바엔 `Q33506\|Q1107656\|Q756102` = 박물관·정원·야외 박물관). 255자를 넘으면 QID 중간에서 자르지 않고 앞쪽까지만 |
| `world_heritage` | 유네스코 세계유산 여부 (새 컬럼). 아래 [키워드 마스크](#키워드-마스크) 참고 |
| `summary` | 사실만 담은 짧은 소개. `{location}의 {대표 유형}입니다.` + 세계유산이면 ` 유네스코 세계유산입니다.` 대표 유형은 `category` 중 `ko_label`이 있고 코드표에서 가장 앞선 유형(세계유산 문장을 붙일 때는 `Q9259` 유형 제외), 없으면 `여행지` |
| `info` | 한국어 위키백과 문서 도입부(`exintro`, `explaintext`). `[*]`(한국어 문서가 없는 언어 간 링크 표시), 폭 없는 공백, 빈 괄호를 지우고 공백을 하나로 줄인 평문. 비어 있으면 `null`, 10,000자에서 자름 |
| `source_url` | 한국어 위키백과 문서 주소 (위키데이터 사이트링크 그대로, 새 컬럼) |
| `license` | `CC BY-SA 4.0 (한국어 위키백과), CC0 (Wikidata)` (새 컬럼) |
| `source_revision` | 설명문을 가져온 위키백과 판 번호(revision id) (새 컬럼) |
| `overview_synced_at` / `overview_failed_at` | 설명문을 마지막으로 받은(확인한) 시각 / 마지막 실패 시각. 국내 설명문과 같은 컬럼을 씁니다 |

갱신 규칙:

- 위키데이터에서 다시 받은 값이 저장된 값과 모두 같고 키워드 마스크도 그대로면 **행을 다시 쓰지 않습니다.** 유형 코드표만 바뀐 경우도 마스크를 직접 계산해 비교하므로 다음 목록 동기화에서 저장됩니다.
- 위키데이터가 다른 문서를 가리키게 되면(**문서 제목 변경**) 예전 설명문·판 번호·수집 기록을 지워 다음 설명문 수집 때 새 문서에서 받습니다. 설명문과 출처 주소가 어긋난 채 보이지 않도록 예전 설명문을 남기지 않습니다.
- 설명문 수집은 설명문이 없는 행을 먼저(실패한 적 없는 행 → 오래전에 실패한 행), 남은 개수만큼 받은 지 `wiki.sync.description-refresh-days`(30일)가 지난 행을 다시 확인합니다. **판 번호가 바뀌었으면** 설명문을 새로 저장하고, 그대로면 행을 다시 쓰지 않고 확인 시각만 바꿉니다.
- 문서가 없어졌으면(`missing`) 설명문을 비우고 받은 것으로 칩니다. 응답에 없거나 도입부가 빠져 온 행은 실패 시각을 남겨 대기열 뒤로 보냅니다. 요청이 3번 연속 실패하면 그 실행을 멈춥니다.
- 넘겨주기(redirect)된 문서는 넘겨받은 문서의 도입부와 판 번호를 저장하고, `source_url`은 위키데이터 사이트링크 주소를 그대로 둡니다(그 주소로 들어가면 같은 문서로 넘어갑니다).

### 키워드 마스크

위키데이터 행의 키워드 마스크는 다음의 합집합입니다. 국내(`TOUR_API`)와 기존 해외 행(`source = NULL`)의 규칙은 바뀌지 않았습니다.

```text
KeywordMask.fromKoreanDescription(info)          설명문 키워드 (한 글자 키워드는 독립된 단어일 때만, 국내와 같음)
∪ 유형 코드표에서 category의 모든 QID에 정한 키워드
∪ world_heritage = true 이면 '유네스코'
```

- **세계유산은 `category`에 섞지 않고 `world_heritage` 컬럼으로 따로 둡니다.** `P1435`(문화유산 지정)는 `P31`(유형)과 다른 속성이라 섞으면 `category`의 뜻이 흐려지고, `category`가 255자에서 잘릴 때 함께 사라질 수 있으며, 세계유산만 따로 조회하기도 쉽습니다. 코드표에 `Q9259`(세계유산) 행을 넣으면 `P31 = Q9259`인 항목은 유형으로도 잡힙니다(둘은 독립).
- `summary`는 훑지 않습니다. 소개에는 유형명·지명이 그대로 들어가서(예: `대성당`의 `성`) 코드표에서 일부러 뺀 키워드가 켜지기 때문입니다. 국내와 같은 이유입니다.
- 설명문을 훑을 때 `문화어`라는 낱말은 빼고 훑습니다. 한국어 위키백과는 외국 지명 뒤에 북한 표기를 `(프랑스어: la Seine, 문화어: 쌘느강)`처럼 적어서, 그대로 두면 센강·몽블랑산·레위니옹 같은 문서마다 `문화` 키워드가 켜집니다. 저장된 설명문은 바꾸지 않습니다.

### 유형 → 키워드 코드표

[`src/main/resources/wiki/type-keywords.csv`](../src/main/resources/wiki/type-keywords.csv)

| 열 | 내용 |
| :-- | :-- |
| `qid` | 위키데이터 유형 QID (`P31` 값) |
| `ko_label` | 요약문에 쓰는 한국어 유형명 |
| `en_label` | 영어 유형명 (확인용) |
| `keywords` | 이 유형에 붙는 추천 키워드를 `\|`로 이어 적음. 없으면 빈 칸 |

- 2026-10-17 클래스 집계에서 검토한 1,296개 유형 중 **포함으로 정한 589개**입니다. 집계 대상은 한국어 위키백과 문서와 좌표가 있는 해외 항목의 `P31` 값(7,001종)이고, 행정구역·정주지, 역·공항·도로, 사건·행사, 학교·기업 같은 기관, 호텔·음식점 같은 사업체, 건물 일반 등은 뺐습니다.
- 589개 중 **186개는 `ko_label`이 비어 있습니다**(위키데이터에 쓸 만한 한국어 레이블이 없는 유형, 예: `island group`, `minor basilica`). 수집·키워드에는 쓰고 요약문 대표 유형으로는 쓰지 않습니다. 키워드가 빈 유형은 77개입니다(교회당·공원·다리처럼 공통 키워드가 없는 유형).
- **행 순서는 요약문 대표 유형의 우선순위**이고, 집계에서 항목이 많은 유형부터 적었습니다(섬 → 강 → 산 → …). 그래서 성이면서 박물관인 곳은 `박물관`, 일본의 성 `Q92026`과 성 `Q23413`이 함께 있으면 `일본의 성`이 대표 유형이 됩니다.
- 이 파일은 유형 조건(후보 목록을 만들 때), `category`·키워드 마스크·요약문에 씁니다. 동기화 쿼리에 유형 목록을 넣지는 않습니다([쿼리 설계](#쿼리-설계-60초-제한)).
- `#`으로 시작하는 줄은 주석입니다. 쉼표가 들어간 이름은 큰따옴표로 감쌉니다.
- 키워드는 `KeywordMask`의 키워드 목록에 있는 값만 쓸 수 있습니다. 헤더가 다르거나, 열 수·QID 형식이 틀렸거나, `en_label`이 비었거나, QID·키워드가 중복되었거나, 목록에 없는 키워드가 있으면 조용히 건너뛰지 않고 `WikiTypeKeywordsTest`가 실패하며, 서버도 시작 단계(`WikiDestinationSyncService` 생성 시 코드표를 읽음)에서 몇 번째 줄이 틀렸는지 알리는 예외로 멈춥니다.
- 파일을 고친 뒤에는 서버를 다시 띄우고 `POST /api/admin/wiki-sync/summaries`(소개·마스크 재계산, 위키미디어 호출 없음)를 실행합니다. 새로 추가한 유형의 여행지는 [후보 목록](#후보-목록)을 다시 만든 뒤 목록 동기화를 실행해야 들어옵니다.

### 후보 목록

[`src/main/resources/wiki/candidate-items.txt`](../src/main/resources/wiki/candidate-items.txt): 한 줄에 위키데이터 QID 하나, `#`은 주석. 형식이 틀리거나 중복된 줄이 있으면 서버가 시작 단계에서 멈춥니다(`WikiCandidateItemsTest`).

- **2026-10-17에 만든 14,418곳**입니다. 한국어 위키백과 문서와 좌표가 있는 항목의 `P31` 집계(4개 조각 쿼리, 약 11.5만 항목)에서 유형 코드표에 있는 유형을 가진 항목과 세계유산 항목(546곳)을 골라, 같은 날 후보 확인 쿼리(아래 2단계와 같은 조건)를 통과한 것만 남겼습니다. 유형으로 들어온 곳이 14,312곳, 유형 없이 세계유산으로만 들어온 곳이 106곳입니다.
- 동기화 때마다 이 QID들의 국가·사이트링크 수·한국어 문서·대한민국 관련 여부를 다시 확인하므로, 목록에 있어도 조건이 바뀐 항목은 들어오지 않습니다. 반대로 **목록을 만든 뒤 새로 생긴 항목은 세계유산이 아니면 들어오지 않습니다.**
- 목록을 다시 만들 때는 위와 같은 집계를 다시 합니다. 2026-10-17 측정으로 한국어 문서 사이트링크(약 122만 개)를 훑는 쿼리는 QID 끝자리 2개 조각에서 61.5초에 응답이 끊겼으므로 끝자리 1개씩(10회)처럼 더 잘게 나눠야 하고, 그래도 60초 제한에 걸릴 수 있습니다. 아직 저장소에 자동화하지 않았습니다(남은 일).

### 쿼리 설계 (60초 제한)

위키데이터 쿼리 서비스는 쿼리 하나를 60초에 끊습니다. 2026-10-17에 잰 시간입니다(서버 부하에 따라 달라짐).

| 쿼리 | 걸린 시간 |
| :-- | :-- |
| 국가별 선정 쿼리 (미국, 쿼리 서비스가 순서를 고름) | 49.3초 |
| 국가별 선정 쿼리 (미국, 한국어 문서부터 훑게 고정) | 27.2초 |
| 전 세계 선정 쿼리 (한국어 문서부터, 임시 5개 유형) | 36.9초 |
| 유형부터 훑는 후보 쿼리 (임시 5개 유형: 사찰·대성당·성·박물관·국립공원) | 8.9초 |
| 유형부터 훑는 후보 쿼리 (산 `Q8502` + 작은 유형 4개) | **66초 후 504** (시간 초과) |
| 한국어 문서부터 훑는 후보 쿼리 (QID 끝자리 2개 조각, 유형 589개 `FILTER IN`) | **66초 후 504** |
| 한국어 문서 20만 개(`LIMIT`/`OFFSET` 조각) + 유형 589개 `FILTER IN` | **61초 후 500** (시간 초과) |
| 한국어 문서 + 좌표 + `P31` 전부 (QID 끝자리 2개 조각, 유형 거르기 없음) | 61.5초에 HTTP 200 응답이 중간에 끊김 |
| 유형 589개의 항목 수 세기 (`GROUP BY`) | **66초 후 504** |
| **후보 확인 쿼리 (QID 4,900개 `VALUES`, 아래 조건 전부)** | **15.6~21.3초** |
| 세계유산 후보 쿼리 (전 세계) | 7.4초 |
| 국가 목록 | 5.2초 |
| **국가별 상세 쿼리 (QID 20개)** | **2.3~3.2초** |
| 한국어 위키백과 설명문 (20개 문서) | 1.9초 |

유형 코드표의 589개 유형에는 산·강·호수·언덕·거리·집·교회당·문화유산처럼 위키데이터 전체에 항목이 아주 많은 유형이 들어 있어서, 유형부터 훑으면 산 하나가 든 묶음도 60초를 넘깁니다(유형별 항목 수를 세는 쿼리조차 제한에 걸림). 한국어 문서 사이트링크(약 122만 개) 전체부터 훑는 쿼리도 조각을 나눠도 제한에 걸립니다. 그래서:

- **후보는 [후보 목록](#후보-목록)의 QID를 2,000개씩(`wiki.sync.candidate-batch-size`) `VALUES`로 넘겨 확인합니다.** 순서는 `hint:Query hint:optimizer "None"`으로 고정합니다: 항목 → 사이트링크 수 → 한국어 문서 → 좌표 → 국가 → 대한민국 관련 여부. 세계유산 후보 쿼리는 세계유산 지정 진술(`ps:P1435 wd:Q9259`)부터 훑으므로 전 세계를 한 번에 물어도 가볍습니다.
- 국가별 쿼리는 고른 QID만 `VALUES`로 넘겨 상세 값을 받습니다. 레이블은 레이블 서비스 대신 `rdfs:label` + `FILTER(LANG(...) = "ko")`로 받습니다.
- 후보 확인 쿼리가 서버 쪽 오류(500 시간 초과, 502·504, 연결 시간 초과, **결과를 내보내다 끊긴 HTTP 200 응답**)로 실패하면 묶음을 반으로 나눠 다시 묻습니다. 쿼리 서비스는 결과를 내보내는 도중 제한에 걸리면 200 응답 본문을 중간에 끊기 때문에, 해석할 수 없는 200 응답도 시간 초과로 봅니다. 요청이 3번 연속 실패하면 그 실행을 멈춥니다.

### 요청 예절

[위키미디어 User-Agent 정책](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy)과 [API 이용 안내](https://www.mediawiki.org/wiki/API:Etiquette)를 따릅니다.

- 모든 요청에 `User-Agent: GlopleBot/0.1 (+https://github.com/yss9/Glople; personal portfolio project)`을 붙입니다. 연락처는 저장소 주소로 대신하고 **이메일 주소는 넣지 않습니다**(`wiki.user-agent`에 `@`가 있으면 서버가 시작하지 않습니다).
- 요청은 한 번에 하나씩, 앞 요청이 끝난 뒤 `wiki.request-interval-ms`(1초)가 지나야 보냅니다. 국내 수집과 같은 실행기를 써서 다른 동기화와 동시에 돌지 않습니다.
- HTTP 429(또는 `Retry-After`가 붙은 503)와 위키백과 `maxlag` 오류(`maxlag=5`)는 `Retry-After`(초 또는 HTTP 날짜, 없으면 5초)만큼 기다렸다가 최대 `wiki.max-retries`(3)번 다시 보냅니다. 기다리라는 시간이 `wiki.max-retry-after-seconds`(120초)보다 길거나 재시도를 다 쓰면 그 실행을 멈추고(`stoppedByQuota: true`) 그때까지 저장한 결과는 남깁니다. 같은 요청을 나중에 다시 보내면 이어 갑니다.
- 전체 실행 규모: 목록 동기화는 요청 약 204회(국가 목록 1 + 후보 확인 9 + 국가별 상세 193 + α)로 15분 안팎, 설명문은 7,600곳이면 380회로 20분쯤 걸립니다(요청 간격 1초 포함). 2026-10-17 후보로 계산하면 `limit=150`일 때 **약 7,580곳**(193개국, 150곳을 넘어 잘리는 나라 18곳: 일본 1,929 · 미국 1,824 · 중국 796 · 이탈리아 590 · 영국 580 …)이고, `limit=50`이면 약 4,680곳, `limit=500`이면 약 11,140곳입니다. 대량 수집은 하지 않고, 필요한 만큼 `countries`·`limit`으로 나눠 실행합니다.

## 설정

모두 선택 항목이고 적힌 값이 기본값입니다. 인증키는 없습니다.

```properties
# 위키미디어에 보낼 User-Agent (이메일 주소 금지)
wiki.user-agent=GlopleBot/0.1 (+https://github.com/yss9/Glople; personal portfolio project)
wiki.sparql-url=https://query.wikidata.org/sparql
wiki.api-url=https://ko.wikipedia.org/w/api.php
# 요청 사이 간격, 429·maxlag 재시도 횟수, Retry-After 최대 대기
wiki.request-interval-ms=1000
wiki.max-retries=3
wiki.max-retry-after-seconds=120
# 후보 확인 쿼리 한 번에 넣을 QID 수 (최대 5000)
wiki.sync.candidate-batch-size=2000
# 설명문을 받은 지 이 일수가 지나면 판 번호를 다시 확인 (0이면 안 함)
wiki.sync.description-refresh-days=30
```

### 기존 DB를 쓰는 경우

`ddl-auto=update`가 서버를 띄울 때 새 컬럼 `country_code`(`VARCHAR(2)`), `world_heritage`(`BIT`), `source_url`(`VARCHAR(1000)`), `license`(`VARCHAR(255)`), `source_revision`(`BIGINT`)을 추가합니다(모두 NULL 허용, 기존 행은 `NULL`). 기존 국내·해외 행의 값과 마스크는 바뀌지 않습니다.

## 실행

모두 관리자 전용 API(`/api/admin/**`)입니다. 토큰을 받는 방법은 [국내 수집 문서](domestic-tour-sync.md#실행)와 같습니다. 관리자 비밀번호는 `.env`의 `GLOPLE_ADMIN_PASSWORD`이며(키 목록은 [`.env.example`](../.env.example)), 값이 없으면 서버가 `admin` 계정을 만들지 않습니다.

```bash
# 0) 관리자 토큰 (프로젝트 루트에서, .env의 GLOPLE_ADMIN_PASSWORD 사용)
ADMIN_PASSWORD=$(sed -n 's/^GLOPLE_ADMIN_PASSWORD=//p' .env)
TOKEN=$(curl -s -X POST http://localhost:8080/api/users/login -H "Content-Type: application/json" \
  -d "{\"userId\":\"admin\",\"password\":\"$ADMIN_PASSWORD\"}" | sed -E 's/.*"token":"([^"]+)".*/\1/')

# 1) 목록 동기화 (백그라운드 실행, 202 Accepted). countries를 빼면 전체 국가, limit은 국가별 최대 개수
curl -X POST "http://localhost:8080/api/admin/wiki-sync/destinations?countries=FR,JP&limit=150" -H "Authorization: Bearer $TOKEN"

# 2) 진행 상태 확인 (국내 수집과 같은 상태)
curl http://localhost:8080/api/admin/wiki-sync/status -H "Authorization: Bearer $TOKEN"

# 3) 설명문 동기화 (limit 기본 1000, 요청 1회 = 20건)
curl -X POST "http://localhost:8080/api/admin/wiki-sync/descriptions?limit=1000" -H "Authorization: Bearer $TOKEN"

# 4) 소개·마스크 재계산 (위키미디어 호출 없음, 유형 코드표를 고친 뒤)
curl -X POST http://localhost:8080/api/admin/wiki-sync/summaries -H "Authorization: Bearer $TOKEN"
```

| 응답 | 의미 |
| :-- | :-- |
| `202` | 동기화를 시작함. 본문은 현재 상태 (`task`: `WIKI_DESTINATIONS`·`WIKI_DESCRIPTIONS`·`WIKI_SUMMARIES`) |
| `400` | `countries`가 ISO 두 글자 코드가 아니거나 `KR`·`KP`, `limit`이 범위(목록 1~500, 설명문 1~20,000)를 벗어남 |
| `401` | 토큰이 없거나 만료·위조됨 |
| `403` | 로그인했지만 관리자가 아님 |
| `409` | 다른 동기화(국내 포함)가 이미 진행 중 |

TourAPI 인증키가 없어도 위키 작업은 실행됩니다(`503 NOT_CONFIGURED`는 TourAPI 작업에만 해당). 수집 대상 목록에 없는 국가 코드를 넣으면 실행은 시작되지만 바로 실패하고 상태의 `lastError`에 코드가 나옵니다.

상태의 `lastResult`: 목록 동기화는 `processed`(상세 값을 받은 여행지 수) · `inserted` · `updated` · `failed`(실패한 요청 수: 후보 묶음·국가별 상세) · `stoppedByQuota`(429로 멈춤), 설명문 동기화는 `processed`(처리한 행) · `updated`(설명문을 저장한 행) · `failed`(받지 못한 행)입니다. 바뀐 것이 없는 행은 저장하지 않으므로 두 번째 실행부터는 `updated`가 0에 가깝습니다.

확인용 SQL:

```sql
SELECT country_code, COUNT(*) total, SUM(info IS NOT NULL) with_info, SUM(world_heritage) heritage
FROM destination_info WHERE source = 'WIKIDATA' GROUP BY country_code ORDER BY total DESC;
```

## 수집 기록

| 날짜 | 목록 동기화 (limit 150) | 설명문 | 결과 |
| :-- | :-- | :-- | :-- |
| 2026-10-17 | 7,522곳 추가 (16분). KE·ML·MM 3개국은 WDQS 502로 실패해 다시 실행해 61곳 추가 | 7,583곳 (실패 0, 19분, 평균 281자) | 193개국 7,583곳, 키워드 있음 7,435곳, 전부 출처 URL·라이선스 저장. 대한민국 관련 항목(독도·백두산·경성신사 등) 0건 확인 |

수집 후 확인한 점: 키워드 일치 수가 같으면 id 순서로 정렬되기 때문에, 국내 여행지(먼저 들어간 행)가 있는 키워드는 국내만 나오고 해외끼리는 국가 코드가 A로 시작하는 나라가 먼저 나옵니다. 추천 정렬 개선이 필요합니다. 한국어 위키백과 설명문의 부분 문자열 매칭 때문에 부르즈 할리파가 '모스크'로 잡히는 식의 오검출도 있습니다(키워드 태깅 모델로 개선할 대상).

## 알아 둘 점

- 국가 이름은 위키데이터 한국어 레이블 그대로라 `중화인민공화국`(CN), `네덜란드 왕국`(NL), `중화민국`(TW)처럼 나옵니다.
- 위키데이터 데이터 자체의 특이한 값이 그대로 들어옵니다. 예를 들어 교토부(Q120730, 행정구역)에 세계유산 지정이 붙어 있어 `일본의 여행지입니다. 유네스코 세계유산입니다.`로 들어오고, 사이트링크 수가 많아 일본 4위가 됩니다. 이상한 행은 위키데이터를 고치는 것이 근본 해결입니다.
- 유형 없이 세계유산으로만 들어오는 106곳 중 50곳은 도시·행정구역 항목입니다(발레타, 키토, 베로나, 바티칸 시국 등 도시 자체에 세계유산 지정이 붙은 경우). 유형 코드표에서는 행정구역·정주지를 뺐지만 세계유산 규칙으로는 들어옵니다. 빼려면 세계유산 후보에도 제외 유형 규칙을 더해야 합니다(남은 일).
- 대표 유형명이 나라 이름과 겹칠 수 있습니다. 예: `일본 가라쓰시의 일본의 성입니다.`(일본의 성 `Q92026`). 소개는 설명문이 없을 때만 보여 주는 값입니다.
- 한국어 레이블과 문서 제목이 다를 수 있습니다(예: 레이블 `고도 교토의 문화재`, 문서 `고대 교토의 역사 기념물`). 이름은 레이블, 설명문은 문서에서 받습니다.
- 사이트링크 수는 인기의 대략적인 대용값입니다. 한국인 여행객 기준 인기와는 다를 수 있습니다.

## 관련 코드

- [`WikimediaClient.java`](../src/main/java/proj/travien/service/WikimediaClient.java): SPARQL·위키백과 API 호출, User-Agent, 요청 간격, 429·`Retry-After`·`maxlag` 처리, 응답 해석(여러 행 합치기, 사이트링크 주소 → 문서 제목, normalized·redirects 따라가기)
- [`WikiDestinationSyncService.java`](../src/main/java/proj/travien/service/WikiDestinationSyncService.java): 후보 모으기, 국가별 선정, 추가·갱신, 설명문 수집, 소개(`summaryOf`)와 재계산
- [`WikiTypeKeywords.java`](../src/main/java/proj/travien/service/WikiTypeKeywords.java): 유형 코드표([`type-keywords.csv`](../src/main/resources/wiki/type-keywords.csv)) 읽기, 유형별 키워드 마스크, `category` 만들기
- [`WikiCandidateItems.java`](../src/main/java/proj/travien/service/WikiCandidateItems.java): 후보 목록([`candidate-items.txt`](../src/main/resources/wiki/candidate-items.txt)) 읽기
- [`DestinationInfo.java`](../src/main/java/proj/travien/domain/DestinationInfo.java): 새 컬럼과 `WIKIDATA` 마스크 규칙(`computeKeywordMask`)
- [`TourSyncRunner.java`](../src/main/java/proj/travien/service/TourSyncRunner.java): 국내·해외 동기화를 한 번에 하나씩 실행 (`WIKI_*` 작업)
- [`WikiSyncController.java`](../src/main/java/proj/travien/controller/WikiSyncController.java): 관리자 API
- 테스트(모두 DB 없이 실행): [`WikimediaClientTest`](../src/test/java/proj/travien/service/WikimediaClientTest.java)(실제 응답 픽스처, 127.0.0.1 임시 서버로 User-Agent·폼 본문·429·maxlag 확인), [`WikiDestinationSyncServiceTest`](../src/test/java/proj/travien/service/WikiDestinationSyncServiceTest.java), [`WikiTypeKeywordsTest`](../src/test/java/proj/travien/service/WikiTypeKeywordsTest.java), [`WikiCandidateItemsTest`](../src/test/java/proj/travien/service/WikiCandidateItemsTest.java), [`DestinationKeywordMaskTest`](../src/test/java/proj/travien/service/DestinationKeywordMaskTest.java), [`TourSyncRunnerTest`](../src/test/java/proj/travien/service/TourSyncRunnerTest.java), [`WikiSyncControllerTest`](../src/test/java/proj/travien/controller/WikiSyncControllerTest.java)

```bash
sh gradlew test --offline --tests "proj.travien.service.Wiki*" \
               --tests "proj.travien.service.DestinationKeywordMaskTest" \
               --tests "proj.travien.service.TourSyncRunnerTest" \
               --tests "proj.travien.controller.WikiSyncControllerTest"
```

> `proj.travien.repository.*`로 돌리면 `UserRepositoryTest`(`@SpringBootTest`)가 함께 실행되어 `application.properties`의 DB(로컬 `glople-mysql`)에 붙고 테스트 사용자를 저장합니다. DB 없이 돌리려면 위처럼 클래스를 지정하거나 `DestinationInfoRepositoryQueryTest`만 지정합니다.
