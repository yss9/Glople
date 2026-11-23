package proj.travien.service;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.security.access.AccessDeniedException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import proj.travien.domain.TravelPlan;
import proj.travien.domain.TravelPlanPlace;
import proj.travien.domain.User;
import proj.travien.dto.TravelPlanDTO;
import proj.travien.dto.TravelPlanRequest;
import proj.travien.dto.TravelPlanSummaryDTO;
import proj.travien.repository.TravelPlanRepository;

import java.time.Clock;
import java.time.LocalDateTime;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Optional;
import java.util.Set;

/**
 * 여행 일정 저장·조회·삭제. 주인은 언제나 컨트롤러가 넘긴 토큰 사용자다(docs/authorization.md 원칙 1·3).
 *
 * <ul>
 *   <li>저장: 값 검사(어긋나면 400) → 같은 (사용자, clientRequestId)가 있으면 그 일정을 돌려줌(created=false, 200) → 없으면 저장(201).
 *       같은 키 요청이 동시에 와서 UNIQUE 제약에 걸리면 먼저 저장된 일정을 다시 읽어 돌려준다.</li>
 *   <li>조회: 주인 또는 관리자. 남의 일정과 없는 일정은 똑같이 403이다(채팅 기록 C01처럼 있는지 없는지 숨김).</li>
 *   <li>삭제: 주인만. 남의 일정·없는 일정은 403.</li>
 * </ul>
 */
@Service
public class TravelPlanService {

    private final TravelPlanRepository repository;
    private final Clock clock;

    public record CreateResult(boolean created, TravelPlanDTO plan) {
    }

    @Autowired
    public TravelPlanService(TravelPlanRepository repository) {
        this(repository, Clock.systemDefaultZone());
    }

    TravelPlanService(TravelPlanRepository repository, Clock clock) {
        this.repository = repository;
        this.clock = clock;
    }

    // 트랜잭션은 저장소 호출마다 따로 둔다. UNIQUE 위반으로 저장이 되돌려진 뒤 새 트랜잭션에서 먼저 저장된 일정을 읽기 위해서다.
    public CreateResult create(User owner, TravelPlanRequest request) {
        List<InvalidRequestException.Problem> problems = validate(request);
        if (!problems.isEmpty()) {
            throw new InvalidRequestException("일정 값이 올바르지 않습니다.", problems);
        }

        String key = request.getClientRequestId();
        Optional<TravelPlan> existing = repository.findWithPlacesByUser_IdAndClientRequestId(owner.getId(), key);
        if (existing.isPresent()) {
            return new CreateResult(false, TravelPlanDTO.from(existing.get()));
        }

        TravelPlan plan = newPlan(owner, request);
        try {
            return new CreateResult(true, TravelPlanDTO.from(repository.saveAndFlush(plan)));
        } catch (DataIntegrityViolationException e) {
            // 같은 키로 동시에 들어온 다른 요청이 먼저 저장했다
            return repository.findWithPlacesByUser_IdAndClientRequestId(owner.getId(), key)
                    .map(saved -> new CreateResult(false, TravelPlanDTO.from(saved)))
                    .orElseThrow(() -> e);
        }
    }

    public List<TravelPlanSummaryDTO> listMine(User owner) {
        return repository.findWithPlacesByUser_IdOrderByCreatedAtDescIdDesc(owner.getId()).stream()
                .map(TravelPlanSummaryDTO::from)
                .toList();
    }

    // 주인 또는 관리자. 남의 일정·없는 일정은 403
    public TravelPlanDTO get(User me, boolean admin, Long id) {
        Optional<TravelPlan> plan = admin
                ? repository.findWithPlacesById(id)
                : repository.findWithPlacesByIdAndUser_Id(id, me.getId());
        return plan.map(TravelPlanDTO::from).orElseThrow(TravelPlanService::hidden);
    }

    // 주인만 (관리자도 남의 일정은 지우지 못한다). 남의 일정·없는 일정은 403
    @Transactional
    public void delete(User me, Long id) {
        TravelPlan plan = repository.findWithPlacesByIdAndUser_Id(id, me.getId()).orElseThrow(TravelPlanService::hidden);
        repository.delete(plan);
    }

