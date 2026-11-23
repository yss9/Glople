package proj.travien.dto;

import lombok.Getter;
import lombok.Setter;

import java.util.List;

@Getter
@Setter
public class KeywordRequest {

    private List<String> keywords;

    // 아래는 모두 선택 값이다. 보내지 않으면 예전과 같은 요청(국내·해외 전체 21곳)이 된다. 규칙은 KeywordRecommendService 참고.

    // all(기본) | domestic(국내) | overseas(해외)
    private String scope;

    // 돌려받을 여행지 수 1~50 (기본 21)
    private Integer limit;

    // 동점 정렬 seed. 기본은 오늘 날짜(Asia/Seoul) yyyyMMdd라 같은 날에는 같은 순서다.
    private Long seed;
}
