package proj.travien.controller;

import io.swagger.v3.oas.annotations.Operation;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import proj.travien.domain.User;
import proj.travien.dto.AgentDTO;
import proj.travien.jwt.CurrentUser;
import proj.travien.service.FollowService;
import proj.travien.service.UserService;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

@RestController
@RequestMapping("/api/follow")
public class FollowController {

    @Autowired
    private FollowService followService;

    @Autowired
    private UserService userService;

    @Autowired
    private CurrentUser currentUser;

    @Operation(summary = "예약대행자 팔로우")
    @PostMapping("/follow-agent")
    public ResponseEntity<?> followAgent(@RequestParam(required = false) String userNickname, @RequestParam String agentNickname) {
        // 팔로우하는 사람 = 토큰 사용자. userNickname은 호환을 위해 받되 본인 닉네임(DB)이 아니면 403
        User me = currentUser.requireUser();
        CurrentUser.requireSameNickname(me, userNickname);
        boolean followed = followService.followAgent(me.getNickname(), agentNickname);
        if (followed) {
            return ResponseEntity.ok().build();
        } else {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body("Could not follow the agent");
        }
    }


    @Operation(summary = "팔로우한 예약대행자")
    @GetMapping("/followed-agents")
    public ResponseEntity<List<Map<String, AgentDTO>>> getFollowedAgents(@RequestParam(required = false) String userId) {
        // userId가 없으면 본인. 다른 사용자의 팔로우 목록은 관리자만 볼 수 있다.
        currentUser.requireSelfOrAdmin(userId);
        // 다른 사용자(대행자)의 정보이므로 공개 대행자 프로필(agentDetails)만 내보낸다.
        // 이름·생년월일·성별·포인트·검증 서류 경로 같은 개인정보는 싣지 않는다. 화면은 agentDetails.* 만 읽는다.
        List<User> agents = followService.getFollowedAgents(currentUser.targetUserId(userId));
        List<Map<String, AgentDTO>> agentDTOs = agents.stream()
                .map(agent -> Map.of("agentDetails", userService.createAgentDTO(agent)))
                .collect(Collectors.toList());

        return ResponseEntity.ok(agentDTOs);
    }
}

