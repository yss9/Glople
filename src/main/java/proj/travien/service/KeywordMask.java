package proj.travien.service;

import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public final class KeywordMask {

    private static final int BITS_PER_SEGMENT = 63;

    private static final List<String> KEYWORDS = List.of(
            "해안", "수영", "조각", "사원", "문화", "역사", "건축", "산", "등산", "호수",
            "폭포", "사막", "박물관", "미술관", "자연", "경치", "도시", "해변", "섬",
            "온천", "다이빙", "트레킹", "동물", "사파리", "야경", "시장", "열기구",
            "불교", "기차", "성당", "성", "원숭이", "온천욕", "탐험", "보트", "운하",
            "와인", "카누", "바다", "스노클링", "다문화", "협곡", "지열", "동굴",
            "사찰", "모스크", "탑", "성벽", "화산", "빙하", "설산", "캠핑", "하이킹",
            "스포츠", "레크리에이션", "축제", "음식", "전통", "공연", "예술", "유적",
            "랜드마크", "식물원", "미로", "정원", "유네스코", "해양", "아치",
            "호랑이", "철새", "낙타", "열대우림", "바위", "서핑", "산호", "빙벽",
            "하늘", "설경", "강", "대성당", "불상", "기념물", "전망대", "국립공원",
            "야생동물", "보트 투어", "역사적인 장소", "하이킹 코스", "전통 마을",
            "고대 유적", "야외 활동", "휴양지", "역사적 건축", "철도 여행"
    );

    private static final Map<String, Integer> INDEX_BY_KEYWORD = createIndex();

    private KeywordMask() {
    }

    public static Mask fromKeywords(Collection<String> keywords) {
        if (keywords == null || keywords.isEmpty()) {
            return Mask.EMPTY;
        }

        long low = 0L;
        long high = 0L;

        for (String keyword : keywords) {
            if (keyword == null) {
                continue;
            }

            Integer index = INDEX_BY_KEYWORD.get(keyword.trim());
            if (index == null) {
                continue;
            }

            if (index < BITS_PER_SEGMENT) {
                low |= 1L << index;
            } else {
                high |= 1L << (index - BITS_PER_SEGMENT);
            }
        }

        return new Mask(low, high);
    }

    public static Mask fromDescription(String description) {
        if (description == null || description.isBlank()) {
            return Mask.EMPTY;
        }

        return fromKeywords(KEYWORDS.stream()
                .filter(description::contains)
                .toList());
    }

    private static Map<String, Integer> createIndex() {
        Map<String, Integer> index = new LinkedHashMap<>();
        for (int i = 0; i < KEYWORDS.size(); i++) {
            index.put(KEYWORDS.get(i), i);
        }
        return Map.copyOf(index);
    }

    public record Mask(long low, long high) {

        private static final Mask EMPTY = new Mask(0L, 0L);

        public boolean isEmpty() {
            return low == 0L && high == 0L;
        }
    }
}
