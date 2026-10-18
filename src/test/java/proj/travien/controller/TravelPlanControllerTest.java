package proj.travien.controller;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.dao.DataIntegrityViolationException;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import proj.travien.domain.Role;
import proj.travien.domain.TravelPlan;
import proj.travien.domain.TravelPlanPlace;
import proj.travien.domain.User;
import proj.travien.jwt.CurrentUser;
import proj.travien.jwt.JwtTokenProvider;
import proj.travien.jwt.JwtUtil;
import proj.travien.jwt.SecurityConfig;
import proj.travien.jwt.TestTokens;
import proj.travien.repository.TravelPlanRepository;
import proj.travien.repository.UserRepository;
import proj.travien.service.TravelPlanService;

import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.delete;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.header;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 여행 일정 API를 실제 SecurityConfig·JWT 필터·CurrentUser·TravelPlanService로 확인한다. 저장소만 목(DB 없음).
 * 에이전트(agent/)가 쓰는 계약: 칸 이름, 201/200(같은 clientRequestId), 400 JSON, 401, 남의 것·없는 것 403.
 */
@WebMvcTest(TravelPlanController.class)
@Import({SecurityConfig.class, JwtTokenProvider.class, JwtUtil.class, CurrentUser.class, TravelPlanService.class})
@TestPropertySource(properties = {"jwt.secret=" + TestTokens.SECRET, "jwt.expiration=3600000"})
class TravelPlanControllerTest {

    private static final String BODY = """
            {"clientRequestId":"call_plan_1","title":"부산 1박 2일","region":"부산","days":2,
             "places":[{"name":"해운대해수욕장","dayNo":1,"position":1,"source":"TOOL_RESULT"},
                       {"name":"감천문화마을","dayNo":2,"position":2,"source":"USER"}]}
            """;

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JwtUtil jwtUtil;

    @MockBean
    private TravelPlanRepository travelPlanRepository;

    @MockBean
    private UserRepository userRepository;

    private final ObjectMapper objectMapper = new ObjectMapper();

    private final User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
    private final User bob = TestTokens.user(2L, "bob", "bob", Role.ROLE_USER);
    private final User admin = TestTokens.user(9L, "admin", "admin", Role.ROLE_ADMIN);

    @BeforeEach
    void setUp() {
        given(userRepository.findByUserId("alice")).willReturn(Optional.of(alice));
        given(userRepository.findByUserId("bob")).willReturn(Optional.of(bob));
        given(userRepository.findByUserId("admin")).willReturn(Optional.of(admin));
    }

    // ---------- 비로그인 401 ----------

    @Test
    void anonymousGets401JsonEverywhere() throws Exception {
        List<MockHttpServletRequestBuilder> requests = List.of(
                post("/api/travel-plans").contentType(MediaType.APPLICATION_JSON).content(BODY),
                get("/api/travel-plans"),
                get("/api/travel-plans/5"),
                delete("/api/travel-plans/5"));
        for (MockHttpServletRequestBuilder request : requests) {
            MvcResult result = mockMvc.perform(request).andReturn();
            String path = result.getRequest().getRequestURI();
            assertThat(result.getResponse().getStatus()).as(path).isEqualTo(401);
            assertThat(json(result).get("error").asText()).as(path).isEqualTo("UNAUTHORIZED");
        }
        verifyNoInteractions(travelPlanRepository);
    }

    // ---------- 저장 ----------

