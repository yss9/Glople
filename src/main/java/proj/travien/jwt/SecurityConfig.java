package proj.travien.jwt;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.http.HttpMethod;
import org.springframework.http.MediaType;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.security.web.access.AccessDeniedHandler;
import org.springframework.security.web.authentication.UsernamePasswordAuthenticationFilter;
import org.springframework.security.web.util.matcher.RequestMatcher;
import org.springframework.web.util.UriUtils;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.text.Normalizer;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * 인가 규칙 (위에서부터 먼저 맞는 규칙이 적용된다). 전체 표는 docs/authorization.md 참고.
 * <ol>
 *   <li>차단: 검증 서류 정적 경로, 레거시 /ppap/**</li>
 *   <li>관리자(ROLE_ADMIN): /api/admin/**, 대행자 승인, 포인트 지급·차감, 리뷰 삭제, 장소 일괄 저장</li>
 *   <li>공개: 회원가입·로그인·중복 확인, 공개 대행자 프로필, 추천, 여행지 읽기, 읽기 전용 둘러보기, 정적 파일, 문서, /ws 핸드셰이크</li>
 *   <li>그 밖에는 로그인 필요(예: 여행 일정 /api/travel-plans). 소유자·참가자 검사는 컨트롤러·서비스에서 CurrentUser로 한다.</li>
 * </ol>
 * 비로그인은 401, 권한 없음은 403이며 둘 다 같은 모양의 JSON으로 응답한다.
 */
@Configuration
@EnableWebSecurity
@EnableMethodSecurity
public class SecurityConfig {

    private static final ObjectMapper ERROR_MAPPER = new ObjectMapper();

    // 검증 서류 정적 경로. macOS(APFS)처럼 대소문자를 가리지 않는 파일 시스템에서는 /VERIFICATION-FILES/<이름>도
    // 기본 정적 핸들러가 같은 파일로 내보내므로, 호환 문자(NFKC)와 대소문자를 정규화한 뒤 비교한다.
    // 서블릿 경로와, 정적 핸들러처럼 요청 URI를 UTF-8로 디코딩한 경로를 둘 다 본다.
    static final RequestMatcher VERIFICATION_FILES = request ->
            isVerificationFilePath(normalize(servletPathOf(request)))
                    || isVerificationFilePath(normalize(decodedRequestUri(request)));

    // JwtAuthenticationFilter를 필터 체인에 등록
    @Autowired
    private JwtAuthenticationFilter jwtAuthenticationFilter;

