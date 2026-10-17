package proj.travien.jwt;

import io.jsonwebtoken.Claims;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.authority.SimpleGrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.stereotype.Component;

import java.util.Collections;

@Component
public class JwtTokenProvider {

    private final JwtUtil jwtUtil;

    public JwtTokenProvider(JwtUtil jwtUtil) {
        this.jwtUtil = jwtUtil;
    }

    // 토큰에서 인증 정보 추출
    public Authentication getAuthentication(String token) {
        Claims claims = jwtUtil.extractClaims(token);

        String username = claims.getSubject();  // JWT에서 사용자 ID 추출
        String role = claims.get("role", String.class);  // JWT에서 역할(Role) 추출

        // Spring Security에서 사용할 권한 생성
        // Role enum 이름(ROLE_ADMIN)이 그대로 들어오므로 접두사가 이미 있으면 다시 붙이지 않는다. (ROLE_ROLE_ADMIN 방지)
        String authorityName = role != null && role.startsWith("ROLE_") ? role : "ROLE_" + role;
        SimpleGrantedAuthority authority = new SimpleGrantedAuthority(authorityName);

        // UsernamePasswordAuthenticationToken을 생성해 Authentication 객체로 반환
        return new UsernamePasswordAuthenticationToken(username, null, Collections.singleton(authority));
    }

    // 토큰의 유효성 검증
    public boolean validateToken(String token) {
        // 만료·서명 오류 토큰은 예외 대신 false로 받아 인증 없이 다음 필터로 넘긴다. (500 방지)
        return jwtUtil.validateToken(token);
    }
}
