package proj.travien.domain;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.junit.jupiter.api.Test;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.json.Jackson2ObjectMapperBuilder;
import org.springframework.test.util.ReflectionTestUtils;
import proj.travien.controller.FollowController;
import proj.travien.dto.UserDTO;
import proj.travien.jwt.CurrentUser;
import proj.travien.repository.BookingRepository;
import proj.travien.repository.PointsHistoryRepository;
import proj.travien.repository.PostRepository;
import proj.travien.repository.UserRepository;
import proj.travien.service.FollowService;
import proj.travien.service.UserService;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

// 게시물·좋아요·댓글·리뷰처럼 다른 사용자의 콘텐츠를 엔티티째 내보내는 API에서
// 안에 들어간 User가 비밀번호 해시나 개인정보 없이 공개 프로필(id, nickname, profilePicture)만 내보내는지 확인한다.
class UserJsonExposureTest {

    private static final String PASSWORD_HASH = "$2a$10$7EqJtq98hPqEX7fNZaFWoOhi5BWX4Z7mVjzKqf2KJqv1u7bD3f0hW";
    private static final String BIRTH_DATE = "1999-01-01";
    private static final String VERIFICATION_DIR = "/verification/";

    // 화면(frontend/src)은 다른 사용자의 User를 읽지 않는다. 좋아요 사용자 식별과 표시용 필드만 남긴다.
    private static final Set<String> PUBLIC_USER_FIELDS = Set.of("id", "nickname", "profilePicture");

    // 다른 사용자의 콘텐츠 응답 어디에도 나오면 안 되는 User 필드 (mbti는 게시물 자체 필드라 뺀다)
    private static final Set<String> PRIVATE_USER_FIELDS = Set.of(
            "password", "userId", "name", "dateOfBirth", "gender", "points", "verificationFile",
            "agentCountry", "introduction", "hashtags", "specIntroduction", "averageReviewRating",
            "statusMessage", "role", "agentApprovedDate");

    // 스프링 부트가 MVC 응답에 쓰는 ObjectMapper와 같은 방식으로 만든다. (Jackson2ObjectMapperBuilder + 날짜는 문자열)
    private final ObjectMapper objectMapper = Jackson2ObjectMapperBuilder.json()
            .featuresToDisable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .build();

    @Test
    void postsNeverExposePasswordOrPrivateFieldsOfLikers() throws Exception {
        // GET /api/posts/, /api/posts/{country}, /api/posts/{id}/, /api/posts/search, /api/posts/user/{nickname},
        // GET /api/likes/user/{id}, POST /api/posts/, PUT /api/posts/{boardID} 는 Post 엔티티(likes → Like.user)를 그대로 내보낸다.
        JsonNode json = responseJson(List.of(postLikedBy(user(1L, "alice"), user(2L, "bob"))));

        assertThat(privateUserFieldPaths(json, "")).isEmpty();
        JsonNode likes = json.get(0).get("likes");
        assertThat(likes).hasSize(2);
        likes.forEach(like -> assertPublicUser(like.get("user")));

        // 화면에서 쓰는 게시물 필드는 그대로 나간다
        JsonNode post = json.get(0);
        assertThat(post.get("boardID").asLong()).isEqualTo(1L);
        assertThat(post.get("title").asText()).isEqualTo("오사카 3박 4일");
        assertThat(post.get("nickname").asText()).isEqualTo("writer");
        assertThat(post.get("profilePicture").asText()).isEqualTo("/image/writer.png");
        assertThat(post.get("likesCount").asInt()).isEqualTo(2);
        assertThat(post.get("addresses").get(0).get("address").asText()).isEqualTo("오사카성");
        assertThat(post.get("likeUserIds")).hasSize(2);
    }

