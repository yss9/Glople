package proj.travien.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.EnumType;
import jakarta.persistence.Enumerated;
import jakarta.persistence.FetchType;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.JoinColumn;
import jakarta.persistence.ManyToOne;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import org.hibernate.annotations.JdbcTypeCode;
import org.hibernate.type.SqlTypes;

/**
 * 여행 일정의 장소 한 곳. position은 일정 전체에서의 순서(1부터, 겹치지 않음), dayNo는 며칠째(1 ~ 일정의 days)다.
 */
@NoArgsConstructor
@Getter
@Setter
@Entity
@Table(name = "travel_plan_place", indexes = @Index(name = "idx_travel_plan_place_plan", columnList = "plan_id"))
public class TravelPlanPlace {

    public static final int MAX_NAME_LENGTH = 50;

    /**
     * 장소를 넣은 근거. USER: 사용자가 대화에서 직접 말함, TOOL_RESULT: 도구(검색·추천) 결과에서 고름, USER_EDIT: 확인 화면에서 사용자가 고침.
     * 에이전트의 MODEL_SUGGESTED(모델이 지어낸 제안)는 사용자가 고르기 전에는 저장하지 않으므로 받지 않는다.
     */
    public enum Source {
        USER, TOOL_RESULT, USER_EDIT
    }

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY, optional = false)
    @JoinColumn(name = "plan_id", nullable = false)
    private TravelPlan plan;

    // POSITION은 MySQL 함수 이름이라 열 이름을 position_no로 둔다
    @Column(name = "position_no", nullable = false)
    private int position;

    @Column(name = "day_no", nullable = false)
    private int dayNo;

    @Column(nullable = false, length = MAX_NAME_LENGTH)
    private String name;

    // MySQL에서 Hibernate 기본값은 ENUM 열인데, ddl-auto=update는 나중에 값을 더해도 ENUM 정의를 고치지 않는다. 그래서 VARCHAR로 둔다.
    @Enumerated(EnumType.STRING)
    @JdbcTypeCode(SqlTypes.VARCHAR)
    @Column(nullable = false, length = 20)
    private Source source;
}
