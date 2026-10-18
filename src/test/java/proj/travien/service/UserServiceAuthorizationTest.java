package proj.travien.service;

import org.junit.jupiter.api.Test;
import org.springframework.security.access.AccessDeniedException;
import proj.travien.domain.Booking;
import proj.travien.domain.Post;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.dto.AgentApplicationDTO;
import proj.travien.dto.AgentDTO;
import proj.travien.dto.UserDTO;
import proj.travien.jwt.TestTokens;
import proj.travien.repository.BookingRepository;
import proj.travien.repository.PointsHistoryRepository;
import proj.travien.repository.PostRepository;
import proj.travien.repository.UserRepository;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

// 대행자 신청·승인, 프로필(닉네임·비밀번호) 변경, 포인트 이체의 서비스 규칙 (DB 없음)
class UserServiceAuthorizationTest {

    private final UserRepository userRepository = mock(UserRepository.class);
    private final PostRepository postRepository = mock(PostRepository.class);
    private final BookingRepository bookingRepository = mock(BookingRepository.class);
    private final UserService userService = new UserService(
            userRepository, mock(PointsHistoryRepository.class), postRepository, bookingRepository);

    // ---------- 대행자 신청·승인 ----------

    @Test
    void applyingAsAgentDoesNotGrantAgentRoleOrSelfRating() {
        User applicant = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
        when(userRepository.findByUserId("alice")).thenReturn(Optional.of(applicant));

        boolean applied = userService.applyAsAgent("alice",
                new AgentApplicationDTO("Japan", "intro", List.of("#오사카"), List.of("spec"), 5.0), null);

        assertThat(applied).isTrue();
        assertThat(applicant.isAgent()).isTrue();                 // 신청함
        assertThat(applicant.getRole()).isEqualTo(Role.ROLE_USER); // 승인 전에는 대행자 권한 없음
        assertThat(applicant.getAgentApprovedDate()).isNull();
        assertThat(applicant.getAverageReviewRating()).isZero();   // 스스로 넣은 별점은 무시
        assertThat(UserService.isApprovedAgent(applicant)).isFalse();
    }

    @Test
    void adminApprovalMakesAgentButKeepsAdminRole() {
        User applicant = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
        applicant.setAgent(true);
        User adminApplicant = TestTokens.user(9L, "admin", "admin", Role.ROLE_ADMIN);
        adminApplicant.setAgent(true);
        when(userRepository.findByUserId("alice")).thenReturn(Optional.of(applicant));
        when(userRepository.findByUserId("admin")).thenReturn(Optional.of(adminApplicant));

        assertThat(userService.approveAgentApplication("alice")).isTrue();
        assertThat(applicant.getRole()).isEqualTo(Role.ROLE_AGENT);
        assertThat(UserService.isApprovedAgent(applicant)).isTrue();

        assertThat(userService.approveAgentApplication("admin")).isTrue();
        assertThat(adminApplicant.getRole()).isEqualTo(Role.ROLE_ADMIN);
    }

    @Test
    void publicAgentListsShowOnlyApprovedAgents() {
        User approved = TestTokens.approvedAgent(3L, "carol", "carol");
        User pending = TestTokens.user(4L, "dave", "dave", Role.ROLE_AGENT); // 예전 방식으로 역할만 붙은 미승인 신청자
        pending.setAgent(true);
        when(userRepository.findByIsAgentTrue()).thenReturn(List.of(approved, pending));

        assertThat(userService.getAllAgents()).extracting(AgentDTO::getNickname).containsExactly("carol");
    }

    @Test
    void updatingAgentDetailsIgnoresSelfRating() {
        User agent = TestTokens.approvedAgent(3L, "carol", "carol");
        agent.setAverageReviewRating(3.5);
        when(userRepository.findByUserId("carol")).thenReturn(Optional.of(agent));

        userService.updateAgentDetails("carol", new AgentDTO("Japan", "new intro", List.of(), List.of(), 5.0, "carol", null));

        assertThat(agent.getIntroduction()).isEqualTo("new intro");
        assertThat(agent.getAverageReviewRating()).isEqualTo(3.5);
    }

    // ---------- 프로필 수정 (U11) ----------

    @Test
    void nicknameTakenByAnotherUserIsRejected() {
        User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
        User bob = TestTokens.user(2L, "bob", "bob", Role.ROLE_USER);
        when(userRepository.findByUserId("alice")).thenReturn(Optional.of(alice));
        when(userRepository.findByNickname("bob")).thenReturn(Optional.of(bob));

        assertThat(userService.updateUserProfile("alice", profile("bob")))
                .isEqualTo(UserService.ProfileUpdateResult.NICKNAME_IN_USE);
        assertThat(alice.getNickname()).isEqualTo("alice");
        verify(userRepository, never()).save(any());
        verify(postRepository, never()).saveAll(anyList());
    }

