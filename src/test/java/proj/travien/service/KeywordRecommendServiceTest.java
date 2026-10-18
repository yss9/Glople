package proj.travien.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import proj.travien.domain.DestinationInfo;
import proj.travien.dto.KeywordCandidate;
import proj.travien.dto.KeywordRequest;
import proj.travien.dto.KeywordResultDTO;
import proj.travien.repository.DestinationInfoRepository;

import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.List;
import java.util.Map;
import java.util.function.Function;
import java.util.stream.Collectors;
import java.util.stream.LongStream;
import java.util.zip.CRC32;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doReturn;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class KeywordRecommendServiceTest {

    // 2026-10-18 15:30 UTC = 2026-10-19 00:30 서울 → 기본 seed 20261019 (서버 시간대와 무관하게 서울 날짜)
    private static final Clock CLOCK = Clock.fixed(Instant.parse("2026-10-18T15:30:00Z"), ZoneOffset.UTC);
    private static final long TODAY_SEED = 20261019L;

    private final DestinationInfoRepository repository = mock(DestinationInfoRepository.class);
    private final KeywordRecommendService service = new KeywordRecommendService(repository, CLOCK);

    // ---------- 동점 정렬 ----------

    // 예전에는 동점이면 id 오름차순이라 언제나 '가'로 시작하는 국내 관광지(작은 id)만 나왔다.
    @Test
    void tiesAreOrderedByCrc32OfIdAndSeedNotById() {
        // 일치 수·설명문·이미지가 모두 같은 21곳 (나라를 모두 다르게 해 지역 상한이 끼어들지 않게 함). 쿼리가 id 순으로 줘도 결과는 해시 순.
        List<KeywordCandidate> tied = LongStream.rangeClosed(1, 21)
                .mapToObj(id -> candidate(id, "나라" + id, "도시", null, 1, false, false))
                .toList();
        stubCandidates(tied);

        List<Long> ids = ids(service.search(request(List.of("해안"), null, null, null)));

        List<Long> byId = LongStream.rangeClosed(1, 21).boxed().toList();
        List<Long> byHash = byId.stream()
                .sorted(Comparator.comparingLong((Long id) -> DestinationOrdering.tieHash(id, TODAY_SEED)))
                .toList();
        assertThat(ids).hasSize(21).isNotEqualTo(byId).isEqualTo(byHash);

        // 날마다(seed마다) 첫 자리가 바뀐다. 예전에는 언제나 가장 작은 id(1)가 첫 자리였다.
        List<Long> firstPerDay = LongStream.rangeClosed(20261018, 20261024)
                .mapToObj(seed -> ids(service.search(request(List.of("해안"), null, null, seed))).get(0))
                .toList();
        assertThat(firstPerDay.stream().distinct().count()).isGreaterThan(1);
    }

    @Test
    void sameSeedGivesSameOrderAndAnotherSeedShufflesTies() {
        List<KeywordCandidate> tied = LongStream.rangeClosed(1, 21)
                .mapToObj(id -> candidate(id, "나라" + id, "도시", null, 2, true, true))
                .toList();
        stubCandidates(tied);

        List<Long> first = ids(service.search(request(List.of("해안"), null, null, 7L)));
        List<Long> again = ids(service.search(request(List.of("해안"), null, null, 7L)));
        List<Long> otherSeed = ids(service.search(request(List.of("해안"), null, null, 8L)));

        assertThat(again).isEqualTo(first);
        assertThat(otherSeed).containsExactlyInAnyOrderElementsOf(first).isNotEqualTo(first);
    }

    @Test
    void descriptionThenImageWinTiesBeforeTheShuffle() {
        stubCandidates(List.of(
                candidate(1, "가나", "도시", null, 3, false, false),
                candidate(2, "다라", "도시", null, 3, false, true),
                candidate(3, "마바", "도시", null, 3, true, false),
                candidate(4, "사아", "도시", null, 3, true, true),
                candidate(5, "자차", "도시", null, 4, false, false)));

        // 일치 수가 많은 5번이 먼저, 그다음 설명문+이미지, 설명문, 이미지, 둘 다 없음
        assertThat(ids(service.search(request(List.of("해안"), null, null, null)))).containsExactly(5L, 4L, 3L, 2L, 1L);
    }

    // Java 쪽 해시는 MySQL CRC32와 같은 값이어야 한다: MySQL 문서의 SELECT CRC32('MySQL') = 3259397556
    @Test
    void tieHashIsMySqlCrc32OfIdColonSeed() {
        CRC32 crc = new CRC32();
        crc.update("MySQL".getBytes(StandardCharsets.UTF_8));
        assertThat(crc.getValue()).isEqualTo(3259397556L);

        CRC32 expected = new CRC32();
        expected.update("17:20261018".getBytes(StandardCharsets.UTF_8));
        assertThat(DestinationOrdering.tieHash(17, 20261018)).isEqualTo(expected.getValue());
        assertThat(DestinationOrdering.tieHash(17, -5)).isEqualTo(crc32("17:-5"));
    }

    @Test
    void defaultSeedIsTodayInSeoul() {
        stubCandidates(List.of(candidate(1, "대한민국", "부산광역시 해운대구", DestinationInfo.SOURCE_TOUR_API, 1, true, true)));

        service.searchByKeywords(List.of("해안"));

        KeywordMask.Mask mask = KeywordMask.fromKeywords(List.of("해안"));
        verify(repository).findKeywordCandidates(mask.low(), mask.high(), "tagger", "all", TODAY_SEED, 84);
        assertThat(DestinationOrdering.defaultSeed(Clock.fixed(Instant.parse("2026-10-18T14:59:59Z"), ZoneOffset.UTC)))
                .isEqualTo(20261018L);
    }

    // ---------- 요청 옵션 ----------

    @Test
    void optionsAreValidatedAndPassedToTheQuery() {
        stubCandidates(List.of());
        KeywordMask.Mask mask = KeywordMask.fromKeywords(List.of("섬"));

        service.search(request(List.of("섬"), " Domestic ", 5, 42L));
        verify(repository).findKeywordCandidates(mask.low(), mask.high(), "tagger", "domestic", 42L, 20);

        service.search(request(List.of("섬"), "overseas", 50, 1L));
        verify(repository).findKeywordCandidates(mask.low(), mask.high(), "tagger", "overseas", 1L, KeywordRecommendService.MAX_POOL);

        assertThatThrownBy(() -> service.search(request(List.of("섬"), "korea", null, null)))
                .isInstanceOf(InvalidRequestException.class)
                .satisfies(e -> assertThat(((InvalidRequestException) e).getProblems())
                        .extracting(InvalidRequestException.Problem::field).containsExactly("scope"));
        assertThatThrownBy(() -> service.search(request(List.of("섬"), null, 0, null)))
                .isInstanceOf(InvalidRequestException.class);
        assertThatThrownBy(() -> service.search(request(List.of("섬"), "all", 51, null)))
                .isInstanceOf(InvalidRequestException.class)
                .satisfies(e -> assertThat(((InvalidRequestException) e).getProblems())
                        .extracting(InvalidRequestException.Problem::code).containsExactly("out_of_range"));
    }

    // ---------- 마스크 모드 (규칙 ∪ 태거 / 태거만 / 규칙만) ----------

    // 기본은 union. 설정 값은 대소문자·공백을 무시하고, 모르는 값은 서버 시작 단계에서 멈추게 예외
    @Test
    void maskModeComesFromThePropertyAndIsPassedToTheQuery() {
        stubCandidates(List.of());
        KeywordMask.Mask mask = KeywordMask.fromKeywords(List.of("섬"));

        new KeywordRecommendService(repository, CLOCK, KeywordMaskMode.TAGGER).search(request(List.of("섬"), null, null, 3L));
        verify(repository).findKeywordCandidates(mask.low(), mask.high(), "tagger", "all", 3L, 84);
        new KeywordRecommendService(repository, CLOCK, KeywordMaskMode.RULE).search(request(List.of("섬"), null, null, 3L));
        verify(repository).findKeywordCandidates(mask.low(), mask.high(), "rule", "all", 3L, 84);

        assertThat(KeywordMaskMode.parse(null)).isEqualTo(KeywordMaskMode.TAGGER);
        assertThat(KeywordMaskMode.parse(" ")).isEqualTo(KeywordMaskMode.TAGGER);
        assertThat(KeywordMaskMode.parse(" Tagger ")).isEqualTo(KeywordMaskMode.TAGGER);
        assertThat(KeywordMaskMode.parse("RULE")).isEqualTo(KeywordMaskMode.RULE);
        assertThat(KeywordMaskMode.DEFAULT_VALUE).isEqualTo("tagger");
        assertThat(KeywordMaskMode.PROPERTY).isEqualTo("keyword.recommend.mask-mode");
        assertThatThrownBy(() -> new KeywordRecommendService(repository, "model-only"))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("keyword.recommend.mask-mode")
                .hasMessageContaining("union, tagger, rule");
    }

    @Test
    void limitCutsTheResult() {
        stubCandidates(LongStream.rangeClosed(1, 30)
                .mapToObj(id -> candidate(id, "나라" + id, "도시", null, 1, false, false))
                .toList());

        assertThat(service.search(request(List.of("해안"), null, 7, null))).hasSize(7);
        assertThat(service.search(request(List.of("해안"), null, null, null))).hasSize(KeywordRecommendService.DEFAULT_LIMIT);
    }

    @Test
    void skipsQueryWhenNoSupportedKeywordWasSelected() {
        assertThat(service.searchByKeywords(List.of("알 수 없는 키워드"))).isEmpty();
        assertThat(service.searchByKeywords(null)).isEmpty();
        verifyNoInteractions(repository);
    }

    // ---------- 지역 상한 ----------

    @Test
    void oneRegionCannotTakeAllSlotsWhenOtherRegionsExist() {
        // 순위 1~20위가 모두 서울(국내 시도), 그 아래에 부산·제주·일본·프랑스가 5곳씩. 상한 max(2, ⌈21/4⌉) = 6
        List<KeywordCandidate> ranked = new ArrayList<>();
        for (long id = 1; id <= 20; id++) {
            ranked.add(candidate(id, "대한민국", "서울특별시 종로구", DestinationInfo.SOURCE_TOUR_API, 9, true, true));
        }
        String[][] others = {
                {"대한민국", "부산광역시 해운대구", DestinationInfo.SOURCE_TOUR_API},
                {"대한민국", "제주특별자치도 제주시", DestinationInfo.SOURCE_TOUR_API},
                {"일본", "일본 교토부", DestinationInfo.SOURCE_WIKIDATA},
                {"프랑스", "프랑스 파리", DestinationInfo.SOURCE_WIKIDATA}};
        long id = 21;
        for (String[] region : others) {
            for (int i = 0; i < 5; i++) {
                ranked.add(candidate(id++, region[0], region[1], region[2], 1, true, true));
            }
        }

        List<KeywordCandidate> picked = KeywordRecommendService.pickWithRegionCap(sorted(ranked, 1L), 21);

        Map<String, Long> perRegion = picked.stream().collect(Collectors.groupingBy(
                c -> DestinationOrdering.regionKey(c.getSource(), c.getCountry(), c.getLocation()), Collectors.counting()));
        assertThat(KeywordRecommendService.regionCap(21)).isEqualTo(6);
        assertThat(picked).hasSize(21);
        assertThat(perRegion.get("서울특별시")).isEqualTo(6L); // 점수가 높아도 6곳까지
        assertThat(perRegion.values()).allSatisfy(count -> assertThat(count).isLessThanOrEqualTo(6L));
        assertThat(perRegion).containsKeys("부산광역시", "제주특별자치도", "일본", "프랑스");
        // 결과는 원래 순위 순서: 서울 6곳이 먼저
        assertThat(picked.subList(0, 6)).allSatisfy(c -> assertThat(c.getLocation()).startsWith("서울특별시"));
    }

    @Test
    void leftoverSlotsAreFilledInRankOrderWhenRegionsRunOut() {
        // 서울 10곳 + 부산 1곳뿐인데 limit 8 → 다른 지역이 모자라 남은 자리는 상한에 걸린 서울 후보로 순위대로 채움
        List<KeywordCandidate> ranked = new ArrayList<>();
        for (long id = 1; id <= 10; id++) {
            ranked.add(candidate(id, "대한민국", "서울특별시 중구", DestinationInfo.SOURCE_TOUR_API, 20 - id, false, false));
        }
        ranked.add(candidate(11, "대한민국", "부산광역시 중구", DestinationInfo.SOURCE_TOUR_API, 1, false, false));

        List<KeywordCandidate> picked = KeywordRecommendService.pickWithRegionCap(ranked, 8);

        assertThat(KeywordRecommendService.regionCap(8)).isEqualTo(2);
        // 상한 2: 서울 1·2, 부산 11 → 남은 5자리는 서울 3·4·5·6·7. 결과는 원래 순위 순서
        assertThat(picked).extracting(KeywordCandidate::getId).containsExactly(1L, 2L, 3L, 4L, 5L, 6L, 7L, 11L);
    }

    @Test
    void regionKeyIsSidoForDomesticAndCountryForOverseas() {
        assertThat(DestinationOrdering.regionKey("TOUR_API", "대한민국", "부산광역시 해운대구")).isEqualTo("부산광역시");
        assertThat(DestinationOrdering.regionKey("TOUR_API", "대한민국", "세종특별자치시")).isEqualTo("세종특별자치시");
        assertThat(DestinationOrdering.regionKey("TOUR_API", "대한민국", null)).isEqualTo("대한민국");
        assertThat(DestinationOrdering.regionKey("WIKIDATA", "일본", "일본 교토부")).isEqualTo("일본");
        assertThat(DestinationOrdering.regionKey(null, "프랑스", "파리")).isEqualTo("프랑스");
        assertThat(DestinationOrdering.regionKey(null, null, "파리 시내")).isEqualTo("파리");
        assertThat(DestinationOrdering.regionKey(null, null, null)).isEmpty();
    }

    // ---------- 응답 모양 ----------

    // 고른 행만 전체 값을 읽고(findAllById), 응답은 순위 순서. info는 예전 쿼리처럼 COALESCE(info, summary)
    @Test
    void mapsChosenRowsInRankOrderWithAttributionFields() {
        stubCandidates(List.of(
                candidate(10, "대한민국", "부산광역시 해운대구", DestinationInfo.SOURCE_TOUR_API, 2, false, true),
                candidate(5, "프랑스", "프랑스 파리", DestinationInfo.SOURCE_WIKIDATA, 1, true, true),
                candidate(1, "일본", "교토", null, 1, true, false)));

        DestinationInfo tour = destination(10L, "대한민국", "부산광역시 해운대구", "해운대해수욕장");
        tour.setSummary("부산광역시 해운대구의 관광지입니다.");
        tour.setSource(DestinationInfo.SOURCE_TOUR_API);
        tour.setCopyrightType("Type3");
        tour.setImageUrl("http://tong.visitkorea.or.kr/a.jpg");
        tour.setContentTypeId("12");
        DestinationInfo wiki = destination(5L, "프랑스", "프랑스 파리", "에펠탑");
        wiki.setInfo("에펠탑은 파리에 있는 탑이다.");
        wiki.setSummary("프랑스 파리의 탑입니다.");
        wiki.setSource(DestinationInfo.SOURCE_WIKIDATA);
        wiki.setSourceUrl("https://ko.wikipedia.org/wiki/에펠탑");
        wiki.setLicense(WikiDestinationSyncService.LICENSE);
        wiki.setCountryCode("FR");
        DestinationInfo legacy = destination(1L, "일본", "교토", "기요미즈데라");
        legacy.setInfo("사원과 경치");
        doReturn(List.of(legacy, wiki, tour)).when(repository).findAllById(any()); // 저장소 순서는 상관없다

        List<KeywordResultDTO> result = service.searchByKeywords(List.of("해변"));

        assertThat(result).extracting(KeywordResultDTO::getId).containsExactly(10L, 5L, 1L);
        KeywordResultDTO tourDto = result.get(0);
        assertThat(tourDto.getInfo()).isEqualTo("부산광역시 해운대구의 관광지입니다.");
        assertThat(tourDto.getInfoKind()).isEqualTo(KeywordResultDTO.INFO_KIND_SUMMARY);
        assertThat(tourDto.getSource()).isEqualTo("TOUR_API");
        assertThat(tourDto.getCopyrightType()).isEqualTo("Type3");
        assertThat(tourDto.getImageUrl()).isEqualTo("http://tong.visitkorea.or.kr/a.jpg");
        assertThat(tourDto.getContentTypeId()).isEqualTo("12");

        KeywordResultDTO wikiDto = result.get(1);
        assertThat(wikiDto.getInfo()).isEqualTo("에펠탑은 파리에 있는 탑이다.");
        assertThat(wikiDto.getInfoKind()).isEqualTo(KeywordResultDTO.INFO_KIND_DESCRIPTION);
        assertThat(wikiDto.getSourceUrl()).isEqualTo("https://ko.wikipedia.org/wiki/에펠탑");
        assertThat(wikiDto.getLicense()).isEqualTo("CC BY-SA 4.0 (한국어 위키백과), CC0 (Wikidata)");
        assertThat(wikiDto.getCountryCode()).isEqualTo("FR");

        // 기존 여행지는 출처 값이 모두 null (화면에 출처를 표시하지 않음)
        KeywordResultDTO old = result.get(2);
        assertThat(old.getInfo()).isEqualTo("사원과 경치");
        assertThat(old.getInfoKind()).isEqualTo(KeywordResultDTO.INFO_KIND_DESCRIPTION);
        assertThat(old.getSource()).isNull();
        assertThat(old.getCopyrightType()).isNull();
        assertThat(old.getSourceUrl()).isNull();
        assertThat(old.getLicense()).isNull();
        assertThat(old.getImageUrl()).isNull();
    }

    // 화면(keyword.js → personal.js, SourceAttribution)이 읽던 JSON 이름은 그대로이고, 새 값은 더하기만 했다.
    @Test
    void keepsTheJsonNamesTheFrontendReads() {
        KeywordResultDTO dto = new KeywordResultDTO();
        dto.setId(3L);
        dto.setDestName("해운대해수욕장");
        dto.setInfo("해운대 설명문");
        dto.setSource(DestinationInfo.SOURCE_TOUR_API);
        dto.setCopyrightType("Type1");
        dto.setInfoKind(KeywordResultDTO.INFO_KIND_DESCRIPTION);

        JsonNode json = new ObjectMapper().valueToTree(dto);

        assertThat(json.get("destName").asText()).isEqualTo("해운대해수욕장");
        assertThat(json.get("info").asText()).isEqualTo("해운대 설명문");
        assertThat(json.get("source").asText()).isEqualTo("TOUR_API");
        assertThat(json.get("copyrightType").asText()).isEqualTo("Type1");
        assertThat(json.get("infoKind").asText()).isEqualTo("DESCRIPTION");
        assertThat(json.has("sourceUrl")).isTrue();
        assertThat(json.get("sourceUrl").isNull()).isTrue();
        List<String> names = new ArrayList<>();
        json.fieldNames().forEachRemaining(names::add);
        assertThat(names).containsExactlyInAnyOrder("id", "country", "location", "destName", "info", "source",
                "copyrightType", "sourceUrl", "license", "infoKind", "imageUrl", "countryCode", "contentTypeId");
    }

    // ---------- 도우미 ----------

    private void stubCandidates(List<KeywordCandidate> candidates) {
        when(repository.findKeywordCandidates(anyLong(), anyLong(), anyString(), anyString(), anyLong(), anyInt())).thenReturn(candidates);
        when(repository.findAllById(any())).thenAnswer(invocation -> {
            Collection<Long> ids = invocation.getArgument(0);
            Map<Long, KeywordCandidate> byId = candidates.stream()
                    .collect(Collectors.toMap(KeywordCandidate::getId, Function.identity()));
            return ids.stream().map(id -> destination(id, byId.get(id).getCountry(), byId.get(id).getLocation(), "곳" + id)).toList();
        });
    }

    private static List<KeywordCandidate> sorted(List<KeywordCandidate> candidates, long seed) {
        List<KeywordCandidate> copy = new ArrayList<>(candidates);
        copy.sort(KeywordRecommendService.rankOrder(seed));
        return copy;
    }

    private static List<Long> ids(List<KeywordResultDTO> results) {
        return results.stream().map(KeywordResultDTO::getId).toList();
    }

    private static long crc32(String value) {
        CRC32 crc = new CRC32();
        crc.update(value.getBytes(StandardCharsets.UTF_8));
        return crc.getValue();
    }

    private static KeywordRequest request(List<String> keywords, String scope, Integer limit, Long seed) {
        KeywordRequest request = new KeywordRequest();
        request.setKeywords(keywords);
        request.setScope(scope);
        request.setLimit(limit);
        request.setSeed(seed);
        return request;
    }

    private static DestinationInfo destination(Long id, String country, String location, String name) {
        DestinationInfo destination = new DestinationInfo();
        destination.setId(id);
        destination.setCountry(country);
        destination.setLocation(location);
        destination.setDestName(name);
        return destination;
    }

    private static KeywordCandidate candidate(long id, String country, String location, String source,
                                              long score, boolean hasInfo, boolean hasImage) {
        return new KeywordCandidate() {
            public Long getId() { return id; }
            public String getCountry() { return country; }
            public String getLocation() { return location; }
            public String getSource() { return source; }
            public Long getScore() { return score; }
            public Long getHasInfo() { return hasInfo ? 1L : 0L; }
            public Long getHasImage() { return hasImage ? 1L : 0L; }
        };
    }
}