    @Test
    void createSaves201WithLocationAndOwnerFromToken() throws Exception {
        given(travelPlanRepository.findWithPlacesByUser_IdAndClientRequestId(1L, "call_plan_1")).willReturn(Optional.empty());
        given(travelPlanRepository.saveAndFlush(any())).willAnswer(invocation -> {
            TravelPlan plan = invocation.getArgument(0);
            plan.setId(100L);
            return plan;
        });

        // 본문에 남의 userId를 넣어도 주인은 토큰 사용자(alice). 모르는 칸(schema·createdVia)은 무시
        String body = BODY.replace("{\"clientRequestId\"", "{\"userId\":2,\"schema\":\"glople.plan.v1\",\"createdVia\":\"AGENT\",\"clientRequestId\"");
        mockMvc.perform(post("/api/travel-plans").header(HttpHeaders.AUTHORIZATION, bearer(alice))
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isCreated())
                .andExpect(header().string(HttpHeaders.LOCATION, "/api/travel-plans/100"))
                .andExpect(jsonPath("$.id").value(100))
                .andExpect(jsonPath("$.clientRequestId").value("call_plan_1"))
                .andExpect(jsonPath("$.title").value("부산 1박 2일"))
                .andExpect(jsonPath("$.region").value("부산"))
                .andExpect(jsonPath("$.days").value(2))
                .andExpect(jsonPath("$.places.length()").value(2))
                .andExpect(jsonPath("$.places[0].name").value("해운대해수욕장"))
                .andExpect(jsonPath("$.places[0].dayNo").value(1))
                .andExpect(jsonPath("$.places[0].position").value(1))
                .andExpect(jsonPath("$.places[0].source").value("TOOL_RESULT"))
                .andExpect(jsonPath("$.places[1].source").value("USER"))
                .andExpect(jsonPath("$.createdAt").isNotEmpty())
                .andExpect(jsonPath("$.userId").doesNotExist());

        ArgumentCaptor<TravelPlan> saved = ArgumentCaptor.forClass(TravelPlan.class);
        verify(travelPlanRepository).saveAndFlush(saved.capture());
        assertThat(saved.getValue().getUser().getId()).isEqualTo(1L);
        assertThat(saved.getValue().getPlaces()).extracting(TravelPlanPlace::getSource)
                .containsExactly(TravelPlanPlace.Source.TOOL_RESULT, TravelPlanPlace.Source.USER);
        assertThat(saved.getValue().getPlaces()).allSatisfy(place -> assertThat(place.getPlan()).isSameAs(saved.getValue()));
    }

    @Test
    void sameClientRequestIdReturns200WithTheFirstPlanAndSavesNothing() throws Exception {
        TravelPlan existing = plan(77L, alice, "call_plan_1", "처음 저장한 제목");
        given(travelPlanRepository.findWithPlacesByUser_IdAndClientRequestId(1L, "call_plan_1")).willReturn(Optional.of(existing));

        mockMvc.perform(post("/api/travel-plans").header(HttpHeaders.AUTHORIZATION, bearer(alice))
                        .contentType(MediaType.APPLICATION_JSON).content(BODY))
                .andExpect(status().isOk())
                .andExpect(header().doesNotExist(HttpHeaders.LOCATION))
                .andExpect(jsonPath("$.id").value(77))
                .andExpect(jsonPath("$.title").value("처음 저장한 제목"))
                .andExpect(jsonPath("$.createdAt").value("2026-10-18T14:03:00"));
        verify(travelPlanRepository, never()).saveAndFlush(any());
    }

