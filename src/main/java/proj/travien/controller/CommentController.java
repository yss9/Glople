package proj.travien.controller;

import org.springframework.http.HttpStatus;
import proj.travien.domain.Comment;
import proj.travien.domain.User;
import proj.travien.dto.CommentRequest;
import proj.travien.dto.CommentResponse;
import proj.travien.jwt.CurrentUser;
import proj.travien.service.CommentService;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/comments")
public class CommentController {

    @Autowired
    private CommentService commentService;

    @Autowired
    private CurrentUser currentUser;

    /**
     * 댓글 작성 (작성자 = 토큰 사용자. body.userId는 호환을 위해 받되 본인이 아니면 403)
     */
    @PostMapping("/")
    public ResponseEntity<Comment> createComment(@RequestBody CommentRequest commentRequest) {
        User me = currentUser.requireUser();
        CurrentUser.requireSameId(me, commentRequest.getUserId());
        Long boardID = commentRequest.getBoardID(); // 필요한 경우 boardID에서 postId를 가져오기
        String content = commentRequest.getContent();

        Comment createdComment = commentService.addComment(boardID, me.getId(), content);
        return ResponseEntity.status(HttpStatus.CREATED).body(createdComment);
    }

    /**
     * 게시물 댓글 목록 확인
     */
    @GetMapping("/{boardID}")
    public ResponseEntity<List<CommentResponse>> getCommentsByBoardID(@PathVariable Long boardID) {
        List<CommentResponse> comments = commentService.getCommentsByBoardID(boardID);
        return ResponseEntity.ok(comments);
    }

    /**
     * 게시물 댓글 개수 확인
     */
    @GetMapping("/{boardID}/count")
    public ResponseEntity<Long> getCommentCountByBoardID(@PathVariable Long boardID) {
        Long commentCount = commentService.getCommentsByBoardID(boardID).stream().count();
        return ResponseEntity.ok(commentCount);
    }

    /**
     * 댓글 삭제 (작성자 본인 또는 관리자)
     */
    @DeleteMapping("/{commentId}")
    public ResponseEntity<Void> deleteComment(@PathVariable Long commentId) {
        commentService.deleteCommentAs(commentId, currentUser.requireUser(), currentUser.isAdmin());
        return ResponseEntity.noContent().build();
    }




}
