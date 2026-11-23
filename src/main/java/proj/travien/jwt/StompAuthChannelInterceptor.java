package proj.travien.jwt;

import org.springframework.messaging.Message;
import org.springframework.messaging.MessageChannel;
import org.springframework.messaging.simp.SimpMessageHeaderAccessor;
import org.springframework.messaging.simp.SimpMessageType;
import org.springframework.messaging.simp.stomp.StompCommand;
import org.springframework.messaging.simp.stomp.StompHeaderAccessor;
import org.springframework.messaging.support.ChannelInterceptor;
import org.springframework.messaging.support.MessageHeaderAccessor;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.security.core.Authentication;
import org.springframework.stereotype.Component;
import proj.travien.domain.User;
import proj.travien.repository.UserRepository;
import proj.travien.service.ChatAccessService;

import java.security.Principal;
import java.util.EnumSet;
import java.util.Set;

/**
 * STOMP 인증·인가 (WebSocketConfig.configureClientInboundChannel에 등록).
 * <ul>
 *   <li>CONNECT: Authorization 네이티브 헤더의 JWT를 검증해 세션 사용자로 둔다. 토큰이 없으면('Bearer undefined' 포함) 익명 연결,
 *       토큰이 있는데 무효·만료면 연결을 거부한다.</li>
 *   <li>SUBSCRIBE: /topic/public/{roomId}는 그 방 참가자만, /topic/public·/user/topic/chatbot(챗봇)은 로그인 사용자만.
 *       와일드카드(* ? { })가 든 목적지와 그 밖의 목적지(다른 사용자의 /user/... 포함)는 거부한다.</li>
 *   <li>SEND: /app/chat.sendMessage/{roomId}·/app/chat.addUser/{roomId}는 참가자만, 챗봇(/app/chat.sendMessage·/app/chat.addUser)은
 *       로그인 사용자만. /app/이 아닌 목적지(/topic/..., /user/...)로 직접 보내는 것은 거부한다.</li>
 *   <li>서버만 보내는 프레임(MESSAGE·CONNECTED·RECEIPT·ERROR)을 클라이언트가 보내면 거부한다.
 *       (MESSAGE 프레임은 SEND와 같은 메시지 종류라 브로커가 그대로 방송한다)</li>
 * </ul>
 * 거부하면 예외를 던지고, 스프링이 클라이언트에 ERROR 프레임을 보낸다.
 */
@Component
public class StompAuthChannelInterceptor implements ChannelInterceptor {

    private static final String ROOM_TOPIC_PREFIX = "/topic/public/";
    private static final String CHATBOT_PUBLIC_TOPIC = "/topic/public";
    private static final String CHATBOT_USER_DESTINATION = "/user/topic/chatbot";
    private static final String APP_PREFIX = "/app/";
    private static final String ROOM_SEND_PREFIX = "/app/chat.sendMessage/";
    private static final String ROOM_JOIN_PREFIX = "/app/chat.addUser/";
    private static final Set<String> CHATBOT_SENDS = Set.of("/app/chat.sendMessage", "/app/chat.addUser");
    // 검사 없이 통과시키는 클라이언트 프레임 (목적지로 메시지를 보내거나 구독하지 않는 것)
    private static final Set<StompCommand> PASS_THROUGH_CLIENT_COMMANDS = EnumSet.of(
            StompCommand.UNSUBSCRIBE, StompCommand.DISCONNECT, StompCommand.ACK, StompCommand.NACK,
            StompCommand.BEGIN, StompCommand.COMMIT, StompCommand.ABORT);

    private final JwtTokenProvider jwtTokenProvider;
    private final UserRepository userRepository;
    private final ChatAccessService chatAccessService;

    public StompAuthChannelInterceptor(JwtTokenProvider jwtTokenProvider, UserRepository userRepository,
                                       ChatAccessService chatAccessService) {
        this.jwtTokenProvider = jwtTokenProvider;
        this.userRepository = userRepository;
        this.chatAccessService = chatAccessService;
    }

