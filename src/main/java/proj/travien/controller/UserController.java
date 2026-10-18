package proj.travien.controller;

import io.swagger.v3.oas.annotations.Operation;
import jakarta.servlet.http.HttpServletRequest;
import lombok.Getter;
import lombok.Setter;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.core.io.Resource;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;
import proj.travien.dto.AgentApplicationDTO;
import proj.travien.jwt.CurrentUser;
import proj.travien.jwt.JwtUtil;
import proj.travien.domain.User;
import proj.travien.dto.AgentDTO;
import proj.travien.dto.UserDTO;
import proj.travien.service.InvalidRequestException;
import proj.travien.service.UserService;

import java.io.IOException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@RestController
@RequestMapping("/api/users")
public class UserController {

    private static final Logger log = LoggerFactory.getLogger(UserController.class);

    @Autowired
    private UserService userService;

    @Autowired
    private JwtUtil jwtUtil;

    // 행위자는 토큰 사용자로만 정한다. 요청의 userId·nickname은 호환을 위해 받되 본인이 아니면 403
    @Autowired
    private CurrentUser currentUser;

    @Operation(summary = "회원가입", description = "새로운 사용자를 등록")
    @PostMapping("/signup")
    public ResponseEntity<?> signUp(@RequestPart("user") UserDTO userDTO,
                                    @RequestPart(value = "profilePicture", required = false) MultipartFile profilePictureFile) {
        if (userService.isUserIdInUse(userDTO.getUserId()) || userService.isNicknameInUse(userDTO.getNickname())) {
            return ResponseEntity.status(HttpStatus.CONFLICT).body("Username or nickname already in use");
        }

        // 기본 사용자로만 가입
        userDTO.setAgent(false); // 기본 사용자는 예약대행자가 아님
        boolean userCreated = userService.createUser(userDTO, profilePictureFile, null);

        if (userCreated) {
            return ResponseEntity.ok().build();
        } else {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body("User could not be created");
        }
    }

    @Operation(summary = "예약대행자 신청")
    @PostMapping("/apply-agent")
    public ResponseEntity<?> applyAsAgent(@RequestParam(value = "userId", required = false) String userId,
                                          @RequestPart("agentDetails") AgentApplicationDTO agentDTO,
                                          @RequestPart("verificationFile") MultipartFile verificationFile) {
        // 신청자는 토큰 사용자. 신청만으로는 ROLE_AGENT가 붙지 않고, 관리자가 승인해야 대행자가 된다.
        User me = currentUser.requireUser();
        currentUser.requireSelf(userId);
        boolean applied = userService.applyAsAgent(me.getUserId(), agentDTO, verificationFile);

        if (applied) {
            return ResponseEntity.ok().body("Successfully applied as an agent");
        } else {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body("Agent application failed");
        }
    }

    @Operation(summary = "관리자가 예약대행자 신청 승인")
    @PreAuthorize("hasRole('ADMIN')")
    @PostMapping("/approve-agent/{userId}")
    public ResponseEntity<?> approveAgentApplication(@PathVariable String userId) {
        boolean approved = userService.approveAgentApplication(userId);

        if (approved) {
            return ResponseEntity.ok("Agent application approved successfully");
        } else {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body("Failed to approve agent application");
        }
    }

    @Operation(summary = "해시태그로 예약대행자 검색")
    @GetMapping("/search-agents-by-hashtag")
    public ResponseEntity<List<AgentDTO>> searchAgentsByHashtag(@RequestParam("hashtag") String hashtag) {
        List<AgentDTO> agents = userService.searchAgentsByHashtag(hashtag);
        if (agents.isEmpty()) {
            return ResponseEntity.noContent().build();
        }
        return ResponseEntity.ok(agents);
    }

    @Operation(summary = "최근 승인된 예약대행자 리스트8명")
    @GetMapping("/recent-agents")
    public ResponseEntity<List<Map<String, Object>>> getRecentAgents() {
        List<Map<String, Object>> recentAgents = userService.getRecentAgents(8);
        return ResponseEntity.ok(recentAgents);
    }

