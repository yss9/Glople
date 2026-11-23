package proj.travien.dto;

import java.util.Set;

/**
 * 수집 후보 여행지. 국가별 상위 N곳을 고르기 전에 유형(P31) 또는 세계유산 조건으로 모은 값이다.
 *
 * @param qid          여행지 QID
 * @param sitelinks    위키데이터 사이트링크 수 (여러 언어 위키백과에 문서가 있을수록 큼, 인기 순 정렬에 사용)
 * @param countryCodes 국가(P17)의 ISO 3166-1 alpha-2 코드. 여러 나라에 걸친 여행지는 여러 개
 */
public record WikiCandidate(
        String qid,
        int sitelinks,
        Set<String> countryCodes
) {
}