    @Override
    public Message<?> preSend(Message<?> message, MessageChannel channel) {
        // wrap()은 복사본이라 setUser가 세션에 남지 않는다. 메시지에 붙은 원본 accessor를 꺼내 쓴다.
        StompHeaderAccessor accessor = MessageHeaderAccessor.getAccessor(message, StompHeaderAccessor.class);
        StompCommand command = accessor == null ? null : accessor.getCommand();
        if (command == null) {
            // 하트비트처럼 명령이 없는 프레임만 통과. 목적지로 가는 메시지·구독인데 명령이 없으면 거부한다.
            SimpMessageType type = SimpMessageHeaderAccessor.getMessageType(message.getHeaders());
            if (type == SimpMessageType.MESSAGE || type == SimpMessageType.SUBSCRIBE) {
                throw denied();
            }
            return message;
        }
        if (command == StompCommand.CONNECT || command == StompCommand.STOMP) {
            authenticate(accessor);
        } else if (command == StompCommand.SUBSCRIBE) {
            authorizeSubscribe(accessor.getDestination(), accessor.getUser());
        } else if (command == StompCommand.SEND) {
            authorizeSend(accessor.getDestination(), accessor.getUser());
        } else if (!PASS_THROUGH_CLIENT_COMMANDS.contains(command)) {
            // MESSAGE·CONNECTED·RECEIPT·ERROR는 서버만 보내는 프레임이다. 클라이언트가 MESSAGE 프레임을 보내면
            // SEND 검사를 거치지 않고 SimpleBroker가 /topic/...에 그대로 방송하고(가짜 메시지 주입),
            // /user/{이름}/... 이면 그 사용자의 세션으로 전달된다. 그래서 거부한다.
            throw denied();
        }
        return message;
    }

    private void authenticate(StompHeaderAccessor accessor) {
        String header = accessor.getFirstNativeHeader("Authorization");
        if (header == null) {
            header = accessor.getFirstNativeHeader("authorization");
        }
        String token = JwtTokenProvider.resolveBearerToken(header);
        if (token == null) {
            return; // 익명 연결. 개인 방·챗봇 구독과 전송은 아래 검사에서 거부된다.
        }
        if (!jwtTokenProvider.validateToken(token)) {
            throw new AccessDeniedException("유효하지 않거나 만료된 토큰입니다.");
        }
        Authentication authentication = jwtTokenProvider.getAuthentication(token);
        if (authentication == null) {
            throw new AccessDeniedException("사용자를 알 수 없는 토큰입니다.");
        }
        accessor.setUser(authentication);
    }

    private void authorizeSubscribe(String destination, Principal user) {
        if (destination == null || hasPattern(destination)) {
            throw denied();
        }
        if (destination.equals(CHATBOT_PUBLIC_TOPIC) || destination.equals(CHATBOT_USER_DESTINATION)) {
            requireLogin(user);
            return;
        }
        if (destination.startsWith(ROOM_TOPIC_PREFIX)) {
            requireParticipant(destination.substring(ROOM_TOPIC_PREFIX.length()), user);
            return;
        }
        throw denied();
    }

    private void authorizeSend(String destination, Principal user) {
        // 클라이언트는 /app/ 으로만 보낸다. /topic/...에 직접 보내면 SimpleBroker가 그대로 방송하고, /user/...는 남의 세션으로 간다.
        if (destination == null || !destination.startsWith(APP_PREFIX) || hasPattern(destination)) {
            throw denied();
        }
        if (CHATBOT_SENDS.contains(destination)) {
            requireLogin(user);
            return;
        }
        if (destination.startsWith(ROOM_SEND_PREFIX)) {
            requireParticipant(destination.substring(ROOM_SEND_PREFIX.length()), user);
            return;
        }
        if (destination.startsWith(ROOM_JOIN_PREFIX)) {
            requireParticipant(destination.substring(ROOM_JOIN_PREFIX.length()), user);
            return;
        }
        throw denied();
    }

    // roomId는 목적지의 마지막 한 세그먼트 전체와 정확히 비교한다
    private void requireParticipant(String roomId, Principal user) {
        if (roomId.isEmpty() || roomId.contains("/")) {
            throw denied();
        }
        String userId = requireLogin(user);
        User me = userRepository.findByUserId(userId).orElseThrow(StompAuthChannelInterceptor::denied);
        if (!chatAccessService.isParticipant(roomId, me.getId())) {
            throw denied();
        }
    }

    private static String requireLogin(Principal user) {
        if (user == null || user.getName() == null || user.getName().isBlank()) {
            throw new AccessDeniedException("로그인이 필요합니다.");
        }
        return user.getName();
    }

    // SimpleBroker는 구독 목적지를 패턴으로 해석한다(/topic/public/* 한 번이면 모든 방 도청). 패턴 문자는 막는다.
    private static boolean hasPattern(String destination) {
        return destination.indexOf('*') >= 0 || destination.indexOf('?') >= 0
                || destination.indexOf('{') >= 0 || destination.indexOf('}') >= 0;
    }

    private static AccessDeniedException denied() {
        return new AccessDeniedException("허용되지 않는 목적지입니다.");
    }
}
