package proj.travien.controller;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.messaging.handler.annotation.DestinationVariable;
import org.springframework.messaging.handler.annotation.MessageMapping;
import org.springframework.messaging.handler.annotation.SendTo;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import proj.travien.domain.ChatMessage;
import proj.travien.domain.ChatRoom;
import proj.travien.domain.User;
import proj.travien.domain.UserChatRoom;
import proj.travien.jwt.CurrentUser;
import proj.travien.repository.ChatMessageRepository;
import proj.travien.repository.ChatRoomRepository;
import proj.travien.repository.UserChatRoomRepository;
import proj.travien.repository.UserRepository;
import proj.travien.service.ChatAccessService;
import proj.travien.service.SpeechToTextService;
import proj.travien.service.UploadPaths;

import java.io.IOException;
import java.nio.file.Paths;
import java.security.Principal;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/chat")
public class ChatController {

    @Value("${upload.location}")  // application.properties에서 경로 가져오기
    private String uploadDir;

    @Autowired
    private ChatMessageRepository chatMessageRepository;

    @Autowired
    private ChatRoomRepository chatRoomRepository;

    @Autowired
    private UserChatRoomRepository userChatRoomRepository;

    @Autowired
    private UserRepository userRepository;

    @Autowired
    private SpeechToTextService speechToTextService;

    @Autowired
    private ChatAccessService chatAccessService;

    @Autowired
    private CurrentUser currentUser;

    // 클라이언트가 보낼 수 있는 메시지 종류와 길이 (JOIN·LEAVE는 서버만 만든다)
    private static final Set<ChatMessage.MessageType> CLIENT_MESSAGE_TYPES =
            Set.of(ChatMessage.MessageType.CHAT, ChatMessage.MessageType.FILE);
    private static final int MAX_CONTENT_LENGTH = 10000;

    // STOMP: 참가자 검사는 StompAuthChannelInterceptor가 SEND 단계에서 먼저 한다. 여기서도 한 번 더 확인한다.
    // 보낸 사람은 클라이언트 값(sender)이 아니라 연결한 토큰 사용자의 닉네임(DB)으로 덮어쓴다.
    @MessageMapping("/chat.sendMessage/{roomId}")
    @SendTo("/topic/public/{roomId}")
    public ChatMessage sendMessage(@DestinationVariable String roomId, ChatMessage chatMessage, Principal principal) {
        User me = requireParticipant(roomId, principal);
        ChatMessage.MessageType type = chatMessage.getType() == null ? ChatMessage.MessageType.CHAT : chatMessage.getType();
        if (!CLIENT_MESSAGE_TYPES.contains(type)) {
            throw new IllegalArgumentException("허용되지 않는 메시지 종류입니다.");
        }
        if (chatMessage.getContent() != null && chatMessage.getContent().length() > MAX_CONTENT_LENGTH) {
            throw new IllegalArgumentException("메시지가 너무 깁니다.");
        }
        chatMessage.setId(null); // id가 오면 save()가 merge로 다른 방의 기존 메시지를 덮어쓴다
        chatMessage.setType(type);
        chatMessage.setSender(me.getNickname());
        chatMessage.setRoomId(roomId);
        chatMessageRepository.save(chatMessage);
        return chatMessage;
    }

    // 이미 참가자인 사용자의 입장 알림만 보낸다. 참가자 등록은 createRoom에서만 한다. (프론트는 지금 쓰지 않는다)
    @MessageMapping("/chat.addUser/{roomId}")
    @SendTo("/topic/public/{roomId}")
    public ChatMessage addUser(@DestinationVariable String roomId, ChatMessage chatMessage, Principal principal) {
        User me = requireParticipant(roomId, principal);
        chatMessage.setId(null);
        chatMessage.setContent(null);
        chatMessage.setSender(me.getNickname());
        chatMessage.setType(ChatMessage.MessageType.JOIN);
        chatMessage.setRoomId(roomId);
        chatMessageRepository.save(chatMessage);
        return chatMessage;
    }

    // 채팅 기록: 그 방의 참가자만 (방이 없어도 403 — 방 이름으로 두 사람의 대화 여부를 알아내지 못하게)
    @GetMapping(value = "/history/{roomId}", produces = "application/json")
    public ResponseEntity<List<ChatMessage>> getChatHistory(@PathVariable String roomId) {
        requireParticipant(roomId, currentUser.requireUser());
        List<ChatMessage> chatMessages = chatMessageRepository.findByRoomId(roomId);
        if (chatMessages.isEmpty()) {
            return new ResponseEntity<>(HttpStatus.NO_CONTENT);
        }
        return new ResponseEntity<>(chatMessages, HttpStatus.OK);
    }