    @Test
    void bestReviewsNeverExposePasswordOrPrivateFieldsOfLikers() throws Exception {
        // GET /api/likes/best-reviews 는 {"bestPosts": [{"post": Post, "commentCount": n}]}
        Map<String, Object> postDetails = new HashMap<>();
        postDetails.put("post", postLikedBy(user(1L, "alice")));
        postDetails.put("commentCount", 3L);
        JsonNode json = responseJson(Map.of("bestPosts", List.of(postDetails)));

        assertThat(privateUserFieldPaths(json, "")).isEmpty();
        JsonNode best = json.get("bestPosts").get(0);
        assertPublicUser(best.get("post").get("likes").get(0).get("user"));
        assertThat(best.get("post").get("nickname").asText()).isEqualTo("writer");
        assertThat(best.get("commentCount").asLong()).isEqualTo(3L);
    }

    @Test
    void createdCommentExposesOnlyPublicProfilesOfWriterAndLikers() throws Exception {
        // POST /api/comments/ 는 Comment 엔티티(작성자 user + 게시물 post 전체)를 그대로 돌려준다
        Comment comment = Comment.builder()
                .id(5L)
                .post(postLikedBy(user(2L, "bob")))
                .user(user(1L, "alice"))
                .content("저도 다녀왔어요")
                .build();
        JsonNode json = responseJson(comment);

        assertThat(privateUserFieldPaths(json, "")).isEmpty();
        assertPublicUser(json.get("user"));
        assertThat(json.get("user").get("nickname").asText()).isEqualTo("alice");
        assertPublicUser(json.get("post").get("likes").get(0).get("user"));
        assertThat(json.get("content").asText()).isEqualTo("저도 다녀왔어요");
    }

    @Test
    void bookReviewsExposeOnlyPublicProfileOfAgent() throws Exception {
        // GET /api/bookReviews/, /api/bookReviews/agent/{userId}, /api/bookReviews/agent/review,
        // POST /api/bookReviews/agent/{nickname} 는 BookReview 엔티티(리뷰 대상 대행자 user)를 그대로 내보낸다
        BookReview review = new BookReview(user(3L, "agent"), "친절해요", 5);
        review.setId(7L);
        JsonNode json = responseJson(List.of(review));

        assertThat(privateUserFieldPaths(json, "")).isEmpty();
        assertPublicUser(json.get(0).get("user"));
        // 화면(reservation/modal.js, Agent.js)은 content와 star만 읽는다
        assertThat(json.get(0).get("content").asText()).isEqualTo("친절해요");
        assertThat(json.get(0).get("star").asInt()).isEqualTo(5);
    }

    @Test
    void otherEntitiesReferencingUsersExposeOnlyPublicProfiles() throws Exception {
        // 지금은 엔티티째 내보내는 API가 없지만, 나중에 그대로 반환해도 개인정보가 새지 않아야 한다
        Follow follow = new Follow();
        follow.setId(1L);
        follow.setUser(user(1L, "alice"));
        follow.setAgent(user(3L, "agent"));

        Booking booking = new Booking(user(1L, "alice"), user(3L, "agent"), 2);
        booking.setId(1L);

        Message message = new Message();
        message.setId(1L);
        message.setSender(user(1L, "alice"));
        message.setReceiver(user(3L, "agent"));
        message.setContent("안녕하세요");

        ReportedPost reportedPost = new ReportedPost();
        reportedPost.setId(1L);
        reportedPost.setPost(postLikedBy(user(2L, "bob")));
        reportedPost.setReporter(user(1L, "alice"));

        Map<String, Object> body = new LinkedHashMap<>();
        body.put("follow", follow);
        body.put("booking", booking);
        body.put("message", message);
        body.put("reportedPost", reportedPost);
        JsonNode json = responseJson(body);

        assertThat(privateUserFieldPaths(json, "")).isEmpty();
        assertPublicUser(json.at("/follow/user"));
        assertPublicUser(json.at("/follow/agent"));
        assertPublicUser(json.at("/booking/user"));
        assertPublicUser(json.at("/booking/agent"));
        assertPublicUser(json.at("/message/sender"));
        assertPublicUser(json.at("/message/receiver"));
        assertPublicUser(json.at("/reportedPost/reporter"));
        assertPublicUser(json.at("/reportedPost/post/likes/0/user"));
        assertThat(json.at("/booking/progress").asInt()).isEqualTo(2);
    }

