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

import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

// 해외 여행지(위키데이터·한국어 위키백과) 수집 관리자 API. 국내 수집(/api/admin/tour-sync)과 같은 실행기를 써서 동시에 하나만 돈다.
@RestController
@RequestMapping("/api/admin/wiki-sync")
@RequiredArgsConstructor
public class WikiSyncController {

    static final int MAX_DESTINATION_LIMIT = 500;
    static final int MAX_DESCRIPTION_LIMIT = 20_000;
    private static final Set<String> DOMESTIC_CODES = Set.of("KR", "KP");

    private final TourSyncRunner tourSyncRunner;

    // 관리자 전용 API: 위키데이터로 해외 여행지를 국가별 최대 limit곳씩 추가·갱신 (백그라운드 실행).
    // countries를 비우면 전체 국가, 예: countries=FR,JP
    @PreAuthorize("hasRole('ADMIN')")
    @PostMapping("/destinations")
    public ResponseEntity<?> syncDestinations(
            @RequestParam(required = false) List<String> countries,
            @RequestParam(defaultValue = "150") int limit
    ) {
        if (limit < 1 || limit > MAX_DESTINATION_LIMIT) {
            return ResponseEntity.badRequest().body("limit은 1 이상 " + MAX_DESTINATION_LIMIT + " 이하여야 합니다.");
        }

        Set<String> codes = new LinkedHashSet<>();
        if (countries != null) {
            for (String raw : countries) {
                String code = raw == null ? "" : raw.trim().toUpperCase();
                if (code.isEmpty()) {
                    continue;
                }
                if (!code.matches("[A-Z]{2}")) {
                    return ResponseEntity.badRequest().body("countries는 ISO 3166-1 alpha-2 국가 코드(예: FR,JP)여야 합니다: " + raw);
                }
                if (DOMESTIC_CODES.contains(code)) {
                    return ResponseEntity.badRequest().body("대한민국·북한은 해외 여행지 수집 대상이 아닙니다: " + code);
                }
                codes.add(code);
            }
        }
        return toResponse(tourSyncRunner.startWikiDestinationSync(new ArrayList<>(codes), limit));
    }

    // 관리자 전용 API: 설명문이 없는(또는 오래된) 해외 여행지의 한국어 위키백과 도입부를 최대 limit건 수집 (요청 1회 = 20건)
    @PreAuthorize("hasRole('ADMIN')")
    @PostMapping("/descriptions")
    public ResponseEntity<?> syncDescriptions(@RequestParam(defaultValue = "1000") int limit) {
        if (limit < 1 || limit > MAX_DESCRIPTION_LIMIT) {
            return ResponseEntity.badRequest().body("limit은 1 이상 " + MAX_DESCRIPTION_LIMIT + " 이하여야 합니다.");
        }
        return toResponse(tourSyncRunner.startWikiDescriptionSync(limit));
    }

    // 관리자 전용 API: 해외 여행지 전체의 소개와 키워드 마스크를 유형 코드표로 다시 계산 (위키미디어 호출 없음)
    @PreAuthorize("hasRole('ADMIN')")
    @PostMapping("/summaries")
    public ResponseEntity<?> rebuildSummaries() {
        return toResponse(tourSyncRunner.startWikiSummaryRebuild());
    }

    // 관리자 전용 API: 진행 중이거나 마지막으로 끝난 동기화 상태 (국내 수집과 같은 상태)
    @PreAuthorize("hasRole('ADMIN')")
    @GetMapping("/status")
    public ResponseEntity<TourSyncRunner.Status> getStatus() {
        return ResponseEntity.ok(tourSyncRunner.status());
    }

    private ResponseEntity<?> toResponse(TourSyncRunner.StartResult result) {
        return switch (result) {
            case STARTED -> ResponseEntity.accepted().body(tourSyncRunner.status());
            case ALREADY_RUNNING -> ResponseEntity.status(HttpStatus.CONFLICT).body("이미 동기화가 진행 중입니다.");
            // 위키 작업은 인증키가 없어 NOT_CONFIGURED가 나오지 않지만, switch가 모든 경우를 다루도록 둔다.
            case NOT_CONFIGURED -> ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body("동기화 설정이 없습니다.");
        };
    }
}
