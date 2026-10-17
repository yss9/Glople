package proj.travien.dto;

/**
 * 위키데이터에서 받은 수집 대상 국가 한 곳.
 *
 * @param qid     국가 QID (예: Q142)
 * @param isoCode ISO 3166-1 alpha-2 코드 (P297, 예: FR)
 * @param name    한국어 이름. 한국어 이름이 없으면 영어 이름
 */
public record WikiCountry(
        String qid,
        String isoCode,
        String name
) {
}
