package proj.travien.dto;

import lombok.Getter;
import lombok.Setter;

import java.util.List;

/**
 * 여행 일정 저장 요청 (POST /api/travel-plans). 에이전트(agent/)가 이 칸 이름 그대로 보낸다.
 *
 * <pre>{"clientRequestId":"call_1","title":"부산 1박 2일","region":"부산","days":2,
 *  "places":[{"name":"해운대해수욕장","dayNo":1,"position":1,"source":"TOOL_RESULT"}]}</pre>
 *
 * 주인은 토큰 사용자로 정하므로 userId 칸은 없다(보내도 무시). 모르는 칸도 무시한다.
 * 값 검사는 TravelPlanService가 한다(어긋나면 400과 칸별 problems). 숫자 칸은 Jackson 기본 규칙대로 "3" 같은 문자열 숫자도 받는다.
 */
@Getter
@Setter
public class TravelPlanRequest {

    // 1~80자. 같은 사용자가 같은 값으로 다시 보내면 처음 저장한 일정을 200으로 돌려준다.
    private String clientRequestId;

    // 1~100자
    private String title;

    // 1~50자 (예: 부산, 제주, 일본 교토). 여행지 조회의 region과 같은 말을 쓰지만 서버가 해석하지는 않는다.
    private String region;

    // 1~30
    private Integer days;

    // 1~30곳
    private List<Place> places;

    @Getter
    @Setter
    public static class Place {

        // 1~50자
        private String name;

        // 1~days
        private Integer dayNo;

        // 1 이상, 일정 안에서 겹치지 않음
        private Integer position;

        // USER | TOOL_RESULT | USER_EDIT
        private String source;
    }
}
