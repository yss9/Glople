package proj.travien.controller;

import io.swagger.v3.oas.annotations.Operation;
import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;
import proj.travien.dto.DestinationItemDTO;
import proj.travien.service.DestinationSearchService;

import java.util.List;

/**
 * 여행지 읽기 API. 모두 공개(SecurityConfig 화이트리스트)이고 저장된 여행지를 읽기만 한다.
 * 화면 상세·주변 여행지와 에이전트 도구(search_destinations, get_destination_detail)가 같이 쓴다.
 * 규칙은 {@link DestinationSearchService}, 틀린 값은 400 JSON({@link ApiErrorAdvice}).
 */
@RestController
@RequestMapping("/api/destinations")
public class DestinationController {

    private final DestinationSearchService destinationSearchService;

    public DestinationController(DestinationSearchService destinationSearchService) {
        this.destinationSearchService = destinationSearchService;
    }

    @Operation(summary = "여행지 목록 (지역·분류·검색어, 모두 선택)")
    @GetMapping
    public List<DestinationItemDTO> search(@RequestParam(value = "region", required = false) String region,
                                           @RequestParam(value = "category", required = false) String category,
                                           @RequestParam(value = "query", required = false) String query,
                                           @RequestParam(value = "limit", required = false) Integer limit,
                                           @RequestParam(value = "seed", required = false) Long seed) {
        return destinationSearchService.search(region, category, query, limit, seed);
    }

    @Operation(summary = "이름으로 여행지 찾기 (같은 이름이 여러 곳이면 모두)")
    @GetMapping("/lookup")
    public List<DestinationItemDTO> lookup(@RequestParam(value = "name", required = false) String name) {
        return destinationSearchService.lookup(name);
    }

    @Operation(summary = "여행지 상세")
    @GetMapping("/{id}")
    public ResponseEntity<?> detail(@PathVariable("id") Long id, HttpServletRequest request) {
        return destinationSearchService.detail(id)
                .<ResponseEntity<?>>map(ResponseEntity::ok)
                .orElseGet(() -> ApiErrorAdvice.body(HttpStatus.NOT_FOUND, "여행지를 찾을 수 없습니다.", request, List.of()));
    }
}
