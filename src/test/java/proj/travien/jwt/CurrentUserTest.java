package proj.travien.jwt;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.AnonymousAuthenticationToken;
import org.springframework.security.authentication.AuthenticationCredentialsNotFoundException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.AuthorityUtils;
import org.springframework.security.core.context.SecurityContextHolder;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.repository.UserRepository;

import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

// 스프링 컨텍스트·DB 없이 SecurityContext만 바꿔 가며 확인한다.
class CurrentUserTest {

    private final UserRepository userRepository = mock(UserRepository.class);
    private final CurrentUser currentUser = new CurrentUser(userRepository);
    private final User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);

    @AfterEach
    void clearContext() {
        SecurityContextHolder.clearContext();
    }

    @Test
    void anonymousHasNoUserAndRequireGives401Exception() {
        assertThat(currentUser.userId()).isEmpty();
        assertThat(currentUser.isAdmin()).isFalse();
        assertThatThrownBy(currentUser::requireUserId).isInstanceOf(AuthenticationCredentialsNotFoundException.class);
        assertThatThrownBy(currentUser::requireUser).isInstanceOf(AuthenticationCredentialsNotFoundException.class);
        assertThatThrownBy(() -> currentUser.requireSelf(null)).isInstanceOf(AuthenticationCredentialsNotFoundException.class);

        // 스프링 시큐리티가 넣는 익명 토큰도 비로그인으로 본다
        SecurityContextHolder.getContext().setAuthentication(new AnonymousAuthenticationToken(
                "key", "anonymousUser", AuthorityUtils.createAuthorityList("ROLE_ANONYMOUS")));
        assertThat(currentUser.userId()).isEmpty();
    }

    @Test
    void loggedInUserComesFromTokenAndDatabase() {
        login("alice", "ROLE_USER");
        when(userRepository.findByUserId("alice")).thenReturn(Optional.of(alice));

        assertThat(currentUser.requireUserId()).isEqualTo("alice");
        assertThat(currentUser.requireUser()).isSameAs(alice);
        assertThat(currentUser.isAdmin()).isFalse();
    }

    @Test
    void tokenOfDeletedUserIsTreatedAsNotLoggedIn() {
        login("ghost", "ROLE_USER");
        when(userRepository.findByUserId("ghost")).thenReturn(Optional.empty());

        assertThat(currentUser.user()).isEmpty();
        assertThatThrownBy(currentUser::requireUser).isInstanceOf(AuthenticationCredentialsNotFoundException.class);
    }

    @Test
    void requireSelfAllowsMissingOrOwnIdAndRejectsOthers() {
        login("alice", "ROLE_USER");

        assertThatCode(() -> currentUser.requireSelf(null)).doesNotThrowAnyException();
        assertThatCode(() -> currentUser.requireSelf("")).doesNotThrowAnyException();
        assertThatCode(() -> currentUser.requireSelf("alice")).doesNotThrowAnyException();
        assertThatThrownBy(() -> currentUser.requireSelf("bob")).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> currentUser.requireSelf("ALICE")).isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void requireSelfAcceptsTheSameIdSentTwice() {
        // 프론트가 userId를 쿼리와 폼에 같이 보내면 스프링이 'alice,alice'로 합친다 (U09)
        login("alice", "ROLE_USER");

        assertThatCode(() -> currentUser.requireSelf("alice,alice")).doesNotThrowAnyException();
        assertThatCode(() -> currentUser.requireSelf("alice, alice")).doesNotThrowAnyException();
        assertThatThrownBy(() -> currentUser.requireSelf("alice,bob")).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> currentUser.requireSelf("bob,bob")).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> currentUser.requireSelf(",")).isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void adminMayTargetOthersButUserAlwaysTargetsSelf() {
        login("alice", "ROLE_USER");
        assertThatThrownBy(() -> currentUser.requireSelfOrAdmin("bob")).isInstanceOf(AccessDeniedException.class);
        assertThat(currentUser.targetUserId("alice,alice")).isEqualTo("alice");
        assertThat(currentUser.targetUserId(null)).isEqualTo("alice");

        login("admin", "ROLE_ADMIN");
        assertThat(currentUser.isAdmin()).isTrue();
        assertThatCode(() -> currentUser.requireSelfOrAdmin("bob")).doesNotThrowAnyException();
        assertThat(currentUser.targetUserId("bob")).isEqualTo("bob");
        assertThat(currentUser.targetUserId(null)).isEqualTo("admin");
    }

    @Test
    void nicknameAndIdChecksCompareWithDatabaseValues() {
        assertThatCode(() -> CurrentUser.requireSameNickname(alice, null)).doesNotThrowAnyException();
        assertThatCode(() -> CurrentUser.requireSameNickname(alice, "alice")).doesNotThrowAnyException();
        assertThatThrownBy(() -> CurrentUser.requireSameNickname(alice, "bob")).isInstanceOf(AccessDeniedException.class);

        assertThatCode(() -> CurrentUser.requireSameId(alice, null)).doesNotThrowAnyException();
        assertThatCode(() -> CurrentUser.requireSameId(alice, 1L)).doesNotThrowAnyException();
        assertThatThrownBy(() -> CurrentUser.requireSameId(alice, 2L)).isInstanceOf(AccessDeniedException.class);
    }

    private static void login(String userId, String role) {
        SecurityContextHolder.getContext().setAuthentication(
                new UsernamePasswordAuthenticationToken(userId, null, AuthorityUtils.createAuthorityList(role)));
    }
}
