package proj.travien.service;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import proj.travien.domain.Booking;
import proj.travien.domain.User;
import proj.travien.repository.BookingRepository;
import proj.travien.repository.UserRepository;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.stream.Collectors;

@Service
public class BookingService {

    @Autowired
    private BookingRepository bookingRepository;

    @Autowired
    private UserRepository userRepository; // 유저를 조회하기 위해 추가

    /**
     * 예약 진행 단계 변경. me = 토큰 사용자, counterpartNickname = 상대(컨트롤러가 '당사자인지' 확인한 뒤 넘긴다).
     * <ul>
     *   <li>1: 예약 요청 · 2: 대행자 수락 · 3: 입금 완료 · 4: 여행 완료</li>
     *   <li>손님(승인된 대행자가 아닌 쪽): 예약이 없거나 끝났을 때(4) 1로 새 예약만 만든다.</li>
     *   <li>대행자(승인된 대행자 쪽): 있는 예약을 1(초기화)·2·4로 바꾼다.</li>
     *   <li>3은 포인트 이체로 송금 표시(isSended)가 된 예약에서만, 여행 완료(4)를 되돌리지 않는 범위에서 허용한다.</li>
     *   <li>1로 돌아가면 송금 표시도 지운다. (다음 예약에서 송금 없이 3을 찍지 못하게)</li>
     * </ul>
     * 그 밖은 403, 단계 값이 1~4가 아니면 400, 상대가 없으면 404.
     */
    @Transactional
    public void updateProgressAs(User me, String counterpartNickname, int progress) {
        if (progress < 1 || progress > 4) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "progress는 1 이상 4 이하여야 합니다.");
        }
        User counterpart = userRepository.findByNickname(counterpartNickname)
                .orElseThrow(() -> new ResponseStatusException(HttpStatus.NOT_FOUND, "상대 사용자를 찾을 수 없습니다."));
        if (Objects.equals(counterpart.getId(), me.getId())) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "자기 자신과는 예약할 수 없습니다.");
        }

        boolean meIsAgent = UserService.isApprovedAgent(me);
        if (!meIsAgent && !UserService.isApprovedAgent(counterpart)) {
            throw new AccessDeniedException("승인된 예약대행자와만 예약할 수 있습니다.");
        }

        Optional<Booking> mine = bookingRepository.findByUser_NicknameAndAgent_Nickname(me.getNickname(), counterpart.getNickname());
        Optional<Booking> reverse = bookingRepository.findByUser_NicknameAndAgent_Nickname(counterpart.getNickname(), me.getNickname());
        boolean exists = mine.isPresent() || reverse.isPresent();
        int current = mine.or(() -> reverse).map(Booking::getProgress).orElse(0);
        boolean sended = mine.map(Booking::isSended).orElse(false) || reverse.map(Booking::isSended).orElse(false);

        if (progress == 3) {
            if (!exists || !sended || current == 4) {
                throw new AccessDeniedException("입금 완료(3)는 포인트를 보낸 예약에서만 표시할 수 있습니다.");
            }
        } else if (meIsAgent) {
            if (!exists) {
                throw new AccessDeniedException("손님이 만든 예약만 바꿀 수 있습니다.");
            }
        } else if (progress != 1 || (exists && current != 4)) {
            throw new AccessDeniedException("손님은 새 예약 요청(1)만 할 수 있습니다.");
        }

        applyProgress(mine.orElseGet(() -> new Booking(me, counterpart, progress)), progress);
        applyProgress(reverse.orElseGet(() -> new Booking(counterpart, me, progress)), progress);
    }

    // 양방향 Booking(손님→대행자, 대행자→손님) 두 행에 같은 진행도를 쓴다
    private void applyProgress(Booking booking, int progress) {
        booking.setProgress(progress);
        if (progress == 1) {
            booking.setSended(false);
        }
        bookingRepository.save(booking);
    }

    public int getProgress(String userNickname, String agentNickname) {
        return bookingRepository.findByUser_NicknameAndAgent_Nickname(userNickname, agentNickname)
                .map(Booking::getProgress)
                .orElse(0);  // 예약이 존재하지 않으면 0 반환
    }

    public List<Booking> getAllUniqueProgress(String userNickname) {
        List<Booking> bookings = bookingRepository.findAllByUser_NicknameOrAgent_Nickname(userNickname, userNickname);

        // user-agent 쌍의 중복 제거 (userNickname과 agentNickname의 조합을 키로 사용)
        Map<String, Booking> uniqueBookings = bookings.stream()
                .filter(booking -> booking.getProgress() > 0) // 진행도가 0보다 큰 것만
                .collect(Collectors.toMap(
                        booking -> generateBookingKey(booking.getUser().getNickname(), booking.getAgent().getNickname()),
                        booking -> booking,
                        (existing, replacement) -> existing // 중복되는 경우 기존 값을 유지
                ));

        return new ArrayList<>(uniqueBookings.values());
    }

    // 송금 표시(isSended)는 포인트 이체가 성공할 때 UserService.transferPointsToAgent만 설정한다.

    public Boolean checkSendedStatus(String userNickname, String agentNickname) {
        return bookingRepository.findByUser_NicknameAndAgent_Nickname(userNickname, agentNickname)
                .map(Booking::isSended)
                .orElse(null); // null 반환 시 NOT_FOUND
    }

    // userNickname과 agentNickname을 조합하여 유니크한 키를 생성하는 헬퍼 메서드
    private String generateBookingKey(String userNickname, String agentNickname) {
        if (userNickname.compareTo(agentNickname) < 0) {
            return userNickname + "_" + agentNickname;
        } else {
            return agentNickname + "_" + userNickname;
        }
    }
}
