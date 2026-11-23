package proj.travien.dto;

/**
 * 키워드 추천 후보 한 행. 순위와 지역 개수 상한에 필요한 열만 읽는다. (설명문 같은 긴 글은 고른 행만 따로 읽는다)
 * 값은 DestinationInfoRepository.findKeywordCandidates가 채운다.
 */
public interface KeywordCandidate {

    Long getId();

    String getCountry();

    String getLocation();

    String getSource();

    // 고른 키워드 중 이 여행지에 켜진 키워드 수
    Long getScore();

    // 실제 설명문(info)이 있으면 1
    Long getHasInfo();

    // 대표 이미지(image_url)가 있으면 1
    Long getHasImage();
}