    @Bean
    public SecurityFilterChain securityFilterChain(HttpSecurity http) throws Exception {
        http
                .csrf(csrf -> csrf.disable())  // CSRF 비활성화 (쿠키 세션 없이 Authorization 헤더만 쓴다)
                // 프론트(3000)가 Authorization 헤더를 붙여 보내면 브라우저가 토큰 없는 OPTIONS 사전 요청을 먼저 보낸다.
                // 인가보다 먼저 CORS를 처리해 사전 요청이 401로 막히지 않게 한다. (설정은 WebMvcConfigurer의 CORS 매핑)
                .cors(Customizer.withDefaults())
                .sessionManagement(session -> session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                // JWT라 서버 로그아웃이 없다. 프론트가 jwt 쿠키를 지워 로그아웃한다.
                .logout(logout -> logout.disable())
                .authorizeHttpRequests(auth -> auth
                        // 1. 차단
                        .requestMatchers(VERIFICATION_FILES).denyAll()
                        .requestMatchers("/ppap/**").denyAll()
                        // 2. 관리자
                        .requestMatchers("/api/admin/**").hasRole("ADMIN")
                        .requestMatchers(HttpMethod.POST,
                                "/api/users/approve-agent/**",
                                "/api/users/*/points/add",
                                "/api/users/*/points/deduct",
                                "/api/places").hasRole("ADMIN")
                        .requestMatchers(HttpMethod.DELETE, "/api/bookReviews/**").hasRole("ADMIN")
                        // 3. 공개 — 계정
                        .requestMatchers(HttpMethod.POST, "/api/users/signup", "/api/users/login").permitAll()
                        .requestMatchers(HttpMethod.GET, "/api/users/check-userid", "/api/users/check-nickname").permitAll()
                        // 3. 공개 — 대행자 공개 프로필
                        .requestMatchers(HttpMethod.GET,
                                "/api/users/agents",
                                "/api/users/search-agents-by-hashtag",
                                "/api/users/recent-agents",
                                "/api/users/profile-picture",
                                "/api/users/profile-picture/*").permitAll()
                        // 3. 공개 — 추천
                        .requestMatchers(HttpMethod.POST, "/api/search", "/api/info-recommend").permitAll()
                        // 3. 공개 — 여행지 읽기 (목록·이름 찾기·상세. 저장된 여행지를 읽기만 한다)
                        .requestMatchers(HttpMethod.GET, "/api/destinations", "/api/destinations/**").permitAll()
                        // 3. 공개 — 읽기 전용 둘러보기 (/api/likes/**를 통째로 열면 /api/likes/user/{id}까지 열리므로 하나씩)
                        .requestMatchers(HttpMethod.GET,
                                "/api/posts/**",
                                "/api/comments/**",
                                "/api/bookReviews/**",
                                "/api/likes/*/count",
                                "/api/likes/*/is-liked",
                                "/api/likes/best-reviews").permitAll()
                        // 3. 공개 — 비로그인 경로 탐색 화면이 쓰는 외부 장소 검색
                        .requestMatchers(HttpMethod.GET, "/api/search-place").permitAll()
                        // 3. 공개 — 정적 파일·문서 (<img>는 토큰을 보내지 않는다)
                        .requestMatchers(HttpMethod.GET,
                                "/image/**",
                                "/profile-pictures/**",
                                "/",
                                "/index.html",
                                "/app.js",
                                "/chat.html",
                                "/data/**",
                                "/favicon.ico",
                                "/swagger-ui/**",
                                "/swagger-ui.html",
                                "/v3/api-docs/**").permitAll()
                        // 브라우저 WebSocket은 핸드셰이크에 헤더를 못 붙인다. STOMP 단계에서 따로 검사한다. (StompAuthChannelInterceptor)
                        .requestMatchers("/ws/**").permitAll()
                        // 비로그인 사용자의 400·404·500 응답이 401로 덮이지 않게 한다
                        .requestMatchers("/error").permitAll()
                        // 4. 그 밖에는 로그인 필요
                        .anyRequest().authenticated()
                )
                .exceptionHandling(exceptionHandling -> exceptionHandling
                        .authenticationEntryPoint(authenticationEntryPoint())
                        .accessDeniedHandler(accessDeniedHandler())
                )
                // JWT 필터를 UsernamePasswordAuthenticationFilter 앞에 추가
                .addFilterBefore(jwtAuthenticationFilter, UsernamePasswordAuthenticationFilter.class);

        return http.build();
    }

    // JwtAuthenticationFilter는 @Component라 스프링 부트가 서블릿 필터로도 한 번 더 등록한다. 보안 필터 체인 안에서만 돌게 끈다.
    @Bean
    public FilterRegistrationBean<JwtAuthenticationFilter> jwtAuthenticationFilterRegistration(JwtAuthenticationFilter filter) {
        FilterRegistrationBean<JwtAuthenticationFilter> registration = new FilterRegistrationBean<>(filter);
        registration.setEnabled(false);
        return registration;
    }

    // 비로그인(토큰 없음·만료·무효) → 401
    public AuthenticationEntryPoint authenticationEntryPoint() {
        return (request, response, authException) ->
                writeError(request, response, HttpServletResponse.SC_UNAUTHORIZED, "UNAUTHORIZED", "로그인이 필요합니다.");
    }

    // 로그인했지만 권한 없음(관리자 전용, 남의 자원) → 403
    public AccessDeniedHandler accessDeniedHandler() {
        return (request, response, accessDeniedException) ->
                writeError(request, response, HttpServletResponse.SC_FORBIDDEN, "FORBIDDEN", "권한이 없습니다.");
    }

    @Bean
    public PasswordEncoder passwordEncoder() {
        return new BCryptPasswordEncoder();  // 비밀번호 암호화에 BCrypt 사용
    }

    static void writeError(HttpServletRequest request, HttpServletResponse response,
                           int status, String error, String message) throws IOException {
        if (response.isCommitted()) {
            return;
        }
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", status);
        body.put("error", error);
        body.put("message", message);
        body.put("path", request.getRequestURI());

        response.setStatus(status);
        response.setCharacterEncoding("UTF-8");
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        response.getWriter().write(ERROR_MAPPER.writeValueAsString(body));
    }

    private static boolean isVerificationFilePath(String path) {
        return path.equals("/verification-files") || path.startsWith("/verification-files/");
    }

    // 서블릿 경로(+pathInfo)
    private static String servletPathOf(HttpServletRequest request) {
        String path = request.getServletPath();
        String pathInfo = request.getPathInfo();
        if (pathInfo != null) {
            path = (path == null || path.isEmpty()) ? pathInfo : path + pathInfo;
        }
        return path == null ? "" : path;
    }

    // 컨텍스트 경로를 뺀 요청 URI를 UTF-8로 디코딩한 값
    private static String decodedRequestUri(HttpServletRequest request) {
        String uri = request.getRequestURI();
        if (uri == null) {
            return "";
        }
        String contextPath = request.getContextPath();
        if (contextPath != null && !contextPath.isEmpty() && uri.startsWith(contextPath)) {
            uri = uri.substring(contextPath.length());
        }
        try {
            return UriUtils.decode(uri, StandardCharsets.UTF_8);
        } catch (IllegalArgumentException e) {
            return uri;
        }
    }

    // NFKC 정규화(합자·전각 문자 등) 후 소문자
    private static String normalize(String path) {
        return Normalizer.normalize(path, Normalizer.Form.NFKC).toLowerCase(Locale.ROOT);
    }
}
