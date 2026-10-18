package proj.travien.controller;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import proj.travien.service.InvalidRequestException;

import java.util.List;
import java.util.Map;

/**
 * 새 API(여행지 조회, 여행 일정, 키워드 마스크 가져오기)에서 스프링이 직접 내는 400(읽을 수 없는 JSON, 형식이 틀린 경로·쿼리 값)도
 * {@link ApiErrorAdvice}와 같은 JSON으로 돌려준다. 기존 컨트롤러의 오류 응답은 건드리지 않도록 이 컨트롤러들에만 건다.
 */
@RestControllerAdvice(assignableTypes = {DestinationController.class, TravelPlanController.class, KeywordMaskController.class})
public class ApiRequestErrorAdvice {

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<Map<String, Object>> unreadableBody(HttpMessageNotReadableException e, HttpServletRequest request) {
        return ApiErrorAdvice.body(HttpStatus.BAD_REQUEST, "요청 본문을 JSON으로 읽을 수 없습니다. 칸 이름과 값의 형식을 확인해 주세요.",
                request, List.of(new InvalidRequestException.Problem("body", "unreadable", "JSON 형식이 아니거나 값의 형식이 맞지 않습니다.")));
    }

    @ExceptionHandler(MethodArgumentTypeMismatchException.class)
    public ResponseEntity<Map<String, Object>> typeMismatch(MethodArgumentTypeMismatchException e, HttpServletRequest request) {
        String message = e.getName() + " 값의 형식이 맞지 않습니다.";
        return ApiErrorAdvice.body(HttpStatus.BAD_REQUEST, message, request,
                List.of(new InvalidRequestException.Problem(e.getName(), "bad_format", message)));
    }
}