    @Operation(summary = "로그인")
    @PostMapping("/login")
    public ResponseEntity<?> login(@RequestBody UserDTO request) {
        try {
            String userId = request.getUserId();
            String password = request.getPassword();

            User user = userService.login(userId, password);
            if (user != null) {
                String token = jwtUtil.generateToken(user.getId(),user.getUserId(), user.getNickname(), user.getRole());
                return ResponseEntity.ok(new AuthResponse(token));
            } else {
                return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body("Invalid username or password.");
            }
        } catch (Exception e) {
            log.error("Error during login", e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body("An error occurred during login.");
        }
    }

    @Operation(summary = "username으로 userid 체크")
    @GetMapping("/check-userid")
    public ResponseEntity<?> checkUserId(@RequestParam String username) {
        boolean exists = userService.isUserIdInUse(username);
        return ResponseEntity.ok(exists);
    }

    @Operation(summary = "닉네임 중복체크")
    @GetMapping("/check-nickname")
    public ResponseEntity<?> checkNickname(@RequestParam String nickname) {
        boolean exists = userService.isNicknameInUse(nickname);
        return ResponseEntity.ok(exists);
    }

    @Operation(summary = "프로필사진 업로드")
    @PostMapping("/upload-profile-picture")
    public ResponseEntity<?> uploadProfilePicture(@RequestParam(value = "userId", required = false) String userId,
                                                  @RequestPart("profilePicture") MultipartFile profilePictureFile) {
        // 프론트가 userId를 쿼리와 폼에 같이 보내 'id,id'로 들어올 수 있다. CurrentUser.requireSelf가 이를 본인으로 본다.
        User me = currentUser.requireUser();
        currentUser.requireSelf(userId);
        boolean uploaded = userService.uploadProfilePicture(me.getUserId(), profilePictureFile);
        if (uploaded) {
            return ResponseEntity.ok().build();
        } else {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body("Profile picture could not be uploaded");
        }
    }

    @Operation(summary = "검증 문서 업로드")
    @PostMapping("/upload-verification-file")
    public ResponseEntity<?> uploadVerificationFile(@RequestParam(value = "userId", required = false) String userId,
                                                    @RequestPart("verificationFile") MultipartFile verificationFile) {
        User me = currentUser.requireUser();
        currentUser.requireSelf(userId);
        boolean uploaded = userService.uploadVerificationFile(me.getUserId(), verificationFile);
        if (uploaded) {
            return ResponseEntity.ok().build();
        } else {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body("Verification file could not be uploaded");
        }
    }

    @Operation(summary = "프로필 수정 (비밀번호를 바꿀 때는 currentPassword도 함께)")
    @PostMapping("/update-profile")
    public ResponseEntity<?> updateUserProfile(@RequestParam(value = "userId", required = false) String userId,
                                               @RequestBody UserDTO userDTO,
                                               HttpServletRequest request) {
        User me = currentUser.requireUser();
        currentUser.requireSelf(userId);

        UserService.ProfileUpdateResult result = userService.updateUserProfile(me.getUserId(), userDTO);
        switch (result) {
            case NICKNAME_IN_USE:
                return ResponseEntity.status(HttpStatus.CONFLICT).body("Nickname already in use");
            case USER_NOT_FOUND:
                return ResponseEntity.status(HttpStatus.NOT_FOUND).body("User not found");
            // 비밀번호(password)를 바꾸려면 지금 비밀번호(currentPassword)가 필요하다. 이때는 아무 칸도 바꾸지 않았다.
            case CURRENT_PASSWORD_REQUIRED:
                return ApiErrorAdvice.body(HttpStatus.BAD_REQUEST,
                        "비밀번호를 바꾸려면 현재 비밀번호(currentPassword)를 함께 보내야 합니다.", request,
                        List.of(new InvalidRequestException.Problem("currentPassword", "empty", "현재 비밀번호를 입력해 주세요.")));
            case CURRENT_PASSWORD_MISMATCH:
                return ApiErrorAdvice.body(HttpStatus.FORBIDDEN, "현재 비밀번호가 맞지 않습니다.", request, List.of());
            default:
                break;
        }

        // 닉네임이 바뀌면 토큰의 nickname 클레임이 낡는다. 프론트는 이 클레임을 예약·채팅·이체·팔로우 요청에 그대로 보내므로
        // 지금 DB 값으로 새 토큰을 발급해 응답에 싣는다. 프론트는 이 token으로 jwt 쿠키를 바꾼다.
        User updated = userService.findByUserId(me.getUserId());
        String token = jwtUtil.generateToken(updated.getId(), updated.getUserId(), updated.getNickname(), updated.getRole());
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("message", "User profile updated successfully");
        body.put("token", token);
        return ResponseEntity.ok(body);
    }

    @Operation(summary = "예약대행자 정보 수정")
    @PostMapping("/update-agent-details")
    public ResponseEntity<?> updateAgentDetails(@RequestParam(value = "userId", required = false) String userId,
                                                @RequestBody AgentDTO agentDTO) {
        User me = currentUser.requireUser();
        currentUser.requireSelf(userId);
        boolean updated = userService.updateAgentDetails(me.getUserId(), agentDTO);
        if (updated) {
            return ResponseEntity.ok().build();
        } else {
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body("Agent details could not be updated");
        }
    }

    @Operation(summary = "전체 예약대행자 조회 및 특정 국가의 예약대행자 조회")
    @GetMapping("/agents")
    public ResponseEntity<List<AgentDTO>> getAgents(@RequestParam(value = "country", required = false) String country) {
        List<AgentDTO> agents;
        if (country != null) {
            // 특정 국가의 에이전트 조회
            agents = userService.getAgentsByCountry(country);
        } else {
            // 전체 에이전트 조회
            agents = userService.getAllAgents();
        }
        return ResponseEntity.ok(agents);
    }

    @Operation(summary = "마이페이지 정보 조회")
    @GetMapping("/my-info")
    public ResponseEntity<?> getMyInfo(@RequestParam(value = "userId", required = false) String userId) {
        // userId가 없으면 본인. 다른 사용자는 관리자만 조회할 수 있다.
        currentUser.requireSelfOrAdmin(userId);
        UserDTO userDTO = userService.getUserInfo(currentUser.targetUserId(userId));
        if (userDTO != null) {
            return ResponseEntity.ok(userDTO);
        } else {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body("User not found");
        }
    }

    @Operation(summary = "모든 유저 닉네임 조회")
    @GetMapping("/nicknames")
    public ResponseEntity<List<String>> getAllNicknames() {
        List<String> nicknames = userService.getAllNicknames();
        return new ResponseEntity<>(nicknames, HttpStatus.OK);
    }



    @Operation(summary = "프로필 사진 경로 조회")
    @GetMapping("/profile-picture/{nickname}")
    public ResponseEntity<String> getProfilePicture(@PathVariable String nickname) {
        try {
            String profilePicturePath = userService.getProfilePicturePath(nickname);
            if (profilePicturePath == null || profilePicturePath.isEmpty()) {
                return ResponseEntity.status(HttpStatus.NOT_FOUND).body("Profile picture not set for user: " + nickname);
            }
            return ResponseEntity.ok(profilePicturePath);
        } catch (RuntimeException e) {
            log.error("Error retrieving profile picture path for nickname: {}", nickname, e);
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(e.getMessage());
        } catch (Exception e) {
            log.error("Unexpected error retrieving profile picture path for nickname: {}", nickname, e);
            return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body("Error retrieving profile picture");
        }
    }
    @GetMapping("/profile-picture")
    public ResponseEntity<String> getProfilePictureByNickname(@RequestParam("nickname") String nickname) {
        try {
            String profilePicturePath = userService.getProfilePictureByNickname(nickname);
            return ResponseEntity.ok(profilePicturePath);
        } catch (RuntimeException e) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body("User not found");
        }
    }

