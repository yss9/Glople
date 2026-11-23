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
 * 해외 여행지(위키데이터) 유형 코드표: 수집할 P31(instance of) 유형과 유형별 추천 키워드.
 *
 * <p>클래스패스의 {@code wiki/type-keywords.csv}를 처음 쓸 때 한 번만 읽는다. 형식은
 * {@code qid,ko_label,en_label,keywords}이고, 키워드는 {@link KeywordMask}가 지원하는 값만 '|'로 이어 적는다(없으면 빈 칸).
 * ko_label은 비워 둘 수 있다(위키데이터에 쓸 만한 한국어 레이블이 없는 유형). 그런 유형은 수집·키워드에는 쓰고 요약문 대표 유형으로는 쓰지 않는다.
 * 쉼표가 들어간 이름은 큰따옴표로 감싸고, '#'으로 시작하는 줄은 주석으로 건너뛴다.
 * 형식이 틀린 행, 중복 QID, 지원하지 않는 키워드가 있으면 조용히 건너뛰지 않고 바로 예외를 던진다.
 *
 * <p>이 파일의 QID 목록이 곧 위키데이터 수집 대상 유형(SPARQL VALUES)이고, 행 순서는 요약문에 쓰는
 * 대표 유형의 우선순위다. 자세한 내용은 docs/overseas-wiki-sync.md의 '유형 → 키워드 코드표'를 참고한다.
 */
public final class WikiTypeKeywords {

    static final String RESOURCE = "wiki/type-keywords.csv";
    private static final String HEADER = "qid,ko_label,en_label,keywords";
    private static final int COLUMNS = 4;
    private static final String QID_PATTERN = "Q[1-9][0-9]*";

    private WikiTypeKeywords() {
    }

    /**
     * @param qid      위키데이터 유형 QID (예: Q33506)
     * @param koLabel  요약문에 쓰는 한국어 유형명 (예: 박물관). 한국어 이름이 없는 유형은 빈 문자열
     * @param enLabel  영어 유형명 (확인용)
     * @param keywords 이 유형에 붙는 추천 키워드
     * @param mask     keywords로 만든 키워드 마스크
     */
    public record Type(
            String qid,
            String koLabel,
            String enLabel,
            List<String> keywords,
            KeywordMask.Mask mask
    ) {
    }

    public static Optional<Type> find(String qid) {
        if (qid == null) {
            return Optional.empty();
        }
        return Optional.ofNullable(Holder.BY_QID.get(qid.trim()));
    }

    // 파일에 적힌 순서 그대로의 유형 QID 목록 (= 수집 대상 유형)
    public static List<String> qids() {
        return List.copyOf(Holder.BY_QID.keySet());
    }

    public static Collection<Type> all() {
        return Holder.BY_QID.values();
    }

    /**
     * destination_info.category('|'로 이은 QID)에 들어 있는 유형들의 키워드 마스크를 합친다.
     * 코드표에 없는 QID나 키워드가 없는 유형은 건너뛴다.
     */
    public static KeywordMask.Mask keywordMask(String category) {
        KeywordMask.Mask mask = KeywordMask.fromKeywords(List.of());
        for (String qid : split(category)) {
            Type type = Holder.BY_QID.get(qid);
            if (type != null) {
                mask = mask.union(type.mask());
            }
        }
        return mask;
    }

    /**
     * 여행지의 P31 값 중 코드표에 있는 유형만 코드표 순서대로 '|'로 잇는다. 하나도 없으면 null.
     * 컬럼 길이(maxLength)를 넘으면 QID 중간에서 자르지 않고 앞쪽 QID까지만 남긴다.
     */
    public static String categoryOf(Collection<String> instanceOfQids, int maxLength) {
        if (instanceOfQids == null || instanceOfQids.isEmpty()) {
            return null;
        }
        Set<String> wanted = new HashSet<>(instanceOfQids);
        StringBuilder category = new StringBuilder();
        for (String qid : Holder.BY_QID.keySet()) {
            if (!wanted.contains(qid)) {
                continue;
            }
            int extra = (category.isEmpty() ? 0 : 1) + qid.length();
            if (category.length() + extra > maxLength) {
                break;
            }
            if (!category.isEmpty()) {
                category.append('|');
            }
            category.append(qid);
        }
        return category.isEmpty() ? null : category.toString();
    }

