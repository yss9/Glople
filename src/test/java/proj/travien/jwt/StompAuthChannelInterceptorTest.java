package proj.travien.jwt;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.messaging.Message;
import org.springframework.messaging.MessageChannel;
import org.springframework.messaging.simp.stomp.StompCommand;
import org.springframework.messaging.simp.stomp.StompHeaderAccessor;
import org.springframework.messaging.support.MessageBuilder;
import org.springframework.messaging.support.MessageHeaderAccessor;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.authority.AuthorityUtils;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.repository.UserRepository;
import proj.travien.service.ChatAccessService;

import java.security.Principal;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

// 스프링 컨텍스트·DB 없이 STOMP 프레임을 직접 만들어 인터셉터 규칙을 확인한다.
class StompAuthChannelInterceptorTest {

    private final JwtUtil jwtUtil = TestTokens.jwtUtil();
    private final UserRepository userRepository = mock(UserRepository.class);
    private final ChatAccessService chatAccessService = mock(ChatAccessService.class);
    private final StompAuthChannelInterceptor interceptor =
            new StompAuthChannelInterceptor(new JwtTokenProvider(jwtUtil), userRepository, chatAccessService);
    private final MessageChannel channel = mock(MessageChannel.class);

    private final User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
    private final Principal aliceSession = new UsernamePasswordAuthenticationToken(
            "alice", null, AuthorityUtils.createAuthorityList("ROLE_USER"));

    @BeforeEach
    void setUp() {
        when(userRepository.findByUserId("alice")).thenReturn(Optional.of(alice));
        when(chatAccessService.isParticipant("alice_bob", 1L)).thenReturn(true);
        when(chatAccessService.isParticipant("bob_carol", 1L)).thenReturn(false);
    }

    // ---------- CONNECT ----------

    @Test
    void connectWithoutTokenIsAnonymous() {
        Message<byte[]> connect = connect(null);
        interceptor.preSend(connect, channel);
        assertThat(accessorOf(connect).getUser()).isNull();

        // 프론트는 쿠키가 없으면 'Bearer undefined'를 보낸다
        Message<byte[]> undefined = connect("Bearer undefined");
        interceptor.preSend(undefined, channel);
        assertThat(accessorOf(undefined).getUser()).isNull();
    }

    @Test
    void connectWithValidTokenSetsUserOnTheOriginalAccessor() {
        Message<byte[]> connect = connect("Bearer " + TestTokens.tokenFor(jwtUtil, alice));

        interceptor.preSend(connect, channel);

        // StompSubProtocolHandler는 메시지에 붙은 이 accessor의 user를 세션 사용자로 저장한다 (wrap() 복사본이 아니라 원본)
        Principal user = accessorOf(connect).getUser();
        assertThat(user).isNotNull();
        assertThat(user.getName()).isEqualTo("alice");
    }

