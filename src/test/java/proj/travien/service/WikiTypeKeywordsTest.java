package proj.travien.service;

import org.junit.jupiter.api.Test;

import java.io.IOException;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class WikiTypeKeywordsTest {

    private static final String HEADER = "qid,ko_label,en_label,keywords";

    @Test
    void loadsTheVerifiedTypeTable() throws IOException {
        // 2026-10-17 클래스 집계에서 검토한 1,296개 중 포함으로 정한 589개 유형 (docs/overseas-wiki-sync.md)
        assertThat(WikiTypeKeywords.qids()).hasSize(589).doesNotHaveDuplicates();
        assertThat(rawLines().stream().filter(line -> line.startsWith("#"))).isNotEmpty();
        assertThat(rawLines().stream().filter(line -> line.startsWith("Q"))).hasSize(WikiTypeKeywords.all().size());

        WikiTypeKeywords.Type museum = WikiTypeKeywords.find(" Q33506 ").orElseThrow();
        assertThat(museum.koLabel()).isEqualTo("박물관");
        assertThat(museum.enLabel()).isEqualTo("museum");
        assertThat(museum.keywords()).containsExactly("박물관", "문화");
        assertThat(WikiTypeKeywords.find("Q46169").orElseThrow().keywords()).containsExactly("국립공원", "자연", "경치", "야외 활동");
        // 위키데이터에 한국어 레이블이 없는 유형은 ko_label이 비어 있다.
        WikiTypeKeywords.Type openAirMuseum = WikiTypeKeywords.find("Q756102").orElseThrow();
        assertThat(openAirMuseum.koLabel()).isEmpty();
        assertThat(openAirMuseum.keywords()).containsExactly("박물관", "문화");
        assertThat(WikiTypeKeywords.find("Q1").isEmpty()).isTrue();
        assertThat(WikiTypeKeywords.find(null).isEmpty()).isTrue();

        // 검토에서 뺀 유형(행정구역·정주지, 교통시설, 건물 일반, 사업체)은 수집 대상이 아니다.
        assertThat(WikiTypeKeywords.qids()).doesNotContain(
                "Q515",      // 도시
                "Q18663566", // 일본의 폐지된 시구정촌
                "Q55488",    // 철도역
                "Q1248784",  // 공항
                "Q41176",    // 건물
                "Q27686",    // 호텔
                "Q11707");   // 음식점
    }

    @Test
    void usesOnlySupportedKeywords() {
        for (WikiTypeKeywords.Type type : WikiTypeKeywords.all()) {
            assertThat(type.qid()).matches("Q[1-9][0-9]*");
            assertThat(type.koLabel()).isNotNull().isEqualTo(type.koLabel().trim());
            assertThat(type.enLabel()).isNotBlank();
            assertThat(type.keywords()).allSatisfy(keyword -> assertThat(KeywordMask.isSupported(keyword)).isTrue());
            assertThat(type.keywords()).doesNotHaveDuplicates();
            assertThat(type.mask()).isEqualTo(KeywordMask.fromKeywords(type.keywords()));
        }
        // 대부분의 유형은 한국어 유형명과 키워드가 있다. (빈 칸은 요약문·마스크에서 건너뛴다)
        assertThat(WikiTypeKeywords.all().stream().filter(type -> !type.koLabel().isEmpty())).hasSizeGreaterThan(400);
        assertThat(WikiTypeKeywords.all().stream().filter(type -> !type.keywords().isEmpty())).hasSizeGreaterThan(500);
    }

    @Test
    void unionsKeywordMasksOfEveryTypeInCategory() {
        KeywordMask.Mask mask = WikiTypeKeywords.keywordMask("Q23413|Q33506|Q999999| ");

        assertThat(mask).isEqualTo(KeywordMask.fromKeywords(List.of("성", "역사", "역사적인 장소", "박물관", "문화")));
        // 키워드가 없는 유형(교회당 Q16970)은 마스크에 아무것도 더하지 않는다.
        assertThat(WikiTypeKeywords.keywordMask("Q16970").isEmpty()).isTrue();
        assertThat(WikiTypeKeywords.keywordMask(null).isEmpty()).isTrue();
        assertThat(WikiTypeKeywords.keywordMask("Q999999").isEmpty()).isTrue();
    }

    @Test
    void buildsCategoryInTableOrderWithinLength() {
        // P31 순서와 상관없이 코드표 순서(박물관 → 정원 → 야외 박물관)로, 코드표에 없는 유형은 빼고 잇는다.
        assertThat(WikiTypeKeywords.categoryOf(List.of("Q756102", "Q1107656", "Q33506", "Q999999"), 255))
                .isEqualTo("Q33506|Q1107656|Q756102");
        assertThat(WikiTypeKeywords.categoryOf(List.of("Q999999"), 255)).isNull();
        assertThat(WikiTypeKeywords.categoryOf(List.of(), 255)).isNull();
        // 길이를 넘으면 QID 중간에서 자르지 않는다.
        assertThat(WikiTypeKeywords.categoryOf(List.of("Q756102", "Q1107656", "Q33506"), 23)).isEqualTo("Q33506|Q1107656|Q756102");
        assertThat(WikiTypeKeywords.categoryOf(List.of("Q756102", "Q1107656", "Q33506"), 22)).isEqualTo("Q33506|Q1107656");
        assertThat(WikiTypeKeywords.categoryOf(List.of("Q756102", "Q1107656", "Q33506"), 14)).isEqualTo("Q33506");

        // 대표 유형은 category 순서가 아니라 코드표 순서로 고른다. (박물관 Q33506이 성 Q23413보다 앞)
        assertThat(WikiTypeKeywords.primaryType("Q999999|Q23413|Q33506").map(WikiTypeKeywords.Type::qid)).contains("Q33506");
        // 한국어 유형명이 없는 유형(군도 island group Q1402592)은 코드표에서 앞서도 고르지 않는다.
        assertThat(WikiTypeKeywords.primaryType("Q1402592|Q23413").map(WikiTypeKeywords.Type::qid)).contains("Q23413");
        assertThat(WikiTypeKeywords.primaryType("Q1402592")).isEmpty();
        assertThat(WikiTypeKeywords.primaryType("Q9259|Q23413", List.of("Q23413")).map(WikiTypeKeywords.Type::qid)).contains("Q9259");
        assertThat(WikiTypeKeywords.primaryType(null)).isEmpty();
    }

    @Test
    void skipsCommentsAndParsesQuotedFields() {
        Map<String, WikiTypeKeywords.Type> types = WikiTypeKeywords.parse(List.of(
                "﻿# 주석",
                HEADER,
                "",
                "Q1,\"성, 요새\",\"castle, fort\",성|역사",
                "# 중간 주석",
                "Q2,광장,square,",
                "Q3, ,\"open-air museum\",박물관"));

        assertThat(types).containsOnlyKeys("Q1", "Q2", "Q3");
        assertThat(types.get("Q1").koLabel()).isEqualTo("성, 요새");
        assertThat(types.get("Q2").keywords()).isEmpty();
        assertThat(types.get("Q2").mask().isEmpty()).isTrue();
        // 한국어 유형명은 비워 둘 수 있다.
        assertThat(types.get("Q3").koLabel()).isEmpty();
        assertThat(types.get("Q3").keywords()).containsExactly("박물관");
    }

    @Test
    void failsFastOnMalformedRows() {
        assertInvalid(List.of("qid,name"), "첫 줄");
        assertInvalid(List.of("# 주석만"), "첫 줄");
        assertInvalid(List.of(HEADER), "유형이 하나도 없습니다");
        assertInvalid(List.of(HEADER, "Q1,성,castle"), "2번째 줄", "열이 4개");
        assertInvalid(List.of(HEADER, "q1,성,castle,성"), "QID 형식");
        assertInvalid(List.of(HEADER, "Q01,성,castle,성"), "QID 형식");
        assertInvalid(List.of(HEADER, "Q1,성,,성"), "en_label");
        assertInvalid(List.of(HEADER, "Q1,성,castle,성|없는키워드"), "지원하지 않는 키워드", "없는키워드");
        assertInvalid(List.of(HEADER, "Q1,성,castle,성|성"), "키워드가 중복");
        assertInvalid(List.of(HEADER, "Q1,성,castle,성||역사"), "빈 키워드");
        assertInvalid(List.of(HEADER, "Q1,성,castle,성", "Q1,요새,fort,성"), "3번째 줄", "QID가 중복");
        assertInvalid(List.of(HEADER, "Q1,\"성,castle,성"), "wiki/type-keywords.csv", "큰따옴표가 닫히지 않았습니다");
    }

    private static void assertInvalid(List<String> lines, String... messageParts) {
        assertThatThrownBy(() -> WikiTypeKeywords.parse(lines))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContainingAll(messageParts);
    }

    private static List<String> rawLines() throws IOException {
        try (InputStream input = WikiTypeKeywords.class.getClassLoader().getResourceAsStream(WikiTypeKeywords.RESOURCE)) {
            assertThat(input).isNotNull();
            return new String(input.readAllBytes(), StandardCharsets.UTF_8).lines().toList();
        }
    }
}
