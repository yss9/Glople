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
    private String info;

    // 화면에 info를 보여줄 때 함께 표시할 출처 정보. 기존 여행지는 모두 null이다.
    private String source;
    private String copyrightType;
    private String sourceUrl;
    private String license;
    private String infoKind;

}
