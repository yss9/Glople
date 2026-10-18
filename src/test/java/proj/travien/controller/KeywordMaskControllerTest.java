package proj.travien.controller;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.security.access.prepost.PreAuthorize;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import proj.travien.domain.DestinationInfo;
import proj.travien.domain.Role;
import proj.travien.domain.User;
import proj.travien.jwt.CurrentUser;
import proj.travien.jwt.JwtTokenProvider;
import proj.travien.jwt.JwtUtil;
import proj.travien.jwt.SecurityConfig;
import proj.travien.jwt.TestTokens;
import proj.travien.repository.DestinationInfoRepository;
import proj.travien.repository.UserRepository;
import proj.travien.service.KeywordMask;
import proj.travien.service.KeywordTaggerImportService;
import proj.travien.service.TourSyncRunner;

import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Optional;
import java.util.function.Supplier;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.BDDMockito.willReturn;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * 태거 예측 가져오기 API를 실제 SecurityConfig·JWT 필터·KeywordTaggerImportService로 확인한다. 저장소와 동기화 실행기만 목(DB 없음).
 * 관리자만 200, 일반 사용자 403, 비로그인 401, 형식 오류 400 JSON(아무것도 쓰지 않음), 동기화 중 409.
 */
@WebMvcTest(KeywordMaskController.class)
@Import({SecurityConfig.class, JwtTokenProvider.class, JwtUtil.class, CurrentUser.class, KeywordTaggerImportService.class})
@TestPropertySource(properties = {"jwt.secret=" + TestTokens.SECRET, "jwt.expiration=3600000"})
class KeywordMaskControllerTest {

    private static final String INFO = "가거도는 섬이다.";
    // predict_all.py 출력 형식 그대로 (info_crc32 = zlib.crc32(INFO.encode()))
    private static final String LINES = """
            {"source": "TOUR_API", "id": "127480", "keywords": ["섬", "경치"], "scores": {"섬": 0.9, "경치": 0.8}, "model": "roberta-base-v3", "info_crc32": 2972272251}
            {"source": "WIKIDATA", "id": "Q404", "keywords": ["섬"], "scores": {"섬": 0.7}, "model": "roberta-base-v3", "info_crc32": 1}
            """;

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private JwtUtil jwtUtil;

    @MockBean
    private DestinationInfoRepository destinationInfoRepository;

    @MockBean
    private UserRepository userRepository;

    @MockBean
    private TourSyncRunner tourSyncRunner;

    private String adminToken;
    private String userToken;
    private DestinationInfo island;

    @BeforeEach
    void setUp() {
        User admin = TestTokens.user(9L, "admin", "admin", Role.ROLE_ADMIN);
        User alice = TestTokens.user(1L, "alice", "alice", Role.ROLE_USER);
        given(userRepository.findByUserId("admin")).willReturn(Optional.of(admin));
        given(userRepository.findByUserId("alice")).willReturn(Optional.of(alice));
        adminToken = TestTokens.tokenFor(jwtUtil, admin);
        userToken = TestTokens.tokenFor(jwtUtil, alice);

        island = new DestinationInfo();
        island.setId(1L);
        island.setSource(DestinationInfo.SOURCE_TOUR_API);
        island.setExternalId("127480");
        island.setInfo(INFO);
        given(destinationInfoRepository.findAllBySourceAndExternalIdIn(eq("TOUR_API"), anyCollection())).willReturn(List.of(island));
        // 실행기는 받은 작업을 바로 실행한다 (다른 동기화가 없을 때)
        given(tourSyncRunner.runExclusively(any())).willAnswer(invocation -> Optional.of(((Supplier<?>) invocation.getArgument(0)).get()));
    }

    @Test
    void adminImportsPredictAllJsonLines() throws Exception {
        mockMvc.perform(post("/api/admin/keyword-masks/import")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_NDJSON)
                        .content(LINES.getBytes(StandardCharsets.UTF_8)))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.dryRun").value(false))
                .andExpect(jsonPath("$.received").value(2))
                .andExpect(jsonPath("$.updated").value(1))
                .andExpect(jsonPath("$.unchanged").value(0))
                .andExpect(jsonPath("$.notFound").value(1))
                .andExpect(jsonPath("$.staleDescription").value(0))
                .andExpect(jsonPath("$.models[0]").value("roberta-base-v3"))
                .andExpect(jsonPath("$.notFoundSamples[0]").value("WIKIDATA:Q404"))
                .andExpect(jsonPath("$.recommendMaskMode").value("tagger"));

