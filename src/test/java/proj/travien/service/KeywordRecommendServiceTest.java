package proj.travien.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import proj.travien.domain.DestinationInfo;
import proj.travien.dto.KeywordResultDTO;
import proj.travien.dto.KeywordResultProjection;
import proj.travien.repository.DestinationInfoRepository;

import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class KeywordRecommendServiceTest {

    private final DestinationInfoRepository repository = mock(DestinationInfoRepository.class);
    private final KeywordRecommendService service = new KeywordRecommendService(repository);

    @Test
    void delegatesFilteringAndRankingToBitmaskQuery() {
        KeywordMask.Mask mask = KeywordMask.fromKeywords(List.of("해안", "문화"));
        KeywordResultProjection projection = projection(3L, "대한민국", "부산", "감천문화마을", "해안과 문화");
        when(repository.findTop21ByKeywordMask(mask.low(), mask.high()))
                .thenReturn(List.of(projection));

        List<KeywordResultDTO> result = service.searchByKeywords(List.of("해안", "문화"));

        assertThat(result).hasSize(1);
        assertThat(result.get(0).getId()).isEqualTo(3L);
        assertThat(result.get(0).getDestName()).isEqualTo("감천문화마을");
        verify(repository).findTop21ByKeywordMask(mask.low(), mask.high());
    }

    @Test
    void skipsQueryWhenNoSupportedKeywordWasSelected() {
        assertThat(service.searchByKeywords(List.of("알 수 없는 키워드"))).isEmpty();
        verifyNoInteractions(repository);
    }

    // 화면에 출처를 표시할 수 있도록 출처 값을 그대로 넘기고, 쿼리가 정한 순서는 바꾸지 않는다.
    @Test
    void passesSourceAttributionFieldsThroughInQueryOrder() {
        KeywordMask.Mask mask = KeywordMask.fromKeywords(List.of("해변"));
        KeywordResultProjection tourSummary = projection(
                10L, "대한민국", "부산광역시 해운대구", "해운대해수욕장",
                "부산광역시 해운대구의 관광지입니다.",
                DestinationInfo.SOURCE_TOUR_API, "Type3", null, null, KeywordResultDTO.INFO_KIND_SUMMARY);
        KeywordResultProjection wiki = projection(
                5L, "프랑스", "파리", "에펠탑", "에펠탑은 파리에 있는 탑이다.",
                DestinationInfo.SOURCE_WIKIDATA, null, "https://ko.wikipedia.org/wiki/에펠탑",
                WikiDestinationSyncService.LICENSE, KeywordResultDTO.INFO_KIND_DESCRIPTION);
        KeywordResultProjection legacy = projection(1L, "일본", "교토", "기요미즈데라", "사원과 경치");
        when(repository.findTop21ByKeywordMask(mask.low(), mask.high()))
                .thenReturn(List.of(tourSummary, wiki, legacy));

        List<KeywordResultDTO> result = service.searchByKeywords(List.of("해변"));

        assertThat(result).extracting(KeywordResultDTO::getId).containsExactly(10L, 5L, 1L);

        KeywordResultDTO tour = result.get(0);
        assertThat(tour.getInfo()).isEqualTo("부산광역시 해운대구의 관광지입니다.");
        assertThat(tour.getSource()).isEqualTo("TOUR_API");
        assertThat(tour.getCopyrightType()).isEqualTo("Type3");
        assertThat(tour.getSourceUrl()).isNull();
        assertThat(tour.getLicense()).isNull();
        assertThat(tour.getInfoKind()).isEqualTo("SUMMARY");

        KeywordResultDTO wikidata = result.get(1);
        assertThat(wikidata.getSource()).isEqualTo("WIKIDATA");
        assertThat(wikidata.getCopyrightType()).isNull();
        assertThat(wikidata.getSourceUrl()).isEqualTo("https://ko.wikipedia.org/wiki/에펠탑");
        assertThat(wikidata.getLicense()).isEqualTo("CC BY-SA 4.0 (한국어 위키백과), CC0 (Wikidata)");
        assertThat(wikidata.getInfoKind()).isEqualTo("DESCRIPTION");

        // 기존 여행지는 출처 값이 모두 null (화면에 출처를 표시하지 않음)
        KeywordResultDTO old = result.get(2);
        assertThat(old.getInfo()).isEqualTo("사원과 경치");
        assertThat(old.getSource()).isNull();
        assertThat(old.getCopyrightType()).isNull();
        assertThat(old.getSourceUrl()).isNull();
        assertThat(old.getLicense()).isNull();
        assertThat(old.getInfoKind()).isNull();
    }

    // 화면(SourceAttribution)이 읽는 JSON 이름: source, copyrightType, sourceUrl, license, infoKind
    @Test
    void serializesAttributionFieldsWithNamesTheFrontendReads() {
        KeywordResultDTO dto = new KeywordResultDTO();
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
        assertThat(json.has("license")).isTrue();
    }

    private KeywordResultProjection projection(
            Long id,
            String country,
            String location,
            String destName,
            String info
    ) {
        return projection(id, country, location, destName, info, null, null, null, null, null);
    }

    private KeywordResultProjection projection(
            Long id,
            String country,
            String location,
            String destName,
            String info,
            String source,
            String copyrightType,
            String sourceUrl,
            String license,
            String infoKind
    ) {
        return new KeywordResultProjection() {
            public Long getId() { return id; }
            public String getCountry() { return country; }
            public String getLocation() { return location; }
            public String getDestName() { return destName; }
            public String getInfo() { return info; }
            public String getSource() { return source; }
            public String getCopyrightType() { return copyrightType; }
            public String getSourceUrl() { return sourceUrl; }
            public String getLicense() { return license; }
            public String getInfoKind() { return infoKind; }
        };
    }
}
