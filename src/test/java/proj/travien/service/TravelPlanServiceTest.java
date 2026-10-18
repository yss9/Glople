package proj.travien.service;

import org.junit.jupiter.api.Test;
import proj.travien.domain.Role;
import proj.travien.domain.TravelPlan;
import proj.travien.domain.User;
import proj.travien.dto.TravelPlanRequest;
import proj.travien.jwt.TestTokens;
import proj.travien.repository.TravelPlanRepository;

import java.time.Clock;
import java.time.Instant;
import java.time.LocalDateTime;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;
import java.util.stream.IntStream;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

// 일정 값 규칙의 경계 (에이전트 agent/src/glople_agent/plan/draft.py check_draft와 같은 한도)
class TravelPlanServiceTest {

    private final TravelPlanRepository repository = mock(TravelPlanRepository.class);
    private final TravelPlanService service = new TravelPlanService(repository,
            Clock.fixed(Instant.parse("2026-10-18T05:03:04.987654321Z"), ZoneOffset.UTC));
    private final User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);

    @Test
    void validPlanAtTheLimitsHasNoProblems() {
        TravelPlanRequest request = request("k".repeat(80), "제".repeat(100), "부".repeat(50), 30, 30);
        request.getPlaces().get(0).setName("가".repeat(50));
        assertThat(TravelPlanService.validate(request)).isEmpty();
    }

    // 길이는 글자 수(코드 포인트)로 센다. 이모지 하나(서로게이트 쌍)도 한 글자다. (에이전트 파이썬 len과 같음)
    @Test
    void lengthsAreCountedInCharactersNotUtf16Units() {
        TravelPlanRequest request = request("call_1", "😀".repeat(100), "부산", 1, 1);
        assertThat(TravelPlanService.validate(request)).isEmpty();
        request.setTitle("😀".repeat(101));
        assertThat(codes(TravelPlanService.validate(request))).containsExactly("title:too_long");
    }

    @Test
    void tooManyPlacesAndDayBeyondTripAreRejected() {
        assertThat(codes(TravelPlanService.validate(request("c", "t", "r", 3, 31)))).containsExactly("places:too_many");

        TravelPlanRequest request = request("c", "t", "r", 2, 2);
        request.getPlaces().get(1).setDayNo(3);
        request.getPlaces().get(0).setPosition(0);
        request.getPlaces().get(1).setSource("tool_result");
        assertThat(codes(TravelPlanService.validate(request)))
                .containsExactly("places[0].position:out_of_range", "places[1].dayNo:out_of_range", "places[1].source:unknown_value");

        request.getPlaces().set(0, null);
        assertThat(codes(TravelPlanService.validate(request))).startsWith("places[0]:empty");
        assertThat(codes(TravelPlanService.validate(null))).containsExactly("body:empty");
    }

    // 저장 값: 앞뒤 공백을 뗀 제목·지역·이름, 초 단위 시각, 주인 = 넘겨받은 토큰 사용자
    @Test
    void storesTrimmedValuesWithSecondPrecisionTimestamp() {
        when(repository.findWithPlacesByUser_IdAndClientRequestId(1L, "call_1")).thenReturn(Optional.empty());
        when(repository.saveAndFlush(any())).thenAnswer(invocation -> invocation.getArgument(0));
        TravelPlanRequest request = request("call_1", "  부산 여행 ", " 부산 ", 1, 1);
        request.getPlaces().get(0).setName(" 해운대 ");
        request.getPlaces().get(0).setSource(" USER ");

        TravelPlanService.CreateResult result = service.create(alice, request);

        assertThat(result.created()).isTrue();
        assertThat(result.plan().getTitle()).isEqualTo("부산 여행");
        assertThat(result.plan().getRegion()).isEqualTo("부산");
        assertThat(result.plan().getPlaces().get(0).getName()).isEqualTo("해운대");
        assertThat(result.plan().getPlaces().get(0).getSource()).isEqualTo("USER");
        assertThat(result.plan().getClientRequestId()).isEqualTo("call_1");
        assertThat(result.plan().getCreatedAt()).isEqualTo(LocalDateTime.of(2026, 10, 18, 5, 3, 4));
    }

    @Test
    void existingKeyIsReturnedEvenWhenTheBodyDiffers() {
        TravelPlan stored = new TravelPlan();
        stored.setId(3L);
        stored.setUser(alice);
        stored.setClientRequestId("call_1");
        stored.setTitle("처음 제목");
        stored.setRegion("부산");
        stored.setDays(1);
        when(repository.findWithPlacesByUser_IdAndClientRequestId(1L, "call_1")).thenReturn(Optional.of(stored));

        TravelPlanService.CreateResult result = service.create(alice, request("call_1", "다른 제목", "제주", 3, 2));

        assertThat(result.created()).isFalse();
        assertThat(result.plan().getId()).isEqualTo(3L);
        assertThat(result.plan().getTitle()).isEqualTo("처음 제목");
    }

    private static TravelPlanRequest request(String key, String title, String region, int days, int placeCount) {
        TravelPlanRequest request = new TravelPlanRequest();
        request.setClientRequestId(key);
        request.setTitle(title);
        request.setRegion(region);
        request.setDays(days);
        List<TravelPlanRequest.Place> places = new ArrayList<>();
        IntStream.rangeClosed(1, placeCount).forEach(i -> {
            TravelPlanRequest.Place place = new TravelPlanRequest.Place();
            place.setName("장소" + i);
            place.setDayNo(Math.min(i, days));
            place.setPosition(i);
            place.setSource("TOOL_RESULT");
            places.add(place);
        });
        request.setPlaces(places);
        return request;
    }

    private static List<String> codes(List<InvalidRequestException.Problem> problems) {
        return problems.stream().map(p -> p.field() + ":" + p.code()).toList();
    }
}
