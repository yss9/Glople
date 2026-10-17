package proj.travien.jwt;

import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;
import org.springframework.test.util.ReflectionTestUtils;
import proj.travien.domain.Role;
import proj.travien.domain.User;

import java.time.LocalDateTime;
import java.util.Date;

// 인가 테스트에서 같이 쓰는 테스트 전용 비밀값·사용자·토큰. (실제 .env 비밀값은 읽지 않는다)
public final class TestTokens {

    // Base64("glople-authz-test-secret-glople-authz-test") — @TestPropertySource의 jwt.secret과 같은 값
    public static final String SECRET = "Z2xvcGxlLWF1dGh6LXRlc3Qtc2VjcmV0LWdsb3BsZS1hdXRoei10ZXN0";

    private TestTokens() {
    }

    public static JwtUtil jwtUtil(long expirationMillis) {
        JwtUtil jwtUtil = new JwtUtil();
        ReflectionTestUtils.setField(jwtUtil, "secretKey", SECRET);
        ReflectionTestUtils.setField(jwtUtil, "expiration", expirationMillis);
        return jwtUtil;
    }

    public static JwtUtil jwtUtil() {
        return jwtUtil(3_600_000L);
    }

    public static String tokenFor(JwtUtil jwtUtil, User user) {
        return jwtUtil.generateToken(user.getId(), user.getUserId(), user.getNickname(), user.getRole());
    }

    // 수정 전 형식(subject 없이 userId 클레임만 있는) 토큰
    public static String legacyTokenWithoutSubject(Long id, String userId, String nickname, Role role) {
        return Jwts.builder()
                .claim("id", id)
                .claim("userId", userId)
                .claim("nickname", nickname)
                .claim("role", role.name())
                .setExpiration(new Date(System.currentTimeMillis() + 60_000))
                .signWith(SignatureAlgorithm.HS256, SECRET)
                .compact();
    }

    public static User user(long id, String userId, String nickname, Role role) {
        User user = new User(id);
        user.setUserId(userId);
        user.setNickname(nickname);
        user.setRole(role);
        return user;
    }

    // 관리자가 승인한 예약대행자
    public static User approvedAgent(long id, String userId, String nickname) {
        User agent = user(id, userId, nickname, Role.ROLE_AGENT);
        agent.setAgent(true);
        agent.setAgentApprovedDate(LocalDateTime.of(2026, 1, 1, 0, 0));
        return agent;
    }
}
