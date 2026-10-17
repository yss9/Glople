package proj.travien.jwt;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.messaging.Message;
import org.springframework.messaging.simp.SimpMessageHeaderAccessor;
import org.springframework.messaging.simp.stomp.StompCommand;
import org.springframework.messaging.simp.stomp.StompHeaderAccessor;
import org.springframework.messaging.support.ExecutorSubscribableChannel;
import org.springframework.messaging.support.ImmutableMessageChannelInterceptor;
import org.springframework.web.socket.TextMessage;
import org.springframework.web.socket.WebSocketMessage;
import org.springframework.web.socket.WebSocketSession;
import org.springframework.web.socket.messaging.StompSubProtocolHandler;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.repository.UserRepository;
import proj.travien.service.ChatAccessService;

import java.security.Principal;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.concurrent.ConcurrentHashMap;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.atLeastOnce;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 실제 StompSubProtocolHandler에 STOMP 프레임 문자열을 넣어, CONNECT 때 정한 사용자가 이후 프레임에도 남는지와
 * 거부된 프레임이 채널로 가지 않고 ERROR 프레임으로 돌아가는지 확인한다. (브로커·DB·서버 없음)
 * 스프링 설정처럼 clientInboundChannel에는 우리 인터셉터 다음에 ImmutableMessageChannelInterceptor를 둔다.
 */
class StompSessionFlowTest {

    private final JwtUtil jwtUtil = TestTokens.jwtUtil();
    private final UserRepository userRepository = mock(UserRepository.class);
    private final ChatAccessService chatAccessService = mock(ChatAccessService.class);
    private final StompSubProtocolHandler protocolHandler = new StompSubProtocolHandler();
    private final ExecutorSubscribableChannel clientInbound = new ExecutorSubscribableChannel(); // 실행기 없음 = 같은 스레드
    private final List<Message<?>> delivered = new ArrayList<>();
    private final WebSocketSession session = mock(WebSocketSession.class);

    private final User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);

    @BeforeEach
    void setUp() throws Exception {
        clientInbound.addInterceptor(new StompAuthChannelInterceptor(new JwtTokenProvider(jwtUtil), userRepository, chatAccessService));
        clientInbound.addInterceptor(new ImmutableMessageChannelInterceptor());
        clientInbound.subscribe(delivered::add);

        Map<String, Object> attributes = new ConcurrentHashMap<>();
        when(session.getId()).thenReturn("session-1");
        when(session.getAttributes()).thenReturn(attributes);
        when(session.isOpen()).thenReturn(true);

        when(userRepository.findByUserId("alice")).thenReturn(Optional.of(alice));
        when(chatAccessService.isParticipant("alice_bob", 1L)).thenReturn(true);

        protocolHandler.afterSessionStarted(session, clientInbound); // 세션별 STOMP 디코더 준비
    }

    @Test
    void userFromConnectStaysOnTheSessionForLaterFrames() throws Exception {
        send("CONNECT\naccept-version:1.2\nAuthorization:Bearer " + TestTokens.tokenFor(jwtUtil, alice) + "\n\n\0");
        send("SUBSCRIBE\nid:sub-0\ndestination:/topic/public/alice_bob\n\n\0");
        send("SEND\ndestination:/app/chat.sendMessage/alice_bob\ncontent-type:application/json\n\n{\"content\":\"hi\"}\0");

        assertThat(delivered).hasSize(3);
        for (Message<?> message : delivered) {
            Principal user = SimpMessageHeaderAccessor.getUser(message.getHeaders());
            assertThat(user).as(StompHeaderAccessor.wrap(message).getCommand().name()).isNotNull();
            assertThat(user.getName()).isEqualTo("alice");
        }
    }

    @Test
    void anonymousSessionCannotSubscribeToRoomsAndGetsErrorFrame() throws Exception {
        // 쿠키가 없으면 프론트는 'Bearer undefined'를 보낸다 → 익명 연결은 허용
        send("CONNECT\naccept-version:1.2\nAuthorization:Bearer undefined\n\n\0");
        send("SUBSCRIBE\nid:sub-0\ndestination:/topic/public/alice_bob\n\n\0");
        send("SUBSCRIBE\nid:sub-1\ndestination:/topic/public/*\n\n\0");

        assertThat(delivered).extracting(m -> StompHeaderAccessor.wrap(m).getCommand()).containsExactly(StompCommand.CONNECT);
        assertThat(errorFrames()).hasSize(2);
    }

    @Test
    void clientCannotInjectServerMessageFrames() throws Exception {
        // 서버만 보내는 MESSAGE 프레임을 클라이언트가 보내도 채널(브로커)로 가지 않아야 한다.
        // 막지 않으면 SimpleBroker가 남의 방 구독자에게 가짜 메시지(sender 위조)를 그대로 전달한다.
        send("CONNECT\naccept-version:1.2\n\n\0");
        send("MESSAGE\ndestination:/topic/public/alice_bob\nsubscription:sub-0\nmessage-id:1\n\n{\"sender\":\"bob\",\"content\":\"fake\"}\0");
        send("MESSAGE\ndestination:/user/alice/topic/chatbot\nsubscription:sub-0\nmessage-id:2\n\n{}\0");

        assertThat(delivered).extracting(m -> StompHeaderAccessor.wrap(m).getCommand()).containsExactly(StompCommand.CONNECT);
        assertThat(errorFrames()).hasSize(2);
    }

    @Test
    void connectWithForgedTokenIsRefused() throws Exception {
        send("CONNECT\naccept-version:1.2\nAuthorization:Bearer forged.token.value\n\n\0");

        assertThat(delivered).isEmpty();
        assertThat(errorFrames()).hasSize(1);
    }

    private void send(String frame) throws Exception {
        protocolHandler.handleMessageFromClient(session, new TextMessage(frame), clientInbound);
    }

    private List<String> errorFrames() throws Exception {
        @SuppressWarnings({"rawtypes", "unchecked"})
        ArgumentCaptor<WebSocketMessage<?>> captor = (ArgumentCaptor) ArgumentCaptor.forClass(WebSocketMessage.class);
        verify(session, atLeastOnce()).sendMessage(captor.capture());
        return captor.getAllValues().stream()
                .map(message -> String.valueOf(message.getPayload()))
                .filter(payload -> payload.startsWith("ERROR"))
                .toList();
    }
}
