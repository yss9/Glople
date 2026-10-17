package proj.travien.controller;

import io.swagger.v3.oas.annotations.Operation;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import proj.travien.domain.User;
import proj.travien.dto.AgentDTO;
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

    @Operation(summary = "예약대행자 팔로우")
    @PostMapping("/follow-agent")
    public ResponseEntity<?> followAgent(@RequestParam String userNickname, @RequestParam String agentNickname) {
        boolean followed = followService.followAgent(userNickname, agentNickname);
        if (followed) {
            return ResponseEntity.ok().build();
        } else {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body("Could not follow the agent");
        }
    }


    @Operation(summary = "팔로우한 예약대행자")
    @GetMapping("/followed-agents")
    public ResponseEntity<List<Map<String, AgentDTO>>> getFollowedAgents(@RequestParam String userId) {
        // 다른 사용자(대행자)의 정보이므로 공개 대행자 프로필(agentDetails)만 내보낸다.
        // 이름·생년월일·성별·포인트·검증 서류 경로 같은 개인정보는 싣지 않는다. 화면은 agentDetails.* 만 읽는다.
        List<User> agents = followService.getFollowedAgents(userId);
        List<Map<String, AgentDTO>> agentDTOs = agents.stream()
                .map(agent -> Map.of("agentDetails", userService.createAgentDTO(agent)))
                .collect(Collectors.toList());

        return ResponseEntity.ok(agentDTOs);
    }
}

