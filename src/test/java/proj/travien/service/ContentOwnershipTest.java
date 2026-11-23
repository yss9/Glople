package proj.travien.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.test.util.ReflectionTestUtils;
import org.springframework.web.server.ResponseStatusException;
import proj.travien.domain.BookReview;
import proj.travien.domain.Booking;
import proj.travien.domain.ChatRoom;
import proj.travien.domain.Comment;
import proj.travien.domain.Post;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.domain.UserChatRoom;
import proj.travien.jwt.TestTokens;
import proj.travien.repository.AddressesRepository;
import proj.travien.repository.BookReviewRepository;
import proj.travien.repository.BookingRepository;
import proj.travien.repository.ChatRoomRepository;
import proj.travien.repository.CommentRepository;
import proj.travien.repository.PostRepository;
import proj.travien.repository.UserChatRoomRepository;
import proj.travien.repository.UserRepository;

import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatCode;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

// 서비스 안에 있는 소유자·참가자 검사: 게시글, 댓글, 리뷰, 채팅방 (DB 없음)
class ContentOwnershipTest {

    private final User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
    private final User bob = TestTokens.user(2L, "bob", "bob", Role.ROLE_USER);
    private final User carol = TestTokens.approvedAgent(3L, "carol", "carol");

    // ---------- 게시글 (P07·P08·P11) ----------

    @Test
    void onlyAuthorOrAdminMayEditPost() {
        PostRepository postRepository = mock(PostRepository.class);
        PostService postService = new PostService(mock(AddressesRepository.class), postRepository, new ObjectMapper());
        Post post = new Post();
        post.setBoardID(5L);
        post.setNickname("alice");
        Post legacy = new Post(); // 작성자 닉네임이 없는 예전 글
        legacy.setBoardID(6L);
        when(postRepository.findById(5L)).thenReturn(Optional.of(post));
        when(postRepository.findById(6L)).thenReturn(Optional.of(legacy));
        when(postRepository.findById(7L)).thenReturn(Optional.empty());

        assertThat(postService.requireEditablePost(5L, alice, false)).isSameAs(post);
        assertThatCode(() -> postService.requireEditablePost(5L, bob, true)).doesNotThrowAnyException();
        assertThatThrownBy(() -> postService.requireEditablePost(5L, bob, false)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> postService.requireEditablePost(6L, alice, false)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> postService.requireEditablePost(7L, alice, false))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode()).isEqualTo(HttpStatus.NOT_FOUND));
    }

    // ---------- 댓글 (M04) ----------

    @Test
    void onlyAuthorOrAdminMayDeleteComment() {
        CommentRepository commentRepository = mock(CommentRepository.class);
        CommentService commentService = new CommentService();
        ReflectionTestUtils.setField(commentService, "commentRepository", commentRepository);
        Comment comment = Comment.builder().id(7L).user(alice).content("hi").build();
        when(commentRepository.findById(7L)).thenReturn(Optional.of(comment));

        assertThatThrownBy(() -> commentService.deleteCommentAs(7L, bob, false)).isInstanceOf(AccessDeniedException.class);
        verify(commentRepository, never()).delete(any());

        commentService.deleteCommentAs(7L, alice, false);
        commentService.deleteCommentAs(7L, bob, true);
        verify(commentRepository, org.mockito.Mockito.times(2)).delete(comment);
    }

    // ---------- 리뷰 (R01) ----------

    @Test
    void reviewNeedsPaidOrFinishedBookingWithApprovedAgent() {
        BookReviewRepository reviewRepository = mock(BookReviewRepository.class);
        BookingRepository bookingRepository = mock(BookingRepository.class);
        BookReviewService reviewService = new BookReviewService(reviewRepository, mock(UserRepository.class), bookingRepository);
        when(reviewRepository.findByUserId(3L)).thenReturn(List.of());

        // 예약 없음 → 403
        when(bookingRepository.findByUser_NicknameAndAgent_Nickname("alice", "carol")).thenReturn(Optional.empty());
        assertThatThrownBy(() -> reviewService.addReviewAsCustomer(alice, carol, "좋아요", 5)).isInstanceOf(AccessDeniedException.class);

        // 손님이 스스로 만든 예약(1)만 있음 → 403
        Booking requested = new Booking(alice, carol, 1);
        when(bookingRepository.findByUser_NicknameAndAgent_Nickname("alice", "carol")).thenReturn(Optional.of(requested));
        assertThatThrownBy(() -> reviewService.addReviewAsCustomer(alice, carol, "좋아요", 5)).isInstanceOf(AccessDeniedException.class);
        verify(reviewRepository, never()).save(any());

        // 이체로 송금 표시된 예약 → 작성
        requested.setSended(true);
        BookReview review = reviewService.addReviewAsCustomer(alice, carol, "좋아요", 5);
        assertThat(review.getUser()).isSameAs(carol);

        // 별점 범위, 자기 자신, 승인 안 된 대상
        assertThatThrownBy(() -> reviewService.addReviewAsCustomer(alice, carol, "x", 6))
                .isInstanceOfSatisfying(ResponseStatusException.class,
                        e -> assertThat(e.getStatusCode()).isEqualTo(HttpStatus.BAD_REQUEST));
        assertThatThrownBy(() -> reviewService.addReviewAsCustomer(carol, carol, "x", 5)).isInstanceOf(AccessDeniedException.class);
        assertThatThrownBy(() -> reviewService.addReviewAsCustomer(alice, bob, "x", 5)).isInstanceOf(AccessDeniedException.class);
    }

    // ---------- 채팅방 참가자 (C01·S02) ----------

    @Test
    void participantIsDecidedByUserChatRoomRowsNotByRoomName() {
        ChatRoomRepository chatRoomRepository = mock(ChatRoomRepository.class);
        UserChatRoomRepository userChatRoomRepository = mock(UserChatRoomRepository.class);
        ChatAccessService access = new ChatAccessService(chatRoomRepository, userChatRoomRepository);
        ChatRoom room = new ChatRoom();
        room.setId(10L);
        room.setRoomId("alice_bob");
        when(chatRoomRepository.findByRoomId("alice_bob")).thenReturn(Optional.of(room));
        when(chatRoomRepository.findByRoomId("ghost_room")).thenReturn(Optional.empty());
        when(userChatRoomRepository.findByUserId(1L)).thenReturn(List.of(new UserChatRoom(1L, 10L)));
        when(userChatRoomRepository.findByUserId(2L)).thenReturn(List.of(new UserChatRoom(2L, 99L)));

        assertThat(access.isParticipant("alice_bob", 1L)).isTrue();
        // 방 이름에 닉네임이 들어 있어도 참가자 행이 없으면 아니다 (닉네임을 바꾼 뒤 옛 닉네임을 가져간 사람 등)
        assertThat(access.isParticipant("alice_bob", 2L)).isFalse();
        assertThat(access.isParticipant("ghost_room", 1L)).isFalse();
        assertThat(access.isParticipant(null, 1L)).isFalse();
        assertThat(access.isParticipant("alice_bob", null)).isFalse();
    }
}