    @Operation(summary = "검증파일 조회")
    @GetMapping("/verification-file/{userId}")
    public ResponseEntity<Resource> getVerificationFile(@PathVariable String userId) {
        // 서류 주인 본인 또는 관리자만. 검증 서류는 이 엔드포인트로만 내려준다(정적 /verification-files/** 는 차단).
        currentUser.requireSelfOrAdmin(userId);
        String owner = currentUser.targetUserId(userId);
        try {
            Resource file = userService.loadVerificationFile(owner);
            return ResponseEntity.ok()
                    .header(HttpHeaders.CONTENT_DISPOSITION, "attachment; filename=\"" + file.getFilename() + "\"")
                    .header("X-Content-Type-Options", "nosniff")
                    .body(file);
        } catch (IOException e) {
            log.error("Error loading verification file for userId: {}", owner, e);
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(null);
        }
    }

    @Operation(summary = "포인트 조회")
    @GetMapping("/{userId}/points")
    public ResponseEntity<?> getUserPoints(@PathVariable String userId) {
        // 본인 또는 관리자만
        currentUser.requireSelfOrAdmin(userId);
        String target = currentUser.targetUserId(userId);
        try {
            int points = userService.getUserPoints(target);
            return ResponseEntity.ok(points);
        } catch (IllegalArgumentException e) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(e.getMessage());
        }
    }

    @Operation(summary = "포인트 추가 (관리자)")
    @PreAuthorize("hasRole('ADMIN')")
    @PostMapping("/{nickname}/points/add")
    public ResponseEntity<?> addUserPoints(@PathVariable String nickname, @RequestParam int points) {
        try {
            boolean success = userService.addPointsByNickname(nickname, points);
            if (success) {
                return ResponseEntity.ok("Points added successfully");
            } else {
                return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body("Failed to add points");
            }
        } catch (IllegalArgumentException e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(e.getMessage());
        }
    }

    @Operation(summary = "포인트 차감 (관리자)")
    @PreAuthorize("hasRole('ADMIN')")
    @PostMapping("/{nickname}/points/deduct")
    public ResponseEntity<?> deductUserPoints(@PathVariable String nickname, @RequestParam int points) {
        try {
            boolean success = userService.deductPointsByNickname(nickname, points);
            if (success) {
                return ResponseEntity.ok("Points deducted successfully");
            } else {
                return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR).body("Failed to deduct points");
            }
        } catch (IllegalArgumentException e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(e.getMessage());
        }
    }

    @Operation(summary = "포인트 전송")
    @PostMapping("/transfer-points")
    public ResponseEntity<?> transferPoints(@RequestBody Map<String, Object> request) {
        // 보내는 사람은 토큰 사용자. userNickname은 호환을 위해 받되 본인 닉네임(DB)이 아니면 403
        User me = currentUser.requireUser();
        Object userNickname = request.get("userNickname");
        CurrentUser.requireSameNickname(me, userNickname == null ? null : userNickname.toString());

        Object agentNickname = request.get("agentNickname");
        Integer points = toInt(request.get("points"));
        if (agentNickname == null || points == null) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body("agentNickname and points are required");
        }

        try {
            // 받는 쪽은 나와 예약(Booking)이 있는 승인 대행자여야 한다(아니면 403). 송금 표시·진행 단계 3은 서버가 설정한다.
            userService.transferPointsToAgent(me, agentNickname.toString(), points);
            return ResponseEntity.ok("Points transferred successfully");
        } catch (IllegalArgumentException e) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).body(e.getMessage());
        }
    }

    private static Integer toInt(Object value) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        if (value instanceof String text) {
            try {
                return Integer.parseInt(text.trim());
            } catch (NumberFormatException e) {
                return null;
            }
        }
        return null;
    }

    @Setter
    @Getter
    static class AuthResponse {
        private String token;

        public AuthResponse(String token) {
            this.token = token;
        }

    }
}
