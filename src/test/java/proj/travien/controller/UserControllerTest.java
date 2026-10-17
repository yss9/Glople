package proj.travien.controller;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.dto.UserDTO;
import proj.travien.jwt.CurrentUser;
import proj.travien.jwt.JwtTokenProvider;
import proj.travien.jwt.JwtUtil;
import proj.travien.jwt.SecurityConfig;
import proj.travien.jwt.TestTokens;
import proj.travien.repository.UserRepository;
import proj.travien.service.UserService;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.BDDMockito.given;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

// 실제 SecurityConfig·JWT 필터를 올린 컨트롤러 슬라이스 (DB 없음).
// 예전에는 JwtAuthenticationFilter가 필요로 하는 JwtTokenProvider가 없어 컨텍스트가 뜨지 않았다.
@WebMvcTest(UserController.class)
@Import({SecurityConfig.class, JwtTokenProvider.class, JwtUtil.class, CurrentUser.class})
@TestPropertySource(properties = {"jwt.secret=" + TestTokens.SECRET, "jwt.expiration=3600000"})
public class UserControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JwtUtil jwtUtil;

    @MockBean
    private UserService userService;

    @MockBean
    private UserRepository userRepository;

    @Test
    void testSignUp() throws Exception {
        given(userService.isUserIdInUse(anyString())).willReturn(false);
        given(userService.isNicknameInUse(anyString())).willReturn(false);
        given(userService.createUser(any(UserDTO.class), any(), isNull())).willReturn(true);

        // 컨트롤러는 user를 JSON 파트(@RequestPart)로 받는다
        MockMultipartFile user = new MockMultipartFile("user", "", MediaType.APPLICATION_JSON_VALUE,
                "{\"userId\":\"testUser\",\"password\":\"testPass\",\"nickname\":\"testNick\"}".getBytes(StandardCharsets.UTF_8));
        mockMvc.perform(multipart("/api/users/signup")
                        .file(user)
                        .file("profilePicture", new byte[0]))
                .andExpect(status().isOk());
    }

    @Test
    void testLogin() throws Exception {
        User user = TestTokens.user(1L, "testUser", "testNick", Role.ROLE_USER);
        given(userService.login(anyString(), anyString())).willReturn(user);

        mockMvc.perform(post("/api/users/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"userId\":\"testUser\", \"password\":\"testPass\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.token").isNotEmpty());
    }

    @Test
    void testCheckUserId() throws Exception {
        given(userService.isUserIdInUse(anyString())).willReturn(true);

        mockMvc.perform(get("/api/users/check-userid")
                        .param("username", "testUser"))
                .andExpect(status().isOk())
                .andExpect(content().string("true"));
    }

    @Test
    void testGetAllNicknames() throws Exception {
        List<String> nicknames = Arrays.asList("nick1", "nick2");
        given(userService.getAllNicknames()).willReturn(nicknames);

        // 닉네임 전체 목록은 로그인 사용자만
        mockMvc.perform(get("/api/users/nicknames"))
                .andExpect(status().isUnauthorized());

        User user = TestTokens.user(1L, "testUser", "testNick", Role.ROLE_USER);
        given(userRepository.findByUserId("testUser")).willReturn(Optional.of(user));
        mockMvc.perform(get("/api/users/nicknames")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + TestTokens.tokenFor(jwtUtil, user)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0]").value("nick1"))
                .andExpect(jsonPath("$[1]").value("nick2"));
    }
}
