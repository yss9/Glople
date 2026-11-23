package proj.travien.service;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Collections;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.Set;

/**
 * TourAPI 분류체계 코드표(lclsSystm1~3)와 분류별 추천 키워드.
 *
 * <p>클래스패스의 {@code tour/category-keywords.csv}를 처음 쓸 때 한 번만 읽는다. 파일 형식은
 * {@code code,depth1,depth2,depth3,keywords}이고, 키워드는 {@link KeywordMask}가 지원하는 값만 '|'로 이어 적는다
 * (없으면 빈 칸). 쉼표가 들어간 분류명은 큰따옴표로 감싼다. 형식이 틀린 행이나 지원하지 않는 키워드가 있으면
 * 조용히 건너뛰지 않고 바로 예외를 던진다.
 *
 * <p>코드와 분류명은 TourAPI {@code lclsSystmCode2}(분류체계 코드 조회) 2026-10-17 응답의 246개 코드 그대로다.
 * 코드표를 고치는 방법은 docs/domestic-tour-sync.md의 '분류 → 키워드 코드표'를 참고한다.
 */
public final class TourCategories {

    static final String RESOURCE = "tour/category-keywords.csv";
    private static final String HEADER = "code,depth1,depth2,depth3,keywords";
    private static final int COLUMNS = 5;

    private TourCategories() {
    }

    /**
     * @param code     분류체계 3Depth 코드 (예: NA020900)
     * @param depth1   대분류명 (예: 자연관광)
     * @param depth2   중분류명 (예: 자연경관(하천‧해양))
     * @param depth3   소분류명 (예: 해변. 해수욕장)
     * @param keywords 이 분류에 붙는 추천 키워드
     * @param mask     keywords로 만든 키워드 마스크
     */
    public record Category(
            String code,
            String depth1,
            String depth2,
            String depth3,
            List<String> keywords,
            KeywordMask.Mask mask
    ) {
    }

    public static Optional<Category> find(String code) {
        if (code == null) {
            return Optional.empty();
        }
        return Optional.ofNullable(Holder.BY_CODE.get(code.trim()));
    }

    // 모르는 코드나 키워드가 없는 분류는 빈 마스크
    public static KeywordMask.Mask keywordMask(String code) {
        return find(code).map(Category::mask).orElseGet(() -> KeywordMask.fromKeywords(List.of()));
    }

    public static Collection<Category> all() {
        return Holder.BY_CODE.values();
    }

    // 키워드가 하나 이상 정해진 분류 코드. 이 코드를 가진 행은 설명문이 없어도 키워드 마스크가 생긴다.
    public static List<String> codesWithKeywords() {
        return Holder.BY_CODE.values().stream()
                .filter(category -> !category.keywords().isEmpty())
                .map(Category::code)
                .toList();
    }

    // 처음 쓸 때 한 번만 읽는다. 읽다 실패하면 이 클래스를 쓰는 곳에서 예외가 난다.
    private static final class Holder {
        private static final Map<String, Category> BY_CODE = load();

