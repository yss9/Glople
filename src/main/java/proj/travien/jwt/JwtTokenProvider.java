package proj.travien.jwt;

import io.jsonwebtoken.Claims;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.stereotype.Component;

import java.util.Collections;

@Component
public class JwtTokenProvider {

    private static final String BEARER_PREFIX = "Bearer ";

    private final JwtUtil jwtUtil;

    public JwtTokenProvider(JwtUtil jwtUtil) {
        this.jwtUtil = jwtUtil;
    }

    // 토큰에서 인증 정보 추출. 사용자를 알 수 없는 토큰이면 null
    public Authentication getAuthentication(String token) {
        Claims claims = jwtUtil.extractClaims(token);

        // principal = 로그인 아이디(userId). 수정 전에 발급된 토큰은 subject가 없으므로 userId 클레임을 쓴다.
        String userId = claims.getSubject();
        if (userId == null || userId.isBlank()) {
            userId = claims.get("userId", String.class);
        }
        if (userId == null || userId.isBlank()) {
            return null;
        }
        String role = claims.get("role", String.class);  // JWT에서 역할(Role) 추출

        // Spring Security에서 사용할 권한 생성
        // Role enum 이름(ROLE_ADMIN)이 그대로 들어오므로 접두사가 이미 있으면 다시 붙이지 않는다. (ROLE_ROLE_ADMIN 방지)
        String authorityName = role != null && role.startsWith("ROLE_") ? role : "ROLE_" + role;
        SimpleGrantedAuthority authority = new SimpleGrantedAuthority(authorityName);

        // UsernamePasswordAuthenticationToken을 생성해 Authentication 객체로 반환
        return new UsernamePasswordAuthenticationToken(userId, null, Collections.singleton(authority));
    }

    // 토큰의 유효성 검증
    public boolean validateToken(String token) {
        // 만료·서명 오류 토큰은 예외 대신 false로 받아 인증 없이 다음 필터로 넘긴다. (500 방지)
        return jwtUtil.validateToken(token);
    }

    // "Bearer <토큰>" 헤더 값에서 토큰만 꺼낸다. HTTP 요청과 STOMP CONNECT가 같이 쓴다.
    // 프론트가 쿠키가 없을 때 보내는 "Bearer undefined"·"Bearer null"·빈 값은 토큰 없음으로 본다.
    public static String resolveBearerToken(String headerValue) {
        if (headerValue == null || !headerValue.startsWith(BEARER_PREFIX)) {
            return null;
        }
        String token = headerValue.substring(BEARER_PREFIX.length()).trim();
        if (token.isEmpty() || token.equals("undefined") || token.equals("null")) {
            return null;
        }
        return token;
    }
}