        assertThat(island.taggerMask()).isEqualTo(KeywordMask.fromKeywords(List.of("섬", "경치")));
        assertThat(island.getTaggerModel()).isEqualTo("roberta-base-v3");
        verify(destinationInfoRepository).saveAll(List.of(island));
    }

    // JSON 배열 + model 파라미터, dryRun=true면 세기만
    @Test
    void dryRunWithJsonArrayWritesNothing() throws Exception {
        mockMvc.perform(post("/api/admin/keyword-masks/import").param("dryRun", "true").param("model", "roberta-base-v3")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("[{\"source\":\"TOUR_API\",\"externalId\":\"127480\",\"keywords\":[\"섬\"]}]"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.dryRun").value(true))
                .andExpect(jsonPath("$.updated").value(1));

        assertThat(island.getTaggerModel()).isNull();
        verify(destinationInfoRepository, never()).saveAll(any());
    }

    @Test
    void onlyAdminsCanImport() throws Exception {
        mockMvc.perform(post("/api/admin/keyword-masks/import").contentType(MediaType.APPLICATION_NDJSON).content(LINES))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.error").value("UNAUTHORIZED"));
        mockMvc.perform(post("/api/admin/keyword-masks/import")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + userToken)
                        .contentType(MediaType.APPLICATION_NDJSON).content(LINES))
                .andExpect(status().isForbidden())
                .andExpect(jsonPath("$.error").value("FORBIDDEN"));

        verifyNoInteractions(destinationInfoRepository);
        verifyNoInteractions(tourSyncRunner);
    }

    // 목록에 없는 키워드가 하나라도 있으면 400 JSON (errors 칸별), 아무것도 쓰지 않는다
    @Test
    void invalidFileIsRejectedWithFieldErrors() throws Exception {
        String bad = LINES + "{\"source\":\"TOUR_API\",\"id\":\"9\",\"keywords\":[\"바닷가\"],\"model\":\"roberta-base-v3\"}\n";

        mockMvc.perform(post("/api/admin/keyword-masks/import")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_NDJSON).content(bad.getBytes(StandardCharsets.UTF_8)))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.status").value(400))
                .andExpect(jsonPath("$.error").value("BAD_REQUEST"))
                .andExpect(jsonPath("$.path").value("/api/admin/keyword-masks/import"))
                .andExpect(jsonPath("$.errors[0].field").value("lines[3].keywords[0]"))
                .andExpect(jsonPath("$.errors[0].code").value("unknown_keyword"));

        mockMvc.perform(post("/api/admin/keyword-masks/import").param("dryRun", "maybe")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_NDJSON).content(LINES))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("dryRun"));

        verifyNoInteractions(destinationInfoRepository);
        verifyNoInteractions(tourSyncRunner);
    }

    // curl --data-binary 기본값(form)처럼 본문 형식을 밝히지 않으면 받지 않는다 (form 본문은 파라미터로 먼저 읽힐 수 있음)
    @Test
    void requiresJsonLinesJsonOrTextContentType() throws Exception {
        mockMvc.perform(post("/api/admin/keyword-masks/import")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_FORM_URLENCODED).content(LINES))
                .andExpect(status().isUnsupportedMediaType());
        mockMvc.perform(post("/api/admin/keyword-masks/import")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + adminToken)
                        .contentType(MediaType.TEXT_PLAIN).content(LINES.getBytes(StandardCharsets.UTF_8)))
                .andExpect(status().isOk());
    }

    @Test
    void conflictWhileAnotherSyncRuns() throws Exception {
        // setUp의 응답(받은 작업 실행)을 부르지 않도록 willReturn(...).given(...) 형태로 바꾼다
        willReturn(Optional.empty()).given(tourSyncRunner).runExclusively(any());

        mockMvc.perform(post("/api/admin/keyword-masks/import")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer " + adminToken)
                        .contentType(MediaType.APPLICATION_NDJSON).content(LINES.getBytes(StandardCharsets.UTF_8)))
                .andExpect(status().isConflict())
                .andExpect(jsonPath("$.status").value(409))
                .andExpect(jsonPath("$.error").value("CONFLICT"));

        verify(destinationInfoRepository, never()).saveAll(any());
        assertThat(island.getTaggerModel()).isNull();
    }

    @Test
    void mapsToAnAdminOnlyRoute() throws NoSuchMethodException {
        assertThat(KeywordMaskController.class.getAnnotation(RequestMapping.class).value()).containsExactly("/api/admin/keyword-masks");
        Method method = KeywordMaskController.class.getMethod("importPredictions", byte[].class, String.class, boolean.class,
                jakarta.servlet.http.HttpServletRequest.class);
        assertThat(method.getAnnotation(PostMapping.class).value()).containsExactly("/import");
        assertThat(method.getAnnotation(PreAuthorize.class).value()).isEqualTo("hasRole('ADMIN')");
    }
}
