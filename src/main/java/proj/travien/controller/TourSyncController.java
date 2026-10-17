package proj.travien.controller;

import lombok.RequiredArgsConstructor;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import proj.travien.service.TourSyncRunner;

@RestController
@RequestMapping("/api/admin/tour-sync")
@RequiredArgsConstructor
public class TourSyncController {

    private static final int MAX_OVERVIEW_LIMIT = 10_000;

    private final TourSyncRunner tourSyncRunner;

    // 관리자 전용 API: TourAPI 지역기반 목록으로 국내 여행지를 추가·갱신 (백그라운드 실행)
    @PreAuthorize("hasRole('ADMIN')")
    @PostMapping("/destinations")
    public ResponseEntity<?> syncDestinations() {
        return toResponse(tourSyncRunner.startDestinationSync());
    }

    // 관리자 전용 API: 설명문이 없는 국내 여행지의 overview를 최대 limit건 수집 (1건 = TourAPI 호출 1회)
    @PreAuthorize("hasRole('ADMIN')")
    @PostMapping("/overviews")
    public ResponseEntity<?> syncOverviews(@RequestParam(defaultValue = "900") int limit) {
        if (limit < 1 || limit > MAX_OVERVIEW_LIMIT) {
            return ResponseEntity.badRequest().body("limit은 1 이상 " + MAX_OVERVIEW_LIMIT + " 이하여야 합니다.");
        }
        return toResponse(tourSyncRunner.startOverviewSync(limit));
    }

    // 관리자 전용 API: 국내 여행지 전체의 소개(summary)와 키워드 마스크를 다시 계산 (백그라운드 실행, TourAPI 호출 없음)
    @PreAuthorize("hasRole('ADMIN')")
    @PostMapping("/summaries")
    public ResponseEntity<?> rebuildSummaries() {
        return toResponse(tourSyncRunner.startSummaryRebuild());
    }

    // 관리자 전용 API: 진행 중이거나 마지막으로 끝난 동기화 상태
    @PreAuthorize("hasRole('ADMIN')")
    @GetMapping("/status")
    public ResponseEntity<TourSyncRunner.Status> getStatus() {
        return ResponseEntity.ok(tourSyncRunner.status());
    }

    private ResponseEntity<?> toResponse(TourSyncRunner.StartResult result) {
        return switch (result) {
            case STARTED -> ResponseEntity.accepted().body(tourSyncRunner.status());
            case ALREADY_RUNNING -> ResponseEntity.status(HttpStatus.CONFLICT).body("이미 동기화가 진행 중입니다.");
            case NOT_CONFIGURED -> ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE)
                    .body("tour.api.service-key가 설정되지 않았습니다.");
        };
    }
}