    @Test
    void userEntityItselfNeverExposesPasswordHash() throws Exception {
        String json = objectMapper.writeValueAsString(user(1L, "alice"));

        assertThat(json).doesNotContain(PASSWORD_HASH).doesNotContain("password");
        assertPublicUser(objectMapper.readTree(json));
    }

    @Test
    void signupAndLoginRequestsStillCarryThePassword() throws Exception {
        // 회원가입(@RequestPart("user"))·로그인(@RequestBody)·프로필 수정은 User 엔티티가 아니라 UserDTO로 비밀번호를 받는다
        UserDTO request = objectMapper.readValue(
                "{\"userId\":\"alice-login\",\"password\":\"pw1234\",\"nickname\":\"alice\"}", UserDTO.class);

        assertThat(request.getUserId()).isEqualTo("alice-login");
        assertThat(request.getPassword()).isEqualTo("pw1234");
    }

    @Test
    void proxySubclassOfUserStillExposesOnlyPublicProfile() throws Exception {
        // 지연 로딩 프록시처럼 User를 상속한 클래스로 직렬화돼도 공개 프로필만 나간다
        User proxyLike = new ProxyLikeUser();
        proxyLike.setId(5L);
        proxyLike.setNickname("proxy");
        proxyLike.setPassword(PASSWORD_HASH);
        proxyLike.setDateOfBirth(BIRTH_DATE);

        JsonNode json = responseJson(proxyLike);
        assertPublicUser(json);
    }

    @Test
    void userDtoResponsesNeverCarryPasswordKey() throws Exception {
        // GET /api/users/my-info, GET /api/admin/all-users 는 UserDTO를 내보낸다. 값이 들어 있어도 password 키는 나가지 않는다
        UserDTO dto = new UserDTO("alice-login", PASSWORD_HASH, "홍길동", BIRTH_DATE, "F", "ENFP",
                "/image/alice.png", "alice", false, 1000, "안녕");

        String json = objectMapper.writeValueAsString(dto);
        assertThat(json).doesNotContain(PASSWORD_HASH).doesNotContain("\"password\"");
        assertThat(objectMapper.readTree(json).get("nickname").asText()).isEqualTo("alice");
    }

    @Test
    void followedAgentsExposeOnlyPublicAgentProfile() throws Exception {
        // GET /api/follow/followed-agents 는 다른 사용자(대행자)의 정보라 agentDetails(공개 대행자 프로필)만 내보낸다
        FollowService followService = mock(FollowService.class);
        UserService userService = new UserService(mock(UserRepository.class), mock(PointsHistoryRepository.class),
                mock(PostRepository.class), mock(BookingRepository.class));
        FollowController controller = new FollowController();
        ReflectionTestUtils.setField(controller, "followService", followService);
        ReflectionTestUtils.setField(controller, "userService", userService);
        // 조회 대상은 토큰 사용자(me-login)로 정해진다
        CurrentUser currentUser = mock(CurrentUser.class);
        when(currentUser.targetUserId("me-login")).thenReturn("me-login");
        ReflectionTestUtils.setField(controller, "currentUser", currentUser);
        when(followService.getFollowedAgents("me-login")).thenReturn(List.of(user(1L, "alice"), user(2L, "bob")));

        ResponseEntity<?> response = controller.getFollowedAgents("me-login");
        JsonNode json = responseJson(response.getBody());

        assertThat(json).hasSize(2);
        json.forEach(agent -> {
            List<String> keys = new ArrayList<>();
            agent.fieldNames().forEachRemaining(keys::add);
            assertThat(keys).containsExactly("agentDetails");
        });
        // 화면(MyReservation.js, Agent.js, Agent2.js)이 읽는 값은 그대로 있다
        JsonNode first = json.get(0).get("agentDetails");
        assertThat(first.get("nickname").asText()).isEqualTo("alice");
        assertThat(first.get("profilePicture").asText()).isEqualTo("/image/alice.png");
        assertThat(first.get("introduction").asText()).isEqualTo("오사카 전문 대행자입니다");
        assertThat(first.get("hashtags").get(0).asText()).isEqualTo("오사카");
        assertThat(first.get("specIntroduction").get(0).asText()).isEqualTo("가이드 5년");
        assertThat(first.get("averageReviewRating").asDouble()).isEqualTo(4.5);
        assertThat(json.toString()).doesNotContain("홍길동", "alice-login", "1200");
    }

