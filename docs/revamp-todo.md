# Glople 개편 남은 작업 (2026-10-17 점검)

코드와 문서를 직접 읽는 점검 4갈래(백엔드, 프론트엔드, 데이터·AI, 포트폴리오·라이선스)로 찾은 항목입니다. 점검은 읽기 전용으로 했고, 근거는 점검 시점의 파일 위치입니다.

- **누가**: 직접 = 오너만 할 수 있는 결정이나 작업, Claude = Claude가 할 수 있음, 함께 = 오너 결정 + Claude 작업
- **크기**: S 짧음 / M 반나절~하루 / L 여러 날

## P0 · 꼭 먼저 (5개)

### 백엔드

- **User 엔티티 직렬화로 비밀번호 해시·생년월일이 공개 API에 노출됨** · 누가: Claude · 크기: S
  - 왜: 인증 없이 열린 목록 API가 엔티티를 그대로 JSON으로 내보냅니다. 그래서 좋아요나 리뷰를 남긴 모든 사용자의 BCrypt 해시, 이름, 생년월일, 성별이 응답에 실립니다. 데모나 배포 전에 반드시 막아야 하고, 한 줄로 고칠 수 있습니다.
  - 근거: User.java:21 password에 @JsonIgnore가 없습니다(src 전체에서 grep JsonIgnore 0건). Post.java:48-50 likes(@JsonManagedReference) → Like.java:24-26 user(@ManyToOne EAGER, 무시 설정 없음) → User 전체가 직렬화됩니다. 노출 경로는 GET /api/posts/(PostController.java:136-139 List<Post>), GET /api/likes/best-reviews(LikeController.java:87-), GET /api/bookReviews/(BookReviewController.java:59-60 List<BookReview> → BookReview.java:16-18 user)입니다. POST /api/comments/는 Comment 엔티티를 반환합니다(CommentController.java:25). SecurityConfig.java:34 anyRequest().permitAll()이고, open-in-view 기본값 true라 lazy인 likes·hashtags도 직렬화 중에 로드됩니다.

### 프론트엔드

- **국내 설명문·사진·주소를 실제로 화면에 보여주기 (DTO 확장 + 결과 카드/상세 페이지를 DB 값으로)** · 누가: Claude · 크기: M
  - 왜: 지금 상태로는 950건 실제 설명문, summary 대체 문구, TourAPI 이미지가 어떤 화면에도 나오지 않습니다. 화면에는 이름과 Google 사진만 뜨고, 상세 설명은 위키백과 제목이 정확히 맞을 때만 나옵니다. 그래서 개편 데모에서 보여줄 게 없습니다.
  - 근거: KeywordResultDTO.java:10-14는 id/country/location/destName/info만 내보내고, DestinationInfoRepository.java:40-46 projection에는 imageUrl·address·lat/lng·copyrightType이 없습니다. personal.js:60-61은 `results.map(item => ({ placename: item.destName }))`라서 info·location·id를 버립니다. information.js:65-69, 143-173의 상세 설명은 Google Places와 ko.wikipedia `titles: placename.split(',')[0]` 정확 제목 조회로만 채우고 DB를 쓰지 않습니다. 표본 training/keyword_tagger/data/descriptions.jsonl 1,083건 중 134건이 '마니산(강화)'·'남산공원(서울)' 같은 괄호 제목이라 위키 제목과 맞지 않아 설명이 빈칸입니다. information.js:86-98은 리뷰 조회(fetchReviews)를 Google getDetails 성공 콜백 안에서만 부르므로, Places가 실패하면 이름·사진·리뷰가 모두 비고 'Loading image...'만 남습니다(personal.js:187-191, information.js:184-188). LimitedTextDiv는 4줄 고정에 더보기가 없는데(information.js:19-28) 설명문 길이는 중앙값 375자, 최대 2,761자입니다. 본문은 dangerouslySetInnerHTML로 넣습니다(information.js:192-193).

- **출처 표시 컴포넌트 추가 (TourAPI 공공누리 1/3유형, 위키백과 CC BY-SA 4.0)** · 누가: Claude · 크기: S
  - 왜: 두 라이선스 모두 출처 표시가 의무입니다. 그런데 지금은 어느 화면에도 출처가 없고, 기존 화면은 이미 위키백과 본문을 출처 없이 보여주고 있습니다. 문서에 적어 둔 '화면에 보여줄 때 출처 표시' 약속을 지킬 데이터도 API에서 내려오지 않습니다.
  - 근거: `grep -rn "출처\|한국관광공사\|공공누리\|CC BY\|위키백과" frontend/src` 결과 0건입니다. docs/domestic-tour-sync.md:20은 copyright_type을 기준으로 출처를 표시한다고 적었지만 copyrightType은 응답 DTO에 없습니다(KeywordResultDTO.java:10-14). 위키 본문을 출처 없이 렌더하는 곳은 information.js:147-164와 route/Modal.js:46-53(extract_html 600자 절단)입니다. DestinationInfo.java:49-84에는 원문 URL이나 위키 문서 제목 컬럼이 없어서, 해외(위키) 행의 출처 링크를 만들 근거가 없습니다.

### 데이터·AI

- **키워드 태거 비교 기준선을 지금 서버 규칙(설명문 규칙 ∪ 분류 코드표)으로 다시 재기** · 누가: 함께 · 크기: S
  - 왜: 'F1 0.53 vs 0.357/0.409'는 이미 바뀐 서버 규칙과 비교한 숫자입니다. 지금 국내 마스크는 fromKoreanDescription(info) ∪ TourCategories.keywordMask(category)입니다. 분류 코드표가 역사·자연·사찰처럼 자주 나오는 키워드를 이미 상당히 맞힐 수 있어서, 모델의 실제 개선 폭은 아직 모릅니다. 포트폴리오에 적을 숫자이고 태거를 연동할 가치가 있는지도 이 숫자로 정해집니다.
  - 근거: results/roberta-base.compare.md:17은 국내 규칙을 '서버가 지금 쓰는 fromKoreanDescription'이라고 적었습니다. 그런데 DestinationInfo.java:93-95는 TOUR_API 행에 .union(TourCategories.keywordMask(category))를 씁니다. data/descriptions.jsonl의 필드는 id,title,content_type_id,address,overview뿐이고 lclsSystm3 분류 코드가 없습니다. id는 TourAPI contentid(예: 125452)이므로 DB external_id로 이으면 분류 코드를 얻을 수 있습니다.

### 포트폴리오·라이선스

- **keyword_tagger README가 아직 '학습 전' 상태로 적혀 있음 (커밋 전에 고쳐야 함)** · 누가: Claude · 크기: S
  - 왜: 포트폴리오 문서가 실제 결과와 반대로 적혀 있습니다. README는 학습을 안 했다고 하는데 학습 결과 파일은 이미 있습니다. 저장소에 없는 세션 임시 스크립트도 언급합니다. 이대로 커밋하면 읽는 사람이 결과를 믿기 어렵습니다.
  - 근거: training/keyword_tagger/README.md:7 "본 학습(klue/roberta-base)은 아직 돌리지 않았습니다" 및 세션 임시 폴더의 kt_after_mlx.sh 언급, :186 "본 학습 시간은 아직 재지 않았습니다", :200-202 '다음 단계(아직 안 함)', :215 "(자동 실행이 만들 예정, 아직 없음)". 실제로는 results/roberta-base.json(train_seconds 1231.5, 약 20.5분)과 results/roberta-base.compare.md(micro F1 0.530)가 있습니다. 고칠 방법: 상태 문단을 결과 표로 바꾸고, 학습 시간을 적고, 임시 스크립트 언급과 '아직 없음' 표기를 지웁니다.
  - 상태: 처리함(2026-10-18). 맨 위 상태 문단을 v3 결과표(서버 규칙·v2·v3·키워드별 섞기)와 서버 적용 상태로 바꾸고, '다음 단계'를 새로 썼습니다. 임시 스크립트 언급은 없습니다.

## P1 · 해야 함 (42개)

### 백엔드

- **'No property id found for type Post' 정확한 원인: PostApiController 레거시 클래스 삭제** · 누가: Claude · 크기: S
  - 왜: 게시글 목록(/api/posts/) 오류가 아닙니다. 백엔드 루트 GET /가 PostApiController.index로 매핑돼 있고, 여기에 정렬 키 'id'가 기본값으로 붙는데 Post의 PK 필드 이름은 boardID입니다. 같은 클래스의 나머지 매핑도 PathVariable 이름이 맞지 않아 500을 내고, 인증 없는 엔티티 저장 엔드포인트까지 있습니다.
  - 근거: PostApiController.java:44-45 @GetMapping({"","/"}) + @PageableDefault(sort="id") → PostService.java:44-45 findAll(pageable) → PropertyReferenceException(Post.java:24 필드명 boardID). 같은 클래스 :27-28, :33-34, :57-58은 {id} 매핑인데 @PathVariable Long boardId라 MissingPathVariableException이 납니다. :21-22 POST /ppap/board는 @RequestBody Post를 그대로 저장합니다(likesCount·verified·nickname 임의 지정 가능). @RestController가 "index" 같은 뷰 이름 문자열을 반환하고 thymeleaf 의존성도 없습니다. grep -rn ppap frontend/src → 0건. 해결: 클래스를 삭제하고 PostService.java:38-73(writePost/postList/postDetail/postDelete/postModify)도 정리합니다. 최소 수정이라면 sort="boardID"로 바꿉니다.

- **로그인 JWT에 subject가 없어 인증 principal이 항상 null** · 누가: Claude · 크기: S
  - 왜: 이후의 사용자별 API(로드맵 4 에이전트 도구 호출, 6 일정·AI 리뷰)나 인가 개선은 '토큰에서 현재 사용자 꺼내기'가 전제입니다. 그런데 실제 로그인 토큰으로 만든 Authentication의 이름이 null입니다. 기존 테스트는 subject를 직접 넣은 토큰으로만 검증해서 이 문제를 잡지 못합니다.
  - 근거: JwtUtil.java:32-43은 setClaims(id,userId,nickname,role)만 하고 setSubject가 없습니다. JwtTokenProvider.java:25 username = claims.getSubject()를 :34에서 principal로 씁니다 → null. JwtTokenProviderTest.addsPrefixWhenRoleClaimHasNone(:50-53)만 setSubject("admin")를 직접 넣습니다. 해결: setClaims 뒤에 .setSubject(userId)를 넣거나(순서 주의: setClaims가 덮어씀) provider에서 claims.get("userId")를 씁니다. 그리고 getAuthentication(jwtUtil.generateToken(...)).getName()을 검증하는 테스트를 추가합니다. jjwt 0.9.1(build.gradle:37, 2018년)과 jaxb-api는 0.12.x 업그레이드 후보입니다. 로컬 시크릿은 base64 48바이트라 0.12의 256비트 요구를 충족합니다.
  - 상태: 처리함. subject = 로그인 아이디, subject 없는 예전 토큰은 userId 클레임으로 인정합니다. [authorization.md](authorization.md)

