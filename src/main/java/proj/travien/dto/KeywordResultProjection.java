package proj.travien.dto;

public interface KeywordResultProjection {

    Long getId();

    String getCountry();

    String getLocation();

    String getDestName();

    String getInfo();

    // 아래는 화면에 출처를 표시하기 위한 값. 외부 데이터로 수집하지 않은 기존 여행지는 모두 null이다.

    // 수집 출처 (TOUR_API: 한국관광공사 TourAPI, WIKIDATA: 위키데이터·한국어 위키백과)
    String getSource();

    // TourAPI 저작권 유형 (Type1: 공공누리 출처표시, Type3: 공공누리 출처표시 + 변경금지)
    String getCopyrightType();

    // 설명문을 가져온 한국어 위키백과 문서 주소
    String getSourceUrl();

    // 위키데이터 여행지의 라이선스 (CC BY-SA 4.0 (한국어 위키백과), CC0 (Wikidata))
    String getLicense();

    // info에 담긴 글의 종류. DESCRIPTION: 실제 설명문, SUMMARY: 수집한 필드로만 만든 소개, null: 둘 다 없음
    String getInfoKind();
}
