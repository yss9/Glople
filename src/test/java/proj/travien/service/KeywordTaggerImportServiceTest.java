package proj.travien.service;

import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import proj.travien.domain.DestinationInfo;
import proj.travien.repository.DestinationInfoRepository;

import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.Collection;
import java.util.List;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.assertj.core.groups.Tuple.tuple;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

// DB 없이 태거 예측 가져오기의 읽기·검사와 쓰기 규칙을 확인한다 (저장소는 목)
class KeywordTaggerImportServiceTest {

    private static final Clock CLOCK = Clock.fixed(Instant.parse("2026-10-18T06:00:00Z"), ZoneId.of("Asia/Seoul"));
    private static final String INFO = "가거도는 섬이다.";
    private static final long INFO_CRC = 2972272251L; // zlib.crc32("가거도는 섬이다.".encode())

    private final DestinationInfoRepository repository = mock(DestinationInfoRepository.class);
    private final KeywordTaggerImportService service = new KeywordTaggerImportService(repository, CLOCK, KeywordMaskMode.UNION);

    // ---------- 읽기·검사 ----------

    // predict_all.py 출력 한 줄(2026-10-18 roberta-base-v2 CPU 시험 실행 결과에서 그대로)을 고치지 않고 받는다. scores는 무시
    @Test
    void readsPredictAllJsonLinesAsIs() {
        String body = """
                {"source": "TOUR_API", "id": "127480", "keywords": ["해안", "산", "자연", "경치", "섬", "바다"], "scores": {"해안": 0.423, "산": 0.403, "자연": 0.873, "경치": 0.927, "섬": 0.353, "바다": 0.762}, "model": "roberta-base-v2", "info_crc32": 1439962482}

                {"source": "WIKIDATA", "id": "Q12431", "keywords": ["역사", "산", "자연", "경치"], "scores": {"역사": 0.542, "산": 0.686, "자연": 0.776, "경치": 0.636}, "model": "roberta-base-v2", "info_crc32": 1888951419}
                """;

        List<KeywordTaggerImportService.Prediction> predictions = service.parse(bytes("﻿" + body), null);

        assertThat(predictions).containsExactly(
                new KeywordTaggerImportService.Prediction("TOUR_API", "127480", List.of("해안", "산", "자연", "경치", "섬", "바다"),
                        "roberta-base-v2", 1439962482L),
                new KeywordTaggerImportService.Prediction("WIKIDATA", "Q12431", List.of("역사", "산", "자연", "경치"),
                        "roberta-base-v2", 1888951419L));
        verifyNoInteractions(repository);
    }

    // JSON 배열도 받는다. externalId(숫자도 됨)·infoCrc32 칸 이름, 요청 파라미터 model, 빈 keywords
    @Test
    void readsJsonArrayWithExternalIdAndModelParameter() {
        String body = """
                [{"source": " TOUR_API ", "externalId": 126273, "keywords": []},
                 {"source": "WIKIDATA", "externalId": "Q42", "keywords": ["섬"], "model": "own-model", "infoCrc32": 0}]
                """;

        List<KeywordTaggerImportService.Prediction> predictions = service.parse(bytes(body), "roberta-base-v3");

        assertThat(predictions).containsExactly(
                new KeywordTaggerImportService.Prediction("TOUR_API", "126273", List.of(), "roberta-base-v3", null),
                new KeywordTaggerImportService.Prediction("WIKIDATA", "Q42", List.of("섬"), "own-model", 0L));
    }

