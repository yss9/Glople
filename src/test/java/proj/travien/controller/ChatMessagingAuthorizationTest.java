package proj.travien.controller;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.messaging.simp.annotation.SendToUser;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.AuthorityUtils;
import org.springframework.test.util.ReflectionTestUtils;
import proj.travien.domain.ChatMessage;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.jwt.TestTokens;
import proj.travien.repository.ChatMessageRepository;
import proj.travien.repository.UserRepository;
import proj.travien.service.ChatAccessService;

import java.lang.reflect.Method;
import java.security.Principal;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

// STOMP 메시지 핸들러가 보낸 사람을 토큰 사용자로 덮어쓰고, 챗봇이 JWT를 방송하지 않는지 확인한다. (DB 없음)
class ChatMessagingAuthorizationTest {

    private final ChatMessageRepository chatMessageRepository = mock(ChatMessageRepository.class);
    private final UserRepository userRepository = mock(UserRepository.class);
    private final ChatAccessService chatAccessService = mock(ChatAccessService.class);
    private final ChatController chatController = new ChatController();
    private final ChatBotController chatBotController = new ChatBotController();

    private final User alice = TestTokens.user(1L, "alice", "앨리스", Role.ROLE_USER);
    private final Principal aliceSession = new UsernamePasswordAuthenticationToken(
            "alice", null, AuthorityUtils.createAuthorityList("ROLE_USER"));

    @BeforeEach
    void setUp() {
        ReflectionTestUtils.setField(chatController, "chatMessageRepository", chatMessageRepository);
        ReflectionTestUtils.setField(chatController, "userRepository", userRepository);
        ReflectionTestUtils.setField(chatController, "chatAccessService", chatAccessService);
        ReflectionTestUtils.setField(chatBotController, "userRepository", userRepository);
        when(userRepository.findByUserId("alice")).thenReturn(Optional.of(alice));
        when(chatAccessService.isParticipant("alice_bob", 1L)).thenReturn(true);
    }

    @Test
    void senderIsOverwrittenWithTheConnectedUser() {
        ChatMessage forged = new ChatMessage();
        forged.setId(42L);                    // 다른 방의 기존 메시지 id
        forged.setSender("bob");              // 사칭
        forged.setRoomId("bob_carol");        // 다른 방
        forged.setContent("포인트 요청 1000");
        forged.setType(ChatMessage.MessageType.CHAT);

        ChatMessage sent = chatController.sendMessage("alice_bob", forged, aliceSession);

        assertThat(sent.getSender()).isEqualTo("앨리스");  // DB 닉네임
        assertThat(sent.getId()).isNull();               // merge 덮어쓰기 방지
        assertThat(sent.getRoomId()).isEqualTo("alice_bob");
        verify(chatMessageRepository).save(sent);
    }

    @Test
    void nonParticipantOrAnonymousCannotSend() {
        ChatMessage message = new ChatMessage();
        message.setContent("hi");

        assertThatThrownBy(() -> chatController.sendMessage("bob_carol", message, aliceSession))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> chatController.sendMessage("alice_bob", message, null))
                .isInstanceOf(AccessDeniedException.class);
        verify(chatMessageRepository, never()).save(any());
    }

    @Test
    void clientCannotSendServerOnlyMessageTypes() {
        ChatMessage join = new ChatMessage();
        join.setType(ChatMessage.MessageType.JOIN);

        assertThatThrownBy(() -> chatController.sendMessage("alice_bob", join, aliceSession))
                .isInstanceOf(IllegalArgumentException.class);

        ChatMessage file = new ChatMessage();
        file.setType(ChatMessage.MessageType.FILE);
        file.setContent("/image/x.pdf");
        assertThat(chatController.sendMessage("alice_bob", file, aliceSession).getType()).isEqualTo(ChatMessage.MessageType.FILE);
    }

    @Test
    void chatbotJoinNeverCarriesTheJwtAndUsesTheConnectedUser() throws Exception {
        proj.travien.dto.ChatMessage join = new proj.travien.dto.ChatMessage();
        join.setType(proj.travien.dto.ChatMessage.MessageType.JOIN);
        join.setSender("bob");
        join.setToken("Bearer secret.jwt.value");

        proj.travien.dto.ChatMessage reply = chatBotController.addUser(join, aliceSession);

        assertThat(reply.getToken()).isNull();
        assertThat(reply.getSender()).isEqualTo("앨리스");
        // 혹시 받은 DTO가 그대로 나가더라도 token은 JSON에 실리지 않는다 (WRITE_ONLY)
        String json = new ObjectMapper().writeValueAsString(join);
        assertThat(json).doesNotContain("secret.jwt.value").doesNotContain("\"token\"");
        assertThatThrownBy(() -> chatBotController.addUser(join, null)).isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void chatbotRepliesGoOnlyToTheAskingUser() throws Exception {
        // 예전 @SendTo("/topic/public")는 접속자 모두에게 방송했다. 사용자 목적지는 SimpleBroker 접두사(/topic) 아래여야 전달된다.
        for (String name : new String[]{"sendMessage", "addUser"}) {
            Method method = ChatBotController.class.getMethod(name, proj.travien.dto.ChatMessage.class, Principal.class);
            assertThat(method.getAnnotation(SendToUser.class).value()).containsExactly("/topic/chatbot");
            assertThat(method.getAnnotation(org.springframework.messaging.handler.annotation.SendTo.class)).isNull();
        }
        assertThatThrownBy(() -> chatBotController.sendMessage(new proj.travien.dto.ChatMessage(), null))
                .isInstanceOf(AccessDeniedException.class);
    }
}
