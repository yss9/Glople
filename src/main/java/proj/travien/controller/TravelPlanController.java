package proj.travien.controller;

import io.swagger.v3.oas.annotations.Operation;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import proj.travien.domain.User;
import proj.travien.dto.TravelPlanDTO;
import proj.travien.dto.TravelPlanRequest;
import proj.travien.dto.TravelPlanSummaryDTO;
import proj.travien.jwt.CurrentUser;
import proj.travien.service.TravelPlanService;

import java.net.URI;
import java.util.List;

/**
 * 여행 일정 API. 모두 로그인 필요(비로그인 401)이고, 주인은 토큰 사용자다. 본문에 userId 칸은 없다.
 * <ul>
 *   <li>POST: 새로 저장하면 201 + Location, 같은 clientRequestId로 다시 보내면 처음 일정을 200으로. 값이 틀리면 400 JSON(errors)</li>
 *   <li>GET 목록: 내 일정만 최근 순</li>
 *   <li>GET 한 건: 주인 또는 관리자. 남의 일정·없는 일정 403</li>
 *   <li>DELETE: 주인만 204. 남의 일정·없는 일정 403</li>
 * </ul>
 */
@RestController
@RequestMapping("/api/travel-plans")
public class TravelPlanController {

    private final TravelPlanService travelPlanService;
    private final CurrentUser currentUser;

    public TravelPlanController(TravelPlanService travelPlanService, CurrentUser currentUser) {
        this.travelPlanService = travelPlanService;
        this.currentUser = currentUser;
    }

    @Operation(summary = "여행 일정 저장 (같은 clientRequestId는 한 번만 저장)")
    @PostMapping
    public ResponseEntity<TravelPlanDTO> create(@RequestBody TravelPlanRequest request) {
        User me = currentUser.requireUser();
        TravelPlanService.CreateResult result = travelPlanService.create(me, request);
        if (!result.created()) {
            return ResponseEntity.ok(result.plan());
        }
        return ResponseEntity.created(URI.create("/api/travel-plans/" + result.plan().getId())).body(result.plan());
    }

    @Operation(summary = "내 여행 일정 목록")
    @GetMapping
    public List<TravelPlanSummaryDTO> list() {
        return travelPlanService.listMine(currentUser.requireUser());
    }

    @Operation(summary = "여행 일정 한 건 (주인·관리자)")
    @GetMapping("/{id}")
    public TravelPlanDTO get(@PathVariable("id") Long id) {
        User me = currentUser.requireUser();
        return travelPlanService.get(me, currentUser.isAdmin(), id);
    }

    @Operation(summary = "여행 일정 삭제 (주인만)")
    @DeleteMapping("/{id}")
    public ResponseEntity<Void> delete(@PathVariable("id") Long id) {
        travelPlanService.delete(currentUser.requireUser(), id);
        return ResponseEntity.noContent().build();
    }
}
