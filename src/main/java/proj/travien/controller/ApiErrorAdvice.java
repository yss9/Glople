package proj.travien.controller;

import jakarta.servlet.http.HttpServletRequest;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import proj.travien.service.InvalidRequestException;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 서비스가 던진 {@link InvalidRequestException}을 400 JSON으로 바꾼다. 모양은 SecurityConfig의 401·403 JSON과 같고
 * errors(칸별 사유 목록)만 더한다. 칸 이름 errors는 에이전트(agent/src/glople_agent/plan/spring_store.py)가 읽는 이름과 같다.
 *
 * <pre>{"status":400,"error":"BAD_REQUEST","message":"...","path":"/api/travel-plans","errors":[{"field":"days","code":"out_of_range","message":"..."}]}</pre>
 *
 * 이 예외는 새 코드(추천 옵션, 여행지 조회, 여행 일정)만 던지므로 기존 API의 오류 응답은 바뀌지 않는다.
 */
@RestControllerAdvice
public class ApiErrorAdvice {

    @ExceptionHandler(InvalidRequestException.class)
    public ResponseEntity<Map<String, Object>> invalidRequest(InvalidRequestException e, HttpServletRequest request) {
        return body(HttpStatus.BAD_REQUEST, e.getMessage(), request, e.getProblems());
    }

    // 401·403 JSON(SecurityConfig.writeError)과 같은 키 순서. 칸별 사유가 없으면 errors 키를 싣지 않는다.
    static ResponseEntity<Map<String, Object>> body(HttpStatus status, String message, HttpServletRequest request,
                                                    List<InvalidRequestException.Problem> problems) {
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("status", status.value());
        body.put("error", status.name());
        body.put("message", message);
        body.put("path", request.getRequestURI());
        if (problems != null && !problems.isEmpty()) {
            body.put("errors", problems);
        }
        return ResponseEntity.status(status).contentType(MediaType.APPLICATION_JSON).body(body);
    }
}
