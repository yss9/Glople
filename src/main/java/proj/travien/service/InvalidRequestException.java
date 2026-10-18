package proj.travien.service;

import java.util.List;

/**
 * 요청 값이 규칙에 맞지 않을 때(400). 컨트롤러 밖으로 나가면 {@code ApiErrorAdvice}가
 * {@code {status, error, message, path, errors}} JSON으로 바꾼다. (SecurityConfig의 401·403 JSON과 같은 모양 + errors)
 *
 * <p>errors(= {@link #getProblems()})는 틀린 칸마다 하나씩이다. field는 요청 JSON의 경로(예: {@code places[2].dayNo}), code는 짧은 영문 사유
 * (empty, too_long, out_of_range, duplicate, unknown_value 등), message는 화면·에이전트에 그대로 보여줄 한국어 문장이다.
 */
public class InvalidRequestException extends RuntimeException {

    public record Problem(String field, String code, String message) {
    }

    private final transient List<Problem> problems;

    public InvalidRequestException(String message, List<Problem> problems) {
        super(message);
        this.problems = List.copyOf(problems);
    }

    // 칸 하나만 틀렸을 때. 오류 문장(message)은 그 칸의 문장을 그대로 쓴다.
    public InvalidRequestException(String field, String code, String message) {
        this(message, List.of(new Problem(field, code, message)));
    }

    public List<Problem> getProblems() {
        return problems;
    }
}