    @Test
    void nicknameChangeMovesOwnPostsAndIgnoresClientProfilePicture() {
        User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
        alice.setProfilePicture("static/profile-pictures/me.png");
        when(userRepository.findByUserId("alice")).thenReturn(Optional.of(alice));
        // 대소문자만 바꾸는 경우 DB(대소문자 무시)는 본인을 돌려준다 → 본인은 중복이 아니다
        when(userRepository.findByNickname("Alice")).thenReturn(Optional.of(alice));
        Post post = new Post();
        post.setNickname("alice");
        when(postRepository.findByNickname("alice")).thenReturn(List.of(post));

        UserDTO request = profile("Alice");
        request.setProfilePicture("http://evil.example/x.png");

        assertThat(userService.updateUserProfile("alice", request)).isEqualTo(UserService.ProfileUpdateResult.UPDATED);
        assertThat(alice.getNickname()).isEqualTo("Alice");
        assertThat(post.getNickname()).isEqualTo("Alice"); // 옛 닉네임을 다른 사람이 가져가도 이 글의 주인이 되지 않는다
        assertThat(alice.getProfilePicture()).isEqualTo("static/profile-pictures/me.png");
        verify(postRepository).saveAll(List.of(post));
    }

    @Test
    void missingNicknameKeepsTheCurrentOne() {
        User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
        when(userRepository.findByUserId("alice")).thenReturn(Optional.of(alice));

        assertThat(userService.updateUserProfile("alice", profile(null))).isEqualTo(UserService.ProfileUpdateResult.UPDATED);
        assertThat(alice.getNickname()).isEqualTo("alice");
        verify(postRepository, never()).findByNickname(any());
    }

    // ---------- 비밀번호 변경 (U11) ----------

    // 토큰만으로 비밀번호를 바꾸지 못한다. 현재 비밀번호가 없거나 틀리면 아무 칸도 바꾸지 않는다(닉네임 포함).
    @Test
    void passwordChangeWithoutOrWithWrongCurrentPasswordChangesNothing() {
        User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
        String oldHash = org.springframework.security.crypto.bcrypt.BCrypt.hashpw("old-pass", org.springframework.security.crypto.bcrypt.BCrypt.gensalt(4));
        alice.setPassword(oldHash);
        alice.setMbti("ENFP");
        when(userRepository.findByUserId("alice")).thenReturn(Optional.of(alice));

        UserDTO missing = profile("alice-new");
        missing.setPassword("new-pass");
        assertThat(userService.updateUserProfile("alice", missing))
                .isEqualTo(UserService.ProfileUpdateResult.CURRENT_PASSWORD_REQUIRED);

        UserDTO wrong = profile("alice-new");
        wrong.setPassword("new-pass");
        wrong.setCurrentPassword("not-my-pass");
        assertThat(userService.updateUserProfile("alice", wrong))
                .isEqualTo(UserService.ProfileUpdateResult.CURRENT_PASSWORD_MISMATCH);

        assertThat(alice.getPassword()).isEqualTo(oldHash);
        assertThat(alice.getNickname()).isEqualTo("alice");
        assertThat(alice.getMbti()).isEqualTo("ENFP");
        verify(userRepository, never()).save(any());
    }

    @Test
    void passwordChangesWhenTheCurrentPasswordMatches() {
        User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
        alice.setPassword(org.springframework.security.crypto.bcrypt.BCrypt.hashpw("old-pass", org.springframework.security.crypto.bcrypt.BCrypt.gensalt(4)));
        when(userRepository.findByUserId("alice")).thenReturn(Optional.of(alice));

        UserDTO request = profile(null);
        request.setPassword("new-pass");
        request.setCurrentPassword("old-pass");

        assertThat(userService.updateUserProfile("alice", request)).isEqualTo(UserService.ProfileUpdateResult.UPDATED);
        assertThat(org.springframework.security.crypto.bcrypt.BCrypt.checkpw("new-pass", alice.getPassword())).isTrue();
        assertThat(alice.getMbti()).isEqualTo("INTJ");
        verify(userRepository).save(alice);
    }

    // 비밀번호를 보내지 않거나 빈 문자열이면(화면이 새 비밀번호 칸을 비워 둠) 현재 비밀번호 없이 다른 칸만 바꾼다
    @Test
    void profileWithoutPasswordNeedsNoCurrentPasswordAndKeepsTheHash() {
        User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
        alice.setPassword("$2a$04$existinghashexistinghashexistinghashexistinghashexist");
        when(userRepository.findByUserId("alice")).thenReturn(Optional.of(alice));

        UserDTO request = profile(null);
        request.setStatusMessage("안녕하세요");
        assertThat(userService.updateUserProfile("alice", request)).isEqualTo(UserService.ProfileUpdateResult.UPDATED);
        request.setPassword("");
        assertThat(userService.updateUserProfile("alice", request)).isEqualTo(UserService.ProfileUpdateResult.UPDATED);

        assertThat(alice.getPassword()).isEqualTo("$2a$04$existinghashexistinghashexistinghashexistinghashexist");
        assertThat(alice.getStatusMessage()).isEqualTo("안녕하세요");
        assertThat(alice.getMbti()).isEqualTo("INTJ");
    }

