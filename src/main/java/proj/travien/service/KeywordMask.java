package proj.travien.service;

import java.util.Collection;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

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

    // 한 글자 키워드(산, 섬, 성, 탑, 강) 뒤에 붙어도 같은 단어로 보는 조사. 모두 받침으로 끝나는 키워드라 받침 뒤 조사만 둔다.
    private static final Set<String> PARTICLES = Set.of(
            "은", "이", "을", "과", "의", "에", "에서", "에는", "에도", "으로", "으로는", "까지", "부터", "처럼",
            "이다", "이며", "이고", "이자", "이나", "이라는", "이었다"
    );

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

    // 지원하는 키워드인지 확인한다. fromKeywords는 모르는 키워드를 조용히 건너뛰므로, 키워드를 미리 정해 두는 곳(분류 코드표 등)에서 검사할 때 쓴다.
    public static boolean isSupported(String keyword) {
        return keyword != null && INDEX_BY_KEYWORD.containsKey(keyword);
    }

    public static Mask fromDescription(String description) {
        if (description == null || description.isBlank()) {
            return Mask.EMPTY;
        }

        return fromKeywords(KEYWORDS.stream()
                .filter(description::contains)
                .toList());
    }

    /**
     * 국내(TourAPI) 설명문용 마스크. 두 글자 이상 키워드는 {@link #fromDescription}과 같이 부분 문자열로 찾고,
     * 한 글자 키워드는 독립된 단어(조사만 붙은 형태)일 때만 인정한다.
     *
     * <p>한국어 설명문에는 '부산·울산'(산), '강원·강릉'(강), '구성·조성'(성), '문화유산'(산)처럼 한 글자 키워드를 품은
     * 지명·일반 단어가 많아, 부분 문자열로 찾으면 관련 없는 비트가 켜진다. 대신 '한라산', '한강'처럼 이름에 붙은 경우는
     * 이 규칙으로 잡지 못한다.
     */
    public static Mask fromKoreanDescription(String description) {
        if (description == null || description.isBlank()) {
            return Mask.EMPTY;
        }

        return fromKeywords(KEYWORDS.stream()
                .filter(keyword -> keyword.length() == 1
                        ? containsStandaloneWord(description, keyword)
                        : description.contains(keyword))
                .toList());
    }

    private static boolean containsStandaloneWord(String text, String keyword) {
        int index = text.indexOf(keyword);
        while (index >= 0) {
            int end = index + keyword.length();
            int wordEnd = end;
            while (wordEnd < text.length() && isHangulSyllable(text.charAt(wordEnd))) {
                wordEnd++;
            }

            boolean startsWord = index == 0 || !isHangulSyllable(text.charAt(index - 1));
            String suffix = text.substring(end, wordEnd);
            if (startsWord && (suffix.isEmpty() || PARTICLES.contains(suffix))) {
                return true;
            }
            index = text.indexOf(keyword, index + 1);
        }
        return false;
    }

    private static boolean isHangulSyllable(char c) {
        return c >= '\uAC00' && c <= '\uD7A3';
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

        // 두 마스크 중 하나라도 켜진 키워드를 모두 켠 마스크
        public Mask union(Mask other) {
            if (other == null) {
                return this;
            }
            return new Mask(low | other.low, high | other.high);
        }
    }
}
