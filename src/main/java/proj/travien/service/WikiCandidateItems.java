package proj.travien.service;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.UncheckedIOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;

/**
 * 해외 여행지 후보 항목 목록: 클래스패스의 {@code wiki/candidate-items.txt}에 한 줄에 하나씩 적은 위키데이터 QID.
 *
 * <p>한국어 위키백과 문서·좌표가 있고 P31이 유형 코드표에 있거나 유네스코 세계유산인 항목을 미리 모아 둔 것이다.
 * 위키데이터 쿼리 서비스에서 이 조건을 전 세계로 한 번에 훑는 쿼리는 60초 제한에 걸려서, 목록 동기화는 이 QID를 나눠 넘겨
 * 지금 값(국가·사이트링크 수·조건)을 다시 확인한다. 만든 방법과 다시 만드는 방법은 docs/overseas-wiki-sync.md의 '후보 목록'.
 *
 * <p>처음 쓸 때 한 번만 읽는다. '#'으로 시작하는 줄은 주석이고, QID 형식이 아니거나 중복된 줄이 있으면 바로 예외를 던진다.
 */
public final class WikiCandidateItems {

    static final String RESOURCE = "wiki/candidate-items.txt";
    private static final String QID_PATTERN = "Q[1-9][0-9]*";

    private WikiCandidateItems() {
    }

    // 파일에 적힌 순서 그대로의 후보 QID 목록
    public static List<String> qids() {
        return Holder.QIDS;
    }

    private static final class Holder {
        private static final List<String> QIDS = load();

        private static List<String> load() {
            InputStream input = WikiCandidateItems.class.getClassLoader().getResourceAsStream(RESOURCE);
            if (input == null) {
                throw new IllegalStateException("후보 목록을 찾을 수 없습니다: " + RESOURCE);
            }
            try (BufferedReader reader = new BufferedReader(new InputStreamReader(input, StandardCharsets.UTF_8))) {
                return parse(reader.lines().toList());
            } catch (IOException e) {
                throw new UncheckedIOException("후보 목록을 읽지 못했습니다: " + RESOURCE, e);
            }
        }
    }

    static List<String> parse(List<String> lines) {
        List<String> qids = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (int i = 0; i < lines.size(); i++) {
            String line = (i == 0 ? TourCategories.stripBom(lines.get(i)) : lines.get(i)).trim();
            if (line.isEmpty() || line.startsWith("#")) {
                continue;
            }
            if (!line.matches(QID_PATTERN)) {
                throw new IllegalStateException(RESOURCE + " " + (i + 1) + "번째 줄: 위키데이터 QID 형식이 아닙니다: '" + line + "'");
            }
            if (!seen.add(line)) {
                throw new IllegalStateException(RESOURCE + " " + (i + 1) + "번째 줄: QID가 중복되었습니다: " + line);
            }
            qids.add(line);
        }
        if (qids.isEmpty()) {
            throw new IllegalStateException(RESOURCE + "에 항목이 하나도 없습니다.");
        }
        return List.copyOf(qids);
    }
}
