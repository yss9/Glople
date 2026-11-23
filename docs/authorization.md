# 인가(권한) 규칙

요청 파라미터의 `userId`·`nickname`만 바꿔서 남의 정보를 보거나 바꾸지 못하게 한 규칙입니다. 이 문서는 백엔드(`src/`) 기준입니다. 프론트엔드가 맞춰야 하는 점은 [프론트엔드에서 바뀐 것](#프론트엔드에서-바뀐-것)에 따로 적었습니다.

## 원칙

1. **요청한 사용자는 JWT로만 정합니다.** 로그인 토큰의 `subject`가 로그인 아이디(`userId`)입니다. 서버는 `CurrentUser`([코드](../src/main/java/proj/travien/jwt/CurrentUser.java))로 현재 사용자를 꺼냅니다.
   - 수정 전에 발급된 토큰은 `subject`가 없습니다. 이런 토큰은 만료(24시간) 전까지 `userId` 클레임으로 같은 사용자로 인정합니다.
2. **행위자를 가리키는 파라미터는 호환을 위해 받기만 합니다.** `userId`, `nickname`, `userNickname`, `id`, `sender`, `body.userId` 같은 값입니다.
   - 값이 있는데 토큰 사용자가 아니면 **403**입니다.
   - 값을 생략하면 본인으로 처리합니다. 생략할 수 있게 `required=false`로 바꿨습니다.
   - 관리자만 다른 사용자를 지정할 수 있는 곳은 표에 따로 적었습니다.
3. **자원 주인은 토큰 사용자와 비교합니다.** 게시글, 댓글, 리뷰, 좋아요, 예약, 팔로우, 채팅방, 업로드, 프로필, 포인트, 검증 서류가 대상입니다.
   - 닉네임은 바뀔 수 있습니다. 그래서 토큰의 `nickname` 클레임이 아니라 DB 값으로 비교합니다.
4. **기본은 로그인 필요입니다.** 공개 경로는 아래 표의 화이트리스트만 엽니다. `/api/admin/**`과 관리자 기능은 `ROLE_ADMIN`만 쓸 수 있습니다.
   - `@EnableMethodSecurity`를 켰습니다. 그래서 기존 `@PreAuthorize`도 이제 실제로 동작합니다(URL 규칙과 이중 방어).
5. **비로그인은 401, 권한 없음은 403입니다.** 둘 다 같은 모양의 JSON으로 응답합니다.

접근 수준별 개수(전체 122개)는 다음과 같습니다. `matrix.json`(114개)에 2026-10-18에 더한 여행지 읽기 3개(공개), 여행 일정 4개(로그인 1, 본인 3), 키워드 태거 예측 가져오기 1개(관리자)를 합친 수입니다.

| 공개 | 로그인 | 본인 | 당사자 | 관리자 | 차단 |
|---|---|---|---|---|---|
| 39 | 23 | 20 | 11 | 18 | 11 |

- **본인**: 자원 주인. 일부는 관리자도 허용합니다.
- **당사자**: 채팅방 참가자, 예약의 두 사람입니다.
- **차단**: 아무도 쓸 수 없습니다.

## 보호된 API 호출하기

로그인 응답의 `token`을 `Authorization: Bearer <token>` 헤더에 넣습니다. 토큰은 24시간 유효합니다(`jwt.expiration`).

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/api/users/login \
  -H "Content-Type: application/json" \
  -d '{"userId":"<아이디>","password":"<비밀번호>"}' | sed -E 's/.*"token":"([^"]+)".*/\1/')

# 내 정보 (userId 생략 = 본인)
curl http://localhost:8080/api/users/my-info -H "Authorization: Bearer $TOKEN"

