package proj.travien.controller;

import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import proj.travien.service.KeywordTaggerImportService;
import proj.travien.service.TourSyncRunner;

import java.util.List;

/**
 * 키워드 마스크 관리자 API. 키워드 태거(training/keyword_tagger) 예측을 여행지의 태거 마스크로 가져온다.
 * 규칙 마스크는 그대로 두고, 추천이 둘을 어떻게 쓸지는 설정 keyword.recommend.mask-mode(union | tagger | rule)가 정한다.
 * 실행 순서와 응답은 docs/keyword-tagger-serving.md.
 */
@RestController
@RequestMapping("/api/admin/keyword-masks")
@RequiredArgsConstructor
public class KeywordMaskController {

    private final KeywordTaggerImportService keywordTaggerImportService;
    private final TourSyncRunner tourSyncRunner;

    // 관리자 전용 API: predict_all.py 출력(JSON Lines) 또는 같은 항목의 JSON 배열을 가져온다. dryRun=true면 세기만 한다.
    // 형식이 틀린 항목이 있으면 400(아무것도 쓰지 않음), 다른 동기화가 도는 중이면 409.
    @PreAuthorize("hasRole('ADMIN')")
    @PostMapping(value = "/import",
            consumes = {MediaType.APPLICATION_NDJSON_VALUE, MediaType.APPLICATION_JSON_VALUE, MediaType.TEXT_PLAIN_VALUE})
    public ResponseEntity<?> importPredictions(
            @RequestBody(required = false) byte[] body,
            @RequestParam(value = "model", required = false) String model,
            @RequestParam(value = "dryRun", defaultValue = "false") boolean dryRun,
            HttpServletRequest request
    ) {
        List<KeywordTaggerImportService.Prediction> predictions = keywordTaggerImportService.parse(body, model);
        return tourSyncRunner.runExclusively(() -> keywordTaggerImportService.importPredictions(predictions, dryRun))
                .<ResponseEntity<?>>map(ResponseEntity::ok)
                .orElseGet(() -> ApiErrorAdvice.body(HttpStatus.CONFLICT,
                        "다른 동기화가 진행 중입니다. 끝난 뒤 다시 보내 주세요. (GET /api/admin/tour-sync/status)", request, List.of()));
    }
}