    // 저장된 값이 BCrypt 해시가 아니면(손상·옛 형식) 예외 대신 '맞지 않음'
    @Test
    void brokenStoredHashCountsAsMismatch() {
        User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
        alice.setPassword("plain-text");
        when(userRepository.findByUserId("alice")).thenReturn(Optional.of(alice));

        UserDTO request = profile(null);
        request.setPassword("new-pass");
        request.setCurrentPassword("plain-text");
        assertThat(userService.updateUserProfile("alice", request))
                .isEqualTo(UserService.ProfileUpdateResult.CURRENT_PASSWORD_MISMATCH);
        assertThat(alice.getPassword()).isEqualTo("plain-text");
    }

    // 응답 JSON(my-info 등)에 현재 비밀번호가 실리지 않는다
    @Test
    void currentPasswordIsWriteOnlyInJson() throws Exception {
        UserDTO dto = profile("alice");
        dto.setPassword("p");
        dto.setCurrentPassword("c");
        com.fasterxml.jackson.databind.ObjectMapper mapper = new com.fasterxml.jackson.databind.ObjectMapper();
        String json = mapper.writeValueAsString(dto);
        assertThat(json).doesNotContain("currentPassword").doesNotContain("\"password\"");
        assertThat(mapper.readValue("{\"currentPassword\":\"c\"}", UserDTO.class).getCurrentPassword()).isEqualTo("c");
    }

    // ---------- 포인트 이체 (U22) ----------

    @Test
    void transferNeedsBookingWithApprovedAgent() {
        User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
        alice.setPoints(1000);
        User carol = TestTokens.approvedAgent(3L, "carol", "carol");
        User bob = TestTokens.user(2L, "bob", "bob", Role.ROLE_USER);
        when(userRepository.findByNickname("alice")).thenReturn(Optional.of(alice));
        when(userRepository.findByNickname("carol")).thenReturn(Optional.of(carol));
        when(userRepository.findByNickname("bob")).thenReturn(Optional.of(bob));
        when(bookingRepository.findByUser_NicknameAndAgent_Nickname("alice", "carol")).thenReturn(Optional.empty());

        // 예약 없는 대행자, 대행자가 아닌 사용자, 자기 자신에게는 보낼 수 없다
        assertThatThrownBy(() -> userService.transferPointsToAgent(alice, "carol", 100)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> userService.transferPointsToAgent(alice, "bob", 100)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> userService.transferPointsToAgent(alice, "alice", 100)).isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> userService.transferPointsToAgent(alice, "carol", 0)).isInstanceOf(IllegalArgumentException.class);
        assertThat(alice.getPoints()).isEqualTo(1000);
        assertThat(carol.getPoints()).isZero();
    }

    @Test
    void successfulTransferMarksBookingPaidOnTheServer() {
        User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
        alice.setPoints(1000);
        User carol = TestTokens.approvedAgent(3L, "carol", "carol");
        when(userRepository.findByNickname("alice")).thenReturn(Optional.of(alice));
        when(userRepository.findByNickname("carol")).thenReturn(Optional.of(carol));
        Booking mine = new Booking(alice, carol, 2);
        Booking reverse = new Booking(carol, alice, 2);
        when(bookingRepository.findByUser_NicknameAndAgent_Nickname("alice", "carol")).thenReturn(Optional.of(mine));
        when(bookingRepository.findByUser_NicknameAndAgent_Nickname("carol", "alice")).thenReturn(Optional.of(reverse));

        userService.transferPointsToAgent(alice, "carol", 300);

        assertThat(alice.getPoints()).isEqualTo(700);
        assertThat(carol.getPoints()).isEqualTo(300);
        assertThat(mine.isSended()).isTrue();
        assertThat(mine.getProgress()).isEqualTo(3);
        assertThat(reverse.isSended()).isTrue();
        assertThat(reverse.getProgress()).isEqualTo(3);
    }

    @Test
    void approvalDateIsWhatMakesAnAgent() {
        User user = TestTokens.user(5L, "eve", "eve", Role.ROLE_AGENT);
        user.setAgent(true);
        assertThat(UserService.isApprovedAgent(user)).isFalse();
        user.setAgentApprovedDate(LocalDateTime.now());
        assertThat(UserService.isApprovedAgent(user)).isTrue();
        assertThat(UserService.isApprovedAgent(null)).isFalse();
    }

    private static UserDTO profile(String nickname) {
        UserDTO dto = new UserDTO();
        dto.setNickname(nickname);
        dto.setMbti("INTJ");
        return dto;
    }
}
