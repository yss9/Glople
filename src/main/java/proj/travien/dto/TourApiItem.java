package proj.travien.dto;

/**
 * TourAPI areaBasedList2 응답의 item 한 건.
 *
 * <p>areacode·sigungucode·cat1~3은 TourAPI에서 삭제 예정이라 법정동 코드(lDongRegnCd, lDongSignguCd)와
 * 분류체계 코드(lclsSystm3)를 쓴다.
 */
public record TourApiItem(
        String contentId,
        String contentTypeId,
        String title,
        String addr1,
        String regionCode,
        String sigunguCode,
        String category,
        Double latitude,
        Double longitude,
        String firstImage,
        String tel,
        String copyrightType,
        String modifiedTime
) {
}
