package proj.travien.service;

import org.junit.jupiter.api.Test;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;

class KeywordMaskTest {

    @Test
    void createsTwoSegmentMaskForSupportedKeywords() {
        KeywordMask.Mask mask = KeywordMask.fromKeywords(List.of("해안", "철도 여행"));

        assertThat(mask.low()).isNotZero();
        assertThat(mask.high()).isNotZero();
        assertThat(mask.isEmpty()).isFalse();
    }

    @Test
    void derivesTheSameMaskFromDestinationDescription() {
        KeywordMask.Mask selected = KeywordMask.fromKeywords(List.of("문화", "박물관", "유네스코"));
        KeywordMask.Mask indexed = KeywordMask.fromDescription("유네스코 문화 유산과 박물관을 둘러보는 여행");

        assertThat(indexed.low() & selected.low()).isEqualTo(selected.low());
        assertThat(indexed.high() & selected.high()).isEqualTo(selected.high());
    }

    @Test
    void ignoresUnknownKeywords() {
        assertThat(KeywordMask.fromKeywords(List.of("지원하지 않는 키워드")).isEmpty()).isTrue();
    }

    @Test
    void ignoresSingleSyllableKeywordsInsidePlaceNamesForKoreanDescription() {
        String description = "부산광역시 해운대구에 위치한 해수욕장으로 울산·군산에서도 가깝다. "
                + "강원도 강릉시처럼 해안 산책로가 조성되어 있고 세계문화유산은 없다.";

        KeywordMask.Mask mask = KeywordMask.fromKoreanDescription(description);

        assertThat(contains(mask, "산")).isFalse();
        assertThat(contains(mask, "강")).isFalse();
        assertThat(contains(mask, "성")).isFalse();
        // 두 글자 이상 키워드는 기존과 같이 찾는다.
        assertThat(contains(mask, "해안")).isTrue();
        assertThat(contains(mask, "문화")).isTrue();
        // 해외 여행지에 쓰는 기존 방식은 그대로 부분 문자열로 찾는다.
        assertThat(contains(KeywordMask.fromDescription(description), "산")).isTrue();
    }

    @Test
    void findsSingleSyllableKeywordsUsedAsWordsInKoreanDescription() {
        KeywordMask.Mask mask = KeywordMask.fromKoreanDescription(
                "해발 1,950m의 산으로 정상 부근에 탑이 있고, 강을 따라 걷다 보면 작은 섬에 닿는다. 성(城) 터도 남아 있다.");

        assertThat(contains(mask, "산")).isTrue();
        assertThat(contains(mask, "탑")).isTrue();
        assertThat(contains(mask, "강")).isTrue();
        assertThat(contains(mask, "섬")).isTrue();
        assertThat(contains(mask, "성")).isTrue();
        assertThat(KeywordMask.fromKoreanDescription(" ").isEmpty()).isTrue();
    }

    private static boolean contains(KeywordMask.Mask mask, String keyword) {
        KeywordMask.Mask selected = KeywordMask.fromKeywords(List.of(keyword));
        return (mask.low() & selected.low()) != 0 || (mask.high() & selected.high()) != 0;
    }
}
