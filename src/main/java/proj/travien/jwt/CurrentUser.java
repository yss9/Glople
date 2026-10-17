package proj.travien.jwt;

import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.AnonymousAuthenticationToken;
import org.springframework.security.authentication.AuthenticationCredentialsNotFoundException;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.stereotype.Component;
import proj.travien.domain.User;
import proj.travien.repository.UserRepository;

import java.util.Objects;
import java.util.Optional;

/**
 * 요청한 사용자(행위자)는 JWT로만 정한다. 요청 파라미터의 userId·nickname·id 같은 값은 호환을 위해 받기만 하고,
 * 값이 있는데 본인이 아니면 403으로 막는다.
 *
 * <ul>
 *   <li>비로그인 → {@link AuthenticationCredentialsNotFoundException} → SecurityConfig가 401로 응답</li>
 *   <li>본인 아님 → {@link AccessDeniedException} → SecurityConfig가 403으로 응답</li>
 * </ul>
 * 넓은 catch(RuntimeException) 안에서 부르면 예외가 삼켜지므로 try 블록 앞에서 부른다.
 * 닉네임은 바뀔 수 있어 토큰의 nickname 클레임 대신 DB 값({@link #requireUser()})으로 비교한다.
 */
@Component
public class CurrentUser {

    private static final String ROLE_ADMIN = "ROLE_ADMIN";

    private final UserRepository userRepository;

    public CurrentUser(UserRepository userRepository) {
        this.userRepository = userRepository;
    }

    // 토큰으로 인증된 로그인 아이디. 비로그인(익명)이면 빈 값
    public Optional<String> userId() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null || !authentication.isAuthenticated()
                || authentication instanceof AnonymousAuthenticationToken) {
            return Optional.empty();
        }
        String name = authentication.getName();
        return (name == null || name.isBlank()) ? Optional.empty() : Optional.of(name);
    }

    // 로그인 아이디. 비로그인이면 401
    public String requireUserId() {
        return userId().orElseThrow(() -> new AuthenticationCredentialsNotFoundException("로그인이 필요합니다."));
    }

    // 로그인 사용자 엔티티(DB). 비로그인이면 빈 값
    public Optional<User> user() {
        return userId().flatMap(userRepository::findByUserId);
    }

    // 로그인 사용자 엔티티(DB). 비로그인이거나 토큰의 사용자가 DB에 없으면(삭제된 계정) 401
    public User requireUser() {
        String userId = requireUserId();
        return userRepository.findByUserId(userId)
                .orElseThrow(() -> new AuthenticationCredentialsNotFoundException("로그인 사용자를 찾을 수 없습니다."));
    }

    // 토큰 권한에 ROLE_ADMIN이 있는지
    public boolean isAdmin() {
        Authentication authentication = SecurityContextHolder.getContext().getAuthentication();
        if (authentication == null || authentication instanceof AnonymousAuthenticationToken) {
            return false;
        }
        return authentication.getAuthorities().stream()
                .anyMatch(authority -> ROLE_ADMIN.equals(authority.getAuthority()));
    }

    // 요청이 보낸 로그인 아이디가 있으면 본인이어야 한다. 비어 있으면 통과(= 본인)
    public void requireSelf(String claimedUserId) {
        String me = requireUserId();
        if (isBlank(claimedUserId)) {
            return;
        }
        if (!sameUserId(claimedUserId, me)) {
            throw new AccessDeniedException("본인만 할 수 있습니다.");
        }
    }

    // 본인 또는 관리자만. 관리자는 다른 사용자를 지정할 수 있다.
    public void requireSelfOrAdmin(String claimedUserId) {
        if (isAdmin()) {
            requireUserId();
            return;
        }
        requireSelf(claimedUserId);
    }

    // 조회 대상 로그인 아이디: 관리자가 지정했으면 그 사용자, 그 밖에는 언제나 본인
    // (호출 전에 requireSelfOrAdmin으로 검사한다)
    public String targetUserId(String claimedUserId) {
        if (isAdmin() && !isBlank(claimedUserId)) {
            return claimedUserId;
        }
        return requireUserId();
    }

    // 요청이 보낸 닉네임이 있으면 본인 닉네임(DB)이어야 한다
    public static void requireSameNickname(User me, String claimedNickname) {
        if (isBlank(claimedNickname)) {
            return;
        }
        if (!claimedNickname.equals(me.getNickname())) {
            throw new AccessDeniedException("본인만 할 수 있습니다.");
        }
    }

    // 요청이 보낸 사용자 번호(User.id)가 있으면 본인이어야 한다
    public static void requireSameId(User me, Long claimedId) {
        if (claimedId == null) {
            return;
        }
        if (!Objects.equals(claimedId, me.getId())) {
            throw new AccessDeniedException("본인만 할 수 있습니다.");
        }
    }

    // 같은 이름의 파라미터가 쿼리와 폼에 함께 오면 'alice,alice'처럼 쉼표로 합쳐져 들어온다.
    // 통째로 같거나, 쉼표로 나눈 값이 모두 본인일 때만 본인으로 본다.
    static boolean sameUserId(String claimed, String me) {
        if (claimed.equals(me)) {
            return true;
        }
        String[] parts = claimed.split(",", -1);
        if (parts.length < 2) {
            return false;
        }
        for (String part : parts) {
            if (!part.trim().equals(me)) {
                return false;
            }
        }
        return true;
    }

    private static boolean isBlank(String value) {
        return value == null || value.isBlank();
    }
}
