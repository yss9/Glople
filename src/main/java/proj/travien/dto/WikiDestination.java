package proj.travien.dto;

import java.util.List;
import java.util.Set;

/**
 * 위키데이터 SPARQL로 받은 해외 여행지 한 곳의 상세 값.
 *
 * @param qid           여행지 QID
 * @param name          한국어 이름 (한국어 레이블 → 한국어 위키백과 문서 제목 → 영어 레이블 순으로 대체)
 * @param articleTitle  한국어 위키백과 문서 제목 (사이트링크 주소에서 꺼낸 값, 밑줄은 공백으로)
 * @param articleUrl    한국어 위키백과 문서 주소 (위키데이터 사이트링크 그대로)
 * @param latitude      위도 (P625)
 * @param longitude     경도 (P625)
 * @param instanceOf    모든 P31(instance of) 값의 QID (QID 번호 순)
 * @param worldHeritage 해제되지 않은 유네스코 세계유산 지정(P1435 = Q9259)이 있는지
 * @param admins        소재 행정구역(P131) 중 한국어 이름이 있는 것 (QID 번호 순)
 */
public record WikiDestination(
        String qid,
        String name,
        String articleTitle,
        String articleUrl,
        Double latitude,
        Double longitude,
        List<String> instanceOf,
        boolean worldHeritage,
        List<Admin> admins
) {

    /**
     * 소재 행정구역 하나.
     *
     * @param label        한국어 이름
     * @param countryCodes 그 행정구역이 속한 국가(P17)의 ISO 3166-1 alpha-2 코드. 위키데이터에 없으면 비어 있음
     */
    public record Admin(String label, Set<String> countryCodes) {
    }

    // 소재 행정구역의 한국어 이름 (QID 번호 순)
    public List<String> adminLabels() {
        return admins.stream().map(Admin::label).toList();
    }
}
