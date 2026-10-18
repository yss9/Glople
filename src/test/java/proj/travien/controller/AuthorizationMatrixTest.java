package proj.travien.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.http.HttpStatus;
import proj.travien.domain.ChatMessage;
import proj.travien.domain.Post;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.dto.DestinationDetailDTO;
import proj.travien.dto.UserDTO;
import proj.travien.jwt.CurrentUser;
import proj.travien.jwt.JwtTokenProvider;
import proj.travien.jwt.JwtUtil;
import proj.travien.jwt.SecurityConfig;
import proj.travien.jwt.TestTokens;
import proj.travien.repository.ChatMessageRepository;
import proj.travien.repository.ChatRoomRepository;
import proj.travien.repository.PostRepository;
import proj.travien.repository.UserChatRoomRepository;
import proj.travien.repository.UserRepository;
import proj.travien.service.BookReviewService;
import proj.travien.service.BookingService;
import proj.travien.service.ChatAccessService;
import proj.travien.service.CommentService;
import proj.travien.service.DestinationSearchService;
import proj.travien.service.FollowService;
import proj.travien.service.KeywordRecommendService;
import proj.travien.service.KeywordTaggerImportService;
import proj.travien.service.LikeService;
import proj.travien.service.MyRouteService;
import proj.travien.service.PlaceService;
import proj.travien.service.PostService;
import proj.travien.service.SpeechToTextService;
import proj.travien.service.TourSyncRunner;
import proj.travien.service.TravelPlanService;
import proj.travien.service.UserInfoRecommendationService;
import proj.travien.service.UserService;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.options;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.put;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 인가 매트릭스 대표 경로를 실제 SecurityConfig·JWT 필터·CurrentUser로 확인한다. (DB 없음: 서비스·저장소는 목)
 * 토큰은 테스트 비밀값으로 실제 JwtUtil이 만든다.
 * <ul>
 *   <li>비로그인: 보호 경로 401 / 공개 경로 200</li>
 *   <li>사용자 A 토큰으로 사용자 B 자원: 403</li>
 *   <li>사용자 A 토큰으로 자기 자원: 2xx</li>
 *   <li>일반 사용자 토큰으로 관리자 경로: 403, 관리자 토큰: 2xx</li>
 * </ul>
 */