# 남의 userId를 넣으면 관리자가 아닌 한 403
curl -i "http://localhost:8080/api/users/my-info?userId=someone-else" -H "Authorization: Bearer $TOKEN"
```

- **Swagger**: `/swagger-ui/index.html`의 **Authorize** 버튼에 토큰만 넣습니다. `Bearer `는 붙이지 않습니다.
- **관리자 API**: `admin`으로 로그인한 토큰을 씁니다. 관리자 비밀번호는 [domestic-tour-sync.md](domestic-tour-sync.md#실행)에 적힌 대로 `.env`의 값입니다.
- **STOMP**: SockJS `/ws`에 연결합니다. 토큰은 STOMP **CONNECT 프레임 헤더** `Authorization: Bearer <token>`에 넣습니다.
  - 브라우저 WebSocket은 HTTP 핸드셰이크에 헤더를 붙일 수 없습니다. 그래서 `/ws/**`는 HTTP 단계에서 열어 두고, STOMP 단계에서 검사합니다([`StompAuthChannelInterceptor`](../src/main/java/proj/travien/jwt/StompAuthChannelInterceptor.java)).
- **토큰이 없을 때**: 토큰이 없거나, `Bearer undefined`·`Bearer null`·무효·만료 토큰이면 비로그인으로 처리합니다.
  - 공개 경로는 그대로 열립니다.
  - 보호 경로는 401입니다.

## 오류 응답

```json
{"status":401,"error":"UNAUTHORIZED","message":"로그인이 필요합니다.","path":"/api/users/my-info"}
{"status":403,"error":"FORBIDDEN","message":"권한이 없습니다.","path":"/api/users/my-info"}
```

| 상태 | 언제 |
|---|---|
| `401` | 토큰이 없거나 만료·위조된 경우. 토큰의 사용자가 DB에서 삭제된 경우도 사용자가 필요한 API에서는 401입니다. |
| `403` | 로그인했지만 남의 자원이거나 관리자 전용인 경우. 차단 경로도 여기에 해당합니다. |
| `404`·`409`·`400` | 권한과 관계없는 기존 의미 그대로입니다. 예: 없는 게시글, 이미 쓰는 닉네임, 잘못된 진행 단계 |

- 컨트롤러·서비스가 던진 `AccessDeniedException`과 `AuthenticationCredentialsNotFoundException`도 같은 JSON이 됩니다.
- 새 API(여행지 읽기, 여행 일정, `/api/search`의 선택 값, 프로필 수정의 비밀번호 확인)의 `400`도 같은 모양이고, 틀린 칸마다 `errors` 항목이 붙습니다. 칸 이름 `errors`는 에이전트(`agent/src/glople_agent/plan/spring_store.py`)가 읽는 이름입니다.

```json
{"status":400,"error":"BAD_REQUEST","message":"일정 값이 올바르지 않습니다.","path":"/api/travel-plans",
 "errors":[{"field":"days","code":"out_of_range","message":"여행 일수는 1~30일이어야 합니다."},
           {"field":"places[1].position","code":"duplicate","message":"position 1이 겹칩니다."}]}
```

  - 여행지 읽기·여행 일정에서는 읽을 수 없는 JSON 본문(`field: body`)과 숫자가 아닌 경로·쿼리 값(`field: id`, `limit` 등)도 이 모양의 400입니다. 기존 API의 400은 그대로입니다.
- 넓은 `catch (RuntimeException)` 안에서 검사하면 403이 다른 상태로 바뀝니다. 그래서 검사는 항상 `try` 블록 앞에서 합니다(예: 게시글 수정).

## 규칙 순서 (SecurityConfig)

[`SecurityConfig`](../src/main/java/proj/travien/jwt/SecurityConfig.java)는 위에서부터 먼저 맞는 규칙을 적용합니다.

1. **차단**
   - 검증 서류 정적 경로 `/verification-files/**`. 대소문자와 호환 문자(NFKC)를 정규화한 뒤 비교합니다.
     - 그리고 `build.gradle`의 `processResources`가 `static/verification-files/**`를 클래스패스로 복사하지 않습니다. 그래서 스프링 부트 기본 정적 핸들러(`/** → classpath:/static/`)에는 서류가 아예 없습니다. Windows의 8.3 이름(`VERIFI~1`)·끝 점(`verification-files.`)처럼 URL 비교로는 다 막기 어려운 표기도 이것으로 막힙니다.
   - 레거시 `/ppap/**`
2. **관리자**
   - `/api/admin/**`
   - `POST /api/users/approve-agent/**`
   - `POST /api/users/*/points/add`, `POST /api/users/*/points/deduct`
   - `DELETE /api/bookReviews/**`
   - `POST /api/places`
3. **공개**
   - 계정: 회원가입, 로그인, 아이디·닉네임 중복 확인
   - 공개 대행자 프로필: `agents`, `search-agents-by-hashtag`, `recent-agents`, `profile-picture`
   - 추천: `POST /api/search`, `POST /api/info-recommend`
   - 여행지 읽기: `GET /api/destinations`, `GET /api/destinations/**` (목록·이름 찾기·상세. 같은 경로의 다른 메서드는 로그인 필요)
   - 읽기 전용 둘러보기: `GET /api/posts/**`, `GET /api/comments/**`, `GET /api/bookReviews/**`, `GET /api/likes/*/count`, `GET /api/likes/*/is-liked`, `GET /api/likes/best-reviews`
   - 외부 장소 검색 `GET /api/search-place`
   - 정적 파일: `/image/**`, `/profile-pictures/**`, `/`, `/index.html`, `/app.js`, `/chat.html`, `/data/**`, `/favicon.ico`
   - Swagger 문서, `/ws/**`, `/error`
4. **그 밖에는 로그인 필요**
   - 여행 일정 `/api/travel-plans/**`는 따로 규칙을 두지 않고 여기에 걸립니다. 주인 검사는 `TravelPlanService`가 합니다.

함께 바꾼 설정입니다.

- `.cors(withDefaults())`: 토큰을 붙인 교차 출처 요청은 브라우저가 OPTIONS 사전 요청을 먼저 보냅니다. 이 요청이 401로 막히지 않게 합니다.
- 세션 없음(`STATELESS`)
- `.logout(disable)`: 로그아웃은 프론트가 쿠키를 지워 처리합니다.

## 전체 목록

접근: 공개 = 비로그인 허용, 로그인 = 로그인 사용자, 본인 = 자원 주인(일부 관리자 포함), 당사자 = 채팅방 참가자·예약 당사자, 관리자 = ROLE_ADMIN, 차단 = 아무도 안 됨.

### 사용자 `/api/users`

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| U01 | POST | `/api/users/signup` | 공개 | 누구나(비로그인) |  |
| U02 | POST | `/api/users/apply-agent` | 본인 | 로그인한 본인(자기 계정으로만 신청) | 신청만으로는 ROLE_AGENT가 붙지 않음(승인 대기). 신청자가 보낸 별점은 무시 |
| U03 | POST | `/api/users/approve-agent/{userId}` | 관리자 | 관리자(ROLE_ADMIN) | URL 규칙 + @PreAuthorize. 대상이 관리자면 ROLE_ADMIN 유지 |
| U04 | GET | `/api/users/search-agents-by-hashtag` | 공개 | 누구나 | 승인된 대행자(승인일 있음)만 반환 |
| U05 | GET | `/api/users/recent-agents` | 공개 | 누구나 | 승인된 대행자만 반환 |
| U06 | POST | `/api/users/login` | 공개 | 누구나 | 토큰 subject = 로그인 아이디 |
| U07 | GET | `/api/users/check-userid` | 공개 | 누구나 |  |
| U08 | GET | `/api/users/check-nickname` | 공개 | 누구나 |  |
| U09 | POST | `/api/users/upload-profile-picture` | 본인 | 로그인한 본인 | userId 생략 가능. 쿼리·폼에 같은 값이 두 번 와 'id,id'가 되어도 본인으로 봄 |
| U10 | POST | `/api/users/upload-verification-file` | 본인 | 로그인한 본인 | userId 생략 가능, 본인 아니면 403 |
| U11 | POST | `/api/users/update-profile` | 본인 | 로그인한 본인 | 닉네임 중복(다른 사용자) 409, 바뀌면 내 게시글 Post.nickname도 함께 변경. 응답 {message, token}에 새 토큰. 요청의 profilePicture는 무시. 비밀번호(password)를 바꾸려면 현재 비밀번호(currentPassword)도 보내야 함: 없으면 400, 틀리면 403(둘 다 JSON, 아무 칸도 바꾸지 않음). password가 없거나 빈 문자열이면 비밀번호는 그대로 |
| U12 | POST | `/api/users/update-agent-details` | 본인 | 로그인한 본인(isAgent) | 요청의 averageReviewRating은 무시 |
| U13 | GET | `/api/users/agents` | 공개 | 누구나 | 승인된 대행자만 반환 |
| U14 | GET | `/api/users/my-info` | 본인 | 로그인한 본인 또는 관리자 | userId 생략 = 본인. 남의 userId는 관리자만 |
| U15 | GET | `/api/users/nicknames` | 로그인 | 로그인 사용자 |  |
| U16 | GET | `/api/users/profile-picture/{nickname}` | 공개 | 누구나 |  |
| U17 | GET | `/api/users/profile-picture` | 공개 | 누구나 |  |
| U18 | GET | `/api/users/verification-file/{userId}` | 본인 | 서류 주인 본인 또는 관리자 | 유일한 검증 서류 제공 경로. X-Content-Type-Options: nosniff |
| U19 | GET | `/api/users/{userId}/points` | 본인 | 본인 또는 관리자 | 남의 userId는 관리자만 |
| U20 | POST | `/api/users/{nickname}/points/add` | 관리자 | 관리자 | URL 규칙 + @PreAuthorize |
| U21 | POST | `/api/users/{nickname}/points/deduct` | 관리자 | 관리자 | URL 규칙 + @PreAuthorize |
| U22 | POST | `/api/users/transfer-points` | 본인 | 로그인한 본인(보내는 사람) — 받는 쪽은 예약 관계가 있는 대행자 | 보내는 사람 = 토큰 사용자(userNickname이 다르면 403). 받는 쪽은 나와 예약이 있는 승인 대행자(아니면 403), 자기 자신 400. 성공 시 서버가 isSended=true·progress 3 설정 |

### 관리자 `/api/admin`

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| A01 | POST | `/api/admin/adjust-points` | 관리자 | 관리자 |  |
| A02 | POST | `/api/admin/promote-user` | 관리자 | 관리자 |  |
| A03 | GET | `/api/admin/all-users` | 관리자 | 관리자 |  |
| A04 | DELETE | `/api/admin/delete-user/{userId}` | 관리자 | 관리자 |  |

### 데이터 동기화 `/api/admin/*-sync`

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| T01 | POST | `/api/admin/tour-sync/destinations` | 관리자 | 관리자 |  |
| T02 | POST | `/api/admin/tour-sync/overviews` | 관리자 | 관리자 |  |
| T03 | POST | `/api/admin/tour-sync/summaries` | 관리자 | 관리자 |  |
| T04 | GET | `/api/admin/tour-sync/status` | 관리자 | 관리자 |  |
| T05 | POST | `/api/admin/wiki-sync/destinations` | 관리자 | 관리자 |  |
| T06 | POST | `/api/admin/wiki-sync/descriptions` | 관리자 | 관리자 |  |
| T07 | POST | `/api/admin/wiki-sync/summaries` | 관리자 | 관리자 |  |
| T08 | GET | `/api/admin/wiki-sync/status` | 관리자 | 관리자 |  |

### 키워드 마스크 `/api/admin/keyword-masks`

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| K01 | POST | `/api/admin/keyword-masks/import` | 관리자 | 관리자 | URL 규칙 + @PreAuthorize. 키워드 태거 예측(JSON Lines·배열)을 여행지의 태거 열에 씀. 형식 오류 400 JSON(아무것도 안 씀), 동기화 중 409, 본문 형식이 JSON Lines·JSON·텍스트가 아니면 415. [keyword-tagger-serving.md](keyword-tagger-serving.md) |

### 대행 리뷰 `/api/bookReviews`

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| R01 | POST | `/api/bookReviews/agent/{nickname}` | 당사자 | 해당 대행자와 예약(Booking) 관계가 있는 로그인 사용자(대행자 본인 제외) | 작성자 = 토큰 사용자. 그 대행자와의 예약이 송금됨(isSended) 또는 여행 완료(4)여야 함. 별점 1~5(400) |
| R02 | GET | `/api/bookReviews/agent/{userId}` | 공개 | 누구나 |  |
| R03 | DELETE | `/api/bookReviews/{reviewId}` | 관리자 | 관리자 (writer 컬럼을 추가하면 작성자 본인도 허용) | 리뷰에 작성자 정보가 없어 관리자만 |
| R04 | GET | `/api/bookReviews/` | 공개 | 누구나 |  |
| R05 | GET | `/api/bookReviews/agent/review` | 공개 | 누구나 |  |

### 예약 `/api/booking`

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| B01 | POST | `/api/booking/update-progress` | 당사자 | 예약 당사자(두 닉네임 중 한 명이 본인) | 손님: 예약이 없거나 끝났을 때(4) 1만. 대행자: 있는 예약을 1·2·4로. 3은 송금된 예약만(4에서 되돌리기 금지). 1이 되면 송금 표시 초기화 |
| B02 | GET | `/api/booking/progress` | 당사자 | 예약 당사자 | 두 닉네임 중 하나가 본인(DB 닉네임) |
| B03 | GET | `/api/booking/all-progress` | 본인 | 본인 또는 관리자 | nickname 생략 = 본인. 남의 것은 관리자만 |
| B04 | POST | `/api/booking/set-sended` | 당사자 | 송금한 사용자 본인(userNickname) | 송금 표시는 이체가 설정. 이미 송금됐으면 200, 아니면 409, 예약 없으면 404 |
| B05 | GET | `/api/booking/check-sended` | 당사자 | 예약 당사자 | 두 닉네임 중 하나가 본인 |

### 채팅 REST `/chat`

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| C01 | GET | `/chat/history/{roomId}` | 당사자 | 채팅방 참가자 | UserChatRoom 행으로 참가 판정. 없는 방도 403(방 존재 여부를 숨김) |
| C02 | POST | `/chat/createRoom` | 로그인 | 로그인 사용자 — 두 닉네임 중 하나가 본인 | 목록에 본인 닉네임이 없으면 403, 상대 없으면 404(방 저장 전에 확인), 자기 자신 400 |
| C03 | GET | `/chat/userRooms` | 로그인 | 로그인 사용자(본인 목록만) | 토큰 사용자 기준(토큰 없으면 401) |
| C04 | POST | `/chat/uploadFile/{roomId}` | 당사자 | 채팅방 참가자 | 참가자만. sender가 본인이 아니면 403 |
| C05 | POST | `/chat/uploadVoiceMessage/{roomId}` | 당사자 | 채팅방 참가자 | 참가자만 |

### STOMP (`/ws` 연결 뒤)

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| S01 | CONNECT | `/ws (SockJS) → STOMP CONNECT` | 로그인 | 토큰이 있으면 그 사용자로 연결. 토큰 없이도 연결은 허용하되 개인 방 SEND/SUBSCRIBE는 거부. 잘못된 토큰은 CONNECT 거부 | 'Bearer undefined'·빈 값 = 익명 연결. 무효·만료 토큰은 ERROR 프레임으로 거부 |
| S02 | SUBSCRIBE | `/topic/public/{roomId}` | 당사자 | 채팅방 참가자 | 로그인 + UserChatRoom 참가자. roomId는 마지막 한 세그먼트 전체 비교 |
| S03 | SUBSCRIBE | `/topic/** 패턴·기타 목적지 (예: /topic/public/*, /topic/**)` | 차단 | 허용 안 함 | * ? { } 가 든 목적지와 허용 목록 밖 목적지 거부 |
| S04 | SEND | `/app/chat.sendMessage/{roomId}` | 당사자 | 채팅방 참가자 | 보낸 사람 = 연결 사용자의 DB 닉네임(클라이언트 sender 무시), id=null, type은 CHAT·FILE만, 10000자 제한 |
| S05 | SEND | `/app/chat.addUser/{roomId}` | 당사자 | 이미 참가자인 사용자만(참가자 추가는 C02에서만) | 참가자만. 참가자 등록은 하지 않음 |
| S06 | SUBSCRIBE | `/topic/public` | 로그인 | 로그인 사용자 (요구사항상 현 구조 유지 가능) | 로그인 필요. 이제 서버가 이 토픽에 아무것도 보내지 않음(챗봇은 /user/topic/chatbot) |
| S07 | SEND | `/app/chat.sendMessage (챗봇)` | 로그인 | 로그인 사용자 (요구사항상 현 구조 유지 가능) | 답은 @SendToUser("/topic/chatbot") → 보낸 사용자만 /user/topic/chatbot으로 받음 |
| S08 | SEND | `/app/chat.addUser (챗봇)` | 로그인 | 로그인 사용자 | JOIN 응답에 token을 싣지 않음(DTO의 token은 쓰기 전용). 보낸 사람 = 연결 사용자, 본인에게만 전달 |
| S09 | SEND | `/topic/** (브로커 목적지로 직접 SEND)` | 차단 | 허용 안 함(서버만 /topic에 발행) | SEND는 /app/ 허용 목록만 |
| S10 | SUBSCRIBE/SEND | `/user/** (사용자 목적지, @EnableWebSocketMessageBroker 기본 접두사)` | 차단 | 허용 안 함. 챗봇을 @SendToUser로 바꾸면 SUBSCRIBE '/user/topic/chatbot'(Principal 필수)만 예외 | SUBSCRIBE는 /user/topic/chatbot(로그인)만 예외, SEND는 모두 거부 |
| S11 | MESSAGE·CONNECTED·RECEIPT·ERROR | 클라이언트가 보낸 서버 전용 프레임 (목적지 무관) | 차단 | 허용 안 함 | MESSAGE 프레임은 SEND와 메시지 종류(MESSAGE)가 같아, 막지 않으면 SEND 검사를 건너뛰고 SimpleBroker가 남의 방(`/topic/public/{roomId}`)에 가짜 메시지를 방송하고 `/user/{이름}/...`은 그 사용자 세션으로 전달함. 인터셉터가 ERROR 프레임으로 거부 |

### 댓글 `/api/comments`

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| M01 | POST | `/api/comments/` | 로그인 | 로그인 사용자(작성자 = 본인) | body.userId가 본인이 아니면 403 |
| M02 | GET | `/api/comments/{boardID}` | 공개 | 누구나 |  |
| M03 | GET | `/api/comments/{boardID}/count` | 공개 | 누구나 |  |
| M04 | DELETE | `/api/comments/{commentId}` | 본인 | 댓글 작성자 본인 또는 관리자 | 작성자 본인 또는 관리자 |

### 좋아요 `/api/likes`

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| L01 | POST | `/api/likes/toggle` | 로그인 | 로그인 사용자(본인 좋아요만) | id 생략 가능, 본인이 아니면 403 |
| L02 | GET | `/api/likes/{boardID}/is-liked` | 공개 | 누구나(좋아요 여부는 토큰 사용자 기준) | 비로그인은 항상 false. 로그인 사용자가 남의 id를 보내면 403 |
| L03 | GET | `/api/likes/{boardID}/count` | 공개 | 누구나 |  |
| L04 | GET | `/api/likes/best-reviews` | 공개 | 누구나 |  |
| L05 | GET | `/api/likes/user/{id}` | 본인 | 본인 또는 관리자 | 본인 또는 관리자 |

### 게시글 `/api/posts`

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| P01 | POST | `/api/posts/upload` | 로그인 | 로그인 사용자 |  |
| P02 | POST | `/api/posts/` | 로그인 | 로그인 사용자(작성자 = 본인) | userId 생략 가능, 본인이 아니면 403 |
| P03 | GET | `/api/posts/addresses/{placename}` | 공개 | 누구나 |  |
| P04 | GET | `/api/posts/{id}/` | 공개 | 누구나 |  |
| P05 | GET | `/api/posts/{country}` | 공개 | 누구나 |  |
| P06 | GET | `/api/posts/` | 공개 | 누구나 |  |
| P07 | PUT | `/api/posts/{boardID}` | 본인 | 게시글 작성자 본인 또는 관리자 (작성자 = post.nickname이 본인 DB 닉네임) | 작성자 검사를 try 앞에서 함(403이 404로 바뀌지 않게). 없는 글 404 |
| P08 | DELETE | `/api/posts/{id}/` | 본인 | 게시글 작성자 본인 또는 관리자 (작성자 = post.nickname이 본인 DB 닉네임) | 작성자 본인 또는 관리자. 없는 글 404 |
| P09 | POST | `/api/posts/{id}/report/` | 로그인 | 로그인 사용자 |  |
| P10 | GET | `/api/posts/{id}/image` | 공개 | 누구나 | 공개 유지(남는 위험 참고) |
| P11 | POST | `/api/posts/{boardID}/thumbnail` | 본인 | 게시글 작성자 본인 또는 관리자 (작성자 = post.nickname이 본인 DB 닉네임) | 작성자 본인 또는 관리자 |
| P12 | GET | `/api/posts/{boardID}/thumbnail` | 공개 | 누구나 |  |
| P13 | GET | `/api/posts/search` | 공개 | 누구나 |  |
| P14 | GET | `/api/posts/user/{nickname}` | 공개 | 누구나 |  |
| P15 | GET | `/api/posts/titles/{placeName}` | 공개 | 누구나 |  |

### 레거시 `/ppap`

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| PA1 | POST | `/ppap/board` | 차단 | 허용 안 함(레거시, 프론트 미사용) | /ppap/** denyAll |
| PA2 | DELETE | `/ppap/board/{id}` | 차단 | 허용 안 함(레거시, 프론트 미사용) | /ppap/** denyAll |
| PA3 | PUT | `/ppap/board/{id}` | 차단 | 허용 안 함(레거시, 프론트 미사용) | /ppap/** denyAll |
| PA4 | GET | `/ppap/board/{boardId}` | 차단 | 허용 안 함(레거시, 프론트 미사용) | /ppap/** denyAll |
| PA5 | GET | `/ppap/board/{id}/updateForm` | 차단 | 허용 안 함(레거시, 프론트 미사용) | /ppap/** denyAll |
| PA6 | GET | `/ppap/board/saveForm` | 차단 | 허용 안 함(레거시, 프론트 미사용) | /ppap/** denyAll |
| PA7 | GET | `/ (및 빈 경로)` | 공개 | 누구나 |  |

### 기타 API

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| F01 | POST | `/api/fileUpload/` | 로그인 | 로그인 사용자 |  |
| FO1 | POST | `/api/follow/follow-agent` | 로그인 | 로그인 사용자(팔로우하는 사람 = 본인) | userNickname 생략 가능, 본인이 아니면 403 |
| FO2 | GET | `/api/follow/followed-agents` | 본인 | 본인 또는 관리자 | userId 생략 = 본인. 남의 것은 관리자만 |
| N01 | POST | `/api/navigations` | 로그인 | 로그인 사용자 |  |
| N02 | GET | `/api/navigations` | 로그인 | 로그인 사용자 |  |
| PL1 | POST | `/api/places` | 관리자 | 관리자(프론트 미사용 전역 데이터) | URL 규칙 + @PreAuthorize |
| PL2 | GET | `/api/places` | 로그인 | 로그인 사용자 |  |
| PL3 | GET | `/api/search-place` | 공개 | 누구나(경로 탐색 화면이 비로그인으로 호출) |  |
| RC1 | POST | `/api/search` | 공개 | 누구나 | 선택 값 scope·limit·seed가 틀리면 400 JSON |
| RC2 | POST | `/api/info-recommend` | 공개 | 누구나 |  |
| RC3 | POST | `/api/save-route` | 로그인 | 로그인 사용자(본인 경로만) | body.userId가 본인이 아니면 403, 저장 값은 토큰 사용자 |
| RC4 | GET | `/api/get-routes` | 본인 | 본인 또는 관리자 | userId 생략 = 본인. 남의 것은 관리자만 |
| SP1 | POST | `/api/convert` | 로그인 | 로그인 사용자 | 프론트 미사용 |
| CI1 | GET | `/api/country-info` | 로그인 | 로그인 사용자 | 프론트 미사용이라 공개에서 뺌 |
| WK1 | GET | `/api/wikipedia` | 로그인 | 로그인 사용자 | 프론트 미사용이라 공개에서 뺌 |
| TE1 | GET | `/api/data` | 로그인 | 로그인 사용자(테스트용, 삭제 권장) |  |

### 여행지 읽기 `/api/destinations`

저장된 여행지(`destination_info`)를 읽기만 합니다. 화면과 에이전트 도구(search_destinations, get_destination_detail)가 같이 씁니다.

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| DS1 | GET | `/api/destinations` | 공개 | 누구나 | region·category·query·limit(1~50)·seed 모두 선택. 틀린 값 400 JSON |
| DS2 | GET | `/api/destinations/lookup` | 공개 | 누구나 | name 필수(없으면 400). 같은 이름이 여러 곳이면 최대 10곳 |
| DS3 | GET | `/api/destinations/{id}` | 공개 | 누구나 | 없는 id 404 JSON(공개 데이터라 존재를 숨기지 않음) |

### 여행 일정 `/api/travel-plans`

주인 = 토큰 사용자(`currentUser.requireUser()`)입니다. 요청 본문에는 userId 칸이 없고, 보내도 무시합니다. 존재 여부는 C01(채팅 기록)처럼 숨깁니다.

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| TP1 | POST | `/api/travel-plans` | 로그인 | 로그인 사용자(주인 = 본인) | 새로 저장 201 + Location. 같은 사용자의 같은 clientRequestId는 200으로 처음 일정(UNIQUE(user_id, client_request_id), 새 행 없음). 값 검사가 먼저라 틀린 값은 같은 키여도 400 |
| TP2 | GET | `/api/travel-plans` | 본인 | 로그인한 본인(내 일정만) | 다른 사용자를 고르는 파라미터 없음. 관리자도 자기 일정만. 최근 저장 순 |
| TP3 | GET | `/api/travel-plans/{id}` | 본인 | 일정 주인 또는 관리자 | 남의 일정·없는 일정 모두 403(존재 여부 숨김). 관리자도 없는 일정은 403 |
| TP4 | DELETE | `/api/travel-plans/{id}` | 본인 | 일정 주인만 | 204. 남의 일정·없는 일정 403. 관리자도 남의 일정은 403 |

### 정적 파일·문서·기타 경로

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| ST1 | GET | `/image/**` | 공개 | 누구나(<img> 태그는 토큰을 보내지 않음) |  |
| ST2 | GET | `/profile-pictures/**` | 공개 | 누구나 |  |
| ST3 | GET | `/verification-files/**` | 차단 | 정적 경로로는 아무도 안 됨(본인·관리자는 U18 엔드포인트로) | WebConfig 매핑 삭제 + denyAll. 대소문자·호환 문자(NFKC)를 정규화해 비교(/VERIFICATION-FILES/... 우회 차단). build.gradle processResources에서 제외해 클래스패스 정적 리소스에도 없음 |
| ST4 | GET | `/index.html, /app.js, /chat.html, /data/**, /favicon.ico` | 공개 | 누구나 |  |
| ST5 | GET | `/swagger-ui/**, /swagger-ui.html, /v3/api-docs/**` | 공개 | 누구나(설계 결정) |  |
| ST6 | GET/POST | `/ws/** (SockJS 핸드셰이크·info·전송)` | 공개 | 누구나(HTTP 단계). STOMP 단계에서 S01~S11로 검사 | HTTP 단계는 공개, STOMP 단계에서 검사 |
| ST7 | ANY | `/error` | 공개 | 누구나 | 필수(오류 응답이 401로 덮이지 않게) |
| ST8 | ANY | `/logout` | 차단 | 허용 안 함(기능을 끔). 로그아웃은 프론트가 jwt 쿠키 삭제로 처리(main.js:113) | .logout(disable). 비로그인 401, 로그인 404 |

### 여행 도우미 에이전트 `/ai-api/v1` (agent/, Spring 밖)

LangGraph 에이전트 서비스(`agent/src/glople_agent/service/`, FastAPI `127.0.0.1:8095`)의 경로입니다. Spring `SecurityConfig` 밖이라 위 접근 수준별 개수와 `AuthorizationMatrixTest`에는 들어가지 않고, 같은 원칙을 에이전트가 직접 지킵니다(설계: `agent/docs/design.md` 10.2).

- **요청자**: 모든 요청(AI1 빼고)의 `Authorization` 헤더를 Spring `GET /api/users/my-info`에 그대로 보내 확인하고 `userId`만 씁니다. 에이전트는 JWT 비밀키가 없습니다. `sha256(토큰)` 키로 60초 메모리 캐시. `Bearer undefined`·`Bearer null`·빈 값·헤더로 보낼 수 없는 글자는 Spring에 묻지 않고 401, Spring이 401이면 401, Spring에 닿지 않으면 503.
- **행위자 파라미터**: 새 API라 **아예 받지 않습니다**. 모든 본문이 `extra="forbid"`라 `userId`·`nickname` 같은 칸이 오면 422입니다(Spring의 '받기만 함'과 다름).
- **자원 주인**: 대화(thread)는 에이전트의 주인 표(`agent/var/service/threads.sqlite`의 `threads(thread_id, owner_user_id, …)`)로 판정합니다. thread_id는 서버가 만든 UUID4입니다. **남의 대화·없는 대화·모양이 틀린 id는 모두 같은 403**(C01처럼 존재 여부를 숨김). 일정 검토의 일정은 Spring이 판정합니다(TP3을 사용자 토큰으로 부름).
- **오류 모양**: Spring과 같은 `{status, error, message, path}`에 기계용 `code`(예: `THREAD_BUSY`)를 더합니다. 422의 `errors`에는 칸 이름과 종류만 있고 입력 값은 없습니다.
- **토큰이 가는 곳**: Spring 로그인 경로(my-info, travel-plans)의 그 요청 헤더뿐입니다. 공개 API·모델 서버·체크포인트·주인 표·로그·SSE에는 없습니다(`agent/tests/test_service_hygiene.py`).
- **비로그인**: 지금은 허용하지 않습니다(AI1만 공개). 허용 여부는 오너 결정입니다(허용하면 읽기 도구만, 일정 저장 진입 불가).

| ID | 메서드 | 경로 | 접근 | 허용 대상 | 구현 메모 |
|---|---|---|---|---|---|
| AI1 | GET | `/ai-api/v1/health` | 공개 | 누구나 | `{status, llm: up·down·rule, spring: up·down}`. 내부 주소 없음(TCP 연결만 확인) |
| AI2 | POST | `/ai-api/v1/threads` | 로그인 | 로그인 사용자(주인 = 본인) | 201 `{threadId}`. 본문 없음(칸이 있으면 422). 사용자당 100개 |
| AI3 | GET | `/ai-api/v1/threads` | 본인 | 로그인한 본인(내 대화만) | 다른 사용자를 고르는 파라미터 없음. 최근 순 |
| AI4 | GET | `/ai-api/v1/threads/{id}` | 본인 | 대화 주인 | 화면 복원(턴·카드·대기 중인 확인 카드). 남의 것·없는 것 403 |
| AI5 | DELETE | `/ai-api/v1/threads/{id}` | 본인 | 대화 주인 | 204, 체크포인트와 주인 표 행을 함께 지움. 실행 중이면 409 |
| AI6 | POST | `/ai-api/v1/threads/{id}/messages` | 본인 | 대화 주인 | SSE. 본문 `{text(1~500자), clientMessageId?}`. 실행 중 409 `THREAD_BUSY`, 같은 clientMessageId 409, 사용자별 10분 20번(429) |
| AI7 | POST | `/ai-api/v1/threads/{id}/resume` | 본인 | 대화 주인 | SSE. 본문 `{interruptId, decision: approve·edit·reject, draft?}`. 대기 카드 id가 아니면 409 `INTERRUPT_NOT_PENDING`, 24시간 지난 카드 409 `CONFIRM_EXPIRED`. 일정 저장은 save_plan이 사용자 토큰으로 TP1 |
| AI8 | POST | `/ai-api/v1/threads/{id}/cancel` | 본인 | 대화 주인 | 202 `{cancelled}`. 실행 중인 턴을 멈춤 |
| AI9 | POST | `/ai-api/v1/plans/{planId}/review` | 본인 | 일정 주인(Spring TP3이 판정) | 일정 AI 검토. Spring 403·404 → 403, 401 → 401. planId는 양의 정수(아니면 422). 본문 없음 |

## 예약·송금·리뷰 규칙

손님이 진행 단계나 송금 표시를 스스로 조작하지 못하게 서버가 정합니다. 진행 단계는 1 예약 요청, 2 대행자 수락, 3 입금 완료, 4 여행 완료입니다.

| 누가 | 할 수 있는 것 |
|---|---|
| 손님(승인된 대행자가 아닌 쪽) | 승인된 대행자와 예약이 없거나 끝났을 때(4) `progress=1`로 새 예약 요청 |
| 승인된 대행자 | 손님이 만든 예약을 1(초기화)·2·4로 변경 |
| 둘 다 | 3은 포인트 이체로 송금 표시가 된 예약에서만. 4를 3으로 되돌릴 수는 없음 |

- **포인트 이체**(`POST /api/users/transfer-points`): 보내는 사람과 예약이 있는 승인 대행자에게만 보낼 수 있습니다.
  - 차감·적립·송금 표시(`isSended=true`)·진행 단계 3을 한 트랜잭션에서 처리합니다.
  - `set-sended`는 이미 송금된 예약이면 200, 아니면 409입니다. 송금 표시를 직접 만들지는 못합니다.
  - 지금 프론트 흐름(이체 → set-sended → progress 3)은 그대로 동작합니다.
- **초기화**: 진행 단계가 1이 되면 송금 표시도 지웁니다. 다음 예약에서 송금 없이 3을 찍지 못하게 하기 위해서입니다.
- **리뷰**(`POST /api/bookReviews/agent/{nickname}`): 그 대행자와의 예약이 송금됨이거나 여행 완료(4)여야 쓸 수 있습니다.
- **승인된 대행자**: `isAgent=true`이고 승인일(`agentApprovedDate`)이 있는 사용자입니다.
  - 대행자 신청(`apply-agent`)만으로는 승인되지 않습니다. 관리자가 `POST /api/users/approve-agent/{userId}`로 승인해야 합니다.
  - 승인 전 신청자는 공개 대행자 목록, 이체 대상, 예약의 대행자 권한, 리뷰 대상에서 빠집니다.

## 프론트엔드에서 바뀐 것

프론트는 별도 작업으로 바뀝니다. 백엔드가 기대하는 동작은 다음과 같습니다.

### 요청 헤더

- 백엔드로 가는 모든 요청에 `Authorization: Bearer <jwt 쿠키>`를 붙입니다.
  - 상대 경로 `/api…`, `/chat…`와 `http://localhost:8080/…` 모두 해당합니다.
  - axios 인터셉터 하나와 `fetch` 래퍼로 처리합니다.
- **외부 호출에는 토큰을 붙이지 않습니다.** 예: ko.wikipedia.org, restcountries.com
- 여행 도우미 에이전트(`/ai-api/v1/...`)도 같은 출처 상대 경로라 같은 규칙으로 토큰이 붙습니다. 개발 서버는 `setupProxy.js`가 `/ai-api`를 `http://127.0.0.1:8095`(`AGENT_URL`)로 넘깁니다. 화면: `/assistant`(대화), `/my/plans`(내 여행 일정, TP2~TP4 + AI9).
- 쿠키가 없으면 헤더를 빼는 것이 맞습니다. 서버는 `Bearer undefined`를 토큰 없음으로 처리하지만, 그 값을 보낼 이유가 없습니다.

### 401·403 처리

- 401이 오면, 인터셉터가 쿠키 토큰을 실어 보낸 요청이었을 때만(만료·무효) 쿠키를 지우고 `/login`으로 보냅니다.
- 토큰 없이 보낸 요청의 401은 그대로 실패로 둡니다. 비로그인 공개 화면이 선택적으로 부르는 호출이 이 경우라서, 공개 화면이 깨지지 않습니다.
- 403은 이동하지 않습니다.

### 프로필 수정 응답

`POST /api/users/update-profile`은 이제 JSON `{"message": "...", "token": "<새 토큰>"}`을 돌려줍니다.

- 비밀번호를 바꿀 때만 `password`(새 비밀번호)와 `currentPassword`(현재 비밀번호)를 함께 보냅니다. 화면(`myprofilemodify.js`)은 '새 비밀번호' 칸이 비어 있으면 둘 다 보내지 않습니다. 현재 비밀번호가 없으면 400, 틀리면 403입니다.

- 응답의 `token`으로 `jwt` 쿠키를 바꿉니다. 그러지 않으면 닉네임을 바꾼 뒤 옛 `nickname` 클레임 때문에 예약·채팅·이체·팔로우 호출이 403이 됩니다.
- 다른 사용자가 쓰는 닉네임이면 409입니다.
- 요청의 `profilePicture`는 무시합니다. 프로필 사진은 `upload-profile-picture`로만 바꿉니다.

### 챗봇

- 답은 보낸 사람에게만 갑니다. 구독 목적지를 `/topic/public`에서 **`/user/topic/chatbot`**으로 바꿔야 답을 받습니다.
- `/topic/public` 구독은 로그인 사용자에게 허용되지만, 서버는 더 이상 그 토픽으로 보내지 않습니다.
- JOIN 본문의 `token`은 서버가 읽지 않고 응답에도 싣지 않습니다. 보내지 않아도 됩니다.

### 1:1 채팅

- 지금처럼 CONNECT 헤더에 토큰을 넣습니다. `/topic/public/{roomId}`는 그 방 참가자만 구독할 수 있습니다.
- 메시지의 `sender`는 서버가 연결 사용자의 닉네임으로 덮어씁니다.
- 메시지 종류는 `CHAT`·`FILE`만 받습니다.
- 토큰 없이(`Bearer undefined`) 연결해도 연결은 됩니다. 다만 방 구독·전송은 ERROR 프레임으로 거부됩니다.
- 만료·위조 토큰으로 CONNECT하면 연결이 거부됩니다. 챗봇 클라이언트(`reconnectDelay: 5000`)는 이때 5초마다 다시 연결을 시도하므로, 401을 받았을 때처럼 쿠키를 지우고 로그인으로 보내는 편이 낫습니다.

### 검증 서류

정적 경로 `/verification-files/**`는 막혔습니다. 본인이나 관리자는 다음처럼 받습니다.

```js
axios.get(`/api/users/verification-file/${userId}`, { responseType: 'blob' })
```

### 로그인이 필요해진 API

- `GET /api/users/nicknames`
- `GET /api/wikipedia`, `GET /api/country-info`. 프론트는 지금 이 둘을 쓰지 않습니다.

## 데이터·운영 주의

- **기존 대행자는 관리자 승인이 필요합니다.** 지금까지는 신청만으로 `ROLE_AGENT`가 붙었고 승인일이 비어 있는 행이 있을 수 있습니다. 이 사용자들은 관리자가 승인하기 전까지 목록과 이체 대상에서 빠집니다.
  - 관리자 토큰으로 `POST /api/users/approve-agent/{userId}`를 호출하면 됩니다.
  - 로컬 DB는 확인하지 않았습니다.
- **수정 전 토큰**: `subject`가 없지만 만료 전까지 그대로 쓸 수 있습니다. 새 토큰은 로그인하거나 프로필을 수정할 때 발급됩니다.
- **역할 변경 반영**: 역할(`role`)은 토큰에 들어 있습니다. 관리자 승격·대행자 승인 결과는 그 사용자가 다시 로그인한 뒤 토큰에 반영됩니다.
  - 대행자 판정은 토큰이 아니라 DB 승인일로 하므로, 승인은 바로 반영됩니다.
- **삭제된 사용자**: 그 사용자의 토큰은 서명상 만료 전까지 유효합니다. 하지만 사용자가 필요한 API에서는 DB에서 찾지 못해 401입니다.
  - 관리자 URL 규칙(`/api/admin/**` 등)은 토큰의 `role`만 봅니다. 그래서 삭제된 관리자 계정의 토큰은 만료(`jwt.expiration`) 전까지 관리자 API를 계속 부를 수 있습니다. 바로 막으려면 필터에서 DB 역할을 다시 읽어야 합니다(오너 결정).

## 남은 위험

이번 범위 밖이거나 오너가 결정할 항목입니다.

- **토큰 저장 위치**: JWT를 HttpOnly가 아닌 쿠키(`js-cookie`)에 둡니다. 게시글 본문은 HTML로 렌더링하므로 XSS가 생기면 토큰이 새어 나갑니다.
- **비밀번호 변경**: 2026-10-18부터 현재 비밀번호를 확인합니다(U11). 남은 점은 두 가지입니다.
  - 현재 비밀번호를 틀리는 횟수에 제한이 없습니다(아래 '요청 수 제한'과 같은 문제). 토큰이 있어야 시도할 수 있습니다.
  - 비밀번호를 바꿔도 이미 발급된 토큰은 만료(24시간) 전까지 유효합니다. 응답의 새 토큰과 별개로 옛 토큰을 끊는 장치가 없습니다.
- **채팅 첨부 파일**: `/image/<UUID>`로 공개 서빙됩니다. 참가자 전용으로 하려면 별도 다운로드 엔드포인트가 필요합니다.
- **리뷰 작성자**: 리뷰 엔티티에 작성자 컬럼이 없습니다.
  - 그래서 리뷰 삭제는 관리자만 할 수 있습니다.
  - 송금된 예약 하나로 리뷰를 여러 번 쓸 수 있습니다.
- **채팅방 이름**: 채팅방 id는 `닉네임A_닉네임B` 문자열입니다.
  - 닉네임을 바꾸면 같은 상대와 새 방이 생깁니다. 옛 방은 `userRooms`에 그대로 남습니다.
  - 다른 사람이 옛 닉네임을 가져가면 그 사람과 상대는 같은 이름의 방을 새로 만들 수 없습니다.
  - 열람 권한은 `UserChatRoom` 행으로 판정하므로 남의 대화는 보이지 않습니다.
- **대행자끼리의 예약**: 두 사람이 모두 승인된 대행자이면 양쪽 모두 대행자 권한(2·4)을 가집니다.
- **검증 서류 교체**: 승인 뒤에 본인이 서류를 바꿔도 승인 상태가 유지됩니다.
- **글 작성 포인트**: 글 1개당 +10포인트라 작성·삭제를 반복해 포인트를 만들 수 있습니다. 인가 밖의 업무 규칙입니다.
- **게시글 이미지**: `GET /api/posts/{id}/image`는 지금 항상 500입니다. `imageUrl` 덮어쓰기를 고치면 저장된 경로로 서버 파일을 읽게 되므로, 남길 거면 업로드 폴더 안인지 검사합니다.
- **좋아요 목록**: 좋아요한 사람 목록은 공개 게시글 JSON(`likes[].user`, `likeUserIds`)에도 실려 있습니다.
- **API 키**: `PlaceController`에 Google API 키가 하드코딩돼 있습니다. `.env`로 옮기고 키를 교체할지는 오너가 정합니다.
- **요청 수 제한**: 로그인·외부 프록시에 요청 수 제한이 없습니다.
- **승인 상태 표시**: `my-info`는 승인 여부를 따로 내려주지 않습니다. 프론트는 `agent`(신청 여부)만 봅니다.
- **여행 일정 동시 저장**: 같은 키 요청 두 개가 동시에 오면 둘째 요청은 UNIQUE 위반 뒤 먼저 저장된 일정을 다시 읽어 200으로 답합니다. 이 경로는 저장소를 목으로 둔 테스트로만 확인했습니다(실제 MySQL에서는 확인 못 함).
- **권한과 무관한 기존 버그**
  - `update-profile`이 보내지 않은 `name`을 null로 덮습니다.
  - `recent-agents`는 null 값이 있으면 500입니다(`Map.of`).

## 테스트

모두 DB 없이 실행합니다.

- **토큰·현재 사용자**
  - `JwtTokenProviderTest`: subject = 로그인 아이디, 수정 전 토큰, `Bearer undefined`
  - `CurrentUserTest`
- **HTTP 인가 매트릭스**: `AuthorizationMatrixTest`
  - `@WebMvcTest`에 실제 `SecurityConfig`·JWT 필터·`CurrentUser`를 올리고, 테스트 비밀값으로 토큰을 만듭니다.
  - 비로그인 401 / 공개 200
  - 남의 자원 403 / 내 자원 2xx
  - 일반 사용자의 관리자 경로 403 / 관리자 2xx
  - CORS 사전 요청, 검증 서류 대소문자·합자 우회, 게시글 수정의 403이 404로 바뀌지 않는지
- **STOMP**
  - `StompAuthChannelInterceptorTest`: CONNECT 토큰, 남의 방·와일드카드·사용자 목적지 구독 거부, `/topic` 직접 SEND 거부, 클라이언트가 보낸 MESSAGE 등 서버 전용 프레임 거부
  - `StompSessionFlowTest`: 실제 `StompSubProtocolHandler`에서 CONNECT 사용자가 이후 프레임에 남는지, 거부 시 ERROR 프레임, MESSAGE 프레임 주입이 채널로 가지 않는지
  - `ChatMessagingAuthorizationTest`: sender 덮어쓰기, 챗봇 JOIN에 토큰 없음, `@SendToUser`
- **서비스 규칙**
  - `BookingProgressRulesTest`
  - `UserServiceAuthorizationTest`: 신청·승인, 닉네임 변경, 비밀번호 변경(현재 비밀번호), 이체
  - `ContentOwnershipTest`: 게시글·댓글·리뷰·채팅 참가자
- **여행지 읽기·여행 일정**
  - `TravelPlanControllerTest`: 실제 SecurityConfig·CurrentUser·TravelPlanService, 저장소만 목. 비로그인 401, 201/200(같은 키), 동시 저장, 400 JSON, 남의 것·없는 것 403, 관리자 조회·삭제 규칙
  - `TravelPlanServiceTest`: 값 한도(글자 수 = 코드 포인트), 저장 값
  - `DestinationControllerTest`: 비로그인 공개, 응답 칸, 400·404 JSON, GET만 공개
  - `JpaMappingWithoutDatabaseTest`: DB 연결 없이 Hibernate(MySQL 방언)로 엔티티 매핑·저장소 파생 쿼리를 검사하고, `travel_plan`의 UNIQUE(user_id, client_request_id)·외래 키를 DDL 문자열로 확인
- **키워드 태거 예측 가져오기**
  - `KeywordMaskControllerTest`: 실제 SecurityConfig·KeywordTaggerImportService, 저장소·동기화 실행기만 목. 관리자 200, 일반 사용자 403, 비로그인 401, 형식 오류 400 JSON, 동기화 중 409, form 본문 415
  - `AuthorizationMatrixTest`의 관리자 경로 403·비로그인 401 목록에도 들어 있음

```bash
sh gradlew test --offline --console=plain \
  --tests "proj.travien.jwt.JwtTokenProviderTest" \
  --tests "proj.travien.jwt.CurrentUserTest" \
  --tests "proj.travien.jwt.StompAuthChannelInterceptorTest" \
  --tests "proj.travien.jwt.StompSessionFlowTest" \
  --tests "proj.travien.controller.AuthorizationMatrixTest" \
  --tests "proj.travien.controller.ChatMessagingAuthorizationTest" \
  --tests "proj.travien.controller.UserControllerTest" \
  --tests "proj.travien.service.BookingProgressRulesTest" \
  --tests "proj.travien.service.UserServiceAuthorizationTest" \
  --tests "proj.travien.service.ContentOwnershipTest" \
  --tests "proj.travien.controller.TravelPlanControllerTest" \
  --tests "proj.travien.controller.DestinationControllerTest" \
  --tests "proj.travien.service.TravelPlanServiceTest" \
  --tests "proj.travien.repository.JpaMappingWithoutDatabaseTest" \
  --tests "proj.travien.controller.KeywordMaskControllerTest"
```

에이전트 경로(AI1~AI9)는 `agent/`의 pytest가 가짜 Spring(respx)으로 확인합니다: `cd agent && .venv/bin/pytest -q tests/test_service_api.py tests/test_service_hygiene.py tests/test_service_review.py`(토큰 없음·무효 401, Spring 장애 503, 남의 대화·없는 대화 403, 행위자 칸 422, 남의 일정 검토 403, sentinel 토큰이 저장소·로그·SSE에 0건).

> `proj.travien.repository.UserRepositoryTest`, `TravienApplicationTests`는 실제 DB(`glople-mysql`)에 붙습니다. 그래서 위처럼 클래스를 지정해 실행합니다. `--tests "proj.travien.repository.*"`처럼 패키지 전체를 고르면 `UserRepositoryTest`가 같이 돌아 실제 DB에 테스트 사용자를 남기므로 쓰지 않습니다.

## 관련 파일

- [`jwt/SecurityConfig.java`](../src/main/java/proj/travien/jwt/SecurityConfig.java): URL 규칙, 401·403 JSON, CORS, 세션 없음, 로그아웃 끔
- [`jwt/CurrentUser.java`](../src/main/java/proj/travien/jwt/CurrentUser.java): 현재 사용자, 본인·관리자 확인
- [`jwt/JwtUtil.java`](../src/main/java/proj/travien/jwt/JwtUtil.java), [`jwt/JwtTokenProvider.java`](../src/main/java/proj/travien/jwt/JwtTokenProvider.java): subject, 수정 전 토큰 처리, `Bearer undefined`
- [`jwt/StompAuthChannelInterceptor.java`](../src/main/java/proj/travien/jwt/StompAuthChannelInterceptor.java), [`WebSocketConfig.java`](../src/main/java/proj/travien/WebSocketConfig.java): STOMP 인증·인가
- [`service/ChatAccessService.java`](../src/main/java/proj/travien/service/ChatAccessService.java): 채팅방 참가자 판정
- [`service/TravelPlanService.java`](../src/main/java/proj/travien/service/TravelPlanService.java), [`controller/TravelPlanController.java`](../src/main/java/proj/travien/controller/TravelPlanController.java): 여행 일정 주인 검사, 멱등 저장
- [`controller/ApiErrorAdvice.java`](../src/main/java/proj/travien/controller/ApiErrorAdvice.java): 새 API의 400 JSON(`errors`)
- [`config/WebConfig.java`](../src/main/java/proj/travien/config/WebConfig.java): 검증 서류 정적 매핑 삭제. 기존 파일은 지우지 않았습니다. 히스토리 정리는 오너가 결정합니다.
- [`build.gradle`](../build.gradle): `processResources`에서 `static/verification-files/**` 제외(클래스패스 정적 리소스로 복사하지 않음)