    // 응답 문자열 어디에도 비밀번호 해시·생년월일·검증 파일 경로가 없는지 확인하고 트리로 돌려준다
    private JsonNode responseJson(Object body) throws Exception {
        String json = objectMapper.writeValueAsString(body);
        assertThat(json).doesNotContain(PASSWORD_HASH, BIRTH_DATE, VERIFICATION_DIR);
        return objectMapper.readTree(json);
    }

    private static void assertPublicUser(JsonNode user) {
        assertThat(user.isObject()).as("user object").isTrue();
        List<String> fieldNames = new ArrayList<>();
        user.fieldNames().forEachRemaining(fieldNames::add);
        assertThat(fieldNames).containsExactlyInAnyOrderElementsOf(PUBLIC_USER_FIELDS);
        assertThat(user.get("nickname").asText()).isNotBlank();
    }

    // JSON 전체를 훑어 개인정보 필드가 있는 경로를 모은다 (예: /0/likes/1/user/password)
    private static List<String> privateUserFieldPaths(JsonNode node, String path) {
        List<String> found = new ArrayList<>();
        if (node.isObject()) {
            node.fields().forEachRemaining(entry -> {
                String childPath = path + "/" + entry.getKey();
                if (PRIVATE_USER_FIELDS.contains(entry.getKey())) {
                    found.add(childPath);
                }
                found.addAll(privateUserFieldPaths(entry.getValue(), childPath));
            });
        } else if (node.isArray()) {
            for (int i = 0; i < node.size(); i++) {
                found.addAll(privateUserFieldPaths(node.get(i), path + "/" + i));
            }
        }
        return found;
    }

    private static User user(long id, String nickname) {
        User user = new User(id);
        user.setUserId(nickname + "-login");
        user.setPassword(PASSWORD_HASH);
        user.setName("홍길동");
        user.setDateOfBirth(BIRTH_DATE);
        user.setGender("M");
        user.setMbti("ENFP");
        user.setProfilePicture("/image/" + nickname + ".png");
        user.setNickname(nickname);
        user.setPoints(1200);
        user.setAgent(true);
        user.setVerificationFile(VERIFICATION_DIR + nickname + ".pdf");
        user.setAgentCountry("일본");
        user.setIntroduction("오사카 전문 대행자입니다");
        user.setHashtags(List.of("오사카"));
        user.setSpecIntroduction(List.of("가이드 5년"));
        user.setAverageReviewRating(4.5);
        user.setRole(Role.ROLE_AGENT);
        user.setAgentApprovedDate(LocalDateTime.of(2026, 10, 1, 9, 0));
        return user;
    }

    private static Post postLikedBy(User... likers) {
        Post post = new Post();
        post.setBoardID(1L);
        post.setTitle("오사카 3박 4일");
        post.setBody("<p>본문</p>");
        post.setNickname("writer");
        post.setMbti("INTJ");
        post.setProfilePicture("/image/writer.png");
        post.setCountry("일본");
        post.setLikesCount(likers.length);
        post.setAddresses(new HashSet<>(Set.of(Addresses.builder().address("오사카성").build())));
        for (User liker : likers) {
            Like like = new Like();
            like.setId(liker.getId() * 10);
            like.setPost(post);
            like.setUser(liker);
            post.getLikes().add(like);
        }
        return post;
    }

    // 지연 로딩 프록시(User의 하위 클래스)를 흉내 낸다
    static class ProxyLikeUser extends User {
        public Object getHibernateLazyInitializer() {
            return new Object();
        }
    }
}
