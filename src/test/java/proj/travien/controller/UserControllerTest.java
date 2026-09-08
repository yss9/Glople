package proj.travien.controller;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.multipart.MultipartFile;
import proj.travien.jwt.JwtUtil;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.dto.UserDTO;
import proj.travien.service.UserService;

import java.util.Arrays;
import java.util.List;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.BDDMockito.given;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

@WebMvcTest(UserController.class)
public class UserControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private UserService userService;

    @MockBean
    private JwtUtil jwtUtil;

    @Test
    void testSignUp() throws Exception {
        given(userService.isUserIdInUse(anyString())).willReturn(false);
        given(userService.isNicknameInUse(anyString())).willReturn(false);
        given(userService.createUser(any(UserDTO.class), any(MultipartFile.class), any(MultipartFile.class))).willReturn(true);

        mockMvc.perform(multipart("/api/users/signup")
                        .file("profilePicture", new byte[0])
                        .file("verificationFile", new byte[0])
                        .param("user", "userDTO"))
                .andExpect(status().isOk());
    }

    @Test
    void testLogin() throws Exception {
        User user = new User();
        user.setId(1L);
        user.setUserId("testUser");
        user.setNickname("testNick");
        user.setRole(Role.ROLE_USER);
        given(userService.login(anyString(), anyString())).willReturn(user);
        given(jwtUtil.generateToken(1L, "testUser", "testNick", Role.ROLE_USER)).willReturn("token");

        mockMvc.perform(post("/api/users/login")
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"userId\":\"testUser\", \"password\":\"testPass\"}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.token").value("token"));
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

        mockMvc.perform(get("/api/users/nicknames"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0]").value("nick1"))
                .andExpect(jsonPath("$[1]").value("nick2"));
    }
}
