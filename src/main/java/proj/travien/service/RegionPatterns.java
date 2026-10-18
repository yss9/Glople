package proj.travien.service;

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 여행지 조회의 region 값을 MySQL REGEXP 패턴으로 바꾼다. (agent/docs/design.md 8.3의 접미사 떼기 규칙)
 *
 * <p>region은 공백으로 나눈 단어 1~{@value #MAX_TOKENS}개이고, 단어마다 패턴을 하나 만든다. 여행지는 모든 단어가
 * {@code CONCAT_WS(' ', country, location)}의 한 단어와 맞아야 한다. 예: 국내 '대한민국 부산광역시 해운대구',
 * 위키데이터 '일본 일본 교토부', 기존 해외 행 '일본 교토'.
 *
 * <p>단어 하나가 맞는 규칙: 여행지 쪽 단어 = (바탕 이름) + (행정구역 접미사 하나 또는 없음).
 * <ul>
 *   <li>바탕 이름은 받은 단어 그대로와, 끝의 접미사를 하나 뗀 이름이다. 뗀 결과가 한 글자면 떼지 않는다
 *       ('대구'의 '구', '중구', '광주'의 '주'를 지키려고).</li>
 *   <li>접미사: {@value #SUFFIX_ALTERNATION}</li>
 *   <li>줄임말: 충북·충남·전북·전남·경북·경남 ↔ 충청북·충청남·전라북·전라남·경상북·경상남, 한국·국내 → 대한민국</li>
 * </ul>
 * 예: '부산' → 부산광역시, '해운대' → 해운대구, '강릉시' → 강릉시, '경남' → 경상남도, '전라북도' → 전북특별자치도·전라북도,
 * '울릉도' → 울릉군, '교토' → 교토부·교토. '광주'는 광주광역시와 경기도 광주시 둘 다 맞는다(모호함은 호출하는 쪽이 되묻는다).
 * '경기 광주'처럼 두 단어를 주면 둘 다 맞는 곳만 남는다.
 */
public final class RegionPatterns {

    public static final int MAX_TOKENS = 3;
    public static final int MAX_LENGTH = 50;

    // 모든 글에 맞는 패턴. 쓰지 않는 regionN 칸에 넣는다 (빈 패턴은 MySQL 8에서 오류).
    public static final String MATCH_ALL = "^";

    // 긴 것부터 (정규식 대안은 왼쪽부터 시도하지만, 뒤의 '( |$)'가 맞을 때까지 되돌아가므로 순서와 상관없이 맞는다)
    static final List<String> SUFFIXES = List.of(
            "특별자치도", "특별자치시", "특별시", "광역시", "자치도", "자치시",
            "도", "시", "군", "구", "부", "현", "주", "성");
    static final String SUFFIX_ALTERNATION = "특별자치도|특별자치시|특별시|광역시|자치도|자치시|도|시|군|구|부|현|주|성";

    private static final Map<String, List<String>> ALIASES = Map.ofEntries(
            Map.entry("충북", List.of("충청북")), Map.entry("충청북", List.of("충북")),
            Map.entry("충남", List.of("충청남")), Map.entry("충청남", List.of("충남")),
            Map.entry("전북", List.of("전라북")), Map.entry("전라북", List.of("전북")),
            Map.entry("전남", List.of("전라남")), Map.entry("전라남", List.of("전남")),
            Map.entry("경북", List.of("경상북")), Map.entry("경상북", List.of("경북")),
            Map.entry("경남", List.of("경상남")), Map.entry("경상남", List.of("경남")),
            Map.entry("한국", List.of("대한민국")), Map.entry("국내", List.of("대한민국"))
    );

    private RegionPatterns() {
    }

    /**
     * region 값 → 단어별 패턴 (0~3개). null·빈 값이면 빈 목록(지역 조건 없음).
     *
     * @throws InvalidRequestException 길이가 {@value #MAX_LENGTH}자를 넘거나, 단어가 {@value #MAX_TOKENS}개를 넘거나,
     *                                 글자·숫자·가운뎃점(·)·하이픈·마침표가 아닌 글자가 있을 때
     */
    public static List<String> patterns(String region) {
        if (region == null || region.isBlank()) {
            return List.of();
        }
        String trimmed = region.trim();
        if (trimmed.codePointCount(0, trimmed.length()) > MAX_LENGTH) {
            throw new InvalidRequestException("region", "too_long", "region은 " + MAX_LENGTH + "자까지 쓸 수 있습니다.");
        }
        String[] tokens = trimmed.split("\\s+");
        if (tokens.length > MAX_TOKENS) {
            throw new InvalidRequestException("region", "too_many_words",
                    "region은 단어 " + MAX_TOKENS + "개까지 쓸 수 있습니다. 예: '부산 해운대'");
        }
        List<String> patterns = new ArrayList<>(tokens.length);
        for (String token : tokens) {
            if (!token.codePoints().allMatch(RegionPatterns::isAllowed)) {
                throw new InvalidRequestException("region", "bad_character",
                        "region에는 글자, 숫자, 가운뎃점(·), 하이픈, 마침표만 쓸 수 있습니다.");
            }
            patterns.add(tokenPattern(token));
        }
        return patterns;
    }

    // 단어 하나의 패턴: (^| )(바탕1|바탕2...)(접미사)?( |$)
    static String tokenPattern(String token) {
        StringBuilder alternation = new StringBuilder();
        for (String base : bases(token)) {
            if (!alternation.isEmpty()) {
                alternation.append('|');
            }
            alternation.append(escape(base));
        }
        return "(^| )(" + alternation + ")(" + SUFFIX_ALTERNATION + ")?( |$)";
    }

    // 받은 단어, 접미사를 뗀 이름(두 글자 이상 남을 때), 그 둘의 줄임말
    static Set<String> bases(String token) {
        Set<String> bases = new LinkedHashSet<>();
        bases.add(token);
        String stripped = stripSuffix(token);
        bases.add(stripped);
        for (String base : List.copyOf(bases)) {
            bases.addAll(ALIASES.getOrDefault(base, List.of()));
        }
        return bases;
    }

    static String stripSuffix(String token) {
        for (String suffix : SUFFIXES) {
            if (token.endsWith(suffix)) {
                String base = token.substring(0, token.length() - suffix.length());
                if (base.codePointCount(0, base.length()) >= 2) {
                    return base;
                }
            }
        }
        return token;
    }

    private static boolean isAllowed(int codePoint) {
        return Character.isLetterOrDigit(codePoint) || codePoint == '·' || codePoint == '-' || codePoint == '.';
    }

    // 허용한 글자 중 정규식에서 뜻이 있는 것은 '.'뿐이다. '-'는 문자 집합 밖이라 그대로 둬도 되지만 함께 이스케이프한다.
    private static String escape(String base) {
        return base.replace(".", "\\.").replace("-", "\\-");
    }
}