    // category에 든 유형 중 한국어 유형명이 있고 코드표에서 가장 앞에 있는 유형 (요약문의 대표 유형). 코드표 순서를 바꾸면 대표 유형도 바뀐다.
    public static Optional<Type> primaryType(String category) {
        return primaryType(category, Set.of());
    }

    // excluded에 든 유형은 대표 유형으로 고르지 않는다. (세계유산 문장을 따로 붙일 때 '유네스코 세계유산' 유형을 빼는 용도)
    public static Optional<Type> primaryType(String category, Collection<String> excluded) {
        Set<String> qids = new HashSet<>(split(category));
        qids.removeAll(excluded);
        return Holder.BY_QID.values().stream()
                .filter(type -> qids.contains(type.qid()) && !type.koLabel().isEmpty())
                .findFirst();
    }

    private static List<String> split(String category) {
        if (category == null || category.isBlank()) {
            return List.of();
        }
        List<String> qids = new ArrayList<>();
        for (String raw : category.split("\\|")) {
            String qid = raw.trim();
            if (!qid.isEmpty()) {
                qids.add(qid);
            }
        }
        return qids;
    }

    // 처음 쓸 때 한 번만 읽는다. 읽다 실패하면 이 클래스를 쓰는 곳에서 예외가 난다.
    private static final class Holder {
        private static final Map<String, Type> BY_QID = load();

        private static Map<String, Type> load() {
            InputStream input = WikiTypeKeywords.class.getClassLoader().getResourceAsStream(RESOURCE);
            if (input == null) {
                throw new IllegalStateException("유형 코드표를 찾을 수 없습니다: " + RESOURCE);
            }
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8))) {
                return parse(reader.lines().toList());
            } catch (IOException e) {
                throw new UncheckedIOException("유형 코드표를 읽지 못했습니다: " + RESOURCE, e);
            }
        }
    }

    static Map<String, Type> parse(List<String> lines) {
        Map<String, Type> byQid = new LinkedHashMap<>();
        boolean headerSeen = false;
        for (int i = 0; i < lines.size(); i++) {
            String line = i == 0 ? TourCategories.stripBom(lines.get(i)) : lines.get(i);
            if (line.isBlank() || line.trim().startsWith("#")) {
                continue;
            }
            int lineNo = i + 1;
            if (!headerSeen) {
                if (!HEADER.equals(line.trim())) {
                    throw new IllegalStateException(RESOURCE + " 주석이 아닌 첫 줄은 '" + HEADER + "'여야 합니다.");
                }
                headerSeen = true;
                continue;
            }
            Type type = parseRow(line, lineNo);
            if (byQid.putIfAbsent(type.qid(), type) != null) {
                throw invalid(lineNo, "QID가 중복되었습니다: " + type.qid());
            }
        }
        if (!headerSeen) {
            throw new IllegalStateException(RESOURCE + " 첫 줄은 '" + HEADER + "'여야 합니다.");
        }
        if (byQid.isEmpty()) {
            // 수집 대상 유형이 하나도 없으면 SPARQL VALUES가 비어 아무것도 모을 수 없다.
            throw new IllegalStateException(RESOURCE + "에 유형이 하나도 없습니다.");
        }
        return Collections.unmodifiableMap(byQid);
    }

    private static Type parseRow(String line, int lineNo) {
        List<String> fields = TourCategories.splitCsvLine(RESOURCE, line, lineNo);
        if (fields.size() != COLUMNS) {
            throw invalid(lineNo, "열이 " + COLUMNS + "개여야 하는데 " + fields.size() + "개입니다.");
        }

        String qid = fields.get(0).trim();
        if (!qid.matches(QID_PATTERN)) {
            throw invalid(lineNo, "위키데이터 QID 형식이 아닙니다: '" + qid + "'");
        }
        if (fields.get(2).isBlank()) {
            throw invalid(lineNo, "영어 유형명(en_label)이 비어 있습니다.");
        }

        List<String> keywords = parseKeywords(fields.get(3), lineNo);
        return new Type(qid, fields.get(1).trim(), fields.get(2).trim(), keywords, KeywordMask.fromKeywords(keywords));
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

    private static IllegalStateException invalid(int lineNo, String message) {
        return new IllegalStateException(RESOURCE + " " + lineNo + "번째 줄: " + message);
    }
}
