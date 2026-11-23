package proj.travien.service;

import java.util.Arrays;
import java.util.Locale;
import java.util.stream.Collectors;

/**
 * 키워드 추천(POST /api/search)이 여행지마다 쓰는 마스크. 설정 {@value #PROPERTY}(기본 {@value #DEFAULT_VALUE}).
 *
 * <ul>
 *   <li>{@code union}: 규칙 마스크 ∪ 태거 예측 마스크. 예측이 없는 행은 규칙 마스크만.</li>
 *   <li>{@code tagger}: 태거 예측이 있는 행은 예측 마스크만, 없는 행은 규칙 마스크.</li>
 *   <li>{@code rule}: 규칙 마스크만 (태거 예측을 가져오기 전과 같음).</li>
 * </ul>
 * 기본이 tagger인 이유(2026-10-18, roberta-base-v3, 학습에 쓰지 않은 시험지): F1·정밀도가 국내 test 0.744/0.723, test_new 0.742/0.762,
 * 해외 test_overseas 0.728/0.732로 rule(0.580/0.589, 0.639/0.655, 0.701/0.697)보다 높다. union은 재현율이 가장 높지만 규칙의 오탐을
 * 그대로 남겨(예: 모스크에 차르의 종) 정밀도가 0.584~0.636으로 떨어진다. 상위 21개만 보여 주는 추천에서는 정밀도를 앞에 둔다.
 *
 * 실제 계산은 후보 쿼리({@code DestinationInfoRepository.RECOMMEND_MASK_LOW_SQL})가 한다. 여기 {@link #sqlValue()}가 그 쿼리의
 * {@code :maskMode} 값이다. 규칙 마스크 열은 바뀌지 않으므로 설정만 바꾸고 서버를 다시 띄우면 곧바로 바뀐다.
 */
public enum KeywordMaskMode {
    UNION("union"),
    TAGGER("tagger"),
    RULE("rule");

    public static final String PROPERTY = "keyword.recommend.mask-mode";
    public static final String DEFAULT_VALUE = "tagger";

    private final String sqlValue;

    KeywordMaskMode(String sqlValue) {
        this.sqlValue = sqlValue;
    }

    public String sqlValue() {
        return sqlValue;
    }

    /**
     * 설정 값 → 모드. 대소문자·앞뒤 공백은 무시하고, 빈 값은 기본값(union)이다.
     * 모르는 값은 조용히 기본값으로 바꾸지 않고 예외로 알린다(서버 시작 단계에서 멈춤).
     */
    public static KeywordMaskMode parse(String value) {
        String normalized = value == null || value.isBlank() ? DEFAULT_VALUE : value.trim().toLowerCase(Locale.ROOT);
        for (KeywordMaskMode mode : values()) {
            if (mode.sqlValue.equals(normalized)) {
                return mode;
            }
        }
        throw new IllegalArgumentException(PROPERTY + "는 "
                + Arrays.stream(values()).map(KeywordMaskMode::sqlValue).collect(Collectors.joining(", "))
                + " 중 하나여야 합니다: '" + value + "'");
    }
}
