package proj.travien.service;

import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import proj.travien.domain.DestinationInfo;
import proj.travien.repository.DestinationInfoRepository;

import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class DestinationKeywordMaskTest {

    private static final long PY_CRC32_GAGEODO = 2972272251L;

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
    void moscowIsNotAMosque() {
        // 2026-10-18 실제 추천에서 '모스크'를 고르면 크렘린의 차르의 종·차르 캐넌이 나왔다: '모스크바' 속 '모스크'.
        DestinationInfo bell = wikiRow("차르의 종은 러시아 모스크바의 거대한 청동 종이다. 현재 모스크바 크렘린에 있다.", null, false);
        bell.refreshKeywordMask();
        assertThat(contains(maskOf(bell), "모스크")).isFalse();
        // 진짜 모스크는 그대로 잡는다.
        DestinationInfo mosque = wikiRow("술탄 아흐메트 모스크는 이스탄불에 있는 모스크이다.", null, false);
        mosque.refreshKeywordMask();
        assertThat(contains(maskOf(mosque), "모스크")).isTrue();
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

    // ---------- 규칙 판(keyword_mask_version)과 시작 시 다시 계산 ----------

    @Test
    void refreshRecordsTheRuleVersionOfTheSource() {
        DestinationInfo tour = tourRow("해변", "NA020900");
        DestinationInfo wiki = wikiRow("섬", "Q23442", false);
        DestinationInfo legacy = new DestinationInfo();
        legacy.setInfo("섬");

        tour.refreshKeywordMask();
        wiki.refreshKeywordMask();
        legacy.refreshKeywordMask();

        assertThat(tour.getKeywordMaskVersion()).isEqualTo(KeywordMaskVersion.forSource("TOUR_API")).matches("r2-k[0-9a-f]{8}-t[0-9a-f]{8}");
        assertThat(wiki.getKeywordMaskVersion()).isEqualTo(KeywordMaskVersion.forSource("WIKIDATA")).matches("r2-k[0-9a-f]{8}-w[0-9a-f]{8}");
        assertThat(legacy.getKeywordMaskVersion()).isEqualTo(KeywordMaskVersion.forSource(null)).matches("r2-k[0-9a-f]{8}");
        assertThat(tour.getKeywordMaskVersion()).startsWith(legacy.getKeywordMaskVersion()).hasSizeLessThanOrEqualTo(40);
    }

    // 판의 지문은 키워드 목록·조사·코드표의 코드별 키워드가 바뀌면 달라진다 (분류명만 바뀌면 그대로)
    @Test
    void ruleVersionFingerprintsTheKeywordListAndTables() {
        assertThat(KeywordMask.ruleFingerprintSource()).startsWith("해안|수영|조각").contains("#").contains("이었다");
        assertThat(KeywordMask.keywords()).hasSize(94).startsWith("해안").endsWith("철도 여행");
        assertThat(KeywordMaskVersion.tourCategoryFingerprintSource()).contains("NA020900=해변|바다|해안|수영\n").doesNotContain("해수욕장");
        assertThat(KeywordMaskVersion.wikiTypeFingerprintSource()).contains("Q33506=박물관|문화\n");
        // MySQL CRC32('MySQL') = 3259397556 = 0xc24675b4
        assertThat(KeywordMaskVersion.crc32("MySQL")).isEqualTo("c24675b4");
    }

    // 판이 없거나 낡은 국내·위키데이터 행만 다시 계산: 마스크가 바뀐 행은 마스크와 판을, 같은 행은 판만 쓴다 (엔티티 저장 없음)
    @Test
    void recomputesRowsWhoseRuleVersionIsMissingOrOld() {
        DestinationInfoRepository repository = mock(DestinationInfoRepository.class);
        String tourVersion = KeywordMaskVersion.forSource("TOUR_API");
        String wikiVersion = KeywordMaskVersion.forSource("WIKIDATA");

        DestinationInfo stale = tourRow(null, "NA020900"); // 저장된 마스크 0, 분류 코드표로는 해변·바다·해안·수영
        stale.setId(1L);
        DestinationInfo same = tourRow("박물관", null);
        same.setId(2L);
        KeywordMask.Mask museum = KeywordMask.fromKeywords(List.of("박물관"));
        same.setKeywordMaskLow(museum.low());
        same.setKeywordMaskHigh(museum.high());
        DestinationInfo oldWiki = wikiRow(null, "Q33506", false);
        oldWiki.setId(7L);
        oldWiki.setKeywordMaskVersion("r0-old");

        when(repository.findBySourceAndKeywordMaskVersionIsNullAndIdGreaterThanOrderByIdAsc(eq("TOUR_API"), eq(0L), any()))
                .thenReturn(List.of(stale, same));
        when(repository.findBySourceAndKeywordMaskVersionNotAndIdGreaterThanOrderByIdAsc(eq("WIKIDATA"), eq(wikiVersion), eq(0L), any()))
                .thenReturn(List.of(oldWiki));
        when(repository.updateRuleMask(anyLong(), anyLong(), anyLong(), anyString())).thenReturn(1);

        DestinationKeywordMaskBackfill.RecomputeResult result = new DestinationKeywordMaskBackfill(repository).recomputeOutdatedMasks();

        assertThat(result).isEqualTo(new DestinationKeywordMaskBackfill.RecomputeResult(3, 2));
        KeywordMask.Mask beach = KeywordMask.fromKeywords(List.of("해변", "바다", "해안", "수영"));
        verify(repository).updateRuleMask(1L, beach.low(), beach.high(), tourVersion);
        verify(repository).markRuleMaskVersion(List.of(2L), tourVersion);
        KeywordMask.Mask museumCulture = KeywordMask.fromKeywords(List.of("박물관", "문화"));
        verify(repository).updateRuleMask(7L, museumCulture.low(), museumCulture.high(), wikiVersion);
        // 판으로 다시 계산하는 것은 국내·위키데이터만. 엔티티를 통째로 저장하지 않는다 (설명문 등 다른 열을 덮어쓰지 않음)
        verify(repository, never()).findBySourceAndKeywordMaskVersionIsNullAndIdGreaterThanOrderByIdAsc(isNull(), anyLong(), any());
        verify(repository, never()).saveAll(any());
    }

    // 한 페이지(500건)가 꽉 차면 마지막 id 다음부터 다시 읽는다
    @Test
    void recomputePagesByIdUntilAShortPage() {
        DestinationInfoRepository repository = mock(DestinationInfoRepository.class);
        List<DestinationInfo> full = new java.util.ArrayList<>();
        for (long id = 1; id <= DestinationKeywordMaskBackfill.PAGE_SIZE; id++) {
            DestinationInfo row = tourRow(null, null);
            row.setId(id);
            full.add(row);
        }
        DestinationInfo last = tourRow(null, null);
        last.setId(900L);
        when(repository.findBySourceAndKeywordMaskVersionIsNullAndIdGreaterThanOrderByIdAsc(eq("TOUR_API"), eq(0L), any())).thenReturn(full);
        when(repository.findBySourceAndKeywordMaskVersionIsNullAndIdGreaterThanOrderByIdAsc(eq("TOUR_API"), eq(500L), any())).thenReturn(List.of(last));

        DestinationKeywordMaskBackfill.RecomputeResult result = new DestinationKeywordMaskBackfill(repository).recomputeOutdatedMasks();

        assertThat(result.read()).isEqualTo(501);
        assertThat(result.changed()).isZero(); // 설명문·분류가 없으면 빈 마스크 그대로 → 판만
        verify(repository).markRuleMaskVersion(List.of(900L), KeywordMaskVersion.forSource("TOUR_API"));
    }

    // 출처가 없는 기존 해외 행은 예전처럼 마스크가 0이고 설명문이 있는 행만 채운다
    @Test
    @SuppressWarnings("unchecked")
    void backfillFillsEmptyMasksOfLegacyRowsOnly() {
        DestinationInfoRepository repository = mock(DestinationInfoRepository.class);
        DestinationInfo described = new DestinationInfo();
        described.setInfo("박물관과 미술관");
        when(repository.findAllBySourceIsNullAndKeywordMaskLowAndKeywordMaskHighAndInfoIsNotNull(0L, 0L)).thenReturn(List.of(described));

        new DestinationKeywordMaskBackfill(repository).onApplicationReady();

        ArgumentCaptor<List<DestinationInfo>> saved = ArgumentCaptor.forClass(List.class);
        verify(repository).saveAll(saved.capture());
        assertThat(saved.getValue()).containsExactly(described);
        assertThat(contains(maskOf(described), "박물관")).isTrue();
        // 시작할 때 국내·위키데이터 판 확인도 같이 한다
        verify(repository).findBySourceAndKeywordMaskVersionIsNullAndIdGreaterThanOrderByIdAsc(eq("TOUR_API"), eq(0L), any());
        verify(repository).findBySourceAndKeywordMaskVersionNotAndIdGreaterThanOrderByIdAsc(eq("WIKIDATA"), anyString(), eq(0L), any());
    }

    // ---------- 태거 예측 열 ----------

    // 태거 예측은 규칙 마스크와 따로 저장되고, 저장할 때 규칙 계산이 덮어쓰지 않는다
    @Test
    void taggerPredictionSurvivesRuleRecomputation() {
        DestinationInfo castle = wikiRow("호수 옆 언덕에 세운 성이다.", "Q23413", false);
        KeywordMask.Mask predicted = KeywordMask.fromKeywords(List.of("역사적인 장소", "경치"));
        LocalDateTime at = LocalDateTime.of(2026, 10, 18, 15, 0);
        castle.applyTaggerPrediction(predicted, "roberta-base-v3", DestinationInfo.infoCrc32(castle.getInfo()), at);

        castle.refreshKeywordMask();

        assertThat(castle.taggerMask()).isEqualTo(predicted);
        assertThat(castle.getTaggerModel()).isEqualTo("roberta-base-v3");
        assertThat(castle.getTaggerUpdatedAt()).isEqualTo(at);
        // 규칙 마스크는 규칙만으로 계산한다 (태거 키워드가 섞이지 않음)
        assertThat(maskOf(castle)).isEqualTo(castle.computeKeywordMask());
        assertThat(contains(maskOf(castle), "경치")).isFalse();
    }

    // 설명문이 바뀌거나 지워지면 그 설명문으로 만든 예측은 저장할 때 지운다 (다시 예측해 가져와야 함)
    @Test
    void taggerPredictionIsDroppedWhenTheDescriptionChanges() {
        DestinationInfo beach = tourRow("해수욕장이다.", "NA020900");
        beach.applyTaggerPrediction(KeywordMask.fromKeywords(List.of("해변")), "roberta-base-v3",
                DestinationInfo.infoCrc32(beach.getInfo()), LocalDateTime.of(2026, 10, 18, 15, 0));

        beach.setInfo("해수욕장이다. 2026년부터 공사로 문을 닫았다.");
        beach.refreshKeywordMask();

        assertThat(beach.getTaggerModel()).isNull();
        assertThat(beach.taggerMask().isEmpty()).isTrue();
        assertThat(beach.getTaggerInfoCrc32()).isNull();
        assertThat(beach.getTaggerUpdatedAt()).isNull();

        DestinationInfo cleared = wikiRow("섬이다.", null, false);
        cleared.applyTaggerPrediction(KeywordMask.fromKeywords(List.of("섬")), "m", DestinationInfo.infoCrc32("섬이다."), null);
        cleared.setInfo(null);
        cleared.refreshKeywordMask();
        assertThat(cleared.getTaggerModel()).isNull();
    }

    // 서버의 CRC32는 predict_all.py의 info_crc32(zlib.crc32(overview.encode("utf-8")))와 같은 값이다
    @Test
    void infoCrc32MatchesPythonZlibCrc32OfUtf8() {
        // python3 -c 'import zlib; print(zlib.crc32("가거도는 섬이다.".encode()), zlib.crc32(b"MySQL"), zlib.crc32(b""))'
        assertThat(DestinationInfo.infoCrc32("가거도는 섬이다.")).isEqualTo(PY_CRC32_GAGEODO);
        assertThat(DestinationInfo.infoCrc32("MySQL")).isEqualTo(3259397556L);
        assertThat(DestinationInfo.infoCrc32("")).isZero();
        assertThat(DestinationInfo.infoCrc32(null)).isNull();
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
