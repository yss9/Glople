package proj.travien.dto;

import com.fasterxml.jackson.annotation.JsonPropertyOrder;
import lombok.Getter;
import lombok.Setter;

/**
 * 여행지 조회(GET /api/destinations, /api/destinations/lookup) 응답 한 항목.
 *
 * <p>info는 실제 설명문이 있으면 설명문, 없으면 수집한 필드로 만든 소개(summary)이고, infoKind가 어느 쪽인지 알려 준다.
 * TourAPI 설명문(infoKind=DESCRIPTION)은 공공누리 제3유형(변경금지)일 수 있으므로 화면·에이전트는 source·copyrightType·infoKind를 보고
 * 원문을 그대로(의역·요약 없이) 출처와 함께 보여줘야 한다. 수집하지 않은 기존 여행지는 출처 값이 모두 null이다.
 */
@Getter
@Setter
@JsonPropertyOrder({"id", "name", "country", "location", "category", "info", "infoKind", "imageUrl", "lat", "lng",
        "source", "copyrightType", "license", "sourceUrl"})
public class DestinationItemDTO {

    private Long id;
    private String name;
    private String country;

    // 국내 '부산광역시 해운대구', 위키데이터 '일본 교토부', 기존 해외 행은 도시 이름 등
    private String location;

    // 관광지·문화시설·축제·레포츠·음식점·숙박 중 하나. 국내는 관광 타입, 해외는 키워드 근사(DestinationCategory). 모르면 null
    private String category;

    private String info;

    // DESCRIPTION(실제 설명문) | SUMMARY(수집한 필드로 만든 소개) | null(글 없음)
    private String infoKind;

    private String imageUrl;
    private Double lat;
    private Double lng;

    // TOUR_API | WIKIDATA | null(기존 행)
    private String source;

    // TourAPI 저작권 유형 (Type1: 출처표시, Type3: 출처표시 + 변경금지)
    private String copyrightType;

    // 위키데이터 행의 라이선스 (CC BY-SA 4.0 (한국어 위키백과), CC0 (Wikidata))
    private String license;

    // 설명문을 가져온 한국어 위키백과 문서 주소
    private String sourceUrl;
}
