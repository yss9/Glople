package proj.travien.dto;

import com.fasterxml.jackson.annotation.JsonPropertyOrder;
import lombok.Getter;
import lombok.Setter;
import proj.travien.domain.TravelPlan;
import proj.travien.domain.TravelPlanPlace;

import java.time.LocalDateTime;
import java.util.Comparator;
import java.util.List;

/**
 * 여행 일정 한 건 (POST /api/travel-plans 응답, GET /api/travel-plans/{id}). 장소는 position 순서다.
 * createdAt은 서버 현지 시각(초 단위, 시간대 표시 없음) 예: "2026-10-18T14:03:00".
 */
@Getter
@Setter
@JsonPropertyOrder({"id", "clientRequestId", "title", "region", "days", "places", "createdAt"})
public class TravelPlanDTO {

    private Long id;
    private String clientRequestId;
    private String title;
    private String region;
    private int days;
    private List<Place> places;
    private LocalDateTime createdAt;

    @Getter
    @Setter
    @JsonPropertyOrder({"name", "dayNo", "position", "source"})
    public static class Place {
        private String name;
        private int dayNo;
        private int position;
        private String source;
    }

    public static TravelPlanDTO from(TravelPlan plan) {
        TravelPlanDTO dto = new TravelPlanDTO();
        dto.setId(plan.getId());
        dto.setClientRequestId(plan.getClientRequestId());
        dto.setTitle(plan.getTitle());
        dto.setRegion(plan.getRegion());
        dto.setDays(plan.getDays());
        dto.setCreatedAt(plan.getCreatedAt());
        dto.setPlaces(plan.getPlaces().stream()
                .sorted(Comparator.comparingInt(TravelPlanPlace::getPosition))
                .map(TravelPlanDTO::place)
                .toList());
        return dto;
    }

    private static Place place(TravelPlanPlace entity) {
        Place place = new Place();
        place.setName(entity.getName());
        place.setDayNo(entity.getDayNo());
        place.setPosition(entity.getPosition());
        place.setSource(entity.getSource() == null ? null : entity.getSource().name());
        return place;
    }
}
