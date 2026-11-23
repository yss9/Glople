package proj.travien.repository;

import org.junit.jupiter.api.Test;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import proj.travien.service.DestinationOrdering;
import proj.travien.service.KeywordMaskMode;

import java.lang.reflect.Method;
import java.lang.reflect.Parameter;
import java.util.Arrays;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Objects;
import java.util.Set;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

import static org.assertj.core.api.Assertions.assertThat;

// DB 없이 native query 문자열만 확인한다. (실제 MySQL 실행 확인은 오너가 서버를 띄워서 한다)
class DestinationInfoRepositoryQueryTest {

    private static final Pattern NAMED_PARAMETER = Pattern.compile("(?<![:\\w]):([A-Za-z]\\w*)");

    // ---------- POST /api/search 후보 쿼리 ----------

    // 동점이면 id 오름차순이던 예전 정렬(ORDER BY 점수 DESC, d.id ASC)이 아니라 설명문 → 이미지 → CRC32(id:seed) → id
    @Test
    void candidateQueryBreaksTiesByDescriptionImageAndSeededHash() throws NoSuchMethodException {
        String query = squash(sql("findKeywordCandidates", long.class, long.class, String.class, String.class, long.class, int.class));

        assertThat(query).contains("ORDER BY score DESC,"
                + " (d.info IS NOT NULL) DESC,"
                + " (d.image_url IS NOT NULL) DESC,"
                + " CRC32(CONCAT(d.id, ':', :seed)) ASC,"
                + " d.id ASC LIMIT :poolSize");
        assertThat(query).doesNotContain("DESC, d.id ASC LIMIT 21");
        assertThat(query).doesNotContainPattern("score DESC,\\s*d\\.id ASC");
    }

    @Test
    void candidateQueryKeepsTheBitmaskFilterAndScore() throws NoSuchMethodException {
        String query = squash(sql("findKeywordCandidates", long.class, long.class, String.class, String.class, long.class, int.class));
        String low = DestinationInfoRepository.RECOMMEND_MASK_LOW_SQL;
        String high = DestinationInfoRepository.RECOMMEND_MASK_HIGH_SQL;

        // 필터와 점수는 예전과 같은 모양이고, 행의 마스크만 설정(maskMode)에 따라 정한 마스크로 바뀌었다.
        assertThat(query)
                .contains("WHERE ((" + low + " & :lowMask) <> 0 OR (" + high + " & :highMask) <> 0)")
                .contains("(BIT_COUNT(" + low + " & :lowMask) + BIT_COUNT(" + high + " & :highMask)) AS score")
                .contains("(d.info IS NOT NULL) AS hasInfo")
                .contains("(d.image_url IS NOT NULL) AS hasImage");
        // 후보 쿼리는 글 열(info, summary 본문)을 읽지 않는다. 고른 행만 findAllById로 읽는다.
        assertThat(query.substring(0, query.indexOf("FROM destination_info d"))).doesNotContain("COALESCE");
    }

    // maskMode: rule이거나 예측이 없는 행은 규칙 마스크, tagger는 태거 마스크, 그 밖(union)은 둘의 OR. 세 갈래 모두 BIGINT UNSIGNED
    @Test
    void recommendMaskChoosesRuleTaggerOrUnionPerRow() {
        for (String segment : List.of("low", "high")) {
            String expected = "(CASE WHEN :maskMode = 'rule' OR d.tagger_model IS NULL THEN CAST(d.keyword_mask_" + segment + " AS UNSIGNED)"
                    + " WHEN :maskMode = 'tagger' THEN CAST(d.tagger_mask_" + segment + " AS UNSIGNED)"
                    + " ELSE d.keyword_mask_" + segment + " | d.tagger_mask_" + segment + " END)";
            String actual = segment.equals("low") ? DestinationInfoRepository.RECOMMEND_MASK_LOW_SQL : DestinationInfoRepository.RECOMMEND_MASK_HIGH_SQL;
            assertThat(actual).isEqualTo(expected);
        }
        // :maskMode에 들어가는 값은 KeywordMaskMode의 값과 같다
        assertThat(Arrays.stream(KeywordMaskMode.values()).map(KeywordMaskMode::sqlValue)).containsExactly("union", "tagger", "rule");
    }

    // scope: all | domestic | overseas. 국내 = TourAPI 행이거나 나라가 대한민국 (NULL도 참·거짓이 정해지게 COALESCE)
    @Test
    void candidateQueryFiltersScopeWithTheSameDomesticRuleAsJava() throws NoSuchMethodException {
        String query = squash(sql("findKeywordCandidates", long.class, long.class, String.class, String.class, long.class, int.class));
        String domestic = "(COALESCE(d.source, '') = 'TOUR_API' OR COALESCE(d.country, '') = '대한민국')";

        assertThat(DestinationInfoRepository.DOMESTIC_SQL).isEqualTo(domestic);
        assertThat(query).contains("AND (:scope = 'all'"
                + " OR (:scope = 'domestic' AND " + domestic + ")"
                + " OR (:scope = 'overseas' AND NOT " + domestic + "))");
        assertThat(DestinationOrdering.DOMESTIC_COUNTRY).isEqualTo("대한민국");
    }

