package proj.travien.controller;


import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.annotation.*;
import proj.travien.domain.MyRoute;
import proj.travien.domain.User;
import proj.travien.domain.UserInfo;
import proj.travien.dto.KeywordRequest;
import proj.travien.dto.KeywordResultDTO;
import proj.travien.dto.MyRouteDTO;
import proj.travien.jwt.CurrentUser;
import proj.travien.service.KeywordRecommendService;
import proj.travien.service.MyRouteService;
import proj.travien.service.UserInfoRecommendationService;

import java.util.List;
import java.util.Objects;

@RestController
@RequestMapping("/api")
public class RecommendController {

    @Autowired
    private KeywordRecommendService keywordRecommendService;

    @Autowired
    private UserInfoRecommendationService userInfoRecommendationService;

    @Autowired
    private MyRouteService myRouteService;

    @Autowired
    private CurrentUser currentUser;

    // keywords만 보내면 예전과 같은 21곳. 선택 값 scope·limit·seed는 KeywordRecommendService 참고 (틀린 값은 400 JSON)
    @PostMapping("/search")
    public List<KeywordResultDTO> search(@RequestBody KeywordRequest keywordRequest) {
        return keywordRecommendService.search(keywordRequest);
    }

    @PostMapping("/info-recommend")
    public List<UserInfo> getRecommendations(@RequestBody UserInfo user){
        return userInfoRecommendationService.getSimilarUsers(user);
    }

    // 경로 저장: 저장하는 사람 = 토큰 사용자. body.userId는 호환을 위해 받되 본인이 아니면 403
    @PostMapping("/save-route")
    public MyRoute saveRoute(@RequestBody MyRouteDTO myRoute){
        User me = currentUser.requireUser();
        CurrentUser.requireSameId(me, myRoute.getUserId());
        myRoute.setUserId(me.getId());
        return myRouteService.saveMyRoute(myRoute);
    }

    // 저장한 경로: userId가 없으면 본인. 다른 사용자는 관리자만
    @GetMapping("/get-routes")
    public List<UserInfo> getMyRoutes(@RequestParam(value = "userId", required = false) Long userId){
        User me = currentUser.requireUser();
        if (userId != null && !Objects.equals(userId, me.getId())) {
            if (!currentUser.isAdmin()) {
                throw new AccessDeniedException("본인이 저장한 경로만 볼 수 있습니다.");
            }
            return myRouteService.getMyRoutes(userId);
        }
        return myRouteService.getMyRoutes(me.getId());
    }

}
