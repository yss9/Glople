package proj.travien.dto;

import lombok.Getter;
import lombok.Setter;

@Getter
@Setter
public class KeywordResultDTO {

    // info에 실제 설명문(TourAPI overview, 위키백과 도입부)이 담겼을 때의 infoKind
    public static final String INFO_KIND_DESCRIPTION = "DESCRIPTION";

    // info에 수집한 필드로만 만든 소개(summary)가 담겼을 때의 infoKind
    public static final String INFO_KIND_SUMMARY = "SUMMARY";

    private Long id;
    private String country;
    private String location;
    private String destName;

    // 실제 설명문(info)이 있으면 설명문, 없으면 수집한 필드로 만든 소개(summary)
    private String info;

    // 화면에 info를 보여줄 때 함께 표시할 출처 정보. 기존 여행지는 모두 null이다.
    private String source;
    private String copyrightType;
    private String sourceUrl;
    private String license;
    private String infoKind;

    // 아래는 2026-10 개편에서 더한 값 (기존 화면은 읽지 않는다). 수집하지 않은 기존 여행지는 null이다.
    // 대표 이미지 주소 (TourAPI firstimage). 화면에 보여줄 때도 출처를 함께 표시한다.
    private String imageUrl;

    // ISO 3166-1 alpha-2 국가 코드 (위키데이터 여행지)
    private String countryCode;

    // TourAPI 관광 타입 (12 관광지, 14 문화시설, 15 축제공연행사, 28 레포츠)
    private String contentTypeId;

    // info에 담을 글의 종류. DESCRIPTION: 실제 설명문, SUMMARY: 수집한 필드로만 만든 소개, null: 둘 다 없음
    public static String infoKindOf(String info, String summary) {
        if (info != null) {
            return INFO_KIND_DESCRIPTION;
        }
        if (summary != null) {
            return INFO_KIND_SUMMARY;
        }
        return null;
    }
}
