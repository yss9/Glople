package proj.travien.controller;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.context.annotation.Import;
import org.springframework.http.HttpHeaders;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;
import proj.travien.domain.DestinationInfo;
import proj.travien.jwt.CurrentUser;
import proj.travien.jwt.JwtTokenProvider;
import proj.travien.jwt.JwtUtil;
import proj.travien.jwt.SecurityConfig;
import proj.travien.jwt.TestTokens;
import proj.travien.repository.DestinationInfoRepository;
import proj.travien.repository.UserRepository;
import proj.travien.service.DestinationSearchService;

import java.util.List;
import java.util.Optional;

import static org.mockito.ArgumentMatchers.anyInt;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.BDDMockito.given;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

// 여행지 읽기 API: 비로그인 공개, 응답 칸 이름, 400·404 JSON (실제 SecurityConfig, 저장소만 목)
@WebMvcTest(DestinationController.class)
@Import({SecurityConfig.class, JwtTokenProvider.class, JwtUtil.class, CurrentUser.class, DestinationSearchService.class})
@TestPropertySource(properties = {"jwt.secret=" + TestTokens.SECRET, "jwt.expiration=3600000"})
class DestinationControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private DestinationInfoRepository destinationInfoRepository;

    @MockBean
    private UserRepository userRepository;

    @Test
    void anonymousCanSearchWithItemFieldsForAttribution() throws Exception {
        given(destinationInfoRepository.searchDestinations(anyInt(), anyString(), anyString(), anyString(), anyInt(), anyString(),
                anyInt(), anyLong(), anyLong(), anyString(), anyString(), anyLong(), anyInt()))
                .willReturn(List.of(haeundae()));

        mockMvc.perform(get("/api/destinations").param("region", "부산").param("category", "관광지").param("limit", "5")
                        .header(HttpHeaders.AUTHORIZATION, "Bearer undefined"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$[0].id").value(10))
                .andExpect(jsonPath("$[0].name").value("해운대해수욕장"))
                .andExpect(jsonPath("$[0].country").value("대한민국"))
                .andExpect(jsonPath("$[0].location").value("부산광역시 해운대구"))
                .andExpect(jsonPath("$[0].category").value("관광지"))
                .andExpect(jsonPath("$[0].info").value("부산광역시 해운대구의 관광지입니다."))
                .andExpect(jsonPath("$[0].infoKind").value("SUMMARY"))
                .andExpect(jsonPath("$[0].imageUrl").value("http://tong.visitkorea.or.kr/a.jpg"))
                .andExpect(jsonPath("$[0].lat").value(35.1587))
                .andExpect(jsonPath("$[0].lng").value(129.1604))
                .andExpect(jsonPath("$[0].source").value("TOUR_API"))
                .andExpect(jsonPath("$[0].copyrightType").value("Type3"))
                .andExpect(jsonPath("$[0].license").isEmpty())
                .andExpect(jsonPath("$[0].sourceUrl").isEmpty())
                .andExpect(jsonPath("$[0].address").doesNotExist());
    }

    @Test
    void anonymousCanLookupAndReadDetailWithAddress() throws Exception {
        given(destinationInfoRepository.lookupByName(eq("해운대해수욕장"), anyString(), anyString(), anyString(), anyInt()))
                .willReturn(List.of(haeundae()));
        given(destinationInfoRepository.findById(10L)).willReturn(Optional.of(haeundae()));
        given(destinationInfoRepository.findById(404L)).willReturn(Optional.empty());

        mockMvc.perform(get("/api/destinations/lookup").param("name", "해운대해수욕장"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.length()").value(1))
                .andExpect(jsonPath("$[0].location").value("부산광역시 해운대구"));
        mockMvc.perform(get("/api/destinations/10"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.name").value("해운대해수욕장"))
                .andExpect(jsonPath("$.address").value("부산광역시 해운대구 우동"))
                .andExpect(jsonPath("$.regionCode").value("26"))
                .andExpect(jsonPath("$.sigunguCode").value("350"))
                .andExpect(jsonPath("$.summary").value("부산광역시 해운대구의 관광지입니다."));
        mockMvc.perform(get("/api/destinations/404"))
                .andExpect(status().isNotFound())
                .andExpect(jsonPath("$.status").value(404))
                .andExpect(jsonPath("$.error").value("NOT_FOUND"))
                .andExpect(jsonPath("$.path").value("/api/destinations/404"));
    }

    @Test
    void badValuesAre400JsonWithProblems() throws Exception {
        mockMvc.perform(get("/api/destinations").param("category", "맛집").param("limit", "100"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.status").value(400))
                .andExpect(jsonPath("$.error").value("BAD_REQUEST"))
                .andExpect(jsonPath("$.errors[0].field").value("category"))
                .andExpect(jsonPath("$.errors[1].field").value("limit"));
        mockMvc.perform(get("/api/destinations").param("limit", "많이"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("limit"));
        mockMvc.perform(get("/api/destinations/lookup"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("name"));
        mockMvc.perform(get("/api/destinations/abc"))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.errors[0].field").value("id"));
        verifyNoInteractions(destinationInfoRepository);
    }

    // 읽기만 공개. 같은 경로의 다른 메서드는 화이트리스트 밖이라 로그인이 필요하다.
    @Test
    void onlyGetIsPublic() throws Exception {
        mockMvc.perform(post("/api/destinations")).andExpect(status().isUnauthorized());
    }

    private static DestinationInfo haeundae() {
        DestinationInfo destination = new DestinationInfo();
        destination.setId(10L);
        destination.setDestName("해운대해수욕장");
        destination.setCountry("대한민국");
        destination.setLocation("부산광역시 해운대구");
        destination.setSource(DestinationInfo.SOURCE_TOUR_API);
        destination.setContentTypeId("12");
        destination.setSummary("부산광역시 해운대구의 관광지입니다.");
        destination.setCopyrightType("Type3");
        destination.setImageUrl("http://tong.visitkorea.or.kr/a.jpg");
        destination.setLatitude(35.1587);
        destination.setLongitude(129.1604);
        destination.setAddress("부산광역시 해운대구 우동");
        destination.setRegionCode("26");
        destination.setSigunguCode("350");
        return destination;
    }
}