- **인가가 요청 파라미터(userId/nickname)를 그대로 믿음: 포인트 발행·대행자 승인·남의 글 삭제·DM 열람이 누구나 가능** · 누가: 함께 · 크기: L
  - 왜: /api/admin/**만 토큰을 검사하고 나머지는 전부 permitAll입니다. 그래서 행위 주체를 클라이언트가 보낸 파라미터로 정합니다. 포트폴리오 리뷰어가 Swagger만 열어도 재현되는 수준의 구멍입니다.
  - 근거: SecurityConfig.java:28-34(/api/public·/api/private 경로는 실제로 없고 anyRequest().permitAll()). UserController.java:72 POST /api/users/approve-agent/{userId}(관리자 기능이 /api/admin 밖에 있음), :268 /{nickname}/points/add(포인트 무제한 발행), :298 /transfer-points(남의 포인트 이체), :159 /update-profile?userId=. PostController.java:178 DELETE /api/posts/{id}/(소유자 확인 없음). ChatController.java:84 GET /chat/history/{roomId}에서 roomId가 "닉A_닉B"(:99-100)라 추측 가능합니다 → 남의 DM 열람. UserService.applyAsAgent(:88-109)가 신청 즉시 ROLE_AGENT를 부여해서 승인 절차가 의미가 없습니다. 프론트에서 Authorization 헤더를 보내는 곳은 5곳뿐입니다(grep). 해결 순서: (1) approve-agent와 points add/deduct를 /api/admin으로 옮깁니다. (2) subject 수정 후 주체를 SecurityContext에서 꺼냅니다. (3) 변경 메서드는 기본 authenticated로 두고 로그인·가입·조회·/api/search만 화이트리스트로 엽니다. 프론트 axios 헤더 수정이 같이 필요합니다.
  - 상태: 처리함. 행위자는 토큰으로만 정하고, 기본은 로그인 필요입니다. 관리자 기능은 ROLE_ADMIN만, STOMP도 인증·인가합니다. 프론트는 공통 인터셉터(frontend/src/api/auth.js)가 백엔드 요청에만 쿠키 토큰을 붙입니다. 규칙은 [authorization.md](authorization.md)에 적었습니다. 2026-10-17 실서버에서 테스트 계정으로 REST·STOMP 358건을 점검했고 우회는 없었습니다.

- **업로드 파일명 경로 조작(../)과 대행자 검증 서류의 공개 서빙** · 누가: Claude · 크기: S
  - 왜: 클라이언트가 보낸 파일명을 그대로 경로에 붙입니다. 인증 없이 서버의 임의 위치(예: application.properties)를 덮어쓸 수 있습니다. 대행자 신원 확인 서류는 정적 리소스로 누구나 내려받을 수 있습니다.
  - 근거: PostController.java:45 Paths.get(UPLOAD_DIR + file.getOriginalFilename())은 permitAll인 POST /api/posts/upload입니다. UserService.java:194-196 absoluteDirectory.resolve(filename) → transferTo. 같은 저장소의 FileUploadeService.java:22는 이미 UUID 파일명을 씁니다. WebConfig.java:15-16은 /verification-files/**를 인증 없이 서빙합니다. git ls-files src/main/resources/static/verification-files → 10개(PDF, mp4, 회의록 txt 포함)가 git에 올라가 있습니다. 해결: UUID 파일명, StringUtils.getFilename 후 normalize + startsWith 검사, 검증 파일은 관리자 인증 엔드포인트로만 제공합니다. 팀 시절 업로드 파일의 git rm --cached와 히스토리 처리는 오너가 결정합니다.
  - 상태: 검증 서류 정적 서빙을 막았습니다. WebConfig 매핑을 삭제하고 SecurityConfig로 대소문자 무시 차단을 걸었습니다. 이제 본인·관리자만 GET /api/users/verification-file/{userId}로 받습니다. git에 올라간 기존 파일과 히스토리 정리는 오너가 결정합니다.

- **관리자 초기 비밀번호 하드코딩: .env로 옮기고 기존 admin 행 비밀번호도 교체** · 누가: 함께 · 크기: S
  - 왜: 공개 GitHub 저장소에 관리자 비밀번호가 있습니다. 관리자 토큰으로 /api/admin/tour-sync/*를 호출하면 하루 1,000건인 TourAPI 한도를 소진시킬 수 있고, delete-user도 가능합니다. 초기화 코드는 admin이 없을 때만 돌기 때문에 코드만 고쳐서는 이미 만들어진 행의 비밀번호가 바뀌지 않습니다.
  - 근거: AdminAccountInitializer.java:26-29 findByUserId("admin").isEmpty()일 때 BCrypt.hashpw(코드에 적힌 공개 기본 비밀번호, ...). :21에서 주입받은 PasswordEncoder는 쓰지 않습니다. README.md:201과 docs/domestic-tour-sync.md의 관리자 로그인 curl이 이 계정을 전제로 합니다. 해결: @Value("${GLOPLE_ADMIN_PASSWORD:}")로 받고, 비어 있으면 생성을 건너뛰고 경고만 남깁니다. passwordEncoder.encode를 사용합니다. 오너는 .env에 값을 넣고 DB의 기존 admin 비밀번호를 한 번 갱신합니다.

- **UserServiceTest 2건 실패의 정확한 원인과 UserControllerTest 컨텍스트 실패** · 누가: Claude · 크기: S
  - 왜: sh gradlew test 전체가 초록이 아니라서 회귀를 잡을 수 없습니다. 두 실패 모두 테스트 코드 쪽 결함이라 몇 줄로 고칠 수 있습니다.
  - 근거: (1) testCreateUser: UserServiceTest.java:30 profilePictureFile이 @Mock이라 isEmpty()=false입니다 → UserService.createUser:79-81 saveProfilePicture → :194 getOriginalFilename()=null → :195 Objects.requireNonNull NPE. 해결: null을 넘기거나 isEmpty()를 true로 stub합니다(그렇지 않으면 실제 src/main/resources/static에 파일을 쓰려 함). (2) testLogin: UserServiceTest.java:60 "$2a$10$1234567890123456789012"는 60자짜리 유효한 bcrypt 해시가 아닙니다 → checkpw false → null. 해결: BCrypt.hashpw("testPass", BCrypt.gensalt(4))를 씁니다. (3) UserControllerTest(@WebMvcTest, :25): JwtAuthenticationFilter는 Filter라 로드되지만 @Autowired JwtTokenProvider(JwtAuthenticationFilter.java:18-19)는 로드되지 않아 컨텍스트 생성 실패가 유력합니다(sh gradlew test --tests proj.travien.controller.UserControllerTest로 확인 필요). SecurityConfig(@Component)도 빠져 기본 보안이 적용됩니다. testSignUp은 .param("user")를 보내는데 컨트롤러는 @RequestPart("user")를 받습니다(UserController.java:40). 해결: @Import(SecurityConfig, JwtTokenProvider), multipart JSON part를 사용합니다. build/test-results에는 이 세 클래스의 결과가 없고 마지막 실행은 선택 실행이었습니다.
  - 상태: 처리함. UserServiceTest와 UserControllerTest 모두 DB 없이 통과합니다. UserControllerTest는 실제 SecurityConfig·JWT 필터를 올립니다.

- **@SpringBootTest 테스트가 실제 travien DB(19,949행)에 쓰기, 테스트 격리 필요** · 누가: Claude · 크기: M
  - 왜: 전체 테스트를 돌리는 순간 유일한 수집 데이터 DB에 ddl-auto=update, 관리자 생성, 마스크 백필, 테스트 사용자 insert가 실행됩니다. UserRepositoryTest는 두 번째 실행부터 항상 실패합니다.
  - 근거: src/test/resources가 없습니다(ls src/test → java만 있음). 그래서 TravienApplicationTests와 UserRepositoryTest(@SpringBootTest, :13)가 main의 application.properties(:5 localhost:3307/travien, :8 ddl-auto=update)를 씁니다. UserRepositoryTest.java:21-23, :31-33은 'testuser'/'testnick'을 롤백 없이 저장합니다 → 다음 실행 때 findByUserId가 2행을 반환해 IncorrectResultSizeDataAccessException이 납니다. 비밀번호가 null인 사용자가 남아 그 계정으로 로그인하면 BCrypt.checkpw(UserService.java 로그인 부분)에서 NPE → 500. .gitignore:28이 src/test/resources/application.properties를 무시해서 테스트 설정을 커밋할 수도 없습니다. 해결: .gitignore:28을 제거하고, 별도 스키마 travien_test나 Testcontainers MySQL을 씁니다. UserRepositoryTest는 @DataJpaTest(+AutoConfigureTestDatabase NONE)로 바꿔 롤백되게 하고, DB 테스트는 @Tag("db")로 기본 test에서 제외합니다.

- **Spring Boot BOM을 무시한 고정 버전: spring-security-crypto 5.7.1, jboss-logging 3.4.2** · 누가: Claude · 크기: S
  - 왜: 직접 지정한 버전이 Boot 의존성 관리를 덮어써서 Security 6.2 core 옆에 5.x crypto가 실려 있습니다. 지금은 BCrypt만 써서 우연히 동작합니다. 6.x API(예: PasswordEncoderFactories가 쓰는 5.8+ 메서드)를 타는 순간 NoSuchMethodError가 날 수 있습니다.
  - 근거: build.gradle:35 'spring-security-crypto:5.7.1', :41 'jboss-logging:3.4.2.Final'. unzip -l build/libs/travien-0.0.1-SNAPSHOT.jar → BOOT-INF/lib/spring-security-crypto-5.7.1.jar와 spring-security-core-6.2.2.jar가 같이 있고, jboss-logging-3.4.2.Final.jar와 hibernate-core-6.4.4.Final.jar(3.5.x 기대)가 같이 있습니다. 해결: 두 줄을 삭제합니다(starter-security와 starter-data-jpa가 맞는 버전을 가져옴). 이후 compileJava와 테스트로 확인합니다.

- **다른 개발자용 실행 설정 정리: README가 새 .env 방식과 다르고 설정 템플릿이 없음** · 누가: Claude · 크기: S
  - 왜: README대로 하면 3306 포트, 평문 비밀값 방식으로 안내되고 .env, GLOPLE_*, TourAPI 키, 3307 포트의 glople-mysql 컨테이너는 전혀 나오지 않습니다. application.properties에는 이제 ${...} 자리표시자만 있어 커밋해도 되는데, gitignore 때문에 새로 클론하면 서버가 아예 뜨지 않습니다.
  - 근거: README.md:172-184(구 방식, 3306, 평문 값). 실제 설정은 application.properties:3 spring.config.import=optional:file:.env, :7 ${GLOPLE_DB_PASSWORD}, :10 ${GLOPLE_JWT_SECRET}(기본값 없음 → 없으면 기동 실패), :15 ${TOUR_API_SERVICE_KEY:}입니다. 이 내용은 docs/domestic-tour-sync.md:134-138에만 있고 glople-mysql 컨테이너 생성 명령(docker run)은 어디에도 없습니다. .gitignore:27이 application.properties를, :216이 application-*.properties를 막습니다. .env.example은 .gitignore:211-215에 걸리지 않아 커밋할 수 있습니다. JwtUtil.java:42 signWith(HS256, String)은 jjwt 0.9.1에서 Base64로 디코드하므로 'Base64, 32바이트 이상' 조건을 문서에 적어야 합니다. 해결: application.properties 커밋, .env.example 추가, README 실행 절차 갱신(docker run 포함)입니다.

- **수집 데이터 백업: 19,949행과 설명문이 도커 볼륨 하나에만 있음** · 누가: 함께 · 크기: S
  - 왜: 설명문은 하루 1,000건 한도라 다시 받으려면 약 20일이 걸립니다. 운영계정도 받을 수 없습니다. 볼륨이 지워지면 로드맵 2·3·5의 학습 데이터 원천이 사라지는데 백업 절차가 없습니다.
  - 근거: docs/domestic-tour-sync.md:136 '데이터는 볼륨 glople-mysql-data에 남아서'라고만 적혀 있고 dump·백업 절차가 없습니다. README.md:170 '저장소에는 시드 데이터가 없으므로'. 해결: 수집 후 docker exec glople-mysql mysqldump --single-transaction travien destination_info | gzip > backups/(gitignore 처리)를 하루 1회 실행합니다. 다른 개발자용 소량 시드(예: copyright_type Type1 행만, 출처 표시)는 공공누리 조건을 오너가 판단합니다. 도커 실행은 오너가 하거나, 권한을 받으면 Claude가 합니다.

- **키워드 마스크 재계산 범위 결함: 0인 마스크만 백필, TOUR_API만 재계산, 해외 한국어 설명은 부분 문자열 매칭** · 누가: Claude · 크기: M
  - 왜: 키워드 목록이나 분류 코드표를 바꾸거나 키워드 태거(로드맵 2) 결과를 반영하면, 이미 0이 아닌 마스크를 가진 해외 행과 새 Wikidata 행은 다시 계산되지 않습니다. 지금 만들고 있는 해외 수집기의 한국어 위키백과 설명은 '부산'의 '산' 같은 오탐을 그대로 겪습니다. 시작 비용 자체는 문제가 아닙니다.
  - 근거: DestinationKeywordMaskBackfill.java:25-30은 mask=(0,0) 행만 다시 읽습니다. TourDestinationSyncService.java:168-169 rebuildSummaries는 source='TOUR_API'만 순회합니다. 기존 해외 행의 source는 NULL입니다(DestinationInfo.java:49 주석). DestinationInfo.java:93-95는 TOUR_API가 아니면 KeywordMask.fromDescription(부분 문자열)을 쓰므로 '산','섬','성','탑','강' 같은 한 글자 키워드가 단어 속에서 켜집니다. 시작 비용 추정: 인덱스를 쓸 수 없는 쿼리 2개(마스크 컬럼과 category IN 181개 코드에 인덱스 없음)로 약 2만 행을 두 번 풀스캔하는 수십 ms 수준입니다. 설명은 있는데 키워드가 0인 행은 매번 다시 읽히고(설명이 하루 950건씩 늘면 이 집합도 커짐), 값이 같아서 UPDATE는 0건입니다. 해결(해외 수집기 작업이 끝난 뒤): keyword_mask_version 컬럼과 KeywordMask.VERSION 상수를 두고 백필 조건을 version < 현재로 바꿉니다. 한국어 매처 선택은 source==TOUR_API 대신 언어 기준으로 하고, 마스크 재계산을 전체 source로 넓힙니다.
  - 상태: 처리함(2026-10-18, 실제 MySQL 실행 확인은 아직). `keyword_mask_version` 열과 `KeywordMaskVersion`(규칙 번호 + 키워드·조사·코드표 지문)을 두고, 시작 시 마스크 보정이 국내·위키데이터 행 중 판이 없거나 다른 행만 마스크·판 열 UPDATE로 다시 계산합니다(코드표를 고치면 다시 띄우기만 하면 됨). 위키데이터 한국어 설명은 이미 국내 규칙(`fromKoreanDescription`, '문화어' 제외)을 씁니다. 출처가 없는 기존 해외 행은 원래 데이터의 마스크를 덮지 않으려고 예전처럼 0인 마스크만 채우고, 언어 기준 매처 선택은 하지 않았습니다. [keyword-tagger-serving.md](keyword-tagger-serving.md#규칙-판과-시작-시-다시-계산)

- **좋아요·게시글 엔드포인트 런타임 500: is-liked NPE, 댓글 있는 글 삭제 실패, /image 항상 실패** · 누가: Claude · 크기: S
  - 왜: 새로 쓴 글의 상세 화면에 들어가기만 해도 500이 나는 등 사용자가 바로 보는 오류입니다. 모두 몇 줄로 고칠 수 있습니다.
  - 근거: LikeController.java:53 int likesCount = ...getLikesCount()가 Integer를 int로 언박싱하는데, PostService.createPost(:107-120)는 likesCount를 설정하지 않아 NULL입니다 → 첫 좋아요 전에는 GET /api/likes/{boardID}/is-liked에서 NPE가 납니다. detail.js:185가 상세 화면마다 이를 호출합니다. PostService.deletePost(:329-331) deleteById는 Comment.post FK(Comment.java:23-25)에 cascade가 없어 댓글이 있는 글이면 DataIntegrityViolation → 500입니다. PostService.java:174가 imageUrl을 'http://localhost:8000/api/posts/{id}/image'로 바꾸고 PostController.java:203이 Paths.get(그 URL)로 파일을 읽으려 해서 GET /api/posts/{id}/image는 항상 500입니다(open-in-view 상태에서 관리 엔티티도 오염시킴). 해결: likesCount 0 초기화와 null 안전 처리, 댓글 선삭제나 cascade, /image 엔드포인트 삭제입니다.

### 프론트엔드

- **여행지 라우팅을 이름이 아니라 id로 바꾸기** · 누가: Claude · 크기: S
  - 왜: 국내 데이터에는 같은 이름이 여러 번 나오고 대괄호·괄호가 섞인 이름이 많습니다. 이름으로 라우팅하면 엉뚱한 행이 열리거나 API가 400으로 실패하고, React key도 충돌합니다.
  - 근거: App.js:58 `/recomendation/information/:placename`입니다. personal.js:143, BestCourse.js:32-33, mbti.js:158-160은 encodeURIComponent 없이 `navigate(`/recomendation/information/${placename}`)`를 부르고, routeRecomendation.js:34도 `/api/posts/addresses/${placename}`를 그대로 씁니다. descriptions.jsonl 표본에 같은 제목이 중복됩니다('서울 석촌동 고분군'×2, '익선동 한옥거리'×2, '백련사(서울)'×2). personal.js:186은 key={place.placename}입니다. '[유네스코 세계유산]'이 들어간 제목이 8/1,083건인데, 브라우저는 path의 '['를 인코딩하지 않고 Tomcat 10은 기본 설정에서 '[' ']'를 거부합니다(application.properties에 relaxed-path-chars 설정 없음).

- **personal.js 모듈 전역 배열 버그 수정 (중복 결과, 이전 검색 결과 노출)** · 누가: Claude · 크기: S
  - 왜: 키워드 추천 결과 화면은 데모의 핵심 경로입니다. 그런데 뒤로가기나 TopBar로 다시 들어오면 결과가 중복되거나 이전 검색 결과가 먼저 보입니다.
  - 근거: personal.js:16-17 `const PersonalRecData = []; const CourseData = [];`가 모듈 스코프이고, :59-66에서 마운트할 때마다 push합니다. 비우는 곳은 :147-148('키워드 다시 선정하기')뿐입니다. 시나리오 1: 카드 클릭 → 상세 → 뒤로가기 하면 컴포넌트가 다시 마운트되고(isResultsLoaded=false) 같은 21건이 다시 push되어 하단 BestCourse가 중복됩니다. 시나리오 2: TopBar '키워드 추천'(TopBar.js:146 → /recomendation/personal)을 누르면 state가 없어 /keyword로 튕기는데(personal.js:69-71) 배열은 비우지 않습니다. 그래서 새로 검색하면 CourseData.slice(0,4)(personal.js:34)가 이전 결과를 먼저 보여줍니다. 결과가 0건이어도 안내 없이 /keyword로 돌아갑니다.

- **국내 진입점과 국내/해외 구분 만들기 (지도 한국 클릭, 검색 scope, 카드 지역 배지)** · 누가: Claude · 크기: M
  - 왜: 개편 목표가 국내 확장인데 국내 여행지로 들어가는 입구가 없습니다. 결과 카드만 봐서는 국내인지 해외인지도 알 수 없습니다. 19,949개 국내 행과 기존 해외 행이 한 목록에 섞여서 나옵니다.
  - 근거: main.js:467 `<path //한국`에는 onClick/onMouseEnter가 없습니다. 클릭할 수 있는 나라는 일본(:253-255)·러시아(:333-335)·호주(:382-384)·이탈리아(:551-553)·캐나다(:618-620)·미국(:673-675) 6개뿐이고, 팝업도 여행지가 아니라 게시글 `/api/posts/${country}`를 보여줍니다(test/Popup.js:141). mbti.js:25-42는 16유형 모두 해외 도시를 하드코딩했습니다. keyword.js에는 국내/해외 선택이 없고, KeywordRequest.java:12는 keywords만 받으며, 쿼리(DestinationInfoRepository.java:47-55)에는 country 조건이 없습니다. 카드는 이름만 보여주고 country/location은 표시하지 않습니다(personal.js:192).
  - 상태: 일부 처리함(백엔드, 2026-10-18). `/api/search`가 `scope`(all·domestic·overseas)를 받습니다([travel-api.md](travel-api.md)). 화면(keyword.js의 국내/해외 선택, 지도 한국 클릭, 카드 배지)은 아직입니다.

- **localhost:8080 하드코딩을 API 클라이언트 하나로 모으기 (프록시와 SPA fallback 포함)** · 누가: Claude · 크기: M
  - 왜: 배포한 데모에서는 방문자 PC의 localhost로 요청이 가서 전부 실패합니다. 그렇다고 문자열만 바꾸면 /ws, /chat/*, 정적 이미지가 깨집니다.
  - 근거: `grep -rn "localhost:8080" frontend/src | wc -l` 결과 109건(파일 40개, setupProxy.js 1건 포함)입니다. setupProxy.js:23-24는 /api만 프록시합니다. /api 밖 경로는 /ws 2곳(WebSocketService.js:19, chatbotset.js:164), /chat/history·createRoom·userRooms·uploadFile·uploadVoiceMessage 7곳(ChatController @RequestMapping("/chat")), `http://localhost:8080/${profilePicture...}` 정적 이미지 템플릿 10곳입니다. /chat은 React 라우트(App.js:53)와 겹치므로 통째로 프록시하면 안 됩니다. docker/nginx.conf는 /api/와 /ws/를 프록시하지만 번들이 http://localhost:8080을 직접 부릅니다. docker/Dockerfile.frontend는 기본 nginx:alpine 설정이라 try_files가 없어서 /recomendation/information/... 새로고침이 404입니다.

- **챗봇 UI 결함 정리와 4단계 에이전트용 대화 화면 설계 (REST+SSE, 도구 결과 카드)** · 누가: 함께 · 크기: M
  - 왜: 지금 챗봇은 여러 사용자가 동시에 쓰면 서로의 답을 보게 되고, 비로그인이면 멈춥니다. 4단계 LangGraph 에이전트로 바꿀 때 STOMP 브로드캐스트 구조를 그대로 고치기보다 새 전송 방식으로 가는 게 낫습니다.
  - 근거: ChatBotController.java:22,34의 @SendTo("/topic/public")과 chatbotset.js:172 구독 때문에, 접속한 모든 사용자에게 남의 질문에 대한 답이 보입니다. setLoading(false)(:173)도 남의 답이 오면 풀립니다. JOIN 메시지도 같은 토픽으로 와서 content 없는 빈 말풍선으로 그려집니다(:175, :239). username은 payload.name에서 읽는데(:151) JWT에는 id·userId·nickname·role만 있어서(JwtUtil.java:33-36) undefined가 됩니다. 토큰이 없으면 connected=false라 계속 '로딩 중...'입니다(:142-159, :225). handleAddUser의 cleanup(:188)을 useEffect가 반환하지 않아서 재방문할 때마다 STOMP 연결이 쌓입니다. ChatBotService.java:23은 매번 user 메시지 하나만 gpt-3.5-turbo로 보내서 대화 맥락이 없습니다.
  - 상태: 처리함(2026-10-18). 기존 챗봇은 인가 정비 때 보낸 사람에게만 답하도록 바꿨고(`@SendToUser` → `/user/topic/chatbot`), 에이전트용 새 화면 `/assistant`는 REST + SSE(`/ai-api/v1`, 본인 대화만)로 결과 카드를 먼저, 답을 나중에 보여 줍니다([agent/README.md](../agent/README.md) 'M5').

- **여행지 조회 엔드포인트를 UI와 에이전트 도구가 같이 쓰도록 만들기 (v2 학습 전에 도구 스키마 정리)** · 누가: 함께 · 크기: M
  - 왜: 학습한 도구 스키마에 대응하는 백엔드 API가 없습니다. UI 상세 페이지와 '주변 여행지'도 같은 API가 필요하므로 한 번에 만들면 됩니다. v2 학습 전에 정해야 다시 학습할 일이 없습니다.
  - 근거: training/tools/glople_tools.json에 search_destinations(region, category enum 관광지/문화시설/축제/레포츠/음식점/숙박, query, limit)와 get_destination_detail(name)이 정의되어 있고 data/tool_calling_v2에도 나옵니다. 하지만 백엔드 컨트롤러에서 여행지 관련 매핑은 /api/search, /api/info-recommend, /api/save-route, /api/get-routes뿐이고 지역·분류 검색이나 상세 조회는 없습니다. 수집 타입은 12·14·15·28뿐이라(TourDestinationSyncService.java:39-45, 55-56) enum의 음식점(39)·숙박(32)은 데이터가 0건입니다. get_destination_detail(name)은 위 중복 이름 문제 때문에 모호합니다. DB에는 regionCode/sigunguCode(DestinationInfo.java:58-59)가 있어서 지역 필터를 만들 수 있습니다.
  - 상태: 처리함(S1, 2026-10-18). 공개 `GET /api/destinations`(region·category·query·limit·seed), `/api/destinations/lookup?name=`(같은 이름이면 여러 곳), `/api/destinations/{id}`([travel-api.md](travel-api.md#s1--여행지-읽기-apidestinations)). 음식점·숙박은 국내 수집 타입이 아니라 결과가 비고, 해외 분류는 키워드 마스크 근사입니다. 화면 연결과 실제 MySQL 실행 확인은 아직입니다.

- **6단계 일정 UI 지도 경로: 국내에서는 Google 도보 경로가 나오지 않으니 대안 정하기** · 누가: 함께 · 크기: M
  - 왜: 기존 루트 화면을 재활용해 국내 일정을 그리면, 경로 요청 단계에서 결과가 나오지 않을 가능성이 큽니다. 지도·경로 제공자를 일정 UI 만들기 전에 정해야 합니다.
  - 근거: routeNavigationPage.js:299-305는 google.maps.DirectionsService에 TravelMode.WALKING을 씁니다. Google Directions는 대한민국 안에서 도보·자동차 경로를 제공하지 않는 것으로 알려져 있어서 ZERO_RESULTS가 예상됩니다(외부 호출 금지라 실측은 안 했습니다). routeNavigationPage.js:64-72는 오사카 좌표를 하드코딩한 데모입니다. 반면 국내 행에는 DestinationInfo.latitude/longitude(DestinationInfo.java:67-68)가 채워져 있어 지오코딩 없이 핀을 찍을 수 있습니다.
  - 상태: 피해 감(2026-10-18). `/my/plans`는 지도 없이 일차별 카드 + AI 검토로 만들어 경로 요청을 하지 않습니다. 지도·경로 제공자는 아직 정하지 않았습니다.

- **프론트를 개편 데이터로 실제 실행해서 데모 흐름 확인하기** · 누가: 함께 · 크기: S
  - 왜: 이 체크아웃에서는 프론트를 한 번도 설치하거나 실행한 흔적이 없습니다. 위 문제들이 실제 화면에서 어떻게 보이는지, Google 키가 아직 살아 있는지 확인된 적이 없습니다.
  - 근거: frontend/node_modules와 frontend/build가 없습니다(`ls` 결과 No such file or directory). git status에 frontend 변경이 0건이고, 프론트 테스트 파일은 setupTests.js뿐입니다. Google 키는 2024년 팀 키 3종(information.js:41 외 3곳 공통 1종, review/write/MapComponent.js:10, routeNavigationPage.js:19)이라 결제·제한 상태를 알 수 없습니다. 첫 번째 항목의 폴백이 없으면 데모 화면 대부분이 'Loading image...'로 남을 수 있습니다.
  - 상태: 일부 처리함(2026-10-18). 개발 서버(3000)를 실제 Spring(8080)·에이전트 서비스(8095, 가짜 모델)·문장 검색 서버(8096)에 붙여 임시 계정으로 로그인 → `/assistant`(날씨·검색·추천·상세·일정 저장) → `/my/plans`(AI 검토) → `/search/semantic` → 여행지 상세(설명문·출처)까지 확인했습니다. Google 사진은 'Loading image...'로 남습니다(키 확인은 오너 몫).

### 데이터·AI

- **태거가 test에서 키워드 17개만 예측함: 규칙과 섞는 하이브리드를 고르고 epoch를 늘려 다시 학습** · 누가: Claude · 크기: M
  - 왜: 모델은 학습 라벨 77개 중 17개만 내놓습니다. 그래서 macro F1이 0.170으로, 규칙(0.392)보다 크게 낮습니다. 섬·성·탑·강은 예측이 0건입니다. 학습도 끝까지 수렴하지 않았습니다.
  - 근거: 직접 오프라인 계산(outputs/roberta-base/test_predictions.jsonl + kt_common.korean_rule_predict, compare.md의 0.530/0.170, 0.409/0.392를 그대로 재현함). 모델 단독 micro 0.530 / macro 0.170, 규칙 0.409/0.392. '모델 ∪ 모델이 한 번도 안 내는 키워드는 규칙'은 micro P/R/F1 0.606/0.598/0.602, macro 0.467. 전부 합집합은 0.607/0.480. 단, 키워드 목록을 test에서 골랐으므로 valid에서 다시 골라야 합니다. outputs/auto_run.log를 보면 valid F1이 에폭 9→10에 0.6215→0.6368로 계속 오르고 best_epoch=10/10이며, LR은 선형으로 0까지 줄어듭니다. 고치는 방법: valid로 하이브리드 규칙 고르기(S, GPU 필요 없음), --epochs 25 --patience 3으로 다시 학습(MPS 약 40분), BCE pos_weight 검토.
  - 상태: 처리함(2026-10-18). 1·2·3차 배치로 v3를 다시 학습했고(test macro F1 0.170 → 0.459, micro 0.530 → 0.733), 키워드마다 valid F1로 규칙/태거/합집합을 고르는 섞기(`scripts/hybrid_masks.py`: 태거 29·규칙 61·합집합 4)를 서버에 가져왔습니다. 드문 키워드는 규칙이 맡습니다.

- **태거 예측을 DB에 반영할 경로 만들기: 지금은 써 넣어도 다음 저장 때 덮어씀** · 누가: Claude · 크기: M
  - 왜: README의 '다음 단계'는 '배치로 predict.py → 마스크 재계산'입니다. 하지만 @PrePersist/@PreUpdate가 저장할 때마다 info와 category로 마스크를 다시 계산합니다. 소개 재계산(summaries)도 마스크가 다르면 저장합니다. 그래서 SQL로 넣은 모델 마스크는 다음 목록 갱신이나 재계산 때 규칙 마스크로 되돌아갑니다.
  - 근거: DestinationInfo.java:86-95(refreshKeywordMask가 @PrePersist/@PreUpdate에서 실행), TourDestinationSyncService.java:163-205(rebuildSummaries → refreshSummaryAndMask:202 → 바뀌었으면 saveAll), keyword_tagger/README.md:203. 고치는 방법: predicted_keyword_mask_low/high(또는 predicted_keywords + 모델 버전) 컬럼을 따로 두고 refreshKeywordMask에서 합칩니다. predict.py 결과를 external_id 기준으로 그 컬럼에만 쓰는 배치를 만듭니다.
  - 상태: 처리함(2026-10-18, 로컬 MySQL에 적용). 태거 열(`tagger_mask_low/high`, `tagger_model`, `tagger_info_crc32`, `tagger_updated_at`)을 규칙 마스크와 따로 두어 저장할 때 덮이지 않습니다. `scripts/predict_all.py` → `POST /api/admin/keyword-masks/import`(관리자, (source, external_id)로 태거 열만 씀) → 추천 쿼리가 `keyword.recommend.mask-mode`(기본 tagger = 태거 열, union, rule)로 고릅니다. 태거 열에는 키워드별 섞기 결과를 가져왔습니다(TOUR_API 1,797행, WIKIDATA 7,473행, 가져오기 전 DB 백업 있음). 설명문이 바뀌면 그 행의 예측은 저장할 때 지웁니다. [keyword-tagger-serving.md](keyword-tagger-serving.md)

- **MySQL → 학습용 jsonl 내보내기 스크립트 만들기 (contentid 중복 제거, test 누수 방지)** · 누가: 함께 · 크기: S
  - 왜: docs에는 '학습 데이터는 이 DB에서 꺼내 씁니다'라고 되어 있지만 그런 스크립트가 없습니다. 지금 학습 원본 1,083개는 다른 프로젝트(neoulteo/docs/database/SSAFY_TRIP_Dump.sql) 덤프에서 뽑은 것인데, 뽑는 스크립트도 남아 있지 않습니다. 그래서 data/(gitignore)가 사라지면 다시 만들 수 없습니다. DB 행을 그냥 train에 더하면 test 항목이 섞여 들어갈 수 있습니다.
  - 근거: docs/domestic-tour-sync.md:132('학습 데이터는 이 DB에서 꺼내 씁니다'), keyword_tagger/README.md:13(Neoulteo 덤프), training/ 전체를 grep해도 export·mysql·dump 스크립트가 0건입니다. 목록은 제목순(ARRANGE_BY_TITLE, TourDestinationSyncService.java:34)으로 넣고 설명문은 id순으로 받습니다. 그래서 첫 950건과 겹칠 수 있는 덤프 항목, 즉 'ㄱ'으로 시작하는 서울·인천·광주 관광지가 183개이고 그중 24개가 test 분할(labels/splits.json)에 있습니다. 고치는 방법: training/keyword_tagger/scripts/export_db.py를 만듭니다. docker exec로 읽기 전용 SELECT external_id AS id, dest_name, content_type_id, region_code, category, address, info, source_modified_time WHERE source='TOUR_API' AND info IS NOT NULL을 실행해 jsonl로 쓰고, splits.json의 test id는 빼고, 덤프와 DB 본문이 다른 경우도 기록합니다. 실행에는 소유자 허락(docker)이 필요합니다.

- **키워드 라벨링 가이드 원문을 저장소에 남기기** · 누가: Claude · 크기: S
  - 왜: 새 TourAPI 설명문에 라벨을 더 붙이거나 다시 붙이려면 1,083개 때와 같은 가이드가 필요합니다. 지금 README에는 요약만 있습니다. 가이드가 다르면 기존 라벨과 새 라벨의 기준이 어긋나고, 일치도 0.920이라는 숫자도 다시 낼 수 없습니다.
  - 근거: keyword_tagger/README.md:28 '전체 표는 작업 지시에 있던 원문 가이드를 따름'. 원문 가이드 파일은 저장소에 없습니다(keyword_tagger/에는 README.md, keywords.json, labels/, results/, scripts/만 있음). 고치는 방법: 이전 세션 기록에서 원문을 찾아 keyword_tagger/labeling_guide.md로 저장하고, AI(Claude)가 쓴 가이드라는 점을 적습니다.

- **국내 전체를 대표하는 held-out 평가 세트 만들기: 설명문을 받는 순서 때문에 문화시설·축제·레포츠는 약 2주 동안 데이터가 0** · 누가: 함께 · 크기: M
  - 왜: 태거 학습 데이터는 서울·인천·광주의 관광지뿐입니다. 새로 받은 950건도 전부 관광지이고 'ㄱ' 이름에 몰려 있습니다. 행 id가 관광 타입별로, 그 안에서는 제목순으로 매겨져 있어서, 하루 950건씩 받으면 문화시설(14)은 약 13일 뒤에, 레포츠(28)는 약 17일 뒤에야 설명문이 들어오기 시작합니다. 그 전까지는 이 유형으로 태거나 임베딩을 평가할 수 없습니다.
  - 근거: TourDestinationSyncService.java:80(타입별·제목순 삽입), :119-120 + DestinationInfoRepository.java:28(OrderByOverviewFailedAtAscIdAsc), docs/domestic-tour-sync.md:169(관광지 12,554건이 먼저 들어감), :175. src/main/resources/application.properties에 tour.sync.* 설정이 없어서 overview-cron은 기본값 '-'(꺼짐, docs:83)입니다. 고치는 방법: overviews API에 contentTypeId·regionCode·ids로 대상을 고르는 옵션을 더해 하루 몇백 건을 층화 표본에 씁니다. 지역 17 × 타입 4에서 약 200건을 뽑아 AI silver 라벨을 붙이고, 소유자가 일부를 검수해 national_test로 고정합니다. 소유자는 cron(예: 0 0 4 * * *)을 켤지 정합니다.

- **v2를 학습하기 전에 도구 스키마 확정하기 (keywords enum, 수집하지 않는 카테고리)** · 누가: Claude · 크기: S
  - 왜: 도구 정의는 학습 레코드마다 tools로 들어갑니다. 그래서 4시간짜리 v2 학습 뒤에 스키마를 바꾸면 데이터를 다시 만들고 다시 학습해야 합니다. 지금 모델은 94개 목록 밖의 키워드를 냅니다. 서버는 모르는 키워드를 조용히 버리므로 추천 결과가 말없이 틀려집니다. category enum에 있는 음식점·숙박은 DB에 데이터가 없습니다.
  - 근거: tools/glople_tools.json:10의 keywords는 items {type:string}이고 enum이 없습니다. KeywordMask.java:67 'fromKeywords는 모르는 키워드를 조용히 건너뛰므로'. results/finetuned_human.predictions.jsonl에서 모델이 낸 값: '스노쿨링', '조용한', '와이너리 투어', '맛집', '유네스코 세계유산', '고대 유적지'. glople_tools.json:31 category enum에 '음식점','숙박'이 있지만 docs/domestic-tour-sync.md:79 기본 수집 타입은 12,14,15,28(32 숙박, 39 음식점 제외)입니다. 고치는 방법: keywords items에 94개 enum을 넣고, 수집하지 않는 카테고리는 enum에서 빼거나 수집을 추가합니다. 그 뒤 build_tool_dataset.py로 v2를 다시 만들고 eval_sets/human_style.jsonl의 tools도 맞춥니다.

- **모델이 빠진 인자를 지어냄: 에이전트에 근거 검사를 두고 평가 지표도 추가** · 누가: Claude · 크기: M
  - 왜: 필수 정보가 빠졌을 때 되묻지 않고 그럴듯한 값을 지어냅니다. create_travel_plan처럼 실제로 저장하는 도구에서는 바로 잘못된 데이터가 됩니다. 되묻기 정확도는 파인튜닝 뒤 오히려 기본 모델보다 낮아졌습니다.
  - 근거: results/finetuned_human.predictions.jsonl의 clarify 예: 'ENTP 남자임' → age 30을 지어냄, '28살 ISFJ' → gender '여성', '울진 일정' → days 3과 places ['울진해수욕장','울진국립공원','울진문화박물관'], '내일 나가는데 날씨' → region '나가'. results/report_human.md: clarify 50.0%→25.0%, direct 100%→66.7%. 난이도별(직접 계산): easy 18/19, medium 31/39, hard 17/32. 라벨 규칙은 region·query·name·places 값이 발화 안에 글자 그대로 있어야 한다고 정합니다(eval_sets/README.md '검증' 절). 고치는 방법: 같은 규칙을 에이전트 검증 단계(값이 대화에 없으면 도구를 부르지 않고 되묻기)와 evaluate.py의 'grounded_args' 지표로 구현합니다.
  - 상태: 처리함(M1, 2026-10-18). validate의 근거 검사가 발화에 없는 나이·성별·지역·장소·일수를 되묻기로 바꾸고, 근거 없는 선택 인자는 뺍니다. 측정은 `glople_agent.cli grounding`(v3 + 근거 검사: v2 test 92.3%, 사람 말투 88.9%, 새 사람 말투 82.2%).

- **도구 호출 학습 데이터가 전부 한 턴짜리: 에이전트의 최종 답변을 무엇이 만들지 정하기** · 누가: 함께 · 크기: M
  - 왜: 4단계 LangGraph 에이전트는 도구 결과를 받아 답을 쓰고 '거기 날씨는?' 같은 후속 질문을 처리해야 합니다. 그런데 LoRA는 첫 도구 선택만 배웠습니다. 이걸 정하지 않으면 에이전트를 만든 뒤에야 품질 문제를 발견합니다.
  - 근거: 직접 집계: data/tool_calling/train.jsonl, data/tool_calling_v2/train.jsonl(각 3,000), eval_sets/human_style.jsonl(90)의 메시지 구조가 100% (system, user, assistant)입니다. role=tool 메시지나 여러 턴 대화가 0건입니다. scripts/evaluate.py:91-92는 messages[:-1]만 넣어 첫 응답만 채점합니다. 선택지: (a) LoRA는 라우터로만 쓰고 답변은 기본 모델이나 다른 LLM이 씀, (b) v3 생성기에 도구 결과→답변, 이전 턴을 가리키는 표현이 있는 예시를 추가함.
  - 상태: 처리함(2026-10-18). 답은 답변 전용 프롬프트(a3: 도구 없이 결과만 보고 2~4문장) + 코드 가드(결과에 없는 숫자·이름, 원문 20자 겹침 → 템플릿)가 만듭니다. v3를 일반 루프로 쓰면 결과를 근거로 답한 것이 65/85, 답변 전용은 80/85였습니다([m2-after-tools.md](../agent/docs/m2-after-tools.md)). 도구 결과 뒤 턴을 배운 v4(다턴 데이터)와 루프 모드(M7)를 실제 서버로 비교한 뒤, 권장 구성을 '첫 판단 v3 + 결과 뒤 v4 루프'로 정했습니다(M4 시나리오에서 첫 판단 98%, 가드 통과 92%, 모델이 답한 턴 33 → 56. [m7-ab-hybrid.md](../agent/docs/m7-ab-hybrid.md)). 서버 하나일 때의 기본은 답변 전용 경로(a3, 문구 v2)입니다.

- **LoRA 모델을 OpenAI 호환 서버로 띄우는 스크립트와, 같은 결과가 나오는지 확인하는 평가 만들기** · 누가: Claude · 크기: M
  - 왜: 에이전트가 부를 모델 서버가 아직 없습니다. mlx_lm.server의 기본 포트가 Spring Boot와 같아서 그대로 띄우면 충돌합니다. 서버 경로(채팅 템플릿과 tool_calls 파싱)로 받은 결과가 evaluate.py 점수와 같은지도 확인한 적이 없습니다.
  - 근거: training/.venv/.../mlx_lm/server.py:1943 --port 기본값 8080이고, Spring도 8080입니다(application.properties에 server.port 설정 없음, docs 예시도 localhost:8080). models/qwen3-4b-instruct-2507-4bit/tokenizer_config.json의 tool_parser_type이 'json_tools'라서 /v1/chat/completions가 tool_calls를 돌려줄 수 있습니다. 하지만 --adapter-path를 준 실행 기록은 없습니다. ChatBotService.java:19,23은 https://api.openai.com/v1과 gpt-3.5-turbo를 하드코딩합니다. 고치는 방법: training/serve.sh(mlx_lm.server --model models/... --adapter-path adapters/... --port 8081)를 만들고, human_style을 HTTP로 다시 채점하는 serve_eval.py를 만듭니다. SYSTEM_PROMPT와 tools JSON은 파일 한 곳에 두고 학습 생성기와 에이전트가 같이 읽게 합니다.
  - 상태: 처리함. `training/scripts/serve.sh`(127.0.0.1, 기본 8090)와 `scripts/check_server.py`(서버 응답을 evaluate.py 기준으로 채점), 에이전트 라우터 경로 확인 `agent/scripts/check_router.py`(v3에서 서버 채점과 같은 0.983 / 0.946).

- **도구 8개와 실제 백엔드 API의 계약 불일치 정리 (에이전트 4단계 선행)** · 누가: 함께 · 크기: L
  - 왜: LoRA가 고른 도구를 실제로 실행할 API가 절반은 없거나 인자 형식이 다릅니다. 이걸 맞추기 전에는 에이전트가 끝까지 동작할 수 없습니다.
  - 근거: RecommendController.java:30-46에는 /api/search, /info-recommend, /save-route, /get-routes만 있습니다. search_destinations(region,category)용 지역 검색 API가 없습니다(destination_info에 region_code·content_type_id 컬럼은 있음). get_destination_detail(name)용 이름 조회 API가 없습니다. src/main/java에 weather·날씨가 0건이라 날씨 API 키는 소유자가 받아야 합니다. create_travel_plan(region,days,places) 대응 API도 없습니다(MyRouteDTO에는 userId·userInfoId만 있음, 6단계 일정 기능 필요). recommend_similar_traveler_routes는 age:int와 '남성'/'여성'을 내지만, UserInfoRecommendationService.java:124-128은 생년월일 yyyyMMdd/yyyy-MM-dd를 요구하고 성별은 'M'/'F'입니다(frontend/src/pages/recomendation/Similarity/similarity.js:141). search_gloplers는 /api/users/search-agents-by-hashtag로 연결됩니다.
  - 상태: 처리함(2026-10-18). search_destinations·get_destination_detail은 S1(`/api/destinations`), create_travel_plan은 S2(`/api/travel-plans`, UNIQUE(user_id, client_request_id))로 만들었습니다. get_weather는 M6에서 Open-Meteo(키 없음, CC BY 4.0 출처 표시)로 붙였습니다. recommend_similar_traveler_routes의 나이·성별 형식 차이는 에이전트 어댑터가 변환합니다.

- **재현성 고정: MLX 환경 requirements, HF 모델 revision, 의존성 lock, gitignore된 산출물 백업** · 누가: 함께 · 크기: S
  - 왜: 한 달 뒤나 다른 맥에서 같은 결과를 다시 내기 어렵습니다. mlx-lm은 버전에 따라 어댑터 형식과 학습 동작이 바뀔 수 있고, HF 저장소가 갱신되면 같은 모델 이름으로도 다른 가중치를 받게 됩니다.
  - 근거: training/README.md:18 '.venv/bin/pip install mlx-lm huggingface_hub'(버전 고정 없음, requirements 파일 없음). 설치된 버전: mlx 0.32.4, mlx-lm 0.32.2, transformers 5.20.1, tokenizers 0.23.3. scripts/prepare_model.py:20-21의 snapshot_download/hf_hub_download에 revision이 없습니다. 캐시 refs/main: mlx-community/Qwen3-4B-Instruct-2507-4bit 50d427756c6b, Qwen/Qwen3-4B-Instruct-2507 cdbee75f17c0, klue/roberta-base 02f94ba5e3fc. keyword_tagger/requirements.txt에는 4개만 고정되어 있고 tokenizers 0.23.3, safetensors 0.8.0, huggingface_hub 2.2.0은 빠졌습니다. adapters/(504MB)와 keyword_tagger/outputs/는 gitignore라 백업이 없습니다. 고치는 방법: requirements와 lock 파일 작성, revision 상수 추가는 Claude가 합니다. 어댑터와 태거를 외장 디스크에 백업할지 HF Hub에 비공개로 올릴지는 소유자가 정합니다(TourAPI Type3 원문으로 학습한 가중치를 공개해도 되는지도 같이 판단).

- **ML 문서 최신화: 낡은 상태 문구 고치고 v1·태거 결과와 AI 제작 표시 추가** · 누가: Claude · 크기: S
  - 왜: 지금 README만 보면 태거 본 학습을 안 한 것처럼 읽히고, 도구 호출 모델의 결과는 어디에도 정리되어 있지 않습니다. compare.md만 따로 인용하면 AI silver 라벨 기준이라는 사실이 빠집니다.
  - 근거: keyword_tagger/README.md:7 '본 학습(klue/roberta-base)은 아직 돌리지 않았습니다', 세션 임시 폴더의 kt_after_mlx.sh 언급. :215 roberta-base 결과 '아직 없음'(실제로는 14:04에 완료, results/roberta-base.json 있음). training/README.md에는 v1 결과(test 정확 일치 54.0%→89.3%, human_style 48.7%→78.9%, hard 17/32, reviews 94.4%→66.7% 하락)가 없습니다. results/roberta-base.compare.md:1 제목에 'AI 라벨 기준' 표시가 없습니다. 루트 README.md에는 개편과 ML 작업 언급이 0건입니다(grep lora/F1/TourAPI/학습 0건). training/README.md에는 생성기 템플릿과 eval 세트를 AI가 만들었다는 문장이 없습니다(eval_sets/README.md에는 있음).
  - 상태: 처리함(2026-10-18). training/README(v1~v3 결과, 사람 말투 세트 두 개), keyword_tagger README(v3·섞기), embedding README(결과·서비스 연결)를 실제 결과로 고쳤고, AI silver 라벨·합성 질의 기준임을 표 옆에 적었습니다.

- **임베딩 3단계 선행 작업: 검색 평가 하네스부터 만들고 벡터 저장 방식 정하기** · 누가: 함께 · 크기: M
  - 왜: 임베딩을 파인튜닝해도 지금 키워드 마스크 검색보다 나은지 잴 기준이 없습니다. 학습 데이터, 평가 데이터, 저장 방식 모두 아직 준비된 것이 없습니다.
  - 근거: training/, docs/, README 전체에서 embedding·임베딩 0건입니다. 서버 랭킹은 BIT_COUNT 내림차순, 같으면 id 오름차순(DestinationInfoRepository.java:40-55)이고, kt_common.keyword_mask로 파이썬에서 재현할 수 있습니다. 설명문이 있는 행은 최대 2,033개(덤프 1,083 + DB 950, 일부 중복)이고, 나머지 약 19,000행은 분류 틀 문장(summary)뿐이라 임베딩해도 분류 정보만 담깁니다. 로컬 DB는 mysql 8.4(docs:136)라 VECTOR 타입이 없습니다. 고치는 방법: labels/gold_labels.jsonl을 정답 관련도로 쓰는 retrieval_eval.py(태거 test 163 고정, nDCG@10과 Recall@20, 마스크 검색 기준선)를 만듭니다. 학습 쌍은 키워드 집합에서 AI가 합성한 선호 문장(AI 제작 표시)으로 만듭니다. 기본 모델(e5·bge-m3 등)과 저장 방식(FAISS/numpy 사이드카 vs DB 업그레이드)은 소유자가 정합니다.
  - 상태 메모(2026-10-18): 서버 동점 정렬이 바뀌었습니다. 일치 수 → 설명문 → 이미지 → `CRC32(CONCAT(id, ':', seed))` → id 순이고 지역 상한(최대 max(2, ⌈limit/4⌉)곳)이 붙습니다. 마스크 검색 기준선을 재현할 때 이 순서를 씁니다([travel-api.md](travel-api.md#s0--키워드-추천-동점-정렬-post-apisearch)).
  - 상태: 일부 처리함(2026-10-18). `training/embedding/`에 평가 하네스(합성 질의, AI silver 라벨 기준)와 e5-base 파인튜닝(test nDCG@10 0.802, 키워드 칩 0.484)을 만들었고, 저장은 numpy 사이드카로 했습니다. DB 추출본 9,407곳 색인 + 검색 서버(127.0.0.1:8096) + 화면 '문장으로 찾기'(`/search/semantic`)를 붙였습니다([README](../training/embedding/README.md#서비스에-연결)). 실제 사용자 질의 평가, 해외 지역 조건 학습, 키워드 검색과 섞기, Java 연동 방식은 남았습니다.

### 포트폴리오·라이선스

- **포트폴리오 수치의 비교 기준과 한계를 정직하게 적기 (태거 기준선, LLM 사람 말투 세트 하락)** · 누가: Claude · 크기: S
  - 왜: F1 0.53을 0.357과 비교하면 개선폭이 실제보다 커 보입니다. 서버가 TOUR_API 행에 쓰는 규칙은 국내 규칙(0.409)입니다. LLM 쪽은 합성 test 점수만 보이고, 사람 말투 세트에서 떨어진 작업이 문서에 없습니다.
  - 근거: keyword_tagger/results/roberta-base.compare.md: micro F1 부분 문자열 0.357 / 국내 규칙 0.409 / roberta 0.530, macro F1 0.392 → 0.170으로 떨어짐(학습 예시가 0개인 키워드 17개는 아예 예측하지 않음). keyword_tagger/README.md:202에도 국내 규칙 열과 비교한다고 적혀 있습니다. training/README.md에는 결과 표가 없습니다. results/report_test.md 기준 완전 일치 54.0 → 89.3%(v1 test는 15%가 train과 겹침, README:38). results/finetuned_human.summary.json에서는 48.7 → 78.9%지만 clarify 4/8 → 2/8, direct 6/6 → 4/6로 떨어졌습니다(작업당 6~12개라 표본이 작음). 고칠 방법: 두 README와 루트 README에 비교 기준, 표본 크기, 하락한 작업, silver 라벨이라는 점을 함께 적습니다.
  - 상태: 처리함(2026-10-18). 태거는 서버가 실제로 쓰는 규칙(국내 0.409, 해외 0.701)과 비교하고, LLM은 v2 test와 사람 말투 세트 두 개(설계 때 오답을 본 세트 / 새로 쓴 세트)를 함께 적습니다. 루트 README 요약표도 같은 기준입니다.

- **루트 README에 개인 개편 내용이 없고 실행 방법도 현재 구성과 다름** · 누가: Claude · 크기: M
  - 왜: main 브랜치 README가 포트폴리오의 첫 화면입니다. 그런데 7인 팀 프로젝트와 2024년 사후 개선만 나옵니다. 2026년 개인 개편(TourAPI, 학습 파이프라인, 로드맵 6단계)은 보이지 않습니다. 실행 안내도 지금 로컬 구성과 맞지 않습니다.
  - 근거: README.md:4-12는 팀 프로젝트, 기간 2024.03~2024.11, 사후 개선만 다룹니다. :168 "저장소에는 시드 데이터가 없으므로"라고 하지만 이제 TourAPI 동기화로 채울 수 있습니다. :174-184 설정 예시에 spring.config.import, tour.api.service-key, .env가 없고 3306을 씁니다(실제 로컬은 3307, docs/domestic-tour-sync.md:136). :234-255 폴더 구조에 training/, docs/domestic-tour-sync.md, src/main/resources/tour/가 없습니다. 기술 스택에 Python, MLX, transformers도 없습니다. 고칠 방법: '개인 개편 (2026.10~, 1인)' 섹션에 로드맵 6단계 상태표, 문서 링크, 팀/본인/개편 구분을 넣고 실행 방법을 고칩니다.
  - 상태: 일부 처리함(2026-10-18). '개인 개편 (2026.10~, 1인)' 절(로드맵 6단계 결과표·문서 링크)과 기술 스택 줄을 넣었습니다. 실행 방법의 DB 포트(3307)·.env·시드 데이터 설명은 아직 그대로입니다.

- **application.properties.example과 .env.example 추가** · 누가: Claude · 크기: S
  - 왜: 설정 파일 두 개가 모두 gitignore 대상입니다. 저장소를 새로 받은 사람은 키 이름을 README, docs, 코드에서 따로 모아야 합니다. 지금 로컬 properties는 비밀값을 직접 쓰지 않고 자리표시자로 받으므로 그대로 예시 파일로 만들어도 됩니다.
  - 근거: .gitignore:27이 src/main/resources/application.properties를 무시합니다. .gitignore에 application-*.properties 패턴도 있어서 application-example.properties라는 이름은 무시됩니다. 따라서 application.properties.example이라는 이름이어야 합니다(git check-ignore로 .example과 .env.example은 무시되지 않는 것을 확인). 현재 properties 값은 ${GLOPLE_DB_PASSWORD}, ${GLOPLE_JWT_SECRET}, ${OPENAI_API_KEY:unused}, ${TOUR_API_SERVICE_KEY:}, spring.config.import=optional:file:.env[.properties]입니다. docs/domestic-tour-sync.md:137의 .env 키 목록에는 OPENAI_API_KEY가 빠져 있습니다.

- **Docker 빌드: .dockerignore가 없어 .env와 약 2.4GB 학습 파일이 빌드 컨텍스트에 들어가고, compose 실행은 JWT 값이 없어 시작 단계에서 멈출 것으로 보임** · 누가: 함께 · 크기: S
  - 왜: README:221-228은 docker compose로 실행된다고 안내합니다. 하지만 지금 설정에서는 비밀값이 든 .env가 빌드 단계로 복사되고, 학습 가상환경과 모델 파일까지 컨텍스트로 넘어갑니다. 최종 이미지에는 .env가 없으므로 JWT 자리표시자를 채울 수 없어 서버 시작이 실패할 가능성이 큽니다.
  - 근거: docker/Dockerfile.backend:8 `COPY . .`이고, .dockerignore와 docker/.dockerignore가 모두 없습니다. 루트의 .env에는 TOUR_API_SERVICE_KEY, GLOPLE_DB_PASSWORD, GLOPLE_JWT_SECRET이 있습니다. du 결과: training/.venv 461M, keyword_tagger/.venv 1.0G, training/adapters 504M, keyword_tagger/outputs 423M. docker/docker-compose.yml의 backend environment에는 DATASOURCE 3개만 있고 GLOPLE_JWT_SECRET이 없습니다. application.properties의 jwt.secret=${GLOPLE_JWT_SECRET}에는 기본값이 없습니다. 고칠 방법: .dockerignore(.env, training/, build/, .gradle, node_modules) 추가, compose에 env_file: ../.env 추가. 실제 확인은 오너가 docker를 돌려서 해야 합니다.

- **백엔드 Google Places 서버 키가 공개 저장소 코드에 하드코딩됨 (프론트 Maps 키와 다른 키)** · 누가: 함께 · 크기: S
  - 왜: 서버용 키는 보통 리퍼러 제한이 없습니다. 공개 저장소에 노출되면 과금 남용 위험이 프론트 키보다 큽니다. '프론트 키는 일단 둔다'는 결정과 별개의 키입니다.
  - 근거: src/main/java/proj/travien/controller/PlaceController.java:32 `private static final String API_KEY = "AIzaSyAN…"`. 접두사가 프론트 키(AIzaSyCC…/AIzaSyCB…/AIzaSyDH…)와 다릅니다. README.md:193은 이 상수를 직접 고치라고 안내합니다. 오너가 할 일: GCP 콘솔에서 키 교체나 API·IP 제한(오너만 가능). Claude가 할 일: google.places.api-key=${GOOGLE_PLACES_API_KEY:}로 옮기고 README와 예시 파일을 고칩니다. 키는 git 히스토리에도 남아 있으므로 교체가 핵심입니다.

- **TourAPI 저작권 유형(Type1/Type3)과 원문/생성문 구분이 API 응답에 없음 (에이전트·AI 리뷰 단계 전에 필요)** · 누가: 함께 · 크기: M
  - 왜: Type3(변경금지) 설명문을 4단계 LangGraph 에이전트나 6단계 AI 리뷰가 요약·의역하면 공공누리 조건을 어깁니다. 지금은 응답만 보고 어떤 행이 Type3인지, info가 원문인지 Glople가 만든 소개인지 알 수 없습니다. 출처 표시도 할 수 없습니다.
  - 근거: copyright_type은 저장만 됩니다(DestinationInfo.java:75-76, TourDestinationSyncService.java:255). src/main과 frontend/src를 grep해도 읽는 곳이 없습니다. KeywordResultProjection에는 id, country, location, destName, info만 있습니다. DestinationInfoRepository.java:45 `COALESCE(d.info, d.summary) AS info`가 TourAPI 원문과 생성 소개를 한 필드로 합칩니다. imageUrl(firstimage)도 저장합니다. 고칠 방법: 응답에 source, copyrightType, infoKind(OVERVIEW/SUMMARY)를 추가합니다. 에이전트 규칙은 'Type3 원문은 그대로 인용 + 출처: 한국관광공사, 또는 사실 필드만 사용, 의역·요약 금지'로 정합니다. 오너가 할 일: TourAPI 활용매뉴얼에서 cpyrhtDivCd가 텍스트에 붙는지 대표이미지에 붙는지 확인합니다(docs:20은 항목 전체에 붙는다고 가정). 그리고 SELECT copyright_type, COUNT(*) ... GROUP BY 1로 Type3 비율을 확인합니다.
  - 상태: 처리함(서버 응답, 2026-10-18). `/api/search`와 `/api/destinations` 응답에 source·copyrightType·infoKind(DESCRIPTION/SUMMARY)·license·sourceUrl이 있습니다. textPolicy는 서버가 따로 내려주지 않고 에이전트 판정표(design.md 11절) 하나로 둡니다(두 곳에서 판정하다 어긋나지 않게). 오너 확인 항목(cpyrhtDivCd가 붙는 대상, Type3 비율)은 그대로입니다.

- **위키백과 CC BY-SA 출처 표기와 해외 수집기의 행별 라이선스 필드** · 누가: Claude · 크기: S
  - 왜: 기존 여행지 정보 화면이 한국어 위키백과 본문 전체를 출처 없이 보여줍니다. 곧 들어올 해외 수집기가 위키백과 본문을 DB에 저장하면 행마다 출처 URL, 판, 라이선스를 남겨야 출처 표시와 동일조건 의무를 지킬 수 있습니다.
  - 근거: frontend/src/pages/recomendation/Information/information.js:146-165가 ko.wikipedia.org를 exintro:false, explaintext:true로 본문 전체를 가져와 보여줍니다. 파일 안에 '출처', 'CC BY', 링크가 없습니다. git status에 해외 수집기 파일이 아직 없으므로 다른 에이전트 작업이 끝난 뒤에 반영합니다(지금은 수정 금지). 필요한 것: 행별 source_url, revision_id 또는 수집일, license='CC BY-SA 4.0'. Wikidata 진술은 CC0이라 의무가 없습니다. 하지만 P18 이미지(Commons)는 파일마다 라이선스가 달라 이미지별로 출처를 표시해야 합니다. 위키백과 본문으로 만든 데이터셋을 공개한다면 CC BY-SA 4.0으로 공개해야 합니다.

- **run_overnight.sh가 새로 받은 저장소에서 학습 단계에 멈춤, 학습 환경 버전 미고정** · 누가: Claude · 크기: S
  - 왜: 재현성이 포트폴리오 신뢰도의 핵심인데, 안내된 한 줄 실행이 새 클론에서는 실패합니다. 지금은 README가 우회 방법만 적고 있습니다.
  - 근거: training/run_overnight.sh:17이 build_tool_dataset.py를 부르는데, 이 스크립트는 이제 v2를 data/tool_calling_v2에 씁니다. 그런데 configs/qwen3-4b-lora.yaml:6은 data/tool_calling을 학습하고 scripts/evaluate.py:80 기본값도 v1 test입니다. README:24-25가 이 문제를 우회 방법으로만 설명합니다. 또 training/에는 requirements.txt가 없습니다(README:18은 버전 없이 `pip install mlx-lm huggingface_hub`). 설치된 버전은 mlx_lm 0.32.2, mlx 0.32.4, huggingface_hub 2.2.0, transformers 5.20.1입니다. README:17에는 /opt/homebrew/bin/python3.12 경로가 고정돼 있습니다. 고칠 방법: 스크립트가 build_tool_dataset_v1.py를 부르게 하거나 버전 인자를 받게 하고, 버전을 고정한 training/requirements.txt를 추가합니다.

- **커밋 분할 계획 (63개 경로, 약 0.96MB, 비밀값 노출 없음 확인)** · 누가: 함께 · 크기: S
  - 왜: 모든 변경이 아직 커밋되지 않았습니다. 성격이 다른 작업(인증 버그 수정, 키워드 규칙, TourAPI 수집, 학습 두 개, 문서)을 나눠야 리뷰하기 쉽고 포트폴리오 히스토리도 읽기 좋습니다. 각 커밋이 단독으로 컴파일돼야 합니다.
  - 근거: git status: 수정 6개 + 미추적 57개. 가장 큰 파일은 training/eval_sets/human_style.jsonl 412KB입니다. .venv, models, adapters, data, outputs, logs, *.predictions.jsonl은 무시되는 것을 확인했습니다. .env 값으로 대기 중인 64개 파일을 스캔했고 일치는 0건입니다. 제안 순서: (1) fix(auth) JwtTokenProvider와 JwtTokenProviderTest(HEAD의 JwtUtil.validateToken이 :84에 있음) (2) feat(keyword) KeywordMask.fromKoreanDescription/Mask.union과 KeywordMaskTest (3) feat(tour) DestinationInfo, Repository, Backfill, TourCategories와 csv, TourApiClient, DTO 3개, SyncService, Runner, Controller, SchedulingConfig, 테스트 7개, docs/domestic-tour-sync.md(DestinationInfo.java:14가 TourCategories를 import하므로 같이 커밋) (4) feat(training) 도구 호출 LoRA(training/에서 keyword_tagger 제외) (5) feat(training) keyword_tagger (6) docs README. 해외 수집기는 다른 에이전트가 끝난 뒤 따로 커밋합니다. results/report.md는 report_test.md와 내용이 같으므로(diff 결과 동일) 하나는 뺍니다. 오너 메모리 규칙에 따라 Claude 출처 표기 줄은 넣지 않습니다. 오너가 승인하고, 커밋마다 테스트를 돌립니다.

## P2 · 있으면 좋음 (22개)

### 백엔드

- **새 쿼리용 인덱스·제약 보강 (/api/search 비용 추정 포함)** · 누가: Claude · 크기: S
  - 왜: /api/search의 비트마스크 조건은 B-tree 인덱스를 쓸 수 없어 요청마다 전체 행을 읽습니다. 지금 규모에서는 감당할 만하지만 해외 행이 들어오면 선형으로 늘어납니다. 중복 방지 제약도 없습니다.
  - 근거: DestinationInfoRepository.java:40-55: (mask & :m) <> 0과 ORDER BY BIT_COUNT라 인덱스 사용이 불가능합니다. 약 2만 행 전체(COALESCE(info, summary) 때문에 TEXT 포함)를 읽은 뒤 LIMIT 21 우선순위 큐로 정렬합니다. 추정치는 warm buffer pool 기준 대략 10-30ms이고 행 수에 선형입니다(EXPLAIN ANALYZE로 측정 필요). 개선: (keyword_mask_low, keyword_mask_high) 커버링 인덱스와 deferred join(안쪽 SELECT id ... ORDER BY score LIMIT 21은 약 0.5MB 인덱스만 읽고 바깥에서 21행만 조회)을 적용하면 백필의 (0,0) 조회도 빨라집니다. 설명문 대기열(:28 source=? AND overview_synced_at IS NULL ORDER BY overview_failed_at, id)은 (source, overview_synced_at, overview_failed_at) 인덱스로 filesort를 없앨 수 있습니다. DestinationInfo.java:22 idx(source, external_id)는 UNIQUE가 아닙니다. TourDestinationSyncService.java:69 putIfAbsent가 중복 가능성을 전제로 합니다. ddl-auto=update는 이름이 같은 인덱스를 UNIQUE로 바꿔주지 않으므로 중복 확인 SQL 후 수동 DDL이 필요합니다. User.java:20, :27 user_id와 nickname도 인덱스·UNIQUE가 없습니다.
  - 상태 메모(2026-10-18): `/api/search`는 후보 쿼리(순위 열만, limit×4·최대 200곳)와 고른 행 본문 조회(findAllById)로 나뉘었습니다. 마스크 조건은 여전히 전체 행을 읽고, 인덱스는 그대로입니다.

- **ddl-auto=update 의존 탈피: Flyway 기준선과 validate** · 누가: Claude · 크기: M
  - 왜: update는 기존 컬럼 길이·타입 변경을 반영하지 않습니다. UNIQUE 추가 같은 ALTER가 실패해도 WARN만 남기고 기동을 계속합니다. 테스트도 같은 설정으로 실제 DB를 바꿉니다. 해외 수집기로 컬럼이 늘어나는 지금이 기준선을 잡기 좋은 시점입니다.
  - 근거: application.properties:8 spring.jpa.hibernate.ddl-auto=update. DestinationInfo.java:40(summary 500), :64(address 500), :70(imageUrl 1000)은 길이를 바꿔도 기존 테이블에 반영되지 않습니다. :43-47 마스크 컬럼은 columnDefinition으로 기본값에 의존합니다. 앞의 UNIQUE 전환 항목도 update로는 불가능합니다. 해결: mysqldump --no-data로 V1 기준선(baseline-on-migrate)을 만들고 인덱스·제약은 V2 스크립트로 추가한 뒤 ddl-auto=validate로 바꿉니다. 속도를 위해 update를 유지할지는 오너가 선택합니다.

- **Docker Compose 경로가 새 설정에서 깨짐 (.dockerignore 없음, JWT 시크릿 미주입, CORS 헤더 중복)** · 누가: Claude · 크기: M
  - 왜: README가 Docker Compose를 실행 방법으로 안내하지만, 지금 백엔드 컨테이너는 JWT 시크릿이 없어 기동에 실패합니다. 빌드 컨텍스트에는 2.4GB 학습 폴더와 .env가 통째로 들어갑니다.
  - 근거: docker-compose.yml:23-25는 SPRING_DATASOURCE_*만 설정합니다. jar 안의 application.properties에는 ${GLOPLE_JWT_SECRET}(기본값 없음)이 있고, Dockerfile.backend 최종 단계에는 jar만 있어 .env가 없습니다 → 자리표시자를 풀지 못해 기동 실패. .dockerignore가 없고(ls -a 결과), 컨텍스트 '..'에서 Dockerfile.backend:8 COPY . .이 training/(du 2.4G)와 .env를 빌드 레이어에 넣습니다. nginx.conf:44, :61이 Spring CORS 헤더 위에 Access-Control-Allow-Origin을 또 붙여 헤더가 중복됩니다. 프론트 40개 파일이 http://localhost:8080을 직접 호출해서 프록시 출처(http://localhost)는 CorsConfig에서 허용되지 않습니다. README.md:221-228. 해결: .dockerignore, env_file: ../.env, nginx add_header 제거. 고치기 전까지는 README에서 Compose 안내를 내립니다.

- **CORS 출처 하드코딩 일원화, http.cors() 누락, @PreAuthorize 무효** · 누가: Claude · 크기: S
  - 왜: 출처가 6곳 이상에 고정돼 있습니다. Security 필터가 CORS를 처리하지 않아서 보호 경로에 대한 브라우저 preflight는 403이 납니다. 로드맵 6 UI가 :3000에서 관리자·인증 API를 직접 부르면 바로 걸립니다. @PreAuthorize는 동작하지 않는데 테스트는 어노테이션이 붙어 있는지만 확인합니다.
  - 근거: CorsConfig.java:13, WebSocketConfig.java:22, @CrossOrigin PostApiController.java:16, PostController.java:25, NavigationController.java:12, nginx.conf:44/61, frontend/src/setupProxy.js. SecurityConfig.java:24-44에 .cors()가 없어 OPTIONS 요청이 /api/admin/**에서 Http403ForbiddenEntryPoint로 막힙니다. src 전체에서 EnableMethodSecurity grep 0건 → AdminController와 TourSyncController의 @PreAuthorize는 아무 효과가 없습니다(URL 규칙 :32만 보호). TourSyncControllerTest의 mapsSummaryRebuildToAdminOnlyPost는 어노테이션 존재만 검사합니다. 해결: glople.cors.allowed-origins 속성 → CorsConfigurationSource 빈 + http.cors(withDefaults()), @CrossOrigin 제거, SecurityConfig에 @EnableMethodSecurity 추가입니다.
  - 상태: 일부 처리함. http.cors(withDefaults())와 @EnableMethodSecurity를 넣었습니다. 출처 하드코딩 일원화는 남아 있습니다.

- **devtools를 implementation으로 포함: bootRun 중 컴파일하면 재시작되어 수집이 끊김, 105MB jar** · 누가: Claude · 크기: S
  - 왜: 설명문 수집을 돌리는 동안 다른 에이전트나 IDE가 compileJava를 하면 devtools가 앱을 재시작합니다. 그러면 백그라운드 수집 스레드가 shutdownNow로 끊기고 백필과 관리자 초기화가 다시 실행됩니다. 오래된 Speech 라이브러리가 jar를 키우고 오래된 grpc와 guava를 끌고 옵니다.
  - 근거: build.gradle:39 implementation 'spring-boot-devtools'(jar에 spring-boot-devtools-3.2.3.jar 포함). TourSyncRunner.java:126-129 @PreDestroy executor.shutdownNow(). build.gradle:40 google-cloud-speech:1.28.0 → jar에 grpc-netty-shaded-1.37.0, guava-30.1.1-android가 들어가고 크기는 105,159,432 bytes(ls -la build/libs)입니다. 해결: developmentOnly로 바꾸거나 수집 중에는 spring.devtools.restart.enabled=false로 둡니다. Speech 라이브러리는 최신 BOM으로 올리거나 기능 사용 여부를 확인한 뒤 제거합니다.

- **챗봇 응답을 /topic/public으로 전체 브로드캐스트 (로드맵 4 에이전트의 접점)** · 누가: Claude · 크기: S
  - 왜: 한 사용자가 챗봇과 나눈 질문·답변이 접속 중인 모든 클라이언트에 전달됩니다. LangGraph 에이전트를 붙일 자리가 바로 이 핸들러라서, 붙이기 전에 사용자별 전송으로 바꿔야 합니다.
  - 근거: ChatBotController.java:21-22 @MessageMapping("/chat.sendMessage") @SendTo("/topic/public"), :34도 같습니다. ChatBotService는 gpt-3.5-turbo를 하드코딩하고, application.properties의 gpt_api.key 기본값은 'unused'입니다. 해결: @SendToUser나 세션별 destination으로 바꾸고(프론트 구독 경로도 수정), 챗봇 호출을 인터페이스 뒤로 빼서 로드맵 4의 에이전트 서비스로 교체할 수 있게 합니다.
  - 상태: 처리함. `ChatBotController`가 `@SendToUser("/topic/chatbot")`로 보낸 사람에게만 답하고, 화면은 `/user/topic/chatbot`을 구독합니다.

- **백엔드 Google Places 키 하드코딩과 인증 없는 프록시 (프론트 Maps 키와 별개)** · 누가: 함께 · 크기: S
  - 왜: 오너가 미뤄 두기로 한 것은 프론트의 Maps 키입니다. 백엔드 소스에도 Places 키가 따로 있고, 인증 없는 /api/search-place를 통해 누구나 서버를 거쳐 이 키의 쿼터를 쓸 수 있습니다.
  - 근거: PlaceController.java:32 private static final String API_KEY = "AIza..."(git 추적 파일). :34-37 GET /api/search-place가 query를 인코딩 없이 URL에 이어 붙입니다(&나 { 포함 시 파라미터가 오염되거나 템플릿 오류). 이 경로는 SecurityConfig.java:34 permitAll입니다. README.md:194도 이 상수를 고치라고 안내합니다. 해결: ${GOOGLE_PLACES_API_KEY:}로 .env에서 읽고 UriComponentsBuilder로 인코딩합니다. 키 회전과 사용 제한 설정은 Google Cloud 콘솔에서 오너만 할 수 있습니다.

- **프로필 사진 경로가 OS 경로 문자열로 저장돼 macOS에서 업로드한 아바타가 404** · 누가: Claude · 크기: S
  - 왜: 팀이 Windows에서 개발해 프론트 11곳 이상이 백슬래시 경로만 처리합니다. 오너의 Mac에서 새로 가입하거나 업로드한 사용자는 사진이 보이지 않습니다. 백엔드 한 곳만 고치면 됩니다.
  - 근거: UserService.java:197 directory.resolve(filename).toString() → macOS에서는 'static/profile-pictures/x.jpg'가 됩니다. 프론트는 chatPage.js:371, detail.js:239, reservation.js:22 등에서 replace('static\\','')만 하므로 URL이 /static/profile-pictures/x.jpg가 되는데, WebConfig.java:13-14는 /profile-pictures/**만 매핑해서 404가 납니다. 해결: 저장값을 슬래시 구분 웹 경로 'profile-pictures/x.jpg'로 통일하고(검증 파일도 동일), 필요하면 기존 행을 변환합니다.

### 프론트엔드

- **키워드 선택 화면 손보기 (국내에 없는 키워드, 완료 버튼 위치, 빈 결과 안내)** · 누가: Claude · 크기: S
  - 왜: 국내 사용자에게 사파리·낙타 같은 해외용 키워드가 보입니다. '완료'까지 '다음'을 4번 눌러야 하고, 결과가 없으면 아무 안내 없이 되돌아갑니다. 키워드 목록도 프론트와 백엔드 두 곳에서 따로 관리됩니다.
  - 근거: keyword.js:108-121이 KeywordMask.java:13-26 목록을 그대로 복사해 두었습니다. 국내와 거의 무관한 '사파리','낙타','모스크','원숭이','열대우림','빙하','빙벽','사막'이 노출됩니다. 키워드 94개를 20개씩 보여주므로 '완료'는 5번째 페이지에서만 나옵니다(keyword.js:199-204). 선택이 비거나 매칭이 없으면 백엔드는 빈 목록을 돌려주고(KeywordRecommendService.java:19-21), personal.js:69-71은 말없이 /keyword로 되돌립니다.

- **'루트 추천' 빈 화면 처리와 같은 시군구 여행지로 대체** · 누가: Claude · 크기: S
  - 왜: 국내 여행지 상세에서 '루트 추천'을 누르면, 후기 게시글 기반이라 결과가 없을 가능성이 높습니다. 그런데 빈 상태 문구가 없어서 페이지가 깨진 것처럼 보입니다.
  - 근거: information.js:138-140이 routeRecomendation.js:34 `/api/posts/addresses/${placename}`로 넘어갑니다. 결과가 0건이면 :130의 map만 있어서 아무것도 렌더되지 않습니다. '루트 만들기'는 오사카 좌표가 하드코딩된 routeNavigation으로 갑니다(routeNavigationPage.js:64-72). 국내 후기 게시글 수는 DB를 열 수 없어 확인하지 못했지만, 팀 데이터는 해외 위주입니다(main.js 팝업 국가 6개).

### 데이터·AI

- **v1 중간 체크포인트를 평가해 v2 반복 수를 줄이고 run_overnight.sh를 v2용으로 정리** · 누가: 함께 · 크기: S
  - 왜: v1은 거의 처음부터 학습 데이터를 외웠습니다. 800회 반복(약 4시간)은 필요한 양보다 몇 배 많고, 템플릿에 과적합해 사람 말투 세트 점수를 깎았을 수 있습니다. 반복 수를 줄이면 v2 학습 시간이 절반 아래로 줄어듭니다.
  - 근거: logs/train.log: iter 90에서 train_loss 0.003, val loss는 iter 200 0.007(36행)·400 0.018·600 0.002·800 0.005(105행). adapters/qwen3-4b-tool-calling/에 0000100~0000800 체크포인트가 있습니다(504MB). run_overnight.sh는 configs/qwen3-4b-lora.yaml(v1)에 고정되어 있는데, 데이터 단계(build_tool_dataset.py)는 v2를 만듭니다(README.md:24-25). 고치는 방법: 0000200 체크포인트를 adapters.safetensors 이름으로 둔 폴더를 만들어 human_style(약 5분)과 v2 test로 평가합니다. 그 결과로 v2 iters를 300 정도로 정하고, run_overnight.sh에 VERSION 인자를 둡니다. GPU 실행은 소유자가 시점을 정합니다.

- **작은 평가 세트의 오차 범위를 함께 표시하기 (개수와 부트스트랩 CI)** · 누가: Claude · 크기: S
  - 왜: 작업별 숫자 하나가 예시 1개로 8~17%p씩 움직입니다. 지금 표만 보면 우연한 차이를 개선이나 하락으로 읽게 됩니다.
  - 근거: human_style 작업별 개수 6~12개(direct 6, clarify 8, search 12), report.py 표에는 %만 있고 개수가 없습니다. 태거 test 163개 중 탑 1·성 4·강 7개(keyword_tagger/README.md:91). 시드 13 한 번만 실행했습니다. 고치는 방법: report.py와 compare.py에 n/total과 1,000회 부트스트랩 95% CI를 넣습니다. GPU 없이 predictions 파일만으로 계산할 수 있습니다.

- **커밋 대상 결과 파일에서 로컬 절대경로 빼기** · 누가: Claude · 크기: S
  - 왜: 커밋 대상 결과 파일에 사용자 이름이 들어간 로컬 경로가 있습니다. 다른 환경에서는 의미 없는 값이기도 합니다.
  - 근거: keyword_tagger/results/roberta-base.json:667 "model_dir": "<로컬 절대경로>", training/results/finetuned_human.summary.json:3 "model": "<로컬 절대경로>"(모든 *.summary.json이 같고, .gitignore는 predictions만 제외). 고치는 방법: train_keyword_tagger.py와 evaluate.py가 ROOT 기준 상대경로를 쓰게 하고, 지금 파일도 고칩니다.

- **94개 키워드 목록 세 벌이 어긋나지 않게 자동 검사 추가** · 누가: Claude · 크기: S
  - 왜: KeywordMask에 키워드를 더하거나 순서를 바꾸면, 태거 라벨 공간·비트 순서와 도구 호출 생성기가 아무 경고 없이 어긋납니다.
  - 근거: 직접 비교: KeywordMask.java의 KEYWORDS, keyword_tagger/keywords.json, scripts/build_tool_dataset.py:40의 KEYWORDS가 지금은 94개 모두 같은 순서입니다. 하지만 build_tool_dataset.py:963은 개수(94)와 중복만 검사하고, Java와 비교한 것은 README에 적힌 한 번의 수동 확인뿐입니다(keyword_tagger/README.md:95). 고치는 방법: KeywordMask.java를 파싱해 두 파이썬 목록과 비교하는 check_keywords.py를 만들거나, keywords.json을 읽는 Java 테스트를 둡니다.

### 포트폴리오·라이선스

- **학습 README에 라이선스·재배포 섹션 추가 (klue CC BY-SA, Qwen Apache-2.0, TourAPI 학습 텍스트)** · 누가: Claude · 크기: S
  - 왜: 태거 모델이나 LoRA 어댑터를 Hugging Face에 올려 포트폴리오에 링크하려면 라이선스 의무가 생깁니다. 지금 학습 README 두 개에는 라이선스 언급이 전혀 없습니다.
  - 근거: training/README.md와 keyword_tagger/README.md를 license, Apache, CC BY로 grep하면 0건입니다. klue/roberta-base는 CC BY-SA 4.0이므로 이를 파인튜닝한 모델(outputs/roberta-base, 423M)을 공개하면 CC BY-SA 4.0과 출처 표시가 필요합니다. Qwen3-4B-Instruct-2507과 mlx-community 변환본은 Apache-2.0이라 어댑터를 공개할 때 LICENSE 사본과 변경 고지가 필요합니다. adapters/qwen3-4b-tool-calling에는 중간 체크포인트 8개(0000100~0000800, 합계 504M)가 있으니 adapters.safetensors와 adapter_config.json만 올립니다. 태거는 TourAPI 설명문(Type3 포함 가능)과 Claude가 만든 silver 라벨로 학습했으므로 데이터는 공개하지 않고 지표와 코드만 공개할 것을 권합니다. 공개할지는 오너가 정합니다.
  - 상태: 일부 처리함(2026-10-18). Hub 업로드 준비([training/hf/](../training/hf/README.md))의 모델 카드에 기반 모델·학습 데이터 라이선스와 공개 범위(태거·임베딩은 기본 비공개)를 적었습니다. 학습 README 본문의 라이선스 절은 아직입니다.

- **저장소에 LICENSE 파일 없음: 팀 코드라 오너 혼자 정할 수 없음** · 누가: 직접 · 크기: S
  - 왜: 공개 저장소에 라이선스가 없으면 기본적으로 모든 권리가 유보된 상태입니다. 그런데 코드 대부분을 다른 팀원이 썼기 때문에 오픈소스 라이선스를 붙이려면 팀원 동의가 필요합니다.
  - 근거: 루트와 git ls-files에 LICENSE, NOTICE, COPYING이 없습니다. git log 작성자는 11개 ID입니다(Jin Naeun 198, merona0531 88, Darkroom01 71, Woojeong Hyun 45 등, yss9 계정은 합계 53). 대안: README에 '팀 프로젝트 코드: 별도 라이선스 없음 / 데이터 출처: 한국관광공사 TourAPI(공공누리), 위키백과(CC BY-SA 4.0)' 섹션을 넣거나, 오너가 혼자 쓴 training/에만 별도 LICENSE를 둡니다.

- **추적 중인 런타임 업로드 파일 정리 (회의 메모, 팀 영상, 다른 기관 PDF, 카톡 사진) + .gitignore 보완** · 누가: 함께 · 크기: S
  - 왜: 서버 업로드 폴더가 소스 트리 안에 있어서 팀 시절 테스트 파일이 커밋돼 있습니다. 포트폴리오로는 비전문적으로 보이고, 오너가 로컬에서 회원가입이나 글로플러 신청을 테스트하면 새 업로드 파일이 미추적으로 쌓입니다.
  - 근거: git ls-files src/main/resources/static이 61개입니다. 예: verification-files/졸작 할 일 (11월 11일 회의 내용).txt(팀원 이름과 할 일), 인공지능 28팀.mp4(1.8MB), 도서관정보나루_API_Manual.pdf(1.6MB, 다른 기관 매뉴얼), 연동관련.pdf, image/KakaoTalk_Photo_* 4개, 스크린샷 여러 장, profile-pictures/bluememories_erd.png(다른 프로젝트 ERD). .gitignore:220은 static/image/만 무시하고(이미 추적 중인 파일은 계속 추적됨) profile-pictures/와 verification-files/는 무시하지 않습니다. WebConfig.java:13-16이 이 소스 폴더에서 파일을 제공합니다. 코드가 특정 파일명을 참조하지는 않습니다(git grep 0건). 고칠 방법: git rm --cached, ignore 추가, .gitkeep 추가. 히스토리에는 남으므로 다시 쓸지는 오너가 정합니다.

- **커밋할 결과 JSON에 로컬 절대경로(사용자 이름) 포함** · 누가: Claude · 크기: S
  - 왜: 결과 파일에 맥 사용자 이름과 디렉터리 구조가 그대로 들어가 공개 저장소에 올라갑니다. 다른 사람 환경에서는 쓸모없는 경로이기도 합니다.
  - 근거: training/results/base.summary.json, finetuned.summary.json, base_human.summary.json, finetuned_human.summary.json의 3번째 줄 "model": "<로컬 절대경로>", keyword_tagger/results/roberta-base.json:667 model_dir. 경로가 생기는 곳은 scripts/evaluate.py:78,143(ROOT 절대경로를 기본값으로 그대로 기록)과 keyword_tagger/scripts/train_keyword_tagger.py:361입니다. 고칠 방법: ROOT 기준 상대경로로 기록하게 고치고, 이미 만든 JSON 5개도 커밋 전에 상대경로로 바꿉니다.

- **키워드 태거 학습 데이터의 출처를 재현할 방법이 없음 (Neoulteo DB 덤프)** · 누가: 함께 · 크기: M
  - 왜: gold 라벨에는 id만 있고 설명문은 gitignore된 data/에만 있습니다. 그 data/는 다른 프로젝트 DB 덤프에서 만들었고, 만드는 스크립트가 저장소에 없습니다. 그래서 새로 받은 저장소에서는 prepare_data와 학습을 다시 돌릴 수 없습니다. 이제 Glople DB에 자체 TourAPI 행이 있으니 출처를 Glople로 옮길 수 있습니다.
  - 근거: keyword_tagger/README.md:13 "overview를 Neoulteo 프로젝트 DB 덤프에서 가져옴". scripts/kt_common.py:18은 data/descriptions.jsonl을 읽기만 하고, 이 파일을 만드는 스크립트가 없습니다. labels/gold_labels.jsonl은 {id, keywords} 1,083개(TourAPI contentid). 고칠 방법: glople-mysql의 external_id나 detailCommon2 재요청(약 1,083회, 하루 한도 안)으로 descriptions.jsonl을 만드는 export 스크립트를 추가하고, 덤프 날짜와 출처를 README에 적습니다. DB와 API 실행은 오너가 합니다.

- **LLM 도구 8개 중 3개는 백엔드 엔드포인트가 없다는 점 문서화 (도구 → API 매핑표)** · 누가: Claude · 크기: S
  - 왜: 모델은 도구 8개를 부르도록 학습됐지만 그중 일부는 서버에 대응하는 API가 없습니다. 이 점이 문서에 없으면 포트폴리오에서 과장으로 읽힐 수 있고, 4단계 에이전트 작업 범위도 흐려집니다.
  - 근거: training/tools/glople_tools.json의 search_destinations(region, category), get_weather, create_travel_plan에 맞는 @GetMapping/@PostMapping이 없습니다(grep). 대응하는 것: recommend_by_keywords → POST /api/search, recommend_similar_traveler_routes → POST /api/info-recommend, search_gloplers → GET /api/users/search-agents-by-hashtag, search_reviews → /api/posts 검색. 기존 save-route는 다른 여행자 id를 저장하는 기능이라 create_travel_plan과 다릅니다. training/README.md:11은 매핑 없이 도구 이름만 나열합니다. 고칠 방법: 매핑표에 '미구현(4단계에서 추가)' 표시를 넣습니다.
  - 상태: search_destinations·get_destination_detail·create_travel_plan용 API가 생겨(2026-10-18, [travel-api.md](travel-api.md)) get_weather도 M6에서 Open-Meteo로 붙여 이제 8개 모두 실행할 곳이 있습니다. training/README.md의 매핑표는 고치지 않았습니다.

- **관리자 초기 비밀번호가 코드에 고정돼 있는데, 이제 할당량을 쓰는 동기화 API를 보호함** · 누가: Claude · 크기: S
  - 왜: 예전에는 로컬 편의 설정이었습니다. 하지만 이제 /api/admin/tour-sync/*가 하루 1,000회인 TourAPI 할당량을 쓰고 DB 전체를 다시 계산합니다. 저장소가 공개라 초기 비밀번호를 누구나 압니다.
  - 근거: src/main/java/proj/travien/AdminAccountInitializer.java:29가 BCrypt.hashpw에 고정 문자열을 넘깁니다. README.md:201도 '초기 비밀번호가 코드에 있으므로'라고 인정합니다. docs/domestic-tour-sync.md:100-105의 관리자 로그인 curl은 이 계정을 씁니다. 고칠 방법: .env의 GLOPLE_ADMIN_PASSWORD를 읽고, 값이 없으면 계정을 만들지 않고 경고만 남깁니다. .env.example에도 추가합니다.

- **작은 잡파일 정리: 빈 루트 yarn.lock, 프론트 lockfile 두 개, CRA 기본 README** · 누가: Claude · 크기: S
  - 왜: 작은 항목이지만 포트폴리오 저장소를 처음 볼 때 정돈되지 않은 인상을 줍니다. 패키지 매니저도 하나로 정해집니다.
  - 근거: 루트 yarn.lock은 86바이트에 헤더뿐입니다(2024-08-07 추가). frontend/에는 package-lock.json(909KB)과 yarn.lock(477KB)이 둘 다 있는데 README:213-216은 yarn만 안내합니다. frontend/README.md는 Create React App 기본 문서 그대로입니다. 고칠 방법: 루트 yarn.lock 삭제, package-lock.json 삭제(yarn으로 통일), frontend/README.md는 지우거나 루트 README 링크로 바꿉니다. 팀 시절 파일이므로 오너가 확인한 뒤 진행합니다.