    // 틀린 항목이 하나라도 있으면 400 (칸마다 사유), 아무것도 읽거나 쓰지 않는다
    @Test
    void rejectsTheWholeFileWhenAnyItemIsInvalid() {
        String body = String.join("\n",
                "{\"source\":\"TOUR_API\",\"id\":\"1\",\"keywords\":[\"해변\"],\"model\":\"m\"}",
                "{\"source\":\"TOUR_API\",\"id\":\"2\",\"keywords\":[\"해변가\", 3],\"model\":\"m\"}",
                "{\"source\":\"NAVER\",\"id\":\"3\",\"keywords\":[],\"model\":\"m\"}",
                "{\"source\":\"WIKIDATA\",\"keywords\":[],\"model\":\"m\"}",
                "{\"source\":\"WIKIDATA\",\"id\":\"Q1\",\"keywords\":\"섬\",\"model\":\"m\"}",
                "{\"source\":\"TOUR_API\",\"id\":\"1\",\"keywords\":[],\"model\":\"m\"}",
                "{\"source\":\"TOUR_API\",\"id\":\"9\",\"keywords\":[],\"model\":\"m\",\"info_crc32\":-1}",
                "{\"source\":\"TOUR_API\",\"id\":\"10\",\"keywords\":[]}",
                "{\"source\":\"TOUR_API\",\"id\":\"11\",\"keywords\":[],\"model\":\"v3 final!\"}",
                "{\"source\":\"TOUR_API\",\"id\":\"12\"",
                "{\"source\":\"TOUR_API\",\"id\":\"13\",\"keywords\":[],\"model\":\"m\"} {\"extra\":1}",
                "[1]");

        assertThatThrownBy(() -> service.parse(bytes(body), null))
                .isInstanceOf(InvalidRequestException.class)
                .hasMessageContaining("아무것도 가져오지 않았습니다")
                .satisfies(e -> assertThat(((InvalidRequestException) e).getProblems())
                        .extracting(InvalidRequestException.Problem::field, InvalidRequestException.Problem::code)
                        .containsExactly(
                                // JSON으로 읽을 수 없는 줄을 먼저 모으고, 그다음 항목 순서대로
                                tuple("lines[10]", "bad_json"),
                                tuple("lines[11]", "bad_json"),
                                tuple("lines[2].keywords[0]", "unknown_keyword"),
                                tuple("lines[2].keywords[1]", "bad_format"),
                                tuple("lines[3].source", "unknown_value"),
                                tuple("lines[4].externalId", "empty"),
                                tuple("lines[5].keywords", "bad_format"),
                                tuple("lines[6]", "duplicate"),
                                tuple("lines[7].infoCrc32", "bad_format"),
                                tuple("lines[8].model", "empty"),
                                tuple("lines[9].model", "bad_format"),
                                tuple("lines[12]", "bad_format")));
        verifyNoInteractions(repository);
    }

    @Test
    void rejectsEmptyBodyBadModelParameterAndTooManyRows() {
        assertThatThrownBy(() -> service.parse(null, "m")).isInstanceOf(InvalidRequestException.class)
                .satisfies(e -> assertThat(((InvalidRequestException) e).getProblems()).extracting(InvalidRequestException.Problem::code).containsExactly("empty"));
        assertThatThrownBy(() -> service.parse(bytes(" \n "), "m")).isInstanceOf(InvalidRequestException.class);
        assertThatThrownBy(() -> service.parse(bytes("[]"), "m")).isInstanceOf(InvalidRequestException.class)
                .hasMessageContaining("가져올 예측이 없습니다");
        assertThatThrownBy(() -> service.parse(bytes("[1, 2"), "m")).isInstanceOf(InvalidRequestException.class)
                .satisfies(e -> assertThat(((InvalidRequestException) e).getProblems()).extracting(InvalidRequestException.Problem::code).containsExactly("bad_json"));
        assertThatThrownBy(() -> service.parse(bytes("{\"source\":\"TOUR_API\",\"id\":\"1\",\"keywords\":[]}"), "a/b"))
                .isInstanceOf(InvalidRequestException.class)
                .satisfies(e -> assertThat(((InvalidRequestException) e).getProblems()).extracting(InvalidRequestException.Problem::field).containsExactly("model"));

        String tooMany = String.join("\n", IntStream.rangeClosed(1, KeywordTaggerImportService.MAX_ROWS + 1)
                .mapToObj(i -> "{\"source\":\"TOUR_API\",\"id\":\"" + i + "\",\"keywords\":[]}").toList());
        assertThatThrownBy(() -> service.parse(bytes(tooMany), "m")).isInstanceOf(InvalidRequestException.class)
                .satisfies(e -> assertThat(((InvalidRequestException) e).getProblems()).extracting(InvalidRequestException.Problem::code).containsExactly("too_many"));
    }

