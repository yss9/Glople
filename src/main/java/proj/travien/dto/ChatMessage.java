package proj.travien.dto;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.*;

import java.util.Date;

@Getter
@Setter
public class ChatMessage {
    private MessageType type;
    private String content;
    private String sender;
    // 예전 챗봇 클라이언트가 본문에 'Bearer <JWT>'를 실어 보낸다. 받기만 하고 어떤 응답·방송에도 싣지 않는다.
    @JsonProperty(access = JsonProperty.Access.WRITE_ONLY)
    private String token;

    public enum MessageType {
        CHAT,
        JOIN,
        LEAVE
    }

    // Getters and Setters
}
