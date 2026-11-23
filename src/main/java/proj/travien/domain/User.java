package proj.travien.domain;

import com.fasterxml.jackson.annotation.JsonIgnore;
import com.fasterxml.jackson.annotation.JsonIncludeProperties;
import jakarta.persistence.*;
import lombok.Getter;
import lombok.Setter;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Set;

// User 엔티티는 게시물 좋아요(Post.likes → Like.user), 댓글 작성자, 리뷰 대상 대행자처럼
// 다른 사용자의 콘텐츠 응답 안에 그대로 실린다. 그래서 JSON으로는 공개 프로필만 내보낸다.
// 본인 정보(/api/users/my-info)·관리자 목록은 UserDTO로 따로 내보내므로 여기에 필드를 더하지 않는다.
@JsonIncludeProperties({"id", "nickname", "profilePicture"})
@Entity
@Getter
@Setter
public class User {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String userId;

    // 비밀번호 해시는 어떤 JSON에도 싣지 않고 JSON으로 받지도 않는다. (회원가입·로그인은 UserDTO로 받는다)
    @JsonIgnore
    private String password;
    private String name;
    private String dateOfBirth;
    private String gender;
    private String mbti;
    private String profilePicture;
    private String nickname;
    private int points;

    private boolean isAgent;
    private String verificationFile;
    private String agentCountry;

    @Lob
    private String introduction;

    @ElementCollection
    private List<String> hashtags;

    @ElementCollection
    private List<String> specIntroduction;
    private double averageReviewRating;

    private String statusMessage = "아직 설정되지 않음";

    // 단일 역할 관리 (Enum 타입)
    @Enumerated(EnumType.STRING)
    private Role role;

    //나은이가 추가
    // 기본 생성자 추가
    public User() {
    }

    // ID를 사용하는 생성자 (선택 사항)
    public User(Long id) {
        this.id = id;
    }

    // 예약대행자 승인 시점을 기록할 필드
    @Getter
    @Setter
    private LocalDateTime agentApprovedDate;
}