@WebMvcTest(controllers = {
        UserController.class, AdminController.class, PostController.class, BookingController.class,
        ChatController.class, CommentController.class, LikeController.class, BookReviewController.class,
        RecommendController.class, FollowController.class, PlaceController.class, WikipediaController.class,
        PostApiController.class, TourSyncController.class, DestinationController.class, TravelPlanController.class,
        KeywordMaskController.class
})
@Import({SecurityConfig.class, JwtTokenProvider.class, JwtUtil.class, CurrentUser.class})
@TestPropertySource(properties = {
        "jwt.secret=" + TestTokens.SECRET,
        "jwt.expiration=3600000",
        "upload.location=build/tmp/authz-test-upload/"
})
class AuthorizationMatrixTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JwtUtil jwtUtil;

    @MockBean private UserRepository userRepository;
    @MockBean private UserService userService;
    @MockBean private BookingService bookingService;
    @MockBean private ChatMessageRepository chatMessageRepository;
    @MockBean private ChatRoomRepository chatRoomRepository;
    @MockBean private UserChatRoomRepository userChatRoomRepository;
    @MockBean private ChatAccessService chatAccessService;
    @MockBean private SpeechToTextService speechToTextService;
    @MockBean private PostService postService;
    @MockBean private PostRepository postRepository;
    @MockBean private CommentService commentService;
    @MockBean private LikeService likeService;
    @MockBean private BookReviewService bookReviewService;
    @MockBean private KeywordRecommendService keywordRecommendService;
    @MockBean private UserInfoRecommendationService userInfoRecommendationService;
    @MockBean private MyRouteService myRouteService;
    @MockBean private FollowService followService;
    @MockBean private PlaceService placeService;
    @MockBean private TourSyncRunner tourSyncRunner;
    @MockBean private DestinationSearchService destinationSearchService;
    @MockBean private TravelPlanService travelPlanService;
    @MockBean private KeywordTaggerImportService keywordTaggerImportService;

    private final ObjectMapper objectMapper = new ObjectMapper();

    private final User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
    private final User bob = TestTokens.user(2L, "bob", "bob", Role.ROLE_USER);
    private final User admin = TestTokens.user(9L, "admin", "admin", Role.ROLE_ADMIN);

    private String aliceToken;
    private String adminToken;

    @BeforeEach
    void setUp() {
        given(userRepository.findByUserId("alice")).willReturn(Optional.of(alice));
        given(userRepository.findByUserId("bob")).willReturn(Optional.of(bob));
        given(userRepository.findByUserId("admin")).willReturn(Optional.of(admin));
        aliceToken = TestTokens.tokenFor(jwtUtil, alice);
        adminToken = TestTokens.tokenFor(jwtUtil, admin);
        given(userService.getUserInfo(anyString())).willAnswer(invocation -> userDto(invocation.getArgument(0)));
    }

    // ---------- 비로그인 → 보호 경로 401 ----------

    @Test
    void anonymousGets401JsonOnProtectedEndpoints() throws Exception {
        List<MockHttpServletRequestBuilder> protectedRequests = List.of(
                get("/api/users/my-info"),
                get("/api/users/my-info").param("userId", "bob"),
                post("/api/users/update-profile").param("userId", "bob").contentType(MediaType.APPLICATION_JSON).content("{}"),
                post("/api/users/transfer-points").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"userNickname\":\"bob\",\"agentNickname\":\"carol\",\"points\":10}"),
                get("/api/users/nicknames"),
                get("/api/users/bob/points"),
                get("/api/users/verification-file/bob"),
                get("/chat/history/alice_bob"),
                get("/chat/userRooms"),
                delete("/api/posts/5/"),
                put("/api/posts/5").param("title", "t").param("body", "b").param("addresses", "a")
                        .param("addressTitle", "a").param("country", "KR"),
                post("/api/likes/toggle").param("boardID", "10").param("id", "2"),
                get("/api/likes/user/2"),
                get("/api/booking/all-progress"),
                get("/api/get-routes"),
                get("/api/follow/followed-agents"),
                get("/api/wikipedia").param("query", "seoul"),
                post("/api/travel-plans").contentType(MediaType.APPLICATION_JSON).content("{}"),
                get("/api/travel-plans"),
                get("/api/travel-plans/5"),
                delete("/api/travel-plans/5"),
                get("/api/admin/all-users"),
                post("/api/admin/keyword-masks/import").contentType(MediaType.APPLICATION_NDJSON).content("{}"),
                post("/api/users/bob/points/add").param("points", "10"),
                post("/ppap/board").contentType(MediaType.APPLICATION_JSON).content("{}"),
                get("/verification-files/proof.pdf"),
                get("/logout")
        );
        for (MockHttpServletRequestBuilder request : protectedRequests) {
            MvcResult result = mockMvc.perform(request).andReturn();
            String path = result.getRequest().getRequestURI();
            assertThat(result.getResponse().getStatus()).as(path).isEqualTo(401);
            JsonNode body = objectMapper.readTree(result.getResponse().getContentAsString(StandardCharsets.UTF_8));
            assertThat(body.get("status").asInt()).as(path).isEqualTo(401);
            assertThat(body.get("error").asText()).as(path).isEqualTo("UNAUTHORIZED");
            assertThat(body.get("path").asText()).as(path).isEqualTo(path);
        }
        verify(userService, never()).getUserInfo(anyString());
        verify(userService, never()).transferPointsToAgent(any(), anyString(), anyInt());
        verify(travelPlanService, never()).create(any(), any());
        verify(travelPlanService, never()).listMine(any());
    }

    @Test
    void bearerUndefinedAndBrokenTokensAreTreatedAsAnonymous() throws Exception {
        // 프론트가 쿠키가 없을 때 직접 붙이는 'Bearer undefined'는 토큰 없음 → 공개 경로는 열리고 보호 경로는 401
        mockMvc.perform(get("/api/users/agents").header(HttpHeaders.AUTHORIZATION, "Bearer undefined"))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/users/agents").header(HttpHeaders.AUTHORIZATION, "Bearer not-a-jwt"))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/users/nicknames").header(HttpHeaders.AUTHORIZATION, "Bearer undefined"))
                .andExpect(status().isUnauthorized());

        String expired = TestTokens.tokenFor(TestTokens.jwtUtil(-60_000L), alice);
        mockMvc.perform(get("/api/posts/").header(HttpHeaders.AUTHORIZATION, bearer(expired)))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/users/my-info").header(HttpHeaders.AUTHORIZATION, bearer(expired)))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void tokenOfDeletedUserGets401WhereTheUserIsNeeded() throws Exception {
        User ghost = TestTokens.user(77L, "ghost", "ghost", Role.ROLE_USER); // DB에 없는 사용자
        mockMvc.perform(post("/api/users/update-profile").header(HttpHeaders.AUTHORIZATION, bearer(TestTokens.tokenFor(jwtUtil, ghost)))
                        .contentType(MediaType.APPLICATION_JSON).content("{\"nickname\":\"x\"}"))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("UNAUTHORIZED"));
    }

    // ---------- 비로그인 → 공개 경로 200 ----------

    @Test
    void anonymousCanUsePublicEndpoints() throws Exception {
        given(userService.isUserIdInUse("someone")).willReturn(false);
        given(postService.getAllPosts()).willReturn(List.of());
        given(userService.getAllAgents()).willReturn(List.of());
        given(keywordRecommendService.search(any())).willReturn(List.of());
        given(bookReviewService.getAllReviews()).willReturn(List.of());
        given(commentService.getCommentsByBoardID(10L)).willReturn(List.of());
        Post post = new Post();
        post.setBoardID(10L);
        post.setLikesCount(3);
        given(postRepository.findById(10L)).willReturn(Optional.of(post));

        mockMvc.perform(get("/api/users/check-userid").param("username", "someone")).andExpect(status().isOk());
        mockMvc.perform(get("/api/users/agents")).andExpect(status().isOk());
        mockMvc.perform(get("/api/posts/")).andExpect(status().isOk());
        mockMvc.perform(get("/api/comments/10")).andExpect(status().isOk());
        mockMvc.perform(get("/api/bookReviews/")).andExpect(status().isOk());
        mockMvc.perform(post("/api/search").contentType(MediaType.APPLICATION_JSON).content("{\"keywords\":[\"바다\"]}"))
                .andExpect(status().isOk());
        // 여행지 읽기(목록·이름 찾기·상세)는 공개
        given(destinationSearchService.detail(10L)).willReturn(Optional.of(new DestinationDetailDTO()));
        mockMvc.perform(get("/api/destinations").param("region", "부산")).andExpect(status().isOk());
        mockMvc.perform(get("/api/destinations/lookup").param("name", "해운대")).andExpect(status().isOk());
        mockMvc.perform(get("/api/destinations/10")).andExpect(status().isOk());
        // 비로그인 좋아요 여부는 id 값과 관계없이 false
        mockMvc.perform(get("/api/likes/10/is-liked").param("id", "2"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.isLiked").value(false))
                .andExpect(jsonPath("$.likesCount").value(3));
        verify(likeService, never()).isPostLikedByUser(anyLong(), anyLong());
        // 공개 정적 경로는 보안 단계에서 막히지 않는다 (파일이 없으니 404)
        assertThat(mockMvc.perform(get("/image/no-such-file.png")).andReturn().getResponse().getStatus()).isEqualTo(404);
    }

    @Test
    void loginIssuesTokenWhoseSubjectIsTheLoginId() throws Exception {
        given(userService.login("alice", "pw")).willReturn(alice);

        MvcResult result = mockMvc.perform(post("/api/users/login").contentType(MediaType.APPLICATION_JSON)
                        .content("{\"userId\":\"alice\",\"password\":\"pw\"}"))
                .andExpect(status().isOk())
                .andReturn();

        String token = objectMapper.readTree(result.getResponse().getContentAsString()).get("token").asText();
        assertThat(jwtUtil.extractClaims(token).getSubject()).isEqualTo("alice");
    }

    @Test
    void corsPreflightIsNotBlockedByAuthentication() throws Exception {
        mockMvc.perform(options("/api/users/my-info")
                        .header(HttpHeaders.ORIGIN, "http://localhost:3000")
                        .header(HttpHeaders.ACCESS_CONTROL_REQUEST_METHOD, "GET")
                        .header(HttpHeaders.ACCESS_CONTROL_REQUEST_HEADERS, "authorization"))
                .andExpect(status().isOk())
                .andExpect(header().string(HttpHeaders.ACCESS_CONTROL_ALLOW_ORIGIN, "http://localhost:3000"));
    }

    // ---------- 사용자 A → 사용자 B의 자원: 403 ----------

    @Test
    void userCannotReadOrChangeAnotherUsersAccount() throws Exception {
        expectForbidden(get("/api/users/my-info").param("userId", "bob"));
        expectForbidden(post("/api/users/update-profile").param("userId", "bob")
                .contentType(MediaType.APPLICATION_JSON).content("{\"nickname\":\"bob2\",\"password\":\"x\"}"));
        expectForbidden(get("/api/users/bob/points"));
        expectForbidden(get("/api/users/verification-file/bob"));
        expectForbidden(multipart("/api/users/upload-profile-picture").file(picture()).param("userId", "bob"));
        expectForbidden(get("/api/follow/followed-agents").param("userId", "bob"));
        expectForbidden(post("/api/follow/follow-agent").param("userNickname", "bob").param("agentNickname", "carol"));

        verify(userService, never()).getUserInfo(anyString());
        verify(userService, never()).updateUserProfile(anyString(), any());
        verify(userService, never()).getUserPoints(anyString());
        verify(userService, never()).loadVerificationFile(anyString());
        verify(userService, never()).uploadProfilePicture(anyString(), any());
        verify(followService, never()).getFollowedAgents(anyString());
        verify(followService, never()).followAgent(anyString(), anyString());
    }

    @Test
    void userCannotMovePointsOrBookingsOfOthers() throws Exception {
        expectForbidden(post("/api/users/transfer-points").contentType(MediaType.APPLICATION_JSON)
                .content("{\"userNickname\":\"bob\",\"agentNickname\":\"carol\",\"points\":10}"));
        expectForbidden(get("/api/booking/progress").param("userId", "bob").param("agentId", "carol"));
        expectForbidden(post("/api/booking/update-progress").param("userId", "bob").param("agentId", "carol").param("progress", "4"));
        expectForbidden(get("/api/booking/all-progress").param("nickname", "bob"));
        expectForbidden(get("/api/booking/check-sended").param("userNickname", "bob").param("agentNickname", "carol"));
        expectForbidden(post("/api/booking/set-sended").contentType(MediaType.APPLICATION_JSON)
                .content("{\"userNickname\":\"bob\",\"agentNickname\":\"carol\"}"));

        verify(userService, never()).transferPointsToAgent(any(), anyString(), anyInt());
        verify(bookingService, never()).updateProgressAs(any(), anyString(), anyInt());
        verify(bookingService, never()).getAllUniqueProgress(anyString());
    }

    @Test
    void userCannotReadOrWriteChatRoomsTheyAreNotIn() throws Exception {
        given(chatAccessService.isParticipant("bob_carol", 1L)).willReturn(false);

        expectForbidden(get("/chat/history/bob_carol"));
        expectForbidden(multipart("/chat/uploadFile/bob_carol").file(new MockMultipartFile("file", "a.pdf", "application/pdf", new byte[]{1})));
        expectForbidden(multipart("/chat/uploadFile/alice_bob").file(new MockMultipartFile("file", "a.pdf", "application/pdf", new byte[]{1}))
                .param("sender", "bob"));
        expectForbidden(post("/chat/createRoom").contentType(MediaType.APPLICATION_JSON).content("[\"bob\",\"carol\"]"));

        verify(chatMessageRepository, never()).findByRoomId(anyString());
        verify(chatRoomRepository, never()).save(any());
    }

    @Test
    void userCannotEditOrDeleteOthersContent() throws Exception {
        // 게시글 수정: 서비스의 작성자 검사가 403을 던지면, updatePost의 catch(RuntimeException)에 삼켜져 404가 되면 안 된다
        given(postService.requireEditablePost(eq(5L), any(), eq(false)))
                .willThrow(new AccessDeniedException("게시물 작성자만 할 수 있습니다."));
        expectForbidden(put("/api/posts/5").param("title", "t").param("body", "b").param("addresses", "a")
                .param("addressTitle", "a").param("country", "KR"));
        expectForbidden(delete("/api/posts/5/"));
        expectForbidden(post("/api/posts/5/thumbnail").contentType(MediaType.APPLICATION_JSON).content("{\"body\":\"<p></p>\"}"));
        verify(postService, never()).updatePost(anyLong(), any(), any());
        verify(postService, never()).deletePost(anyLong());
        verify(postService, never()).saveThumbnail(anyLong(), anyString());

        // 게시글 작성·수정 폼의 userId가 남의 것이면 403
        expectForbidden(post("/api/posts/").param("title", "t").param("body", "b").param("addresses", "a")
                .param("addressTitle", "a").param("country", "KR").param("userId", "bob"));

        // 댓글: 남의 이름(body.userId)으로 쓰기 403, 남의 댓글 삭제는 서비스가 403
        expectForbidden(post("/api/comments/").contentType(MediaType.APPLICATION_JSON)
                .content("{\"boardID\":10,\"userId\":2,\"content\":\"x\"}"));
        org.mockito.BDDMockito.willThrow(new AccessDeniedException("댓글 작성자만 삭제할 수 있습니다."))
                .given(commentService).deleteCommentAs(eq(7L), any(), eq(false));
        expectForbidden(delete("/api/comments/7"));
        verify(commentService, never()).addComment(anyLong(), anyLong(), anyString());

        // 좋아요·경로: 남의 id로 토글·조회 403
        expectForbidden(post("/api/likes/toggle").param("boardID", "10").param("id", "2"));
        expectForbidden(get("/api/likes/user/2"));
        expectForbidden(get("/api/get-routes").param("userId", "2"));
        expectForbidden(post("/api/save-route").contentType(MediaType.APPLICATION_JSON).content("{\"userId\":2,\"userInfoId\":3}"));
        verify(likeService, never()).toggleLikePost(anyLong(), anyLong());
        verify(myRouteService, never()).getMyRoutes(anyLong());
        verify(myRouteService, never()).saveMyRoute(any());

        // 여행 일정: 남의 일정·없는 일정은 서비스가 403 (자세한 경우는 TravelPlanControllerTest)
        given(travelPlanService.get(any(), eq(false), eq(5L))).willThrow(new AccessDeniedException("본인 일정만 볼 수 있습니다."));
        org.mockito.BDDMockito.willThrow(new AccessDeniedException("본인 일정만 볼 수 있습니다."))
                .given(travelPlanService).delete(any(), eq(5L));
        expectForbidden(get("/api/travel-plans/5"));
        expectForbidden(delete("/api/travel-plans/5"));
    }

    @Test
    void missingPostIsStill404ForUpdate() throws Exception {
        given(postService.requireEditablePost(eq(404L), any(), eq(false)))
                .willThrow(new ResponseStatusException(HttpStatus.NOT_FOUND, "게시물을 찾을 수 없습니다."));
        mockMvc.perform(put("/api/posts/404").header(HttpHeaders.AUTHORIZATION, bearer(aliceToken))
                        .param("title", "t").param("body", "b").param("addresses", "a")
                        .param("addressTitle", "a").param("country", "KR"))
                .andExpect(status().isNotFound());
    }

    @Test
    void verificationFilesAreNeverServedStatically() throws Exception {
        // 대소문자를 가리지 않는 파일 시스템(macOS)에서 열리던 우회 경로와 호환 문자(ﬁ 합자)도 막는다
        expectForbidden(get("/verification-files/proof.pdf"));
        expectForbidden(get("/VERIFICATION-FILES/proof.pdf"));
        expectForbidden(get("/Verification-Files/proof.pdf"));
        expectForbidden(get("/veriﬁcation-files/proof.pdf"));
        expectForbidden(get("/verification-files/proof.pdf").header(HttpHeaders.AUTHORIZATION, bearer(adminToken)), false);

        // 서류는 빌드 때 클래스패스로 복사하지 않는다(build.gradle processResources exclude).
        // 기본 정적 핸들러(/** → classpath:/static/)가 경로 표기를 바꾼 요청(Windows 8.3 이름·끝 점 등)으로도 찾을 파일이 없어야 한다.
        assertThat(getClass().getResource("/static/verification-files/")).isNull();
        assertThat(getClass().getResource("/static/verification-files/dummy.pdf")).isNull();
        assertThat(getClass().getResource("/static/index.html")).isNotNull(); // 다른 정적 파일은 그대로 복사된다
    }

    @Test
    void legacyEndpointsAreDenied() throws Exception {
        expectForbidden(post("/ppap/board").contentType(MediaType.APPLICATION_JSON).content("{\"title\":\"x\"}"));
        expectForbidden(get("/ppap/board/1"), true);
        expectForbidden(get("/ppap/board/1").header(HttpHeaders.AUTHORIZATION, bearer(adminToken)), false);
        verify(postService, never()).writePost(any());
    }

    // ---------- 사용자 A → 자기 자원: 2xx ----------

    @Test
    void userCanUseOwnResources() throws Exception {
        given(userService.getUserPoints("alice")).willReturn(1000);
        given(userService.uploadProfilePicture(eq("alice"), any())).willReturn(true);
        given(chatAccessService.isParticipant("alice_bob", 1L)).willReturn(true);
        ChatMessage message = new ChatMessage();
        message.setRoomId("alice_bob");
        message.setSender("bob");
        message.setContent("hi");
        given(chatMessageRepository.findByRoomId("alice_bob")).willReturn(List.of(message));
        given(bookingService.getProgress("alice", "carol")).willReturn(2);
        given(likeService.getLikedPostsByUser(1L)).willReturn(List.of());
        given(myRouteService.getMyRoutes(1L)).willReturn(List.of());

        // userId 없이 → 본인, 본인 userId → 본인
        mockMvc.perform(get("/api/users/my-info").header(HttpHeaders.AUTHORIZATION, bearer(aliceToken)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.userId").value("alice"));
        mockMvc.perform(get("/api/users/my-info").param("userId", "alice").header(HttpHeaders.AUTHORIZATION, bearer(aliceToken)))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/users/alice/points").header(HttpHeaders.AUTHORIZATION, bearer(aliceToken)))
                .andExpect(status().isOk());
        // 프론트처럼 userId를 쿼리와 폼에 같이 보내면 'alice,alice'로 들어온다 → 본인으로 본다
        mockMvc.perform(multipart("/api/users/upload-profile-picture?userId=alice").file(picture()).param("userId", "alice")
                        .header(HttpHeaders.AUTHORIZATION, bearer(aliceToken)))
                .andExpect(status().isOk());
        verify(userService).uploadProfilePicture(eq("alice"), any());
        mockMvc.perform(get("/chat/history/alice_bob").header(HttpHeaders.AUTHORIZATION, bearer(aliceToken)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].content").value("hi"));
        mockMvc.perform(get("/api/booking/progress").param("userId", "alice").param("agentId", "carol")
                        .header(HttpHeaders.AUTHORIZATION, bearer(aliceToken)))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/likes/user/1").header(HttpHeaders.AUTHORIZATION, bearer(aliceToken)))
                .andExpect(status().is2xxSuccessful());
        mockMvc.perform(get("/api/get-routes").header(HttpHeaders.AUTHORIZATION, bearer(aliceToken)))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/users/nicknames").header(HttpHeaders.AUTHORIZATION, bearer(aliceToken)))
                .andExpect(status().isOk());
    }

    @Test
    void ownPostUpdatePassesOwnershipCheckBeforeSaving() throws Exception {
        Post saved = new Post();
        saved.setBoardID(6L);
        saved.setTitle("t");
        given(postService.updatePost(eq(6L), any(), any())).willReturn(saved);

        mockMvc.perform(put("/api/posts/6").header(HttpHeaders.AUTHORIZATION, bearer(aliceToken))
                        .param("title", "t").param("body", "b").param("addresses", "a")
                        .param("addressTitle", "a").param("country", "KR").param("userId", "alice"))
                .andExpect(status().isOk());
        verify(postService).requireEditablePost(eq(6L), any(), eq(false));
    }

    @Test
    void profileUpdateReturnsFreshTokenWithNewNickname() throws Exception {
        User renamed = TestTokens.user(1L, "alice", "alice-new", Role.ROLE_USER);
        given(userService.updateUserProfile(eq("alice"), any())).willReturn(UserService.ProfileUpdateResult.UPDATED);
        given(userService.findByUserId("alice")).willReturn(renamed);

        MvcResult result = mockMvc.perform(post("/api/users/update-profile").param("userId", "alice")
                        .header(HttpHeaders.AUTHORIZATION, bearer(aliceToken))
                        .contentType(MediaType.APPLICATION_JSON).content("{\"nickname\":\"alice-new\"}"))
                .andExpect(status().isOk())
                .andReturn();

        String token = objectMapper.readTree(result.getResponse().getContentAsString(StandardCharsets.UTF_8)).get("token").asText();
        assertThat(jwtUtil.extractClaims(token).getSubject()).isEqualTo("alice");
        assertThat(jwtUtil.extractNickname(token)).isEqualTo("alice-new");

        given(userService.updateUserProfile(eq("alice"), any())).willReturn(UserService.ProfileUpdateResult.NICKNAME_IN_USE);
        mockMvc.perform(post("/api/users/update-profile").header(HttpHeaders.AUTHORIZATION, bearer(aliceToken))
                        .contentType(MediaType.APPLICATION_JSON).content("{\"nickname\":\"bob\"}"))
                .andExpect(status().isConflict());
    }

    @Test
    void legacyTokenWithoutSubjectStillIdentifiesTheUser() throws Exception {
        String legacy = TestTokens.legacyTokenWithoutSubject(1L, "alice", "alice", Role.ROLE_USER);
        mockMvc.perform(get("/api/users/my-info").header(HttpHeaders.AUTHORIZATION, bearer(legacy)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.userId").value("alice"));
    }

    // ---------- 일반 사용자 → 관리자 경로 403, 관리자 → 2xx ----------

    @Test
    void userTokenIsForbiddenOnAdminEndpoints() throws Exception {
        expectForbidden(get("/api/admin/all-users"));
        expectForbidden(post("/api/admin/adjust-points").param("userId", "bob").param("points", "100"));
        expectForbidden(post("/api/admin/promote-user").param("userId", "alice"));
        expectForbidden(post("/api/admin/tour-sync/summaries"));
        expectForbidden(post("/api/admin/keyword-masks/import").contentType(MediaType.APPLICATION_NDJSON).content("{}"));
        expectForbidden(post("/api/users/approve-agent/bob"));
        expectForbidden(post("/api/users/alice/points/add").param("points", "1000000"));
        expectForbidden(post("/api/users/bob/points/deduct").param("points", "10"));
        expectForbidden(delete("/api/bookReviews/1"));
        expectForbidden(post("/api/places").contentType(MediaType.APPLICATION_JSON).content("{\"places\":[]}"));

        verify(userService, never()).getAllUsers();
        verify(userService, never()).adjustPoints(anyString(), anyInt());
        verify(userService, never()).promoteToAdmin(anyString());
        verify(userService, never()).approveAgentApplication(anyString());
        verify(userService, never()).addPointsByNickname(anyString(), anyInt());
        verify(userService, never()).deductPointsByNickname(anyString(), anyInt());
        verify(bookReviewService, never()).deleteReview(anyLong());
        verify(tourSyncRunner, never()).startSummaryRebuild();
        verify(keywordTaggerImportService, never()).parse(any(), any());
        verify(keywordTaggerImportService, never()).importPredictions(any(), org.mockito.ArgumentMatchers.anyBoolean());
    }

    @Test
    void adminTokenCanUseAdminEndpointsAndActOnOthers() throws Exception {
        given(userService.getAllUsers()).willReturn(List.of());
        given(userService.approveAgentApplication("bob")).willReturn(true);
        given(userService.addPointsByNickname("bob", 10)).willReturn(true);
        given(userService.loadVerificationFile("bob")).willReturn(new ByteArrayResource(new byte[]{1, 2}) {
            @Override
            public String getFilename() {
                return "0b5f.pdf";
            }
        });

        mockMvc.perform(get("/api/admin/all-users").header(HttpHeaders.AUTHORIZATION, bearer(adminToken)))
                .andExpect(status().isOk());
        mockMvc.perform(post("/api/users/approve-agent/bob").header(HttpHeaders.AUTHORIZATION, bearer(adminToken)))
                .andExpect(status().isOk());
        mockMvc.perform(post("/api/users/bob/points/add").param("points", "10").header(HttpHeaders.AUTHORIZATION, bearer(adminToken)))
                .andExpect(status().isOk());
        mockMvc.perform(get("/api/users/my-info").param("userId", "bob").header(HttpHeaders.AUTHORIZATION, bearer(adminToken)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.userId").value("bob"));
        mockMvc.perform(get("/api/users/verification-file/bob").header(HttpHeaders.AUTHORIZATION, bearer(adminToken)))
                .andExpect(status().isOk())
                .andExpect(header().string("X-Content-Type-Options", "nosniff"));
    }

    // ---------- 도우미 ----------

    // 사용자 A(alice) 토큰으로 보내 403과 JSON 본문을 확인한다
    private void expectForbidden(MockHttpServletRequestBuilder request) throws Exception {
        expectForbidden(request.header(HttpHeaders.AUTHORIZATION, bearer(aliceToken)), false);
    }

    // anonymous=true면 토큰 없이 보내 401, 아니면 요청에 붙은 토큰으로 403
    private void expectForbidden(MockHttpServletRequestBuilder request, boolean anonymous) throws Exception {
        MvcResult result = mockMvc.perform(request).andReturn();
        String path = result.getRequest().getRequestURI();
        int expected = anonymous ? 401 : 403;
        assertThat(result.getResponse().getStatus()).as(path).isEqualTo(expected);
        JsonNode body = objectMapper.readTree(result.getResponse().getContentAsString(StandardCharsets.UTF_8));
        assertThat(body.get("status").asInt()).as(path).isEqualTo(expected);
        assertThat(body.get("error").asText()).as(path).isEqualTo(anonymous ? "UNAUTHORIZED" : "FORBIDDEN");
    }

    private static String bearer(String token) {
        return "Bearer " + token;
    }

    private static MockMultipartFile picture() {
        return new MockMultipartFile("profilePicture", "me.png", "image/png", new byte[]{1});
    }

    private static UserDTO userDto(String userId) {
        UserDTO dto = new UserDTO();
        dto.setUserId(userId);
        dto.setNickname(userId);
        return dto;
    }
}
