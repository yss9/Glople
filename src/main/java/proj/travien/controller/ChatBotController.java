package proj.travien.controller;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.messaging.handler.annotation.MessageMapping;
import org.springframework.messaging.simp.annotation.SendToUser;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Controller;
import proj.travien.domain.User;
import proj.travien.dto.ChatMessage;
import proj.travien.repository.UserRepository;
import proj.travien.service.ChatBotService;
import reactor.core.publisher.Mono;

import java.security.Principal;

// AI 챗봇. 답은 보낸 사용자에게만 보낸다(@SendToUser → 클라이언트는 /user/topic/chatbot 구독).
// 예전에는 /topic/public으로 방송해 접속자 모두가 서로의 질문·답과 JOIN 메시지에 실린 JWT를 받았다.
// 로그인(STOMP CONNECT의 토큰)은 StompAuthChannelInterceptor가 확인한다.
@Controller
public class ChatBotController {

    // 챗봇 답이 가는 사용자 목적지. SimpleBroker 접두사(/topic) 아래여야 브로커가 전달한다.
    static final String CHATBOT_USER_DESTINATION = "/topic/chatbot";

    @Autowired
    private ChatBotService chatBotService;

    @Autowired
    private UserRepository userRepository;

    @MessageMapping("/chat.sendMessage")
    @SendToUser(CHATBOT_USER_DESTINATION)
    public Mono<ChatMessage> sendMessage(ChatMessage chatMessage, Principal principal) {
        requireLogin(principal);
        return chatBotService.getChatResponse(chatMessage.getContent())
                .map(response -> {
                    ChatMessage botMessage = new ChatMessage();
                    botMessage.setSender("ChatBot");
                    botMessage.setContent(response);
                    return botMessage;
                });
    }

    @MessageMapping("/chat.addUser")
    @SendToUser(CHATBOT_USER_DESTINATION)
    public ChatMessage addUser(ChatMessage chatMessage, Principal principal) {
        // 보낸 사람은 CONNECT 때 인증된 사용자의 닉네임(DB). 본문의 token·sender는 읽지 않는다.
        User me = userRepository.findByUserId(requireLogin(principal))
                .orElseThrow(() -> new AccessDeniedException("로그인 사용자를 찾을 수 없습니다."));
        ChatMessage joinMessage = new ChatMessage();
        joinMessage.setType(ChatMessage.MessageType.JOIN);
        joinMessage.setSender(me.getNickname());
        // 받은 DTO를 그대로 돌려주면 본문의 token(Bearer JWT)이 함께 나간다. 새 메시지에는 token을 싣지 않는다.
        return joinMessage;
    }

    private static String requireLogin(Principal principal) {
        if (principal == null || principal.getName() == null || principal.getName().isBlank()) {
            throw new AccessDeniedException("로그인이 필요합니다.");
        }
        return principal.getName();
    }
}