    // 1:1 채팅방 만들기: 두 닉네임 중 하나는 본인(DB 닉네임)이어야 한다
    @PostMapping(value = "/createRoom", produces = "application/json")
    public ResponseEntity<String> createChatRoom(@RequestBody List<String> nicknames) {
        User me = currentUser.requireUser();
        if (nicknames == null || nicknames.size() != 2 || nicknames.contains(null)) {
            return new ResponseEntity<>("Invalid number of users", HttpStatus.BAD_REQUEST);
        }
        if (!nicknames.contains(me.getNickname())) {
            throw new AccessDeniedException("본인이 참가하는 채팅방만 만들 수 있습니다.");
        }
        if (nicknames.get(0).equals(nicknames.get(1))) {
            return new ResponseEntity<>("Cannot create a chat room with yourself", HttpStatus.BAD_REQUEST);
        }

        nicknames = new ArrayList<>(nicknames);
        nicknames.sort(String::compareTo); // 두 사용자 닉네임을 정렬
        String roomId = nicknames.get(0) + "_" + nicknames.get(1); // 고유한 채팅방 번호 생성

        Optional<ChatRoom> existingChatRoom = chatRoomRepository.findByRoomId(roomId);
        if (existingChatRoom.isPresent()) {
            return new ResponseEntity<>(roomId, HttpStatus.OK);
        }

        // 닉네임을 ID로 변환 (방을 저장하기 전에 확인해 참가자 없는 방이 남지 않게 한다)
        Optional<User> found1 = userRepository.findByNickname(nicknames.get(0));
        Optional<User> found2 = userRepository.findByNickname(nicknames.get(1));
        if (found1.isEmpty() || found2.isEmpty()) {
            return new ResponseEntity<>("User not found", HttpStatus.NOT_FOUND);
        }
        User user1 = found1.get();
        User user2 = found2.get();

        ChatRoom chatRoom = new ChatRoom();
        chatRoom.setRoomId(roomId);
        chatRoomRepository.save(chatRoom);

        userChatRoomRepository.save(new UserChatRoom(user1.getId(), chatRoom.getId()));
        userChatRoomRepository.save(new UserChatRoom(user2.getId(), chatRoom.getId()));

        return new ResponseEntity<>(roomId, HttpStatus.CREATED);
    }

    @GetMapping(value = "/userRooms", produces = "application/json")
    public ResponseEntity<List<String>> getUserChatRooms() {
        // 본인 목록만. 토큰이 없으면 401 (예전처럼 토큰을 직접 파싱하다 500이 나지 않게 CurrentUser를 쓴다)
        User me = currentUser.requireUser();
        Long id = me.getId();
        String myNickname = me.getNickname();

        List<UserChatRoom> userChatRooms = userChatRoomRepository.findByUserId(id);
        List<String> otherUserNicknames = userChatRooms.stream()
                .map(userChatRoom -> chatRoomRepository.findById(userChatRoom.getChatRoomId()).get().getRoomId())
                .map(roomId -> {
                    // 방 ID에서 자신의 닉네임을 제외하고 상대방 닉네임을 추출
                    String[] nicknames = roomId.split("_");
                    return (nicknames[0].equals(myNickname)) ? nicknames[1] : nicknames[0];
                })
                .collect(Collectors.toList());

        return new ResponseEntity<>(otherUserNicknames, HttpStatus.OK);
    }


    @PostMapping("/uploadFile/{roomId}")
    public ResponseEntity<String> uploadFile(@PathVariable String roomId,
                                             @RequestParam("file") MultipartFile file,
                                             @RequestParam(value = "sender", required = false) String sender) {
        // 그 방의 참가자만. sender는 호환을 위해 받되 본인 닉네임이 아니면 403
        User me = currentUser.requireUser();
        CurrentUser.requireSameNickname(me, sender);
        requireParticipant(roomId, me);
        if (file.isEmpty()) {
            return new ResponseEntity<>("파일이 없습니다.", HttpStatus.BAD_REQUEST);
        }

        try {
            // 클라이언트 파일명 대신 서버에서 만든 이름(UUID.확장자)으로 저장
            String fileName = UploadPaths.store(file, Paths.get(uploadDir), UploadPaths.DOCUMENT_EXTENSIONS);

            String fileUrl = "/image/" + fileName; // 업로드된 파일 경로 생성

            return new ResponseEntity<>(fileUrl, HttpStatus.OK); // 파일 경로 반환
        } catch (UploadPaths.InvalidUploadException e) {
            return new ResponseEntity<>("허용되지 않는 파일입니다.", HttpStatus.BAD_REQUEST);
        } catch (IOException e) {
            return new ResponseEntity<>("파일 업로드 실패", HttpStatus.INTERNAL_SERVER_ERROR);
        }
    }

    @PostMapping("/uploadVoiceMessage/{roomId}")
    public ResponseEntity<String> uploadVoiceMessage(@PathVariable String roomId, @RequestParam("file") MultipartFile file) {
        requireParticipant(roomId, currentUser.requireUser());
        // 음성 파일을 텍스트로 변환
        String transcript;
        try {
            byte[] audioBytes = file.getBytes();
            transcript = speechToTextService.convertSpeechToText(audioBytes, "en-US"); // 언어 코드를 필요에 따라 변경
        } catch (IOException e) {
            return new ResponseEntity<>("음성 파일 처리 오류", HttpStatus.INTERNAL_SERVER_ERROR);
        }
        System.out.println(transcript);
        // 변환된 텍스트를 WebSocket을 통해 전송하기 위해 텍스트만 반환
        return new ResponseEntity<>(transcript, HttpStatus.OK);
    }

    // REST: 참가자가 아니면 403
    private void requireParticipant(String roomId, User me) {
        if (!chatAccessService.isParticipant(roomId, me.getId())) {
            throw new AccessDeniedException("채팅방 참가자만 할 수 있습니다.");
        }
    }

    // STOMP: 연결한 사용자(Principal 이름 = 로그인 아이디)가 참가자인지 확인하고 사용자 엔티티를 돌려준다
    private User requireParticipant(String roomId, Principal principal) {
        if (principal == null || principal.getName() == null) {
            throw new AccessDeniedException("로그인이 필요합니다.");
        }
        User me = userRepository.findByUserId(principal.getName())
                .orElseThrow(() -> new AccessDeniedException("로그인 사용자를 찾을 수 없습니다."));
        requireParticipant(roomId, me);
        return me;
    }
}
