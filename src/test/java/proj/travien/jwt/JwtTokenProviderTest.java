package proj.travien.jwt;

import io.jsonwebtoken.Jwts;
import io.jsonwebtoken.SignatureAlgorithm;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.security.authorization.AuthorityAuthorizationManager;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.test.util.ReflectionTestUtils;
import proj.travien.domain.Role;

import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Date;

import static org.assertj.core.api.Assertions.assertThat;

class JwtTokenProviderTest {

    private static final String SECRET = Base64.getEncoder()
            .encodeToString("glople-unit-test-secret-glople-unit-test".getBytes(StandardCharsets.UTF_8));

    private final JwtUtil jwtUtil = jwtUtil(3_600_000L);
    private final JwtTokenProvider provider = new JwtTokenProvider(jwtUtil);

    @AfterEach
    void clearContext() {
        SecurityContextHolder.clearContext();
    }

    @Test
    void grantsRoleAdminForLoginTokenOfAdmin() {
        // 로그인 시 Role enum 이름(ROLE_ADMIN)이 그대로 role claim에 들어간다.
        String token = jwtUtil.generateToken(1L, "admin", "admin", Role.ROLE_ADMIN);

        Authentication authentication = provider.getAuthentication(token);

        assertThat(authentication.getAuthorities()).extracting(GrantedAuthority::getAuthority)
                .containsExactly("ROLE_ADMIN");
        // SecurityConfig의 /api/admin/** 규칙(hasRole("ADMIN"))을 통과해야 한다.
        assertThat(isGranted("ADMIN", authentication)).isTrue();
    }

    @Test
    void addsPrefixWhenRoleClaimHasNone() {
        String token = Jwts.builder()
                .setSubject("admin")
                .claim("role", "ADMIN")
                .setExpiration(new Date(System.currentTimeMillis() + 60_000))
                .signWith(SignatureAlgorithm.HS256, SECRET)
                .compact();

        Authentication authentication = provider.getAuthentication(token);

        assertThat(authentication.getAuthorities()).extracting(GrantedAuthority::getAuthority)
                .containsExactly("ROLE_ADMIN");
        assertThat(isGranted("ADMIN", authentication)).isTrue();
    }

    @Test
    void doesNotGrantAdminToUser() {
        String token = jwtUtil.generateToken(2L, "user", "user", Role.ROLE_USER);

        Authentication authentication = provider.getAuthentication(token);

        assertThat(authentication.getAuthorities()).extracting(GrantedAuthority::getAuthority)
                .containsExactly("ROLE_USER");
        assertThat(isGranted("ADMIN", authentication)).isFalse();
    }

    @Test
    void rejectsExpiredOrBrokenTokensWithoutThrowing() {
        String expired = jwtUtil(-60_000L).generateToken(1L, "admin", "admin", Role.ROLE_ADMIN);
        String valid = jwtUtil.generateToken(1L, "admin", "admin", Role.ROLE_ADMIN);

        assertThat(provider.validateToken(expired)).isFalse();
        assertThat(provider.validateToken(valid + "x")).isFalse();
        assertThat(provider.validateToken("not-a-jwt")).isFalse();
        assertThat(provider.validateToken(valid)).isTrue();
    }

    @Test
    void filterPassesExpiredTokenThroughWithoutAuthentication() throws Exception {
        JwtAuthenticationFilter filter = new JwtAuthenticationFilter();
        ReflectionTestUtils.setField(filter, "jwtTokenProvider", provider);
        String expired = jwtUtil(-60_000L).generateToken(1L, "admin", "admin", Role.ROLE_ADMIN);

        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/search");
        request.addHeader("Authorization", "Bearer " + expired);
        MockFilterChain chain = new MockFilterChain();
        filter.doFilter(request, new MockHttpServletResponse(), chain);

        // 예외(500) 없이 다음 필터로 넘어가고, 인증 정보는 설정되지 않는다.
        assertThat(chain.getRequest()).isSameAs(request);
        assertThat(SecurityContextHolder.getContext().getAuthentication()).isNull();
    }

    private static boolean isGranted(String role, Authentication authentication) {
        return AuthorityAuthorizationManager.hasRole(role).check(() -> authentication, new Object()).isGranted();
    }

    private static JwtUtil jwtUtil(long expiration) {
        JwtUtil jwtUtil = new JwtUtil();
        ReflectionTestUtils.setField(jwtUtil, "secretKey", SECRET);
        ReflectionTestUtils.setField(jwtUtil, "expiration", expiration);
        return jwtUtil;
    }
}
