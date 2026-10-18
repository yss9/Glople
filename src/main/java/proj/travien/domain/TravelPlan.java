package proj.travien.domain;

import jakarta.persistence.CascadeType;
import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.OneToMany;
import jakarta.persistence.OrderBy;
import jakarta.persistence.Table;
import jakarta.persistence.UniqueConstraint;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

/**
 * 사용자가 저장한 여행 일정 (POST /api/travel-plans). 주인(user)은 언제나 토큰 사용자이고 요청 본문으로 정하지 않는다.
 *
 * <p>(user_id, client_request_id)가 UNIQUE라서 같은 사용자가 같은 clientRequestId로 다시 저장하면 새 행을 만들지 않고 처음 일정을 돌려준다.
 * 에이전트는 create_travel_plan의 tool_call_id를 clientRequestId로 보낸다(agent/docs/design.md 9.2 중복 저장 방지의 세 번째 겹).
 * 엔티티는 JSON으로 내보내지 않는다(TravelPlanDTO로 바꿔서 응답).
 */
@NoArgsConstructor
@Getter
@Setter
@Entity
@Table(name = "travel_plan",
        uniqueConstraints = @UniqueConstraint(name = "uk_travel_plan_user_client_request",
                columnNames = {"user_id", "client_request_id"}))
public class TravelPlan {

    public static final int MAX_CLIENT_REQUEST_ID_LENGTH = 80;
    public static final int MAX_TITLE_LENGTH = 100;
    public static final int MAX_REGION_LENGTH = 50;
    public static final int MIN_DAYS = 1;
    public static final int MAX_DAYS = 30;
    public static final int MAX_PLACES = 30;

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    // 같은 요청을 다시 보냈는지 알아보는 키 (사용자마다 유일)
    @Column(name = "client_request_id", nullable = false, length = MAX_CLIENT_REQUEST_ID_LENGTH)
    private String clientRequestId;

    @Column(nullable = false, length = MAX_TITLE_LENGTH)
    private String title;

    @Column(nullable = false, length = MAX_REGION_LENGTH)
    private String region;

    @Column(nullable = false)
    private int days;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @OneToMany(mappedBy = "plan", cascade = CascadeType.ALL, orphanRemoval = true)
    @OrderBy("position ASC, id ASC")
    private List<TravelPlanPlace> places = new ArrayList<>();

    public void addPlace(TravelPlanPlace place) {
        place.setPlan(this);
        places.add(place);
    }
}