    // 같은 키 요청 두 개가 동시에 와서 UNIQUE(user_id, client_request_id)에 걸리면 먼저 저장된 일정을 200으로
    @Test
    void concurrentDuplicateFallsBackToTheStoredPlan() throws Exception {
        TravelPlan stored = plan(78L, alice, "call_plan_1", "부산 1박 2일");
        given(travelPlanRepository.findWithPlacesByUser_IdAndClientRequestId(1L, "call_plan_1"))
                .willReturn(Optional.empty(), Optional.of(stored));
        given(travelPlanRepository.saveAndFlush(any())).willThrow(new DataIntegrityViolationException("uk_travel_plan_user_client_request"));

        mockMvc.perform(post("/api/travel-plans").header(HttpHeaders.AUTHORIZATION, bearer(alice))
                        .contentType(MediaType.APPLICATION_JSON).content(BODY))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(78));
    }

    @Test
    void invalidPlanIs400JsonWithProblemsAndNothingSaved() throws Exception {
        String body = """
                {"clientRequestId":"%s","title":"  ","region":"부산","days":2,
                 "places":[{"name":"%s","dayNo":3,"position":1,"source":"MODEL_SUGGESTED"},
                           {"name":"감천문화마을","dayNo":1,"position":1,"source":"USER"}]}
                """.formatted("k".repeat(81), "가".repeat(51));

        MvcResult result = mockMvc.perform(post("/api/travel-plans").header(HttpHeaders.AUTHORIZATION, bearer(alice))
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isBadRequest())
                .andReturn();

        JsonNode json = json(result);
        assertThat(json.get("status").asInt()).isEqualTo(400);
        assertThat(json.get("error").asText()).isEqualTo("BAD_REQUEST");
        assertThat(json.get("path").asText()).isEqualTo("/api/travel-plans");
        List<String> problems = new ArrayList<>();
        json.get("errors").forEach(p -> problems.add(p.get("field").asText() + ":" + p.get("code").asText()));
        assertThat(problems).containsExactly(
                "clientRequestId:too_long",
                "title:empty",
                "places[0].name:too_long",
                "places[0].dayNo:out_of_range",
                "places[0].source:unknown_value",
                "places[1].position:duplicate");
        verify(travelPlanRepository, never()).saveAndFlush(any());
        verify(travelPlanRepository, never()).findWithPlacesByUser_IdAndClientRequestId(anyLong(), anyString());
    }

    @Test
    void outOfRangeDaysAndEmptyPlacesAre400() throws Exception {
        expectProblems("{\"clientRequestId\":\"c\",\"title\":\"t\",\"region\":\"r\",\"days\":0,\"places\":[]}",
                "days:out_of_range", "places:empty");
        expectProblems("{\"clientRequestId\":\"c\",\"title\":\"t\",\"region\":\"r\",\"days\":31,"
                + "\"places\":[{\"name\":\"a\",\"dayNo\":1,\"position\":1,\"source\":\"USER\"}]}", "days:out_of_range");
        expectProblems("{}", "clientRequestId:empty", "title:empty", "region:empty", "days:empty", "places:empty");
        verify(travelPlanRepository, never()).saveAndFlush(any());
    }

    @Test
    void unreadableJsonIs400Json() throws Exception {
        mockMvc.perform(post("/api/travel-plans").header(HttpHeaders.AUTHORIZATION, bearer(alice))
                        .contentType(MediaType.APPLICATION_JSON).content("{\"days\":\"이틀\""))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value("BAD_REQUEST"))
                .andExpect(jsonPath("$.errors[0].field").value("body"));
        mockMvc.perform(post("/api/travel-plans").header(HttpHeaders.AUTHORIZATION, bearer(alice))
                        .contentType(MediaType.APPLICATION_JSON).content("{\"clientRequestId\":\"c\",\"days\":\"이틀\"}"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.status").value(400));
        verify(travelPlanRepository, never()).saveAndFlush(any());
    }

    // ---------- 조회 ----------

    @Test
    void listShowsOnlyMyPlansNewestFirst() throws Exception {
        TravelPlan newer = plan(8L, alice, "c2", "제주 여행");
        TravelPlan older = plan(7L, alice, "c1", "부산 여행");
        given(travelPlanRepository.findWithPlacesByUser_IdOrderByCreatedAtDescIdDesc(1L)).willReturn(List.of(newer, older));

        mockMvc.perform(get("/api/travel-plans").header(HttpHeaders.AUTHORIZATION, bearer(alice)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(2))
                .andExpect(jsonPath("$[0].id").value(8))
                .andExpect(jsonPath("$[0].title").value("제주 여행"))
                .andExpect(jsonPath("$[0].region").value("부산"))
                .andExpect(jsonPath("$[0].days").value(2))
                .andExpect(jsonPath("$[0].placeCount").value(2))
                .andExpect(jsonPath("$[0].createdAt").value("2026-10-18T14:03:00"))
                .andExpect(jsonPath("$[0].places").doesNotExist())
                .andExpect(jsonPath("$[1].id").value(7));
        verify(travelPlanRepository).findWithPlacesByUser_IdOrderByCreatedAtDescIdDesc(1L);
    }

    @Test
    void ownerAndAdminCanReadOthersAndMissingAre403() throws Exception {
        TravelPlan mine = plan(5L, alice, "c1", "부산 여행");
        given(travelPlanRepository.findWithPlacesByIdAndUser_Id(5L, 1L)).willReturn(Optional.of(mine));
        given(travelPlanRepository.findWithPlacesByIdAndUser_Id(5L, 2L)).willReturn(Optional.empty());
        given(travelPlanRepository.findWithPlacesByIdAndUser_Id(404L, 1L)).willReturn(Optional.empty());
        given(travelPlanRepository.findWithPlacesById(5L)).willReturn(Optional.of(mine));
        given(travelPlanRepository.findWithPlacesById(404L)).willReturn(Optional.empty());

        mockMvc.perform(get("/api/travel-plans/5").header(HttpHeaders.AUTHORIZATION, bearer(alice)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(5))
                .andExpect(jsonPath("$.places[1].name").value("감천문화마을"));
        expectForbidden(get("/api/travel-plans/5"), bob);       // 남의 일정
        expectForbidden(get("/api/travel-plans/404"), alice);   // 없는 일정도 403 (있는지 숨김)
        mockMvc.perform(get("/api/travel-plans/5").header(HttpHeaders.AUTHORIZATION, bearer(admin)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.id").value(5));
        expectForbidden(get("/api/travel-plans/404"), admin);
    }

    @Test
    void onlyTheOwnerCanDelete() throws Exception {
        TravelPlan mine = plan(5L, alice, "c1", "부산 여행");
        given(travelPlanRepository.findWithPlacesByIdAndUser_Id(5L, 1L)).willReturn(Optional.of(mine));
        given(travelPlanRepository.findWithPlacesByIdAndUser_Id(5L, 2L)).willReturn(Optional.empty());
        given(travelPlanRepository.findWithPlacesByIdAndUser_Id(5L, 9L)).willReturn(Optional.empty());

        expectForbidden(delete("/api/travel-plans/5"), bob);
        expectForbidden(delete("/api/travel-plans/5"), admin); // 관리자도 남의 일정은 지우지 못한다
        verify(travelPlanRepository, never()).delete(any());

        mockMvc.perform(delete("/api/travel-plans/5").header(HttpHeaders.AUTHORIZATION, bearer(alice)))
                .andExpect(status().isNoContent());
        verify(travelPlanRepository).delete(mine);
    }

    @Test
    void nonNumericIdIs400Json() throws Exception {
        mockMvc.perform(get("/api/travel-plans/abc").header(HttpHeaders.AUTHORIZATION, bearer(alice)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("id"));
    }

    // ---------- 도우미 ----------

    private void expectProblems(String body, String... expected) throws Exception {
        MvcResult result = mockMvc.perform(post("/api/travel-plans").header(HttpHeaders.AUTHORIZATION, bearer(alice))
                        .contentType(MediaType.APPLICATION_JSON).content(body))
                .andExpect(status().isBadRequest())
                .andReturn();
        List<String> problems = new ArrayList<>();
        json(result).get("errors").forEach(p -> problems.add(p.get("field").asText() + ":" + p.get("code").asText()));
        assertThat(problems).containsExactly(expected);
    }

    private void expectForbidden(MockHttpServletRequestBuilder request, User user) throws Exception {
        MvcResult result = mockMvc.perform(request.header(HttpHeaders.AUTHORIZATION, bearer(user))).andReturn();
        String path = result.getRequest().getRequestURI();
        assertThat(result.getResponse().getStatus()).as(path).isEqualTo(403);
        assertThat(json(result).get("error").asText()).as(path).isEqualTo("FORBIDDEN");
    }

    private JsonNode json(MvcResult result) throws Exception {
        return objectMapper.readTree(result.getResponse().getContentAsString(StandardCharsets.UTF_8));
    }

    private String bearer(User user) {
        return "Bearer " + TestTokens.tokenFor(jwtUtil, user);
    }

    private static TravelPlan plan(Long id, User owner, String clientRequestId, String title) {
        TravelPlan plan = new TravelPlan();
        plan.setId(id);
        plan.setUser(owner);
        plan.setClientRequestId(clientRequestId);
        plan.setTitle(title);
        plan.setRegion("부산");
        plan.setDays(2);
        plan.setCreatedAt(LocalDateTime.of(2026, 10, 18, 14, 3, 0));
        plan.addPlace(place(2, 2, "감천문화마을", TravelPlanPlace.Source.USER));
        plan.addPlace(place(1, 1, "해운대해수욕장", TravelPlanPlace.Source.TOOL_RESULT));
        return plan;
    }

    private static TravelPlanPlace place(int position, int dayNo, String name, TravelPlanPlace.Source source) {
        TravelPlanPlace place = new TravelPlanPlace();
        place.setPosition(position);
        place.setDayNo(dayNo);
        place.setName(name);
        place.setSource(source);
        return place;
    }
}
