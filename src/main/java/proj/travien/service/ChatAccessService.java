package proj.travien.service;

import org.springframework.stereotype.Service;
import proj.travien.domain.ChatRoom;
import proj.travien.repository.ChatRoomRepository;
import proj.travien.repository.UserChatRoomRepository;

import java.util.Objects;
import java.util.Optional;

/**
 * 1:1 채팅방 참가자 판정. roomId('닉네임A_닉네임B')는 두 닉네임만 알면 추측할 수 있으므로
 * 문자열을 파싱하지 않고 UserChatRoom(User.id, ChatRoom.id) 행으로만 판정한다. (닉네임 변경·'_'가 든 닉네임에도 안전)
 * REST(ChatController)와 STOMP(StompAuthChannelInterceptor)가 같이 쓴다.
 */
@Service
public class ChatAccessService {

    private final ChatRoomRepository chatRoomRepository;

    private final UserChatRoomRepository userChatRoomRepository;

    public ChatAccessService(ChatRoomRepository chatRoomRepository, UserChatRoomRepository userChatRoomRepository) {
        this.chatRoomRepository = chatRoomRepository;
        this.userChatRoomRepository = userChatRoomRepository;
    }

    // userId(User.id)가 roomId 방의 참가자인지. 방이 없어도 false
    public boolean isParticipant(String roomId, Long userId) {
        if (roomId == null || userId == null) {
            return false;
        }
        Optional<ChatRoom> room = chatRoomRepository.findByRoomId(roomId);
        if (room.isEmpty()) {
            return false;
        }
        Long chatRoomId = room.get().getId();
        return userChatRoomRepository.findByUserId(userId).stream()
                .anyMatch(userChatRoom -> Objects.equals(userChatRoom.getChatRoomId(), chatRoomId));
    }
}
