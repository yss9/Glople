package proj.travien.service;

import java.util.Arrays;
import java.util.List;
import java.util.Optional;

/**
 * 여행지 조회(GET /api/destinations)의 category 값. 에이전트 도구 search_destinations의 enum과 같은 6개다.
 *
 * <ul>
 *   <li>국내(TourAPI): 관광 타입 content_type_id로 고른다. 지금 수집하는 타입은 12·14·15·28이라 음식점(39)·숙박(32)은 결과가 없다.</li>
 *   <li>해외(위키데이터·기존 행): 관광 타입이 없으므로 키워드 마스크로 근사한다. 관광지는 해외 전체, 숙박은 근사할 키워드가 없어 해외 결과가 없다.</li>
 * </ul>
 * 응답의 category 칸도 같은 규칙이다. 해외 행은 {@link #approximateOverseas}의 우선순위(축제 → 문화시설 → 레포츠 → 음식점 → 관광지)로 하나를 붙인다.
 */
public enum DestinationCategory {

    ATTRACTION("관광지", "12", List.of(), true),
    CULTURE("문화시설", "14", List.of("박물관", "미술관", "공연", "예술"), false),
    FESTIVAL("축제", "15", List.of("축제"), false),
    LEISURE("레포츠", "28", List.of(
            "스포츠", "레크리에이션", "야외 활동", "등산", "트레킹", "하이킹", "하이킹 코스", "캠핑",
            "서핑", "다이빙", "스노클링", "카누", "열기구"), false),
    RESTAURANT("음식점", "39", List.of("음식"), false),
    LODGING("숙박", "32", List.of(), false);

    // 해외 행에 category를 붙일 때 먼저 보는 순서
    private static final List<DestinationCategory> OVERSEAS_PRIORITY = List.of(FESTIVAL, CULTURE, LEISURE, RESTAURANT);

    private final String label;
    private final String contentTypeId;
    private final KeywordMask.Mask overseasMask;
    private final boolean overseasAll;

    DestinationCategory(String label, String contentTypeId, List<String> overseasKeywords, boolean overseasAll) {
        this.label = label;
        this.contentTypeId = contentTypeId;
        this.overseasMask = KeywordMask.fromKeywords(overseasKeywords);
        this.overseasAll = overseasAll;
    }

    public String label() {
        return label;
    }

    public String contentTypeId() {
        return contentTypeId;
    }

    public KeywordMask.Mask overseasMask() {
        return overseasMask;
    }

    // 해외 행 전체를 이 분류로 볼지 (관광지)
    public boolean overseasAll() {
        return overseasAll;
    }

    public static Optional<DestinationCategory> fromLabel(String label) {
        if (label == null) {
            return Optional.empty();
        }
        String trimmed = label.trim();
        return Arrays.stream(values()).filter(category -> category.label.equals(trimmed)).findFirst();
    }

    public static Optional<DestinationCategory> fromContentTypeId(String contentTypeId) {
        if (contentTypeId == null) {
            return Optional.empty();
        }
        String trimmed = contentTypeId.trim();
        return Arrays.stream(values()).filter(category -> category.contentTypeId.equals(trimmed)).findFirst();
    }

    // 해외 행의 분류 근사. 어느 키워드도 켜져 있지 않으면 관광지
    public static DestinationCategory approximateOverseas(long keywordMaskLow, long keywordMaskHigh) {
        for (DestinationCategory category : OVERSEAS_PRIORITY) {
            KeywordMask.Mask mask = category.overseasMask;
            if ((keywordMaskLow & mask.low()) != 0 || (keywordMaskHigh & mask.high()) != 0) {
                return category;
            }
        }
        return ATTRACTION;
    }

    public static List<String> labels() {
        return Arrays.stream(values()).map(DestinationCategory::label).toList();
    }
}
