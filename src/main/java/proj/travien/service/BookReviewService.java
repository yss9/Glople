package proj.travien.service;

import org.springframework.http.HttpStatus;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.server.ResponseStatusException;
import proj.travien.domain.BookReview;
import proj.travien.domain.Booking;
import proj.travien.domain.User;
import proj.travien.repository.BookReviewRepository;
import proj.travien.repository.BookingRepository;
import proj.travien.repository.UserRepository;

import java.util.List;
import java.util.Objects;

@Service
@Transactional
public class BookReviewService {

    private final BookReviewRepository bookReviewRepository;
    private final UserRepository userRepository;
    private final BookingRepository bookingRepository;

    public BookReviewService(BookReviewRepository bookReviewRepository, UserRepository userRepository,
                             BookingRepository bookingRepository) {
        this.bookReviewRepository = bookReviewRepository;
        this.userRepository = userRepository;
        this.bookingRepository = bookingRepository;
    }

    public BookReview addReview(Long userId, String content, int star) {
        User user = userRepository.findById(userId)
                .orElseThrow(() -> new IllegalArgumentException("User not found"));

        // 리뷰 추가
        BookReview bookReview = new BookReview(user, content, star);
        bookReviewRepository.save(bookReview);

        // 리뷰 수 및 평균 별점 계산
        updateUserAverageStar(user);

        return bookReview;
    }

    public List<BookReview> getReviewsByUserId(Long userId) {
        return bookReviewRepository.findByUserId(userId);
    }

    public void deleteReview(Long reviewId) {
        BookReview review = bookReviewRepository.findById(reviewId)
                .orElseThrow(() -> new IllegalArgumentException("Review not found"));
        User user = review.getUser();

        // 리뷰 삭제
        bookReviewRepository.deleteById(reviewId);

        // 평균 별점 업데이트
        updateUserAverageStar(user);
    }

    // 모든 리뷰를 반환하는 메서드 추가
    public List<BookReview> getAllReviews() {
        return bookReviewRepository.findAll();
    }

    // 평균 별점 업데이트 메서드
    private void updateUserAverageStar(User user) {
        List<BookReview> userReviews = bookReviewRepository.findByUserId(user.getId());

        if (!userReviews.isEmpty()) {
            double averageStar = userReviews.stream()
                    .mapToInt(BookReview::getStar)
                    .average()
                    .orElse(0.0);

            // 소수점 둘째 자리까지 반올림
            double roundedAverageStar = Math.round(averageStar * 100.0) / 100.0;
            user.setAverageReviewRating(roundedAverageStar);
        } else {
            user.setAverageReviewRating(0.0); // 리뷰가 없으면 평균 별점을 0으로 설정
        }

        // 사용자 업데이트 (persist)
        userRepository.save(user);
    }

    // 리뷰 작성: 작성자(토큰 사용자)가 그 대행자와 예약했고, 송금했거나(isSended) 여행이 끝났을 때(progress 4)만 쓸 수 있다.
    // 예약은 손님이 스스로 만들 수 있으므로 '예약 있음'만으로는 리뷰를 막지 못한다.
    public BookReview addReviewAsCustomer(User writer, User agent, String content, int star) {
        if (star < 1 || star > 5) {
            throw new ResponseStatusException(HttpStatus.BAD_REQUEST, "star는 1 이상 5 이하여야 합니다.");
        }
        if (Objects.equals(writer.getId(), agent.getId())) {
            throw new AccessDeniedException("자기 자신에게는 리뷰를 쓸 수 없습니다.");
        }
        if (!UserService.isApprovedAgent(agent)) {
            throw new AccessDeniedException("승인된 예약대행자에게만 리뷰를 쓸 수 있습니다.");
        }
        Booking booking = bookingRepository.findByUser_NicknameAndAgent_Nickname(writer.getNickname(), agent.getNickname())
                .orElseThrow(() -> new AccessDeniedException("예약한 예약대행자에게만 리뷰를 쓸 수 있습니다."));
        if (!booking.isSended() && booking.getProgress() != 4) {
            throw new AccessDeniedException("송금했거나 여행이 끝난 예약만 리뷰를 쓸 수 있습니다.");
        }
        return addReviewByUser(agent, content, star);
    }

    public BookReview addReviewByUser(User user, String content, int star) {
        BookReview bookReview = new BookReview(user, content, star);
        bookReviewRepository.save(bookReview);
        updateUserAverageStar(user);
        return bookReview;
    }

}
