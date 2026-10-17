package proj.travien.repository;

import org.junit.jupiter.api.Test;
import org.springframework.data.jpa.repository.Query;
import proj.travien.dto.KeywordResultDTO;

import static org.assertj.core.api.Assertions.assertThat;

// DB 없이 추천 쿼리 문자열만 확인한다.
class DestinationInfoRepositoryQueryTest {

    @Test
    void returnsSummaryWhenDescriptionIsMissing() throws NoSuchMethodException {
        String query = recommendQuery();

        // 실제 설명문이 있으면 설명문, 없으면 TourAPI 필드로 만든 소개
        assertThat(query).contains("COALESCE(d.info, d.summary) AS info");
        // 마스크 조건과 정렬·개수는 그대로
        assertThat(query)
                .contains("(d.keyword_mask_low & :lowMask) <> 0")
                .contains("(d.keyword_mask_high & :highMask) <> 0")
                .contains("BIT_COUNT(d.keyword_mask_low & :lowMask)")
                .contains("LIMIT 21");
    }

    // 화면에 출처를 표시할 값(출처, 저작권 유형, 문서 주소, 라이선스, 보여준 글의 종류)을 함께 고른다.
    @Test
    void selectsSourceAttributionColumns() throws NoSuchMethodException {
        String query = recommendQuery();

        assertThat(query)
                .contains("d.source AS source")
                .contains("d.copyright_type AS copyrightType")
                .contains("d.source_url AS sourceUrl")
                .contains("d.license AS license")
                .containsPattern("CASE WHEN d\\.info IS NOT NULL THEN '" + KeywordResultDTO.INFO_KIND_DESCRIPTION + "'"
                        + "\\s+WHEN d\\.summary IS NOT NULL THEN '" + KeywordResultDTO.INFO_KIND_SUMMARY + "'"
                        + "\\s+END AS infoKind");
    }

    // 출처 값은 SELECT에만 더하고, 순위(정렬)·조건·개수는 바꾸지 않는다.
    @Test
    void keepsRankingUnchanged() throws NoSuchMethodException {
        String query = recommendQuery().replaceAll("\\s+", " ");

        assertThat(query).contains("FROM destination_info d"
                + " WHERE (d.keyword_mask_low & :lowMask) <> 0"
                + " OR (d.keyword_mask_high & :highMask) <> 0"
                + " ORDER BY ( BIT_COUNT(d.keyword_mask_low & :lowMask)"
                + " + BIT_COUNT(d.keyword_mask_high & :highMask) ) DESC,"
                + " d.id ASC LIMIT 21");
        // SELECT 목록에서 쓰는 CASE가 ORDER BY 뒤로 넘어가지 않는다.
        assertThat(query.indexOf("AS infoKind")).isLessThan(query.indexOf("FROM destination_info d"));
    }

    private static String recommendQuery() throws NoSuchMethodException {
        Query query = DestinationInfoRepository.class
                .getMethod("findTop21ByKeywordMask", long.class, long.class)
                .getAnnotation(Query.class);

        assertThat(query.nativeQuery()).isTrue();
        return query.value();
    }
}
