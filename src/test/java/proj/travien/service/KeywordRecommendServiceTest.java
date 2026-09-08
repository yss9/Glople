package proj.travien.service;

import org.junit.jupiter.api.Test;
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

    private KeywordResultProjection projection(
            Long id,
            String country,
            String location,
            String destName,
            String info
    ) {
        return new KeywordResultProjection() {
            public Long getId() { return id; }
            public String getCountry() { return country; }
            public String getLocation() { return location; }
            public String getDestName() { return destName; }
            public String getInfo() { return info; }
        };
    }
}
