package proj.travien.service;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.server.ResponseStatusException;
import proj.travien.domain.Booking;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.jwt.TestTokens;
import proj.travien.repository.BookingRepository;
import proj.travien.repository.UserRepository;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

// 예약 진행 단계 규칙 (B01). 손님이 스스로 입금 완료(3)·여행 완료(4)를 찍지 못하게 한다. (DB 없음)
class BookingProgressRulesTest {

    private final BookingRepository bookingRepository = mock(BookingRepository.class);
    private final UserRepository userRepository = mock(UserRepository.class);
    private final BookingService bookingService = new BookingService();

    private final User customer = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
    private final User agent = TestTokens.approvedAgent(3L, "carol", "carol");
    private final User otherCustomer = TestTokens.user(2L, "bob", "bob", Role.ROLE_USER);

    @BeforeEach
    void setUp() {
        ReflectionTestUtils.setField(bookingService, "bookingRepository", bookingRepository);
        ReflectionTestUtils.setField(bookingService, "userRepository", userRepository);
        when(userRepository.findByNickname("alice")).thenReturn(Optional.of(customer));
        when(userRepository.findByNickname("bob")).thenReturn(Optional.of(otherCustomer));
        when(userRepository.findByNickname("carol")).thenReturn(Optional.of(agent));
    }

    @Test
    void customerCreatesNewBookingWithProgressOne() {
        bookingService.updateProgressAs(customer, "carol", 1);

        ArgumentCaptor<Booking> saved = ArgumentCaptor.forClass(Booking.class);
        verify(bookingRepository, times(2)).save(saved.capture());
        List<Booking> rows = saved.getAllValues();
        assertThat(rows).extracting(Booking::getProgress).containsOnly(1);
        assertThat(rows.get(0).getUser()).isSameAs(customer);
        assertThat(rows.get(0).getAgent()).isSameAs(agent);
        assertThat(rows.get(1).getUser()).isSameAs(agent);
    }

    @Test
    void customerCannotMarkPaidOrFinishedByThemselves() {
        Booking[] rows = booking(2, false);

        for (int progress : new int[]{2, 3, 4}) {
            assertThatThrownBy(() -> bookingService.updateProgressAs(customer, "carol", progress))
                    .as("progress " + progress)
                    .isInstanceOf(AccessDeniedException.class);
        }
        // 진행 중인 예약을 손님이 1로 되돌릴 수도 없다
        assertThatThrownBy(() -> bookingService.updateProgressAs(customer, "carol", 1))
                .isInstanceOf(AccessDeniedException.class);
        verify(bookingRepository, never()).save(any());
        assertThat(rows[0].getProgress()).isEqualTo(2);
    }

    @Test
    void progressThreeNeedsAPointTransfer() {
        Booking[] rows = booking(2, true); // 이체(U22)가 서버에서 isSended를 설정한 상태

        bookingService.updateProgressAs(customer, "carol", 3);

        assertThat(rows[0].getProgress()).isEqualTo(3);
        assertThat(rows[1].getProgress()).isEqualTo(3);
    }

    @Test
    void finishedTripCannotBeRolledBackToThree() {
        booking(4, true);
        assertThatThrownBy(() -> bookingService.updateProgressAs(customer, "carol", 3))
                .isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void agentMovesExistingBookingAndResetClearsPayment() {
        Booking[] rows = booking(3, true);

        bookingService.updateProgressAs(agent, "alice", 4);
        assertThat(rows[0].getProgress()).isEqualTo(4);

        bookingService.updateProgressAs(agent, "alice", 1);
        assertThat(rows[0].getProgress()).isEqualTo(1);
        assertThat(rows[0].isSended()).isFalse();
        assertThat(rows[1].isSended()).isFalse();
    }

    @Test
    void customerMayStartAgainAfterTheTripEnded() {
        Booking[] rows = booking(4, true);

        assertThatCode(() -> bookingService.updateProgressAs(customer, "carol", 1)).doesNotThrowAnyException();
        assertThat(rows[0].getProgress()).isEqualTo(1);
        assertThat(rows[0].isSended()).isFalse();
    }

    @Test
    void agentCannotInventBookingsAndTwoCustomersCannotBook() {
        when(bookingRepository.findByUser_NicknameAndAgent_Nickname(any(), any())).thenReturn(Optional.empty());

        assertThatThrownBy(() -> bookingService.updateProgressAs(agent, "alice", 4))
                .isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> bookingService.updateProgressAs(customer, "bob", 1))
                .isInstanceOf(AccessDeniedException.class);
        verify(bookingRepository, never()).save(any());
    }

    @Test
    void unapprovedApplicantIsNotTreatedAsAgent() {
        User applicant = TestTokens.user(4L, "dave", "dave", Role.ROLE_USER);
        applicant.setAgent(true); // 신청만 함 (승인일 없음)
        when(userRepository.findByNickname("dave")).thenReturn(Optional.of(applicant));

        assertThatThrownBy(() -> bookingService.updateProgressAs(customer, "dave", 1))
                .isInstanceOf(AccessDeniedException.class);
    }

    @Test
    void invalidProgressOrUnknownCounterpart() {
        assertThatThrownBy(() -> bookingService.updateProgressAs(customer, "carol", 5))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST));
        when(userRepository.findByNickname("nobody")).thenReturn(Optional.empty());
        assertThatThrownBy(() -> bookingService.updateProgressAs(customer, "nobody", 1))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND));
    }

    // 손님(alice)↔대행자(carol) 양방향 예약 두 행을 만들어 저장소 목에 연결한다
    private Booking[] booking(int progress, boolean sended) {
        Booking mine = new Booking(customer, agent, progress);
        mine.setSended(sended);
        Booking reverse = new Booking(agent, customer, progress);
        reverse.setSended(sended);
        when(bookingRepository.findByUser_NicknameAndAgent_Nickname("alice", "carol")).thenReturn(Optional.of(mine));
        when(bookingRepository.findByUser_NicknameAndAgent_Nickname("carol", "alice")).thenReturn(Optional.of(reverse));
        return new Booking[]{mine, reverse};
    }
}