    // 문제가 많으면 앞의 20건만 싣고 전체 개수는 문장에 남긴다
    @Test
    void reportsAtMostTwentyProblems() {
        String body = String.join("\n", IntStream.rangeClosed(1, 30)
                .mapToObj(i -> "{\"source\":\"TOUR_API\",\"id\":\"" + i + "\",\"keywords\":[\"없는키워드\"],\"model\":\"m\"}").toList());

        assertThatThrownBy(() -> service.parse(bytes(body), null))
                .isInstanceOf(InvalidRequestException.class)
                .hasMessageContaining("30곳")
                .hasMessageContaining("앞의 20건만")
                .satisfies(e -> assertThat(((InvalidRequestException) e).getProblems()).hasSize(KeywordTaggerImportService.MAX_PROBLEMS));
    }

    // ---------- 쓰기 ----------

    // 태거 열만 쓴다: 규칙 마스크는 그대로, 설명문 CRC32·모델·시각을 같이 남긴다
    @Test
    void writesOnlyTheTaggerColumnsOfMatchingRows() {
        DestinationInfo island = row(1L, "TOUR_API", "127480", INFO);
        island.refreshKeywordMask();
        KeywordMask.Mask ruleMask = new KeywordMask.Mask(island.getKeywordMaskLow(), island.getKeywordMaskHigh());
        when(repository.findAllBySourceAndExternalIdIn(eq("TOUR_API"), anyCollection())).thenReturn(List.of(island));

        KeywordTaggerImportService.ImportResult result = service.importPredictions(List.of(
                prediction("TOUR_API", "127480", List.of("섬", "경치", "자연"), INFO_CRC)), false);

        assertThat(result).isEqualTo(new KeywordTaggerImportService.ImportResult(false, 1, 1, 0, 0, 0,
                List.of("roberta-base-v3"), List.of(), List.of(), "union"));
        assertThat(island.taggerMask()).isEqualTo(KeywordMask.fromKeywords(List.of("섬", "경치", "자연")));
        assertThat(island.getTaggerModel()).isEqualTo("roberta-base-v3");
        assertThat(island.getTaggerInfoCrc32()).isEqualTo(INFO_CRC);
        assertThat(island.getTaggerUpdatedAt()).isEqualTo(LocalDateTime.of(2026, 10, 18, 15, 0)); // 서울 시각
        assertThat(new KeywordMask.Mask(island.getKeywordMaskLow(), island.getKeywordMaskHigh())).isEqualTo(ruleMask);
        verify(repository).saveAll(List.of(island));
    }

    // 없는 여행지, 설명문이 바뀌었거나 없는 행, 이미 같은 예측이 있는 행은 쓰지 않고 센다. 출처마다 따로 찾는다
    @Test
    void countsMissingStaleAndUnchangedRows() {
        DestinationInfo changedText = row(1L, "TOUR_API", "1", "바뀐 설명문");
        DestinationInfo noText = row(2L, "WIKIDATA", "Q2", null);
        DestinationInfo already = row(3L, "WIKIDATA", "Q3", INFO);
        already.applyTaggerPrediction(KeywordMask.fromKeywords(List.of("섬")), "roberta-base-v3", INFO_CRC, LocalDateTime.of(2026, 1, 1, 0, 0));
        DestinationInfo noCrcGiven = row(4L, "WIKIDATA", "Q4", INFO);
        when(repository.findAllBySourceAndExternalIdIn(eq("TOUR_API"), anyCollection())).thenReturn(List.of(changedText));
        when(repository.findAllBySourceAndExternalIdIn(eq("WIKIDATA"), anyCollection())).thenReturn(List.of(noText, already, noCrcGiven));

        KeywordTaggerImportService.ImportResult result = service.importPredictions(List.of(
                prediction("TOUR_API", "1", List.of("해변"), INFO_CRC),
                prediction("TOUR_API", "404", List.of("해변"), INFO_CRC),
                prediction("WIKIDATA", "Q2", List.of("섬"), INFO_CRC),
                prediction("WIKIDATA", "Q3", List.of("섬"), INFO_CRC),
                prediction("WIKIDATA", "Q4", List.of("섬"), null)), false);

        assertThat(result.received()).isEqualTo(5);
        assertThat(result.updated()).isEqualTo(1);
        assertThat(result.unchanged()).isEqualTo(1);
        assertThat(result.notFound()).isEqualTo(1);
        assertThat(result.staleDescription()).isEqualTo(2);
        assertThat(result.notFoundSamples()).containsExactly("TOUR_API:404");
        assertThat(result.staleSamples()).containsExactly("TOUR_API:1", "WIKIDATA:Q2");
        assertThat(changedText.getTaggerModel()).isNull();
        assertThat(noText.getTaggerModel()).isNull();
        assertThat(already.getTaggerUpdatedAt()).isEqualTo(LocalDateTime.of(2026, 1, 1, 0, 0)); // 그대로
        // infoCrc32가 없으면 지금 설명문 기준으로 저장
        assertThat(noCrcGiven.getTaggerInfoCrc32()).isEqualTo(INFO_CRC);
        verify(repository).saveAll(List.of(noCrcGiven));
    }