    // ---------- GET /api/destinations ----------

    @Test
    void searchQueryUsesGuardedOptionalFiltersAndTheSameTieBreak() throws NoSuchMethodException {
        String query = squash(sql("searchDestinations", int.class, String.class, String.class, String.class, int.class,
                String.class, int.class, long.class, long.class, String.class, String.class, long.class, int.class));

        // 쓰지 않는 조건은 켜짐 값으로 끈다 (빈 REGEXP 패턴은 MySQL 8에서 오류라 '^'를 넣고, 이 검사로 건너뛴다)
        assertThat(query)
                .contains("(:regionCount < 1 OR CONCAT_WS(' ', d.country, d.location) REGEXP :region1)")
                .contains("(:regionCount < 2 OR CONCAT_WS(' ', d.country, d.location) REGEXP :region2)")
                .contains("(:regionCount < 3 OR CONCAT_WS(' ', d.country, d.location) REGEXP :region3)")
                .contains("(:categoryOn = 0 OR (" + DestinationInfoRepository.DOMESTIC_SQL + " AND d.content_type_id = :contentTypeId)")
                .contains("OR (NOT " + DestinationInfoRepository.DOMESTIC_SQL + " AND (:overseasAll = 1")
                .contains("(:query = '' OR d.dest_name LIKE :queryLike ESCAPE '!' OR d.summary LIKE :queryLike ESCAPE '!')")
                .contains("ORDER BY (d.dest_name = :query) DESC, (d.dest_name LIKE :queryLike ESCAPE '!') DESC,"
                        + " (d.info IS NOT NULL) DESC, (d.image_url IS NOT NULL) DESC,"
                        + " CRC32(CONCAT(d.id, ':', :seed)) ASC, d.id ASC LIMIT :limit");
    }

    @Test
    void lookupQueryMatchesExactParenthesizedAndCompactNames() throws NoSuchMethodException {
        String query = squash(sql("lookupByName", String.class, String.class, String.class, String.class, int.class));

        assertThat(query)
                .contains("WHERE d.dest_name = :name OR d.dest_name LIKE :parenLike ESCAPE '!'"
                        + " OR d.dest_name LIKE :spacedParenLike ESCAPE '!' OR REPLACE(d.dest_name, ' ', '') = :compactName")
                .contains("ORDER BY (d.dest_name = :name) DESC")
                .endsWith("LIMIT :limit");
    }

    // ---------- 모든 @Query ----------

    // 쿼리의 :이름과 메서드의 @Param이 정확히 같아야 서버가 뜬다(다르면 기동 때 IllegalStateException). DB 없이 미리 잡는다.
    @Test
    void everyQueryBindsExactlyItsParameters() {
        for (Method method : DestinationInfoRepository.class.getDeclaredMethods()) {
            Query annotation = method.getAnnotation(Query.class);
            if (annotation == null) {
                continue;
            }
            Set<String> inQuery = new LinkedHashSet<>();
            Matcher matcher = NAMED_PARAMETER.matcher(annotation.value());
            while (matcher.find()) {
                inQuery.add(matcher.group(1));
            }
            Set<String> declared = Arrays.stream(method.getParameters())
                    .map(Parameter::getAnnotations)
                    .flatMap(Arrays::stream)
                    .filter(Param.class::isInstance)
                    .map(a -> ((Param) a).value())
                    .collect(Collectors.toCollection(LinkedHashSet::new));
            assertThat(inQuery).as(method.getName()).containsExactlyInAnyOrderElementsOf(declared);
            assertThat(declared).as(method.getName() + " 모든 인자에 @Param").hasSize(method.getParameterCount());
        }
    }

    // 한 쿼리 안의 작은따옴표가 짝이 맞는지 (문자열 상수를 이어 붙이다 따옴표가 빠지는 실수 방지)
    @Test
    void nativeQueriesHaveBalancedQuotesAndParentheses() {
        for (Method method : DestinationInfoRepository.class.getDeclaredMethods()) {
            Query annotation = method.getAnnotation(Query.class);
            if (annotation == null || !annotation.nativeQuery()) {
                continue;
            }
            String value = annotation.value();
            assertThat(value.chars().filter(c -> c == '\'').count() % 2).as(method.getName()).isZero();
            int depth = 0;
            for (char c : value.replaceAll("'[^']*'", "''").toCharArray()) {
                depth += c == '(' ? 1 : c == ')' ? -1 : 0;
                assertThat(depth).as(method.getName()).isGreaterThanOrEqualTo(0);
            }
            assertThat(depth).as(method.getName()).isZero();
        }
    }

    private static String sql(String name, Class<?>... parameterTypes) throws NoSuchMethodException {
        Query query = DestinationInfoRepository.class.getMethod(name, parameterTypes).getAnnotation(Query.class);
        assertThat(Objects.requireNonNull(query).nativeQuery()).isTrue();
        return query.value();
    }

    private static String squash(String sql) {
        return sql.replaceAll("\\s+", " ").trim();
    }
}
