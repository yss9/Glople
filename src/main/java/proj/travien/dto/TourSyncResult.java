package proj.travien.dto;

/**
 * 여행지 동기화(국내 TourAPI, 해외 위키데이터·위키백과) 한 번의 결과.
 *
 * @param processed     처리 대상 건수 (목록 동기화: 받은 item 수, 설명 동기화: 조회한 여행지 수, 소개 재계산: 읽은 여행지 수)
 * @param inserted      새로 저장한 여행지 수
 * @param updated       내용이 바뀌어 갱신한 여행지 수 (소개 재계산: 소개나 키워드 마스크가 바뀐 여행지 수)
 * @param failed        개별 처리에 실패한 건수 (위키데이터 목록 동기화: 실패한 요청 수)
 * @param stoppedByQuota TourAPI 일일 호출 한도 또는 위키미디어 요청 한도(HTTP 429)에 걸려 중간에 멈췄는지 여부
 */
public record TourSyncResult(
        int processed,
        int inserted,
        int updated,
        int failed,
        boolean stoppedByQuota
) {
}
