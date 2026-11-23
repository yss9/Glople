package proj.travien.controller;


import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import proj.travien.domain.BookReview;
import proj.travien.domain.User;
import proj.travien.jwt.CurrentUser;
import proj.travien.service.BookReviewService;
import proj.travien.service.UserService;

import java.util.List;


@RestController
@RequestMapping("/api/bookReviews")
public class BookReviewController {

    private final BookReviewService bookReviewService;
    private final UserService userService;
    private final CurrentUser currentUser;

    public BookReviewController(BookReviewService bookReviewService, UserService userService, CurrentUser currentUser) {
        this.bookReviewService = bookReviewService;
        this.userService = userService;
        this.currentUser = currentUser;
    }

    /**
     * 리뷰 작성 ({nickname} = 리뷰 대상 대행자). 작성자는 토큰 사용자이고, 그 대행자와 송금했거나 끝난 예약이 있어야 한다.
     */
    @PostMapping("/agent/{nickname}")
    public ResponseEntity<BookReview> addReview(@PathVariable String nickname,
                                                @RequestParam String content,
                                                @RequestParam int star) {
        User writer = currentUser.requireUser();
        User agent;
        try {
            agent = userService.getUserByNickname(nickname);
        } catch (IllegalArgumentException e) {
            throw new ResponseStatusException(HttpStatus.NOT_FOUND, "예약대행자를 찾을 수 없습니다.");
        }
        BookReview bookReview = bookReviewService.addReviewAsCustomer(writer, agent, content, star);
        return ResponseEntity.ok(bookReview);
    }


    /**
     * userId에 대한 리뷰만 확인할 수 있다
     */

    @GetMapping("/agent/{userId}")
    public ResponseEntity<List<BookReview>> getReviewsByUserId(@PathVariable Long userId) {
        List<BookReview> reviews = bookReviewService.getReviewsByUserId(userId);
        return ResponseEntity.ok(reviews);
    }

    /**
     * 리뷰 삭제하는 코드 (리뷰에 작성자 정보가 없어 당분간 관리자만)
     */
    @PreAuthorize("hasRole('ADMIN')")
    @DeleteMapping("/{reviewId}")
    public ResponseEntity<Void> deleteReview(@PathVariable Long reviewId) {
        bookReviewService.deleteReview(reviewId);
        return ResponseEntity.noContent().build();
    }

    /**
     * 모든 리뷰 불러오는 코드
     */
    @GetMapping("/")
    public ResponseEntity<List<BookReview>> getAllReviews() {
        List<BookReview> reviews = bookReviewService.getAllReviews();
        return ResponseEntity.ok(reviews);
    }

    @GetMapping("/agent/review")
    public ResponseEntity<List<BookReview>> getReviewsByUsername(@RequestParam String nickname){
        Long id = userService.getIdByNickname(nickname);
        List<BookReview> reviews = bookReviewService.getReviewsByUserId(id);
        return ResponseEntity.ok(reviews);
    }
}

