package proj.travien.service;

import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import proj.travien.domain.DestinationInfo;
import proj.travien.repository.DestinationInfoRepository;

import java.util.Collection;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class DestinationKeywordMaskTest {

    @Test
    void givesCategoryKeywordsToTourRowsWithoutDescription() {
        DestinationInfo beach = tourRow(null, "NA020900");

        beach.refreshKeywordMask();

        assertThat(maskOf(beach)).isEqualTo(KeywordMask.fromKeywords(List.of("해변", "바다", "해안", "수영")));
    }

    @Test
    void combinesDescriptionKeywordsWithCategoryKeywords() {
        DestinationInfo beach = tourRow("해수욕장 뒤로 등산로가 이어지고 여름에는 축제가 열린다.", "NA020900");

        beach.refreshKeywordMask();

        KeywordMask.Mask expected = KeywordMask.fromKoreanDescription(beach.getInfo())
                .union(KeywordMask.fromKeywords(List.of("해변", "바다", "해안", "수영")));
        assertThat(maskOf(beach)).isEqualTo(expected);
        assertThat(contains(maskOf(beach), "등산")).isTrue();
        assertThat(contains(maskOf(beach), "축제")).isTrue();
        assertThat(contains(maskOf(beach), "해변")).isTrue();
    }

    @Test
    void doesNotScanSummaryText() {
        // 소개에는 '역사관광', '부산광역시 수영구'처럼 키워드가 들어간 분류명·지명이 있지만, 분류 키워드는 코드표에서만 정한다.
        DestinationInfo church = tourRow(null, "HS030200");
        church.setLocation("부산광역시 수영구");
        church.setContentTypeId("12");
        church.setSummary(TourDestinationSyncService.summaryOf("부산광역시 수영구", "12", "HS030200"));
        assertThat(church.getSummary()).contains("역사관광", "수영구");

        church.refreshKeywordMask();

        assertThat(maskOf(church).isEmpty()).isTrue();
    }

    @Test
    void keepsTheOldRuleForOverseasRows() {
        DestinationInfo overseas = new DestinationInfo();
        overseas.setInfo("부산 같은 항구 도시와 호수");
        overseas.setCategory("NA020900");

        overseas.refreshKeywordMask();

        // 해외 여행지는 기존처럼 설명문만 부분 문자열로 훑는다. (분류 코드는 보지 않음)
        assertThat(maskOf(overseas)).isEqualTo(KeywordMask.fromDescription(overseas.getInfo()));
        assertThat(contains(maskOf(overseas), "산")).isTrue();
        assertThat(contains(maskOf(overseas), "해변")).isFalse();
    }

    @Test
    void combinesWikiDescriptionTypeAndWorldHeritageKeywords() {
        DestinationInfo castle = wikiRow("호수 옆 언덕에 세운 성으로, 지금은 박물관으로 쓴다.", "Q23413|Q46169", true);

        castle.refreshKeywordMask();

        // 한국어 설명문 키워드 ∪ 유형별 키워드(성: 성·역사·역사적인 장소, 국립공원: 국립공원·자연·경치·야외 활동) ∪ 세계유산이면 유네스코
        KeywordMask.Mask expected = KeywordMask.fromKoreanDescription(castle.getInfo())
                .union(KeywordMask.fromKeywords(List.of("성", "역사", "역사적인 장소", "국립공원", "자연", "경치", "야외 활동", "유네스코")));
        assertThat(maskOf(castle)).isEqualTo(expected);
        assertThat(contains(maskOf(castle), "호수")).isTrue();
        assertThat(contains(maskOf(castle), "박물관")).isTrue();
        assertThat(contains(maskOf(castle), "유네스코")).isTrue();

        DestinationInfo notHeritage = wikiRow(null, "Q33506", false);
        notHeritage.refreshKeywordMask();
        assertThat(maskOf(notHeritage)).isEqualTo(KeywordMask.fromKeywords(List.of("박물관", "문화")));
        assertThat(notHeritage.computeKeywordMask()).isEqualTo(maskOf(notHeritage));
    }

    @Test
    void ignoresNorthKoreanSpellingLabelInWikiDescription() {
        // 2026-10-17 「센강」 문서 도입부 앞부분: '문화어'(북한 표기)의 '문화'는 문화 키워드가 아니다.
        DestinationInfo seine = wikiRow("센강(프랑스어: la Seine, 문화어: 쌘느강)은 프랑스 중북부를 흐르는 길이 776km의 강이다.", "Q4022", false);
        seine.refreshKeywordMask();

        assertThat(contains(maskOf(seine), "문화")).isFalse();
        assertThat(maskOf(seine)).isEqualTo(KeywordMask.fromKeywords(List.of("강", "자연")));
        // 진짜 '문화'는 그대로 잡는다.
        DestinationInfo kyoto = wikiRow("문화어: 교또. 일본 문화의 중심지였다.", null, false);
        kyoto.refreshKeywordMask();
        assertThat(contains(maskOf(kyoto), "문화")).isTrue();
    }

    @Test
    void doesNotScanWikiSummaryText() {
        // 소개의 유형명·지명('대성당', '성')은 훑지 않는다. 유형 키워드는 코드표에서만 정한다.
        DestinationInfo row = wikiRow(null, null, false);
        row.setSummary("프랑스 파리의 대성당입니다. 유네스코 세계유산입니다.");

        row.refreshKeywordMask();

        assertThat(maskOf(row).isEmpty()).isTrue();
        // 한국어 설명문은 국내와 같이 한 글자 키워드를 독립된 단어로만 인정한다. ('구성'의 '성'은 켜지지 않음)
        DestinationInfo described = wikiRow("여러 건물로 구성된 시설이다.", null, null);
        described.refreshKeywordMask();
        assertThat(contains(maskOf(described), "성")).isFalse();
    }

    @Test
    void unionKeepsBitsOfBothMasks() {
        KeywordMask.Mask low = KeywordMask.fromKeywords(List.of("해안"));
        KeywordMask.Mask high = KeywordMask.fromKeywords(List.of("철도 여행"));

        KeywordMask.Mask union = low.union(high);

        assertThat(union).isEqualTo(KeywordMask.fromKeywords(List.of("해안", "철도 여행")));
        assertThat(low.union(null)).isEqualTo(low);
        assertThat(KeywordMask.isSupported("해안")).isTrue();
        assertThat(KeywordMask.isSupported("해안 ")).isFalse();
        assertThat(KeywordMask.isSupported(null)).isFalse();
    }

    @Test
    @SuppressWarnings("unchecked")
    void backfillAlsoCoversTourRowsWithKeywordCategoryButNoDescription() {
        DestinationInfoRepository repository = mock(DestinationInfoRepository.class);
        DestinationInfo described = new DestinationInfo();
        described.setInfo("박물관과 미술관");
        DestinationInfo categorized = tourRow(null, "NA020900");
        when(repository.findAllByKeywordMaskLowAndKeywordMaskHighAndInfoIsNotNull(0L, 0L)).thenReturn(List.of(described));
        when(repository.findAllByKeywordMaskLowAndKeywordMaskHighAndInfoIsNullAndCategoryIn(eq(0L), eq(0L), anyCollection()))
                .thenReturn(List.of(categorized));

        new DestinationKeywordMaskBackfill(repository).backfillMissingMasks();

        // 키워드가 없는 분류(호텔 등)는 계산해도 빈 마스크라 조회 대상에서 뺀다.
        ArgumentCaptor<Collection<String>> categories = ArgumentCaptor.forClass(Collection.class);
        verify(repository).findAllByKeywordMaskLowAndKeywordMaskHighAndInfoIsNullAndCategoryIn(eq(0L), eq(0L), categories.capture());
        assertThat(categories.getValue()).containsExactlyInAnyOrderElementsOf(TourCategories.codesWithKeywords())
                .contains("NA020900")
                .doesNotContain("AC010100", "HS030200");

        ArgumentCaptor<List<DestinationInfo>> saved = ArgumentCaptor.forClass(List.class);
        verify(repository).saveAll(saved.capture());
        assertThat(saved.getValue()).containsExactly(described, categorized);
        assertThat(contains(maskOf(described), "박물관")).isTrue();
        assertThat(contains(maskOf(categorized), "해변")).isTrue();
    }

    private static DestinationInfo tourRow(String info, String category) {
        DestinationInfo destination = new DestinationInfo();
        destination.setSource(DestinationInfo.SOURCE_TOUR_API);
        destination.setInfo(info);
        destination.setCategory(category);
        return destination;
    }

    private static DestinationInfo wikiRow(String info, String category, Boolean worldHeritage) {
        DestinationInfo destination = new DestinationInfo();
        destination.setSource(DestinationInfo.SOURCE_WIKIDATA);
        destination.setInfo(info);
        destination.setCategory(category);
        destination.setWorldHeritage(worldHeritage);
        return destination;
    }

    private static KeywordMask.Mask maskOf(DestinationInfo destination) {
        return new KeywordMask.Mask(destination.getKeywordMaskLow(), destination.getKeywordMaskHigh());
    }

    private static boolean contains(KeywordMask.Mask mask, String keyword) {
        KeywordMask.Mask selected = KeywordMask.fromKeywords(List.of(keyword));
        return (mask.low() & selected.low()) != 0 || (mask.high() & selected.high()) != 0;
    }
}
