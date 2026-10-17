package proj.travien.service;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.stream.Collectors;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class TourCategoriesTest {

    private static final String HEADER = "code,depth1,depth2,depth3,keywords";

    @Test
    void loadsEveryCodeOfTheTourApiSnapshot() throws IOException {
        // TourAPI lclsSystmCode2 (2026-10-17 기준) 246개 코드. 대분류별 개수로 빠진 코드가 없는지 확인한다.
        assertThat(TourCategories.all()).hasSize(246);
        Map<String, Long> countByGroup = TourCategories.all().stream()
                .collect(Collectors.groupingBy(TourCategoriesTest::groupOf, TreeMap::new, Collectors.counting()));
        assertThat(countByGroup).containsExactlyInAnyOrderEntriesOf(Map.of(
                "AC", 14L, "C01", 6L, "EV", 20L, "EX", 31L, "FD", 21L,
                "HS", 24L, "LS", 40L, "NA", 27L, "SH", 13L, "VE", 50L));

        // 파일에 적힌 행 수와 읽은 코드 수가 같아야 한다. (중복 코드는 읽을 때 예외)
        List<String> lines = rawLines();
        assertThat(lines.get(0)).isEqualTo(HEADER);
        assertThat(lines.stream().skip(1).filter(line -> !line.isBlank())).hasSize(246);
    }

    @Test
    void usesOnlySupportedKeywords() {
        for (TourCategories.Category category : TourCategories.all()) {
            assertThat(category.code()).matches("(AC|EV|EX|FD|HS|LS|NA|SH|VE)\\d{6}|C01\\d{6}");
            assertThat(List.of(category.depth1(), category.depth2(), category.depth3())).allSatisfy(name -> assertThat(name).isNotBlank());
            assertThat(category.keywords()).allSatisfy(keyword -> assertThat(KeywordMask.isSupported(keyword)).isTrue());
            assertThat(category.keywords()).doesNotHaveDuplicates();
            assertThat(category.mask()).isEqualTo(KeywordMask.fromKeywords(category.keywords()));
            assertThat(category.mask().isEmpty()).isEqualTo(category.keywords().isEmpty());
        }
        assertThat(TourCategories.codesWithKeywords()).hasSize(181);
    }

    @Test
    void findsNamesAndKeywordsByCode() {
        TourCategories.Category beach = TourCategories.find("NA020900").orElseThrow();
        assertThat(beach.depth1()).isEqualTo("자연관광");
        assertThat(beach.depth2()).isEqualTo("자연경관(하천‧해양)");
        assertThat(beach.depth3()).isEqualTo("해변. 해수욕장");
        assertThat(beach.keywords()).containsExactly("해변", "바다", "해안", "수영");

        // 쉼표가 들어간 분류명은 큰따옴표로 감싸 저장되어 있다.
        assertThat(TourCategories.find("FD030200").orElseThrow().depth3()).isEqualTo("피자, 햄버거, 샌드위치 및 유사음식");
        assertThat(TourCategories.find(" C01160001 ").orElseThrow().keywords()).containsExactly("캠핑", "야외 활동");

        // 키워드를 일부러 비워 둔 분류와 모르는 코드는 빈 마스크
        assertThat(TourCategories.find("HS030200").orElseThrow().keywords()).isEmpty();
        assertThat(TourCategories.keywordMask("HS030200").isEmpty()).isTrue();
        assertThat(TourCategories.keywordMask("ZZ999999").isEmpty()).isTrue();
        assertThat(TourCategories.keywordMask(null).isEmpty()).isTrue();
        assertThat(TourCategories.codesWithKeywords()).contains("NA020900").doesNotContain("HS030200");
    }

    @Test
    void parsesQuotedFields() {
        assertThat(TourCategories.splitCsvLine("A,\"b, c\",\"say \"\"hi\"\"\",,x|y", 2))
                .containsExactly("A", "b, c", "say \"hi\"", "", "x|y");
        assertThat(TourCategories.parse(List.of("﻿" + HEADER, "NA000001,자연,산,\"산, 고개\",산|자연", "")))
                .containsOnlyKeys("NA000001");
    }

    @Test
    void failsFastOnMalformedRows() {
        assertInvalid(List.of("code,name"), "첫 줄");
        assertInvalid(List.of(HEADER, "NA000001,자연,산,산"), "2번째 줄", "열이 5개");
        assertInvalid(List.of(HEADER, "NA000001,자연,산,산,산", "NA000001,자연,산,봉우리,"), "3번째 줄", "중복");
        assertInvalid(List.of(HEADER, "NA000001,자연,산,산,산|등산로"), "지원하지 않는 키워드", "등산로");
        assertInvalid(List.of(HEADER, "NA000001,자연,산,산,산||자연"), "빈 키워드");
        assertInvalid(List.of(HEADER, "NA000001,자연,산,산,산|산"), "키워드가 중복");
        assertInvalid(List.of(HEADER, "NA000001,자연, ,산,산"), "depth2");
        assertInvalid(List.of(HEADER, "na000001,자연,산,산,산"), "분류 코드 형식");
        assertInvalid(List.of(HEADER, "NA000001,자연,\"산,산,산"), "닫히지 않았습니다");
        assertInvalid(List.of(HEADER, "NA000001,자연,\"산\"x,산,산"), "닫는 큰따옴표");
    }

    private static void assertInvalid(List<String> lines, String... messageParts) {
        assertThatThrownBy(() -> TourCategories.parse(lines))
                .isInstanceOf(IllegalStateException.class)
                .satisfies(e -> assertThat(e.getMessage()).contains(messageParts));
    }

    private static String groupOf(TourCategories.Category category) {
        return category.code().startsWith("C01") ? "C01" : category.code().substring(0, 2);
    }

    private static List<String> rawLines() throws IOException {
        try (InputStream input = TourCategories.class.getClassLoader().getResourceAsStream(TourCategories.RESOURCE)) {
            assertThat(input).isNotNull();
            return Arrays.asList(new String(input.readAllBytes(), StandardCharsets.UTF_8).split("\n", -1));
        }
    }
}