    // dryRun은 세기만 한다
    @Test
    void dryRunCountsWithoutWriting() {
        DestinationInfo island = row(1L, "TOUR_API", "127480", INFO);
        when(repository.findAllBySourceAndExternalIdIn(eq("TOUR_API"), anyCollection())).thenReturn(List.of(island));

        KeywordTaggerImportService.ImportResult result = service.importPredictions(List.of(
                prediction("TOUR_API", "127480", List.of("섬"), INFO_CRC)), true);

        assertThat(result.dryRun()).isTrue();
        assertThat(result.updated()).isEqualTo(1);
        assertThat(island.getTaggerModel()).isNull();
        verify(repository, never()).saveAll(any());
    }

    // 같은 외부 id 행이 둘이면 둘 다 쓰고, 찾기는 500개씩 나눠 한다 (IN 목록이 너무 길어지지 않게)
    @Test
    @SuppressWarnings("unchecked")
    void appliesToDuplicateRowsAndLooksUpInBatches() {
        DestinationInfo first = row(1L, "TOUR_API", "7", INFO);
        DestinationInfo second = row(2L, "TOUR_API", "7", INFO);
        when(repository.findAllBySourceAndExternalIdIn(eq("TOUR_API"), anyCollection())).thenReturn(List.of(first, second));
        List<KeywordTaggerImportService.Prediction> predictions = new ArrayList<>();
        predictions.add(prediction("TOUR_API", "7", List.of("섬"), INFO_CRC));
        for (int i = 0; i < 1100; i++) {
            predictions.add(prediction("TOUR_API", "n" + i, List.of(), INFO_CRC));
        }

        KeywordTaggerImportService.ImportResult result = service.importPredictions(predictions, false);

        assertThat(result.updated()).isEqualTo(2);
        assertThat(result.notFound()).isEqualTo(1100);
        assertThat(result.notFoundSamples()).hasSize(KeywordTaggerImportService.MAX_SAMPLES);
        ArgumentCaptor<Collection<String>> ids = ArgumentCaptor.forClass(Collection.class);
        verify(repository, times(3)).findAllBySourceAndExternalIdIn(eq("TOUR_API"), ids.capture());
        assertThat(ids.getAllValues()).extracting(Collection::size).containsExactly(500, 500, 101);
        assertThat(first.getTaggerModel()).isEqualTo("roberta-base-v3");
        assertThat(second.getTaggerModel()).isEqualTo("roberta-base-v3");
    }

    private static KeywordTaggerImportService.Prediction prediction(String source, String id, List<String> keywords, Long crc) {
        return new KeywordTaggerImportService.Prediction(source, id, keywords, "roberta-base-v3", crc);
    }

    private static DestinationInfo row(long id, String source, String externalId, String info) {
        DestinationInfo row = new DestinationInfo();
        row.setId(id);
        row.setSource(source);
        row.setExternalId(externalId);
        row.setInfo(info);
        return row;
    }

    private static byte[] bytes(String text) {
        return text.getBytes(StandardCharsets.UTF_8);
    }
}
