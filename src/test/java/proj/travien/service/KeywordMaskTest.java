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
}