    @Test
    void connectWithInvalidOrExpiredTokenIsRejected() {
        String expired = TestTokens.tokenFor(TestTokens.jwtUtil(-60_000L), alice);
        assertThatThrownBy(() -> interceptor.preSend(connect("Bearer " + expired), channel))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> interceptor.preSend(connect("Bearer forged.token.value"), channel))
                .isInstanceOf(AccessDeniedException.class);
    }

    // ---------- SUBSCRIBE ----------

    @Test
    void participantMaySubscribeToOwnRoom() {
        assertThatCode(() -> interceptor.preSend(frame(StompCommand.SUBSCRIBE, "/topic/public/alice_bob", aliceSession), channel))
                .doesNotThrowAnyException();
    }

    @Test
    void subscribingToAnotherRoomIsRejected() {
        assertThatThrownBy(() -> interceptor.preSend(frame(StompCommand.SUBSCRIBE, "/topic/public/bob_carol", aliceSession), channel))
                .isInstanceOf(AccessDeniedException.class);
        // 비로그인 연결은 자기 방도 구독할 수 없다
        assertThatThrownBy(() -> interceptor.preSend(frame(StompCommand.SUBSCRIBE, "/topic/public/alice_bob", null), channel))
                .isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void wildcardAndUnknownDestinationsAreRejected() {
        // SimpleBroker는 구독 목적지를 패턴으로 본다 → /topic/public/* 하나로 모든 방을 도청할 수 있었다
        for (String destination : new String[]{
                "/topic/public/*", "/topic/**", "/topic/public/alice_?ob", "/topic/public/{room}",
                "/topic/public/alice_bob/extra", "/topic/public/", "/topic/other", "/topic/chatbot-user123",
                "/user/bob/topic/chatbot", "/user/topic/other", "/queue/anything"}) {
            assertThatThrownBy(() -> interceptor.preSend(frame(StompCommand.SUBSCRIBE, destination, aliceSession), channel))
                    .as(destination)
                    .isInstanceOf(AccessDeniedException.class);
        }
    }

    @Test
    void chatbotChannelsNeedLogin() {
        assertThatCode(() -> interceptor.preSend(frame(StompCommand.SUBSCRIBE, "/user/topic/chatbot", aliceSession), channel))
                .doesNotThrowAnyException();
        assertThatCode(() -> interceptor.preSend(frame(StompCommand.SUBSCRIBE, "/topic/public", aliceSession), channel))
                .doesNotThrowAnyException();
        assertThatThrownBy(() -> interceptor.preSend(frame(StompCommand.SUBSCRIBE, "/user/topic/chatbot", null), channel))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> interceptor.preSend(frame(StompCommand.SEND, "/app/chat.sendMessage", null), channel))
                .isInstanceOf(AccessDeniedException.class);
        assertThatCode(() -> interceptor.preSend(frame(StompCommand.SEND, "/app/chat.addUser", aliceSession), channel))
                .doesNotThrowAnyException();
    }

    // ---------- SEND ----------

    @Test
    void participantMaySendToOwnRoomOnly() {
        assertThatCode(() -> interceptor.preSend(frame(StompCommand.SEND, "/app/chat.sendMessage/alice_bob", aliceSession), channel))
                .doesNotThrowAnyException();
        assertThatThrownBy(() -> interceptor.preSend(frame(StompCommand.SEND, "/app/chat.sendMessage/bob_carol", aliceSession), channel))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> interceptor.preSend(frame(StompCommand.SEND, "/app/chat.addUser/bob_carol", aliceSession), channel))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> interceptor.preSend(frame(StompCommand.SEND, "/app/chat.sendMessage/alice_bob", null), channel))
                .isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void sendingDirectlyToBrokerOrUserDestinationsIsRejected() {
        // /topic/...에 직접 SEND하면 SimpleBroker가 그대로 방송한다 → 참가자 검사를 우회한 가짜 메시지
        for (String destination : new String[]{
                "/topic/public/alice_bob", "/topic/public", "/user/bob/topic/chatbot", "/app/unknown", "/app/chat.sendMessage/*"}) {
            assertThatThrownBy(() -> interceptor.preSend(frame(StompCommand.SEND, destination, aliceSession), channel))
                    .as(destination)
                    .isInstanceOf(AccessDeniedException.class);
        }
    }

    @Test
    void serverOnlyFramesFromClientAreRejected() {
        // MESSAGE 프레임은 SEND와 같은 메시지 종류(MESSAGE)라 막지 않으면 브로커가 방에 그대로 방송한다 (SEND 검사 우회)
        for (StompCommand command : new StompCommand[]{
                StompCommand.MESSAGE, StompCommand.CONNECTED, StompCommand.RECEIPT, StompCommand.ERROR}) {
            for (Principal user : new Principal[]{null, aliceSession}) {
                assertThatThrownBy(() -> interceptor.preSend(frame(command, "/topic/public/alice_bob", user), channel))
                        .as(command + " / " + user)
                        .isInstanceOf(AccessDeniedException.class);
            }
        }
        assertThatThrownBy(() -> interceptor.preSend(frame(StompCommand.MESSAGE, "/user/bob/topic/chatbot", aliceSession), channel))
                .isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void otherFramesPassThrough() {
        assertThatCode(() -> interceptor.preSend(frame(StompCommand.DISCONNECT, null, null), channel)).doesNotThrowAnyException();
        assertThatCode(() -> interceptor.preSend(frame(StompCommand.UNSUBSCRIBE, null, aliceSession), channel)).doesNotThrowAnyException();
    }

    private static Message<byte[]> connect(String authorization) {
        StompHeaderAccessor accessor = StompHeaderAccessor.create(StompCommand.CONNECT);
        if (authorization != null) {
            accessor.setNativeHeader("Authorization", authorization);
        }
        accessor.setSessionId("s1");
        accessor.setLeaveMutable(true); // StompSubProtocolHandler와 같이 변경 가능한 accessor를 붙인다
        return MessageBuilder.createMessage(new byte[0], accessor.getMessageHeaders());
    }

    private static Message<byte[]> frame(StompCommand command, String destination, Principal user) {
        StompHeaderAccessor accessor = StompHeaderAccessor.create(command);
        if (destination != null) {
            accessor.setDestination(destination);
        }
        accessor.setSessionId("s1");
        accessor.setSubscriptionId("sub-0");
        accessor.setUser(user);
        accessor.setLeaveMutable(true);
        return MessageBuilder.createMessage(new byte[0], accessor.getMessageHeaders());
    }

    private static StompHeaderAccessor accessorOf(Message<?> message) {
        return MessageHeaderAccessor.getAccessor(message, StompHeaderAccessor.class);
    }
}