        private static Map<String, Category> load() {
            InputStream input = TourCategories.class.getClassLoader().getResourceAsStream(RESOURCE);
            if (input == null) {
                throw new IllegalStateException("분류 코드표를 찾을 수 없습니다: " + RESOURCE);
            }
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8))) {
                return parse(reader.lines().toList());
            } catch (IOException e) {
                throw new UncheckedIOException("분류 코드표를 읽지 못했습니다: " + RESOURCE, e);
            }
        }
    }

    static Map<String, Category> parse(List<String> lines) {
        if (lines.isEmpty() || !HEADER.equals(stripBom(lines.get(0)).trim())) {
            throw new IllegalStateException(RESOURCE + " 첫 줄은 '" + HEADER + "'여야 합니다.");
        }

        Map<String, Category> byCode = new LinkedHashMap<>();
        for (int i = 1; i < lines.size(); i++) {
            String line = lines.get(i);
            if (line.isBlank()) {
                continue;
            }
            int lineNo = i + 1;
            Category category = parseRow(line, lineNo);
            if (byCode.putIfAbsent(category.code(), category) != null) {
                throw invalid(lineNo, "코드가 중복되었습니다: " + category.code());
            }
        }
        return Collections.unmodifiableMap(byCode);
    }

    private static Category parseRow(String line, int lineNo) {
        List<String> fields = splitCsvLine(line, lineNo);
        if (fields.size() != COLUMNS) {
            throw invalid(lineNo, "열이 " + COLUMNS + "개여야 하는데 " + fields.size() + "개입니다.");
        }

        String code = fields.get(0).trim();
        if (!code.matches("[A-Z][A-Z0-9]+")) {
            throw invalid(lineNo, "분류 코드 형식이 아닙니다: '" + code + "'");
        }
        for (int column = 1; column <= 3; column++) {
            if (fields.get(column).isBlank()) {
                throw invalid(lineNo, "분류명(depth" + column + ")이 비어 있습니다.");
            }
        }

        List<String> keywords = parseKeywords(fields.get(4), lineNo);
        return new Category(code, fields.get(1).trim(), fields.get(2).trim(), fields.get(3).trim(),
                keywords, KeywordMask.fromKeywords(keywords));
    }

    private static List<String> parseKeywords(String value, int lineNo) {
        if (value.isBlank()) {
            return List.of();
        }

        List<String> keywords = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (String raw : value.split("\\|", -1)) {
            String keyword = raw.trim();
            if (keyword.isEmpty()) {
                throw invalid(lineNo, "빈 키워드가 있습니다: '" + value + "'");
            }
            if (!KeywordMask.isSupported(keyword)) {
                throw invalid(lineNo, "지원하지 않는 키워드입니다: '" + keyword + "'");
            }
            if (!seen.add(keyword)) {
                throw invalid(lineNo, "키워드가 중복되었습니다: '" + keyword + "'");
            }
            keywords.add(keyword);
        }
        return List.copyOf(keywords);
    }

    // 큰따옴표로 감싼 칸(안의 쉼표, "" 이스케이프)을 지원하는 한 줄짜리 CSV 분리. 칸 안의 줄바꿈은 쓰지 않는다.
    static List<String> splitCsvLine(String line, int lineNo) {
        return splitCsvLine(RESOURCE, line, lineNo);
    }

    // 오류 메시지에 resource 파일 이름을 붙인다. (위키데이터 유형 코드표 WikiTypeKeywords도 같은 형식을 쓴다)
    static List<String> splitCsvLine(String resource, String line, int lineNo) {
        List<String> fields = new ArrayList<>();
        StringBuilder field = new StringBuilder();
        boolean quoted = false;
        boolean afterQuote = false;
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (quoted) {
                if (c == '"') {
                    if (i + 1 < line.length() && line.charAt(i + 1) == '"') {
                        field.append('"');
                        i++;
                    } else {
                        quoted = false;
                        afterQuote = true;
                    }
                } else {
                    field.append(c);
                }
            } else if (c == ',') {
                fields.add(field.toString());
                field.setLength(0);
                afterQuote = false;
            } else if (afterQuote) {
                throw invalid(resource, lineNo, "닫는 큰따옴표 뒤에 쉼표가 아닌 글자가 있습니다.");
            } else if (c == '"') {
                if (!field.isEmpty()) {
                    throw invalid(resource, lineNo, "큰따옴표는 칸의 처음에만 올 수 있습니다.");
                }
                quoted = true;
            } else {
                field.append(c);
            }
        }
        if (quoted) {
            throw invalid(resource, lineNo, "큰따옴표가 닫히지 않았습니다.");
        }
        fields.add(field.toString());
        return fields;
    }

    static String stripBom(String line) {
        return line.startsWith("﻿") ? line.substring(1) : line;
    }

    private static IllegalStateException invalid(int lineNo, String message) {
        return invalid(RESOURCE, lineNo, message);
    }

    private static IllegalStateException invalid(String resource, int lineNo, String message) {
        return new IllegalStateException(resource + " " + lineNo + "번째 줄: " + message);
    }
}
