package proj.travien.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import proj.travien.domain.DestinationInfo;
import proj.travien.repository.DestinationInfoRepository;

import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeSet;
import java.util.regex.Pattern;

/**
 * 키워드 태거 예측 가져오기 (POST /api/admin/keyword-masks/import, docs/keyword-tagger-serving.md).
 *
 * <p>본문은 JSON 배열이나 JSON Lines(한 줄에 객체 하나)다. 항목 하나는
 * {@code {"source": "TOUR_API"|"WIKIDATA", "externalId"(또는 "id"): "...", "keywords": [...], "model": "...", "infoCrc32"(또는 "info_crc32"): 123}}이고,
 * training/keyword_tagger/scripts/predict_all.py 출력을 그대로 받는다. scores 같은 다른 칸은 무시한다.
 * <ul>
 *   <li>model은 항목마다 적거나 요청 파라미터로 한 번 준다(항목 값이 먼저). 형식은 영문·숫자·{@code . _ -} 100자 이내.</li>
 *   <li>keywords는 {@link KeywordMask} 목록의 값이어야 한다(철자 그대로). 빈 배열은 "맞는 키워드 없음"이라는 예측으로 저장한다.</li>
 *   <li>infoCrc32는 예측에 쓴 설명문의 CRC32다. 지금 설명문의 CRC32와 다르면(설명문이 바뀜) 그 행은 가져오지 않는다. 없으면 지금 설명문 기준으로 저장한다.</li>
 * </ul>
 * 형식이 틀린 항목이 하나라도 있으면 아무것도 쓰지 않고 400(틀린 칸 목록)이다. 형식이 맞으면 (source, external_id)가 같은 여행지 행의
 * 태거 열(tagger_*)만 덮어쓴다. 새 여행지 행을 만들지 않고, 규칙 마스크는 바꾸지 않는다. 한 번에 한 트랜잭션이다.
 */
@Service
public class KeywordTaggerImportService {

    static final int MAX_ROWS = 20_000;
    static final int MAX_PROBLEMS = 20;
    static final int MAX_SAMPLES = 10;
    static final int LOOKUP_BATCH = 500;
    static final int MAX_EXTERNAL_ID_LENGTH = 255;
    static final Pattern MODEL_PATTERN = Pattern.compile("[A-Za-z0-9._-]{1,100}");
    static final Set<String> SOURCES = Set.of(DestinationInfo.SOURCE_TOUR_API, DestinationInfo.SOURCE_WIKIDATA);
    private static final long MAX_CRC32 = 0xFFFFFFFFL;

