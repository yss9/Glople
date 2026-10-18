package proj.travien.service;

import org.junit.jupiter.api.Test;
import proj.travien.domain.DestinationInfo;
import proj.travien.dto.DestinationDetailDTO;
import proj.travien.dto.DestinationItemDTO;
import proj.travien.repository.DestinationInfoRepository;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.List;
import java.util.Optional;
import java.util.regex.Pattern;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class DestinationSearchServiceTest {

    private static final Clock CLOCK = Clock.fixed(Instant.parse("2026-10-18T03:00:00Z"), ZoneOffset.UTC);

    private final DestinationInfoRepository repository = mock(DestinationInfoRepository.class);
    private final DestinationSearchService service = new DestinationSearchService(repository, CLOCK);

    // ---------- 지역 (RegionPatterns) ----------

    // 쿼리는 CONCAT_WS(' ', country, location)에 REGEXP를 건다. 같은 패턴을 Java 정규식으로 돌려 규칙을 확인한다.
    // (쓰는 문법은 (^| ), 대안 |, ?, $, \. 뿐이라 MySQL 8의 ICU 정규식과 뜻이 같다)
    @Test
    void domesticRegionMatchesSidoOrSigunguAfterSuffixStripping() {
        assertMatches("부산", "대한민국 부산광역시 해운대구", "대한민국 부산광역시 중구");
        assertNoMatch("부산", "대한민국 경상남도 김해시");
        assertMatches("부산광역시", "대한민국 부산광역시 해운대구");
        assertMatches("해운대", "대한민국 부산광역시 해운대구");
        assertMatches("강릉시", "대한민국 강원특별자치도 강릉시");
        assertMatches("강원도", "대한민국 강원특별자치도 강릉시");
        assertMatches("서울시", "대한민국 서울특별시 종로구");
        assertMatches("세종", "대한민국 세종특별자치시");
        assertMatches("제주도", "대한민국 제주특별자치도 서귀포시");
        assertMatches("울릉도", "대한민국 경상북도 울릉군");
        assertMatches("강화도", "대한민국 인천광역시 강화군");
        assertMatches("수원", "대한민국 경기도 수원시");
    }

    // 접미사를 떼면 한 글자만 남는 이름은 떼지 않는다 ('대구'의 '구', '중구', '광주'의 '주')
    @Test
    void shortNamesKeepTheirLastSyllable() {
        assertMatches("대구", "대한민국 대구광역시 중구");
        assertNoMatch("대구", "대한민국 대전광역시 대덕구");
        assertMatches("중구", "대한민국 서울특별시 중구", "대한민국 부산광역시 중구");
        assertNoMatch("중구", "대한민국 충청북도 중원군");
        assertMatches("남양주", "대한민국 경기도 남양주시");
        assertMatches("청도", "대한민국 경상북도 청도군");
    }

    @Test
    void abbreviatedProvincesAndRenamedProvincesMatch() {
        assertMatches("경남", "대한민국 경상남도 통영시");
        assertMatches("경상남도", "대한민국 경상남도 통영시");
        assertNoMatch("경남", "대한민국 경상북도 경주시");
        assertMatches("전북", "대한민국 전북특별자치도 전주시", "대한민국 전라북도 전주시");
        assertMatches("전라북도", "대한민국 전북특별자치도 전주시");
        assertMatches("충북", "대한민국 충청북도 단양군");
        assertMatches("한국", "대한민국 부산광역시 해운대구");
    }

    // '광주'는 광주광역시와 경기도 광주시 둘 다. 두 단어를 주면 둘 다 맞는 곳만
    @Test
    void ambiguousNameMatchesBothAndASecondWordNarrowsIt() {
        assertMatches("광주", "대한민국 광주광역시 동구", "대한민국 경기도 광주시");
        assertMatches("경기 광주", "대한민국 경기도 광주시");
        assertNoMatch("경기 광주", "대한민국 광주광역시 동구");
        assertMatches("부산 해운대", "대한민국 부산광역시 해운대구");
        assertNoMatch("부산 해운대", "대한민국 부산광역시 중구");
    }

    @Test
    void overseasRegionMatchesCountryOrLocation() {
        assertMatches("일본", "일본 일본 교토부", "일본 교토");
        assertMatches("교토", "일본 일본 교토부", "일본 교토");
        assertMatches("교토부", "일본 교토");
        assertMatches("캘리포니아", "미국 미국 캘리포니아주");
        assertMatches("파리", "프랑스 프랑스 파리");
        assertNoMatch("파리", "프랑스 프랑스 파리시청");
        assertMatches("워싱턴 D.C.", "미국 미국 워싱턴 D.C.");
        assertNoMatch("D.C.", "미국 미국 DXCX");
    }

    @Test
    void regionValuesAreLimited() {
        assertThat(RegionPatterns.patterns(null)).isEmpty();
        assertThat(RegionPatterns.patterns("  ")).isEmpty();
        assertThat(RegionPatterns.patterns(" 부산   해운대 ")).hasSize(2);
        assertThat(RegionPatterns.SUFFIX_ALTERNATION).isEqualTo(String.join("|", RegionPatterns.SUFFIXES));
        assertProblem(() -> RegionPatterns.patterns("a b c d"), "region", "too_many_words");
        assertProblem(() -> RegionPatterns.patterns("부산(해운대)"), "region", "bad_character");
        assertProblem(() -> RegionPatterns.patterns(".*"), "region", "bad_character");
        assertProblem(() -> RegionPatterns.patterns("가".repeat(51)), "region", "too_long");
    }

    // ---------- 조회 값 → 쿼리 인자 ----------

    @Test
    void searchPassesFiltersAndDefaultsToTheQuery() {
        when(repository.searchDestinations(anyInt(), anyString(), anyString(), anyString(), anyInt(), anyString(), anyInt(),
                anyLong(), anyLong(), anyString(), anyString(), anyLong(), anyInt())).thenReturn(List.of());

        service.search(null, null, null, null, null);
        verify(repository).searchDestinations(0, "^", "^", "^", 0, "", 0, 0L, 0L, "", "%%", 20261018L,
                DestinationSearchService.DEFAULT_LIMIT);

        service.search("부산 해운대", "관광지", "해수욕장", 5, 7L);
        verify(repository).searchDestinations(2, RegionPatterns.tokenPattern("부산"), RegionPatterns.tokenPattern("해운대"), "^",
                1, "12", 1, 0L, 0L, "해수욕장", "%해수욕장%", 7L, 5);

        KeywordMask.Mask culture = KeywordMask.fromKeywords(List.of("박물관", "미술관", "공연", "예술"));
        service.search("일본", " 문화시설 ", "100%_진짜!", 50, null);
        verify(repository).searchDestinations(1, RegionPatterns.tokenPattern("일본"), "^", "^",
                1, "14", 0, culture.low(), culture.high(), "100%_진짜!", "%100!%!_진짜!!%", 20261018L, 50);
    }

    @Test
    void invalidSearchValuesAreAllReportedAtOnce() {
        assertThatThrownBy(() -> service.search("부산(", "맛집", "가".repeat(51), 0, null))
                .isInstanceOf(InvalidRequestException.class)
                .satisfies(e -> assertThat(((InvalidRequestException) e).getProblems())
                        .extracting(InvalidRequestException.Problem::field)
                        .containsExactly("region", "category", "query", "limit"));
        assertProblem(() -> service.search(null, null, null, 51, null), "limit", "out_of_range");
        verifyNoInteractions(repository);
    }

    @Test
    void lookupMatchesNameVariantsAndNeedsAName() {
        when(repository.lookupByName(anyString(), anyString(), anyString(), anyString(), anyInt())).thenReturn(List.of());

        service.lookup("  마니산  ");
        verify(repository).lookupByName("마니산", "마니산(%", "마니산 (%", "마니산", DestinationSearchService.LOOKUP_LIMIT);
        service.lookup("서울 석촌동   고분군");
        verify(repository).lookupByName("서울 석촌동 고분군", "서울 석촌동 고분군(%", "서울 석촌동 고분군 (%", "서울석촌동고분군", 10);

        assertProblem(() -> service.lookup(null), "name", "empty");
        assertProblem(() -> service.lookup(" "), "name", "empty");
        assertProblem(() -> service.lookup("가".repeat(101)), "name", "too_long");
    }

    // ---------- 응답 항목 ----------

    @Test
    void itemCarriesTextPolicyInputsAndCategoryLabel() {
        DestinationInfo tour = new DestinationInfo();
        tour.setId(10L);
        tour.setDestName("해운대해수욕장");
        tour.setCountry("대한민국");
        tour.setLocation("부산광역시 해운대구");
        tour.setSource(DestinationInfo.SOURCE_TOUR_API);
        tour.setContentTypeId("12");
        tour.setInfo("TYPE3-SENTINEL 원문 설명");
        tour.setSummary("부산광역시 해운대구의 관광지입니다.");
        tour.setCopyrightType("Type3");
        tour.setImageUrl("http://tong.visitkorea.or.kr/a.jpg");
        tour.setLatitude(35.1587);
        tour.setLongitude(129.1604);
        tour.setAddress("부산광역시 해운대구 우동");
        tour.setRegionCode("26");
        tour.setSigunguCode("350");

        DestinationItemDTO item = DestinationSearchService.toItem(tour);
        assertThat(item.getName()).isEqualTo("해운대해수욕장");
        assertThat(item.getCategory()).isEqualTo("관광지");
        assertThat(item.getInfo()).isEqualTo("TYPE3-SENTINEL 원문 설명");
        assertThat(item.getInfoKind()).isEqualTo("DESCRIPTION");
        assertThat(item.getCopyrightType()).isEqualTo("Type3");
        assertThat(item.getSource()).isEqualTo("TOUR_API");
        assertThat(item.getLat()).isEqualTo(35.1587);
        assertThat(item.getLng()).isEqualTo(129.1604);

        DestinationDetailDTO detail = DestinationSearchService.toDetail(tour);
        assertThat(detail.getAddress()).isEqualTo("부산광역시 해운대구 우동");
        assertThat(detail.getRegionCode()).isEqualTo("26");
        assertThat(detail.getSigunguCode()).isEqualTo("350");
        assertThat(detail.getSummary()).isEqualTo("부산광역시 해운대구의 관광지입니다.");
        assertThat(detail.getInfoKind()).isEqualTo("DESCRIPTION");

        tour.setInfo(null);
        tour.setContentTypeId("38"); // 쇼핑: 6개 분류 밖
        DestinationItemDTO summaryOnly = DestinationSearchService.toItem(tour);
        assertThat(summaryOnly.getInfo()).isEqualTo("부산광역시 해운대구의 관광지입니다.");
        assertThat(summaryOnly.getInfoKind()).isEqualTo("SUMMARY");
        assertThat(summaryOnly.getCategory()).isNull();
    }

    @Test
    void overseasCategoryIsApproximatedFromKeywords() {
        DestinationInfo museum = new DestinationInfo();
        museum.setSource(DestinationInfo.SOURCE_WIKIDATA);
        museum.setCountry("일본");
        museum.setCategory("Q33506");
        museum.refreshKeywordMask();
        assertThat(DestinationSearchService.categoryLabel(museum)).isEqualTo("문화시설");

        DestinationInfo legacy = new DestinationInfo();
        legacy.setCountry("스위스");
        legacy.setInfo("스키와 하이킹을 즐기는 곳");
        legacy.refreshKeywordMask();
        assertThat(DestinationSearchService.categoryLabel(legacy)).isEqualTo("레포츠");

        DestinationInfo plain = new DestinationInfo();
        plain.setCountry("이탈리아");
        assertThat(DestinationSearchService.categoryLabel(plain)).isEqualTo("관광지");

        DestinationInfo festival = new DestinationInfo();
        festival.setCountry("브라질");
        festival.setInfo("박물관 앞에서 열리는 축제");
        festival.refreshKeywordMask();
        assertThat(DestinationSearchService.categoryLabel(festival)).isEqualTo("축제"); // 축제가 문화시설보다 먼저
    }

    @Test
    void categoryTableMatchesTheAgentEnumAndKnownKeywords() {
        assertThat(DestinationCategory.labels()).containsExactly("관광지", "문화시설", "축제", "레포츠", "음식점", "숙박");
        assertThat(DestinationCategory.values()).extracting(DestinationCategory::contentTypeId)
                .containsExactly("12", "14", "15", "28", "39", "32");
        assertThat(DestinationCategory.ATTRACTION.overseasAll()).isTrue();
        assertThat(DestinationCategory.LODGING.overseasMask().isEmpty()).isTrue();
        // 근사에 쓰는 키워드가 모두 KeywordMask에 있어야 한다 (fromKeywords는 모르는 키워드를 조용히 버린다)
        assertThat(Long.bitCount(DestinationCategory.LEISURE.overseasMask().low())
                + Long.bitCount(DestinationCategory.LEISURE.overseasMask().high())).isEqualTo(13);
        assertThat(Long.bitCount(DestinationCategory.CULTURE.overseasMask().low())).isEqualTo(4);
    }

    @Test
    void detailIsEmptyForUnknownId() {
        when(repository.findById(99L)).thenReturn(Optional.empty());
        assertThat(service.detail(99L)).isEmpty();
        assertThat(service.detail(null)).isEmpty();
    }

    // ---------- 도우미 ----------

    private static void assertMatches(String region, String... texts) {
        for (String text : texts) {
            assertThat(matchesAll(region, text)).as("'%s' → '%s'", region, text).isTrue();
        }
    }

    private static void assertNoMatch(String region, String... texts) {
        for (String text : texts) {
            assertThat(matchesAll(region, text)).as("'%s' ↛ '%s'", region, text).isFalse();
        }
    }

    // 쿼리처럼 단어별 패턴이 모두 맞아야 한다 (MySQL REGEXP = 글 안 어딘가에 맞으면 참 = Matcher.find)
    private static boolean matchesAll(String region, String text) {
        return RegionPatterns.patterns(region).stream().allMatch(pattern -> Pattern.compile(pattern).matcher(text).find());
    }

    private static void assertProblem(Runnable call, String field, String code) {
        assertThatThrownBy(call::run)
                .isInstanceOf(InvalidRequestException.class)
                .satisfies(e -> assertThat(((InvalidRequestException) e).getProblems())
                        .extracting(InvalidRequestException.Problem::field, InvalidRequestException.Problem::code)
                        .contains(org.assertj.core.groups.Tuple.tuple(field, code)));
    }
}