    /**
     * 저장 전에 보는 값 규칙. 틀린 칸을 모두 모아 돌려준다(없으면 빈 목록). 길이는 글자 수(유니코드 코드 포인트)로 센다.
     * 제목·지역·장소 이름은 앞뒤 공백을 뗀 값으로 검사하고 저장한다. clientRequestId는 받은 그대로 검사·비교한다.
     */
    static List<InvalidRequestException.Problem> validate(TravelPlanRequest request) {
        List<InvalidRequestException.Problem> problems = new ArrayList<>();
        if (request == null) {
            problems.add(problem("body", "empty", "일정 본문을 보내 주세요."));
            return problems;
        }

        String key = request.getClientRequestId();
        if (key == null || key.isBlank()) {
            problems.add(problem("clientRequestId", "empty", "clientRequestId를 보내 주세요."));
        } else if (length(key) > TravelPlan.MAX_CLIENT_REQUEST_ID_LENGTH) {
            problems.add(problem("clientRequestId", "too_long",
                    "clientRequestId는 " + TravelPlan.MAX_CLIENT_REQUEST_ID_LENGTH + "자까지 쓸 수 있습니다."));
        }
        checkText(problems, "title", "제목", request.getTitle(), TravelPlan.MAX_TITLE_LENGTH);
        checkText(problems, "region", "여행 지역", request.getRegion(), TravelPlan.MAX_REGION_LENGTH);

        Integer days = request.getDays();
        boolean daysValid = false;
        if (days == null) {
            problems.add(problem("days", "empty", "여행 일수(days)를 보내 주세요."));
        } else if (days < TravelPlan.MIN_DAYS || days > TravelPlan.MAX_DAYS) {
            problems.add(problem("days", "out_of_range",
                    "여행 일수는 " + TravelPlan.MIN_DAYS + "~" + TravelPlan.MAX_DAYS + "일이어야 합니다."));
        } else {
            daysValid = true;
        }

        List<TravelPlanRequest.Place> places = request.getPlaces();
        if (places == null || places.isEmpty()) {
            problems.add(problem("places", "empty", "장소를 1곳 이상 보내 주세요."));
            return problems;
        }
        if (places.size() > TravelPlan.MAX_PLACES) {
            problems.add(problem("places", "too_many", "장소는 " + TravelPlan.MAX_PLACES + "곳까지 저장할 수 있습니다."));
            return problems;
        }

        Set<Integer> positions = new HashSet<>();
        for (int i = 0; i < places.size(); i++) {
            String field = "places[" + i + "]";
            TravelPlanRequest.Place place = places.get(i);
            if (place == null) {
                problems.add(problem(field, "empty", "빈 장소가 있습니다."));
                continue;
            }
            checkText(problems, field + ".name", "장소 이름", place.getName(), TravelPlanPlace.MAX_NAME_LENGTH);

            Integer dayNo = place.getDayNo();
            if (dayNo == null) {
                problems.add(problem(field + ".dayNo", "empty", "며칠째인지(dayNo) 보내 주세요."));
            } else if (dayNo < TravelPlan.MIN_DAYS || dayNo > TravelPlan.MAX_DAYS) {
                problems.add(problem(field + ".dayNo", "out_of_range",
                        "dayNo는 " + TravelPlan.MIN_DAYS + "~" + TravelPlan.MAX_DAYS + "이어야 합니다."));
            } else if (daysValid && dayNo > days) {
                problems.add(problem(field + ".dayNo", "out_of_range", "dayNo는 여행 일수(" + days + "일) 이하여야 합니다."));
            }

            Integer position = place.getPosition();
            if (position == null) {
                problems.add(problem(field + ".position", "empty", "순서(position)를 보내 주세요."));
            } else if (position < 1) {
                problems.add(problem(field + ".position", "out_of_range", "position은 1 이상이어야 합니다."));
            } else if (!positions.add(position)) {
                problems.add(problem(field + ".position", "duplicate", "position " + position + "이 겹칩니다."));
            }

            String source = place.getSource();
            if (source == null || source.isBlank()) {
                problems.add(problem(field + ".source", "empty", "장소 출처(source)를 보내 주세요."));
            } else if (parseSource(source) == null) {
                problems.add(problem(field + ".source", "unknown_value", "source는 USER, TOOL_RESULT, USER_EDIT 중 하나여야 합니다."));
            }
        }
        return problems;
    }

    private TravelPlan newPlan(User owner, TravelPlanRequest request) {
        TravelPlan plan = new TravelPlan();
        plan.setUser(owner);
        plan.setClientRequestId(request.getClientRequestId());
        plan.setTitle(request.getTitle().trim());
        plan.setRegion(request.getRegion().trim());
        plan.setDays(request.getDays());
        plan.setCreatedAt(LocalDateTime.now(clock).truncatedTo(ChronoUnit.SECONDS));
        for (TravelPlanRequest.Place requested : request.getPlaces()) {
            TravelPlanPlace place = new TravelPlanPlace();
            place.setName(requested.getName().trim());
            place.setDayNo(requested.getDayNo());
            place.setPosition(requested.getPosition());
            place.setSource(parseSource(requested.getSource()));
            plan.addPlace(place);
        }
        return plan;
    }

    private static void checkText(List<InvalidRequestException.Problem> problems, String field, String label,
                                  String value, int maxLength) {
        if (value == null || value.isBlank()) {
            problems.add(problem(field, "empty", label + "을(를) 보내 주세요."));
        } else if (length(value.trim()) > maxLength) {
            problems.add(problem(field, "too_long", label + "은(는) " + maxLength + "자까지 쓸 수 있습니다."));
        }
    }

    private static TravelPlanPlace.Source parseSource(String value) {
        for (TravelPlanPlace.Source source : TravelPlanPlace.Source.values()) {
            if (source.name().equals(value.trim())) {
                return source;
            }
        }
        return null;
    }

    private static int length(String value) {
        return value.codePointCount(0, value.length());
    }

    private static InvalidRequestException.Problem problem(String field, String code, String message) {
        return new InvalidRequestException.Problem(field, code, message);
    }

    private static AccessDeniedException hidden() {
        return new AccessDeniedException("본인 일정만 볼 수 있습니다.");
    }
}
