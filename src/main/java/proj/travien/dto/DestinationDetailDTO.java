package proj.travien.dto;

import com.fasterxml.jackson.annotation.JsonPropertyOrder;
import lombok.Getter;
import lombok.Setter;

/**
 * 여행지 상세(GET /api/destinations/{id}). 목록 항목과 같은 값에 주소와 소개를 더한다. 값이 없으면 null이다.
 */
@Getter
@Setter
@JsonPropertyOrder({"id", "name", "country", "location", "category", "info", "infoKind", "imageUrl", "lat", "lng",
        "source", "copyrightType", "license", "sourceUrl", "address", "regionCode", "sigunguCode", "summary"})
public class DestinationDetailDTO extends DestinationItemDTO {

    // TourAPI addr1 (예: 부산광역시 해운대구 우동 ...)
    private String address;

    // 법정동 시도·시군구 코드 (TourAPI lDongRegnCd, lDongSignguCd)
    private String regionCode;
    private String sigunguCode;

    // 수집한 필드(지역·관광 타입·분류, 위키데이터 값)로만 만든 짧은 소개. info가 설명문(DESCRIPTION)이어도 따로 준다.
    // 원문 설명문과 달리 Glople가 만든 틀 문장이다(에이전트 판정표의 FREE_FACTS).
    private String summary;
}