    // 한 줄에 객체가 둘 붙어 있는 것처럼 값 뒤에 글자가 더 있으면 오류로 본다.
    private static final ObjectMapper MAPPER = new ObjectMapper().enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS);

    /**
     * @param infoCrc32 예측에 쓴 설명문의 CRC32. null이면 지금 설명문 기준
     */
    public record Prediction(String source, String externalId, List<String> keywords, String model, Long infoCrc32) {
    }

    /**
     * @param received         받은 항목 수
     * @param updated          태거 예측을 쓴(dryRun이면 쓸) 여행지 행 수
     * @param unchanged        같은 모델·키워드·설명문으로 이미 저장돼 있어 그대로 둔 행 수
     * @param notFound         (source, externalId)가 같은 행이 없는 항목 수
     * @param staleDescription 설명문이 없어졌거나 예측에 쓴 설명문과 달라 가져오지 않은 행 수
     */
    public record ImportResult(
            boolean dryRun,
            int received,
            int updated,
            int unchanged,
            int notFound,
            int staleDescription,
            List<String> models,
            List<String> notFoundSamples,
            List<String> staleSamples,
            String recommendMaskMode
    ) {
    }

    private final DestinationInfoRepository repository;
    private final Clock clock;
    private final KeywordMaskMode recommendMaskMode;

    @Autowired
    public KeywordTaggerImportService(
            DestinationInfoRepository repository,
            @Value("${" + KeywordMaskMode.PROPERTY + ":" + KeywordMaskMode.DEFAULT_VALUE + "}") String recommendMaskMode
    ) {
        // 시각은 다른 수집 시각 열(overview_synced_at 등)처럼 서버 기본 시간대
        this(repository, Clock.systemDefaultZone(), KeywordMaskMode.parse(recommendMaskMode));
    }

    KeywordTaggerImportService(DestinationInfoRepository repository, Clock clock, KeywordMaskMode recommendMaskMode) {
        this.repository = repository;
        this.clock = clock;
        this.recommendMaskMode = recommendMaskMode;
    }

    // ---------- 읽기·검사 (DB를 보지 않는다) ----------

    /**
     * 본문 → 예측 목록. 틀린 곳이 있으면 {@link InvalidRequestException}(400, 앞의 {@value #MAX_PROBLEMS}건).
     * 칸 이름(field)은 JSON 배열이면 {@code rows[0].keywords}(0부터), JSON Lines면 {@code lines[3].keywords}(줄 번호, 1부터)다.
     */
    public List<Prediction> parse(byte[] body, String defaultModel) {
        List<InvalidRequestException.Problem> problems = new ArrayList<>();
        String text = body == null ? "" : new String(body, StandardCharsets.UTF_8);
        if (text.startsWith("﻿")) {
            text = text.substring(1);
        }
        if (text.isBlank()) {
            throw new InvalidRequestException("body", "empty", "가져올 예측이 없습니다. 본문에 JSON 배열이나 JSON Lines를 넣어 주세요.");
        }
        String model = defaultModel == null || defaultModel.isBlank() ? null : defaultModel.trim();
        if (model != null && !MODEL_PATTERN.matcher(model).matches()) {
            throw new InvalidRequestException("model", "bad_format", "model은 영문·숫자·'.'·'_'·'-' 100자 이내여야 합니다.");
        }

        Map<String, JsonNode> items = readItems(text, problems);
        if (items.isEmpty() && problems.isEmpty()) {
            throw new InvalidRequestException("body", "empty", "가져올 예측이 없습니다. 본문에 JSON 배열이나 JSON Lines를 넣어 주세요.");
        }
        if (items.size() > MAX_ROWS) {
            throw new InvalidRequestException("body", "too_many",
                    "한 번에 " + MAX_ROWS + "개까지 가져올 수 있습니다. 받은 항목: " + items.size() + "개");
        }

        List<Prediction> predictions = new ArrayList<>();
        Set<String> seen = new HashSet<>();
        for (Map.Entry<String, JsonNode> entry : items.entrySet()) {
            Prediction prediction = toPrediction(entry.getKey(), entry.getValue(), model, problems);
            if (prediction == null) {
                continue;
            }
            if (!seen.add(prediction.source() + "\n" + prediction.externalId())) {
                problems.add(new InvalidRequestException.Problem(entry.getKey(), "duplicate",
                        "같은 여행지(" + prediction.source() + ":" + prediction.externalId() + ")가 두 번 나옵니다."));
                continue;
            }
            predictions.add(prediction);
        }

        if (!problems.isEmpty()) {
            int total = problems.size();
            String message = "태거 예측 " + total + "곳이 올바르지 않아 아무것도 가져오지 않았습니다."
                    + (total > MAX_PROBLEMS ? " (앞의 " + MAX_PROBLEMS + "건만 보임)" : "");
            throw new InvalidRequestException(message, problems.subList(0, Math.min(total, MAX_PROBLEMS)));
        }
        return predictions;
    }

    // 항목 위치(rows[i] 또는 lines[n]) → JSON 값. JSON으로 읽을 수 없는 곳은 problems에 넣는다.
    private static Map<String, JsonNode> readItems(String text, List<InvalidRequestException.Problem> problems) {
        Map<String, JsonNode> items = new LinkedHashMap<>();
        if (text.stripLeading().startsWith("[")) {
            try {
                JsonNode array = MAPPER.readTree(text);
                for (int i = 0; i < array.size(); i++) {
                    items.put("rows[" + i + "]", array.get(i));
                }
            } catch (JsonProcessingException e) {
                problems.add(new InvalidRequestException.Problem("body", "bad_json", "JSON 배열로 읽을 수 없습니다: " + firstLine(e)));
            }
            return items;
        }
        String[] lines = text.split("\r?\n", -1);
        for (int i = 0; i < lines.length; i++) {
            if (lines[i].isBlank()) {
                continue;
            }
            String where = "lines[" + (i + 1) + "]";
            try {
                items.put(where, MAPPER.readTree(lines[i]));
            } catch (JsonProcessingException e) {
                problems.add(new InvalidRequestException.Problem(where, "bad_json", "JSON으로 읽을 수 없습니다: " + firstLine(e)));
            }
        }
        return items;
    }

    private static Prediction toPrediction(String where, JsonNode item, String defaultModel,
                                           List<InvalidRequestException.Problem> problems) {
        if (item == null || !item.isObject()) {
            problems.add(new InvalidRequestException.Problem(where, "bad_format", "항목은 JSON 객체여야 합니다."));
            return null;
        }
        int before = problems.size();

        String source = text(item.get("source"));
        if (source == null || source.isBlank()) {
            problems.add(new InvalidRequestException.Problem(where + ".source", "empty", "source가 필요합니다 (TOUR_API 또는 WIKIDATA)."));
        } else if (!SOURCES.contains(source.trim())) {
            problems.add(new InvalidRequestException.Problem(where + ".source", "unknown_value",
                    "source는 TOUR_API, WIKIDATA 중 하나여야 합니다: '" + source + "'"));
        }

        JsonNode idNode = first(item, "externalId", "external_id", "id");
        String externalId = idNode != null && idNode.isIntegralNumber() ? idNode.asText() : text(idNode);
        if (externalId == null || externalId.isBlank()) {
            problems.add(new InvalidRequestException.Problem(where + ".externalId", "empty", "externalId(또는 id)가 필요합니다."));
        } else if (externalId.trim().length() > MAX_EXTERNAL_ID_LENGTH) {
            problems.add(new InvalidRequestException.Problem(where + ".externalId", "too_long",
                    "externalId는 " + MAX_EXTERNAL_ID_LENGTH + "자 이내여야 합니다."));
        }

        List<String> keywords = new ArrayList<>();
        JsonNode keywordsNode = item.get("keywords");
        if (keywordsNode == null || !keywordsNode.isArray()) {
            problems.add(new InvalidRequestException.Problem(where + ".keywords", "bad_format", "keywords는 문자열 배열이어야 합니다 (없으면 [])."));
        } else {
            for (int i = 0; i < keywordsNode.size(); i++) {
                JsonNode keyword = keywordsNode.get(i);
                if (!keyword.isTextual()) {
                    problems.add(new InvalidRequestException.Problem(where + ".keywords[" + i + "]", "bad_format", "키워드는 문자열이어야 합니다."));
                } else if (!KeywordMask.isSupported(keyword.asText())) {
                    problems.add(new InvalidRequestException.Problem(where + ".keywords[" + i + "]", "unknown_keyword",
                            "서버 키워드 목록(KeywordMask)에 없는 키워드입니다: '" + keyword.asText() + "'"));
                } else {
                    keywords.add(keyword.asText());
                }
            }
        }

        String model = text(item.get("model"));
        if (model == null || model.isBlank()) {
            model = defaultModel;
        }
        if (model == null) {
            problems.add(new InvalidRequestException.Problem(where + ".model", "empty", "model 칸이나 model 요청 파라미터가 필요합니다."));
        } else if (!MODEL_PATTERN.matcher(model.trim()).matches()) {
            problems.add(new InvalidRequestException.Problem(where + ".model", "bad_format", "model은 영문·숫자·'.'·'_'·'-' 100자 이내여야 합니다."));
        }

        Long infoCrc32 = null;
        JsonNode crcNode = first(item, "infoCrc32", "info_crc32");
        if (crcNode != null && !crcNode.isNull()) {
            if (!crcNode.isIntegralNumber() || !crcNode.canConvertToLong()
                    || crcNode.asLong() < 0 || crcNode.asLong() > MAX_CRC32) {
                problems.add(new InvalidRequestException.Problem(where + ".infoCrc32", "bad_format", "infoCrc32는 0~4294967295 정수여야 합니다."));
            } else {
                infoCrc32 = crcNode.asLong();
            }
        }

        if (problems.size() > before) {
            return null;
        }
        return new Prediction(source.trim(), externalId.trim(), List.copyOf(keywords), model.trim(), infoCrc32);
    }

    // ---------- 쓰기 ----------

    /**
     * 예측을 (source, external_id)가 같은 여행지 행의 태거 열에 쓴다. dryRun이면 세기만 하고 쓰지 않는다.
     * 다른 동기화와 동시에 돌지 않도록 TourSyncRunner.runExclusively 안에서 부른다(KeywordMaskController).
     */
    @Transactional
    public ImportResult importPredictions(List<Prediction> predictions, boolean dryRun) {
        Map<String, List<Prediction>> bySource = new LinkedHashMap<>();
        for (Prediction prediction : predictions) {
            bySource.computeIfAbsent(prediction.source(), key -> new ArrayList<>()).add(prediction);
        }

        LocalDateTime now = LocalDateTime.now(clock);
        int updated = 0;
        int unchanged = 0;
        int notFound = 0;
        int stale = 0;
        List<String> notFoundSamples = new ArrayList<>();
        List<String> staleSamples = new ArrayList<>();
        Set<String> models = new TreeSet<>();
        List<DestinationInfo> changed = new ArrayList<>();

        for (Map.Entry<String, List<Prediction>> entry : bySource.entrySet()) {
            String source = entry.getKey();
            List<Prediction> list = entry.getValue();
            for (int start = 0; start < list.size(); start += LOOKUP_BATCH) {
                List<Prediction> chunk = list.subList(start, Math.min(start + LOOKUP_BATCH, list.size()));
                Map<String, List<DestinationInfo>> rows = new HashMap<>();
                for (DestinationInfo row : repository.findAllBySourceAndExternalIdIn(
                        source, chunk.stream().map(Prediction::externalId).toList())) {
                    rows.computeIfAbsent(row.getExternalId(), key -> new ArrayList<>()).add(row);
                }

                for (Prediction prediction : chunk) {
                    List<DestinationInfo> matches = rows.get(prediction.externalId());
                    if (matches == null) {
                        notFound++;
                        addSample(notFoundSamples, source, prediction.externalId());
                        continue;
                    }
                    KeywordMask.Mask mask = KeywordMask.fromKeywords(prediction.keywords());
                    for (DestinationInfo destination : matches) {
                        Long current = DestinationInfo.infoCrc32(destination.getInfo());
                        if (current == null || (prediction.infoCrc32() != null && !prediction.infoCrc32().equals(current))) {
                            stale++;
                            addSample(staleSamples, source, prediction.externalId());
                            continue;
                        }
                        models.add(prediction.model());
                        if (prediction.model().equals(destination.getTaggerModel())
                                && mask.equals(destination.taggerMask())
                                && current.equals(destination.getTaggerInfoCrc32())) {
                            unchanged++;
                            continue;
                        }
                        updated++;
                        if (!dryRun) {
                            destination.applyTaggerPrediction(mask, prediction.model(), current, now);
                            changed.add(destination);
                        }
                    }
                }
            }
        }

        if (!changed.isEmpty()) {
            repository.saveAll(changed);
        }
        return new ImportResult(dryRun, predictions.size(), updated, unchanged, notFound, stale,
                List.copyOf(models), notFoundSamples, staleSamples, recommendMaskMode.sqlValue());
    }

    private static void addSample(List<String> samples, String source, String externalId) {
        if (samples.size() < MAX_SAMPLES) {
            samples.add(source + ":" + externalId);
        }
    }

    private static JsonNode first(JsonNode item, String... names) {
        for (String name : names) {
            JsonNode node = item.get(name);
            if (node != null && !node.isNull()) {
                return node;
            }
        }
        return null;
    }

    private static String text(JsonNode node) {
        return node != null && node.isTextual() ? node.asText() : null;
    }

    private static String firstLine(JsonProcessingException e) {
        String message = e.getOriginalMessage();
        if (message == null) {
            return e.getClass().getSimpleName();
        }
        int newline = message.indexOf('\n');
        return newline < 0 ? message : message.substring(0, newline);
    }
}
