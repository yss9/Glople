package proj.travien.dto;

import com.fasterxml.jackson.annotation.JsonPropertyOrder;
import lombok.Getter;
import lombok.Setter;
import proj.travien.domain.TravelPlan;

import java.time.LocalDateTime;

// 내 여행 일정 목록 한 줄 (GET /api/travel-plans). 장소는 개수만 싣는다.
@Getter
@Setter
@JsonPropertyOrder({"id", "title", "region", "days", "placeCount", "createdAt"})
public class TravelPlanSummaryDTO {

    private Long id;
    private String title;
    private String region;
    private int days;
    private int placeCount;
    private LocalDateTime createdAt;

    public static TravelPlanSummaryDTO from(TravelPlan plan) {
        TravelPlanSummaryDTO dto = new TravelPlanSummaryDTO();
        dto.setId(plan.getId());
        dto.setTitle(plan.getTitle());
        dto.setRegion(plan.getRegion());
        dto.setDays(plan.getDays());
        dto.setPlaceCount(plan.getPlaces().size());
        dto.setCreatedAt(plan.getCreatedAt());
        return dto;
    }
}
