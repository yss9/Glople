package proj.travien.controller;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import proj.travien.domain.Booking;
import proj.travien.domain.User;
import proj.travien.jwt.CurrentUser;
import proj.travien.service.BookingService;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

// 예약 API의 userId·agentId·nickname 파라미터는 이름과 달리 '닉네임'이다.
// 두 닉네임 중 하나는 토큰 사용자(DB 닉네임)여야 한다. (예약 당사자만)
@RestController
@RequestMapping("/api/booking")
public class BookingController {

    @Autowired
    private BookingService bookingService;

    @Autowired
    private CurrentUser currentUser;

    @PostMapping("/update-progress")
    public ResponseEntity<Void> updateProgress(@RequestParam String userId,
                                               @RequestParam String agentId,
                                               @RequestParam int progress) {
        User me = currentUser.requireUser();
        String counterpart = counterpartOf(me, userId, agentId);
        // 손님·대행자별로 바꿀 수 있는 단계는 서비스가 정한다
        bookingService.updateProgressAs(me, counterpart, progress);
        return ResponseEntity.ok().build();
    }

    @GetMapping("/progress")
    public ResponseEntity<Integer> getProgress(@RequestParam String userId,
                                               @RequestParam String agentId) {
        counterpartOf(currentUser.requireUser(), userId, agentId);
        int progress = bookingService.getProgress(userId, agentId);
        return ResponseEntity.ok(progress);
    }

    @GetMapping("/all-progress")
    public ResponseEntity<List<Map<String, Object>>> getAllProgress(@RequestParam(required = false) String nickname) {
        // nickname이 없으면 본인. 다른 사용자는 관리자만 볼 수 있다.
        User me = currentUser.requireUser();
        String target = me.getNickname();
        if (nickname != null && !nickname.isBlank() && !nickname.equals(me.getNickname())) {
            if (!currentUser.isAdmin()) {
                throw new AccessDeniedException("본인의 예약만 볼 수 있습니다.");
            }
            target = nickname;
        }
        List<Booking> bookings = bookingService.getAllUniqueProgress(target);

        List<Map<String, Object>> result = bookings.stream().map(booking -> {
            Map<String, Object> map = new HashMap<>();
            map.put("userNickname", booking.getUser().getNickname());
            map.put("agentNickname", booking.getAgent().getNickname());
            map.put("progress", booking.getProgress());
            return map;
        }).collect(Collectors.toList());

        return ResponseEntity.ok(result);
    }

    // 송금 완료 표시 확인 API
    // 송금 표시(isSended)는 포인트 이체(/api/users/transfer-points)가 성공할 때 서버가 직접 설정한다.
    // 이 API는 호환을 위해 남기고, 이미 송금된 예약이면 200, 아니면 409를 돌려준다. (클라이언트가 송금 완료를 직접 만들지 못하게)
    @PostMapping("/set-sended")
    public ResponseEntity<Void> setSended(@RequestBody Map<String, String> request) {
        User me = currentUser.requireUser();
        String userNickname = request.get("userNickname");
        String agentNickname = request.get("agentNickname");
        // 송금한 사람 본인만
        CurrentUser.requireSameNickname(me, userNickname);
        if (agentNickname == null || agentNickname.isBlank()) {
            return ResponseEntity.status(HttpStatus.BAD_REQUEST).build();
        }

        Boolean isSended = bookingService.checkSendedStatus(me.getNickname(), agentNickname);
        if (isSended == null) {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(null);
        }
        return isSended ? ResponseEntity.ok().build() : ResponseEntity.status(HttpStatus.CONFLICT).build();
    }


    // 현재 isSended 값을 조회하는 API
    @GetMapping("/check-sended")
    public ResponseEntity<Boolean> checkSended(@RequestParam String userNickname, @RequestParam String agentNickname) {
        counterpartOf(currentUser.requireUser(), userNickname, agentNickname);
        Boolean isSended = bookingService.checkSendedStatus(userNickname, agentNickname);
        if (isSended != null) {
            return ResponseEntity.ok(isSended);
        } else {
            return ResponseEntity.status(HttpStatus.NOT_FOUND).body(null);
        }
    }

    // 두 닉네임 중 본인이 아닌 쪽(상대)을 돌려준다. 본인이 둘 다 아니면 403, 두 값이 같으면 400
    private static String counterpartOf(User me, String first, String second) {
        if (first == null || second == null || first.equals(second)) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "예약 당사자 두 명이 필요합니다.");
        }
        if (first.equals(me.getNickname())) {
            return second;
        }
        if (second.equals(me.getNickname())) {
            return first;
        }
        throw new AccessDeniedException("예약 당사자만 할 수 있습니다.");
    }
}
