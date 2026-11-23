package proj.travien.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.HttpServerErrorException;
import proj.travien.dto.TourApiItem;
import proj.travien.dto.TourApiPage;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URI;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

class TourApiClientTest {

    private final TourApiClient client = client("decoding+key/abc==");
    private final List<String> receivedQueries = new CopyOnWriteArrayList<>();
    private HttpServer server;

    @AfterEach
    void stopServer() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void parsesAreaBasedItemsFromArray() {
        String body = """
                {"response":{"header":{"resultCode":"0000","resultMsg":"OK"},
                 "body":{"items":{"item":[
                   {"contentid":"126508","contenttypeid":"12","title":"해운대해수욕장",
                    "addr1":"부산광역시 해운대구 우동","areacode":"6","sigungucode":"16","cat3":"A01011200",
                    "lDongRegnCd":"26","lDongSignguCd":"350","lclsSystm3":"NA040500",
                    "mapx":"129.1586","mapy":"35.1587","firstimage":"http://tong.visitkorea.or.kr/a.jpg",
                    "tel":"","cpyrhtDivCd":"Type3","modifiedtime":"20250101120000"},
                   {"contentid":"126509","contenttypeid":"12","title":"광안리해수욕장","mapx":"","mapy":null}
                 ]},"numOfRows":2,"pageNo":1,"totalCount":2}}}
                """;

        TourApiPage page = client.parseAreaBasedPage(body);

        assertThat(page.totalCount()).isEqualTo(2);
        assertThat(page.items()).hasSize(2);
        TourApiItem first = page.items().get(0);
        assertThat(first.contentId()).isEqualTo("126508");
        assertThat(first.title()).isEqualTo("해운대해수욕장");
        assertThat(first.latitude()).isEqualTo(35.1587);
        assertThat(first.longitude()).isEqualTo(129.1586);
        // 삭제 예정인 areacode·cat3 대신 법정동·분류체계 코드를 읽는다.
        assertThat(first.regionCode()).isEqualTo("26");
        assertThat(first.sigunguCode()).isEqualTo("350");
        assertThat(first.category()).isEqualTo("NA040500");
        assertThat(first.copyrightType()).isEqualTo("Type3");
        assertThat(first.modifiedTime()).isEqualTo("20250101120000");
        assertThat(page.items().get(1).latitude()).isNull();
    }

    @Test
    void parsesSingleItemObjectAndSkipsItemsWithoutId() {
        String single = """
                {"response":{"header":{"resultCode":"0000"},
                 "body":{"items":{"item":{"contentid":"1","title":"경복궁"}},"totalCount":1}}}
                """;
        String withoutId = """
                {"response":{"header":{"resultCode":"0000"},
                 "body":{"items":{"item":[{"contentid":"","title":"이름만"}]},"totalCount":1}}}
                """;

        assertThat(client.parseAreaBasedPage(single).items()).extracting(TourApiItem::title).containsExactly("경복궁");
        assertThat(client.parseAreaBasedPage(withoutId).items()).isEmpty();
    }

    @Test
    void treatsEmptyItemsStringAsNoResult() {
        String body = """
                {"response":{"header":{"resultCode":"0000"},"body":{"items":"","totalCount":0}}}
                """;

        TourApiPage page = client.parseAreaBasedPage(body);

        assertThat(page.totalCount()).isZero();
        assertThat(page.items()).isEmpty();
    }

    @Test
    void readsOverviewFromDetailCommon() {
        String body = """
                {"response":{"header":{"resultCode":"0000"},
                 "body":{"items":{"item":[{"contentid":"126508","overview":"부산의 대표 해변<br>입니다."}]}}}}
                """;

        assertThat(client.parseOverview(body)).isEqualTo("부산의 대표 해변<br>입니다.");
    }

    @Test
    void mapsGatewayQuotaErrorToQuotaException() {
        String xml = """
                <OpenAPI_ServiceResponse><cmmMsgHeader><errMsg>SERVICE ERROR</errMsg>
                <returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg>
                <returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>
                """;

        assertThatThrownBy(() -> client.parseAreaBasedPage(xml))
                .isInstanceOf(TourApiClient.QuotaExceededException.class);
    }

    @Test
    void reportsOtherErrorsWithCode() {
        String gateway = """
                <OpenAPI_ServiceResponse><cmmMsgHeader>
                <returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>
                <returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>
                """;
        String invalidParameter = """
                {"responseTime":"2026-10-17T10:00:00","resultCode":"10","resultMsg":"INVALID_REQUEST_PARAMETER_ERROR"}
                """;

        assertThatThrownBy(() -> client.parseAreaBasedPage(gateway))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("30")
                .hasMessageContaining("SERVICE_KEY_IS_NOT_REGISTERED_ERROR");
        assertThatThrownBy(() -> client.parseOverview(invalidParameter))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("INVALID_REQUEST_PARAMETER_ERROR");
    }

    @Test
    void encodesDecodingKeyAndKeepsEncodingKey() {
        URI decoding = client.buildUri("/detailCommon2", Map.of("contentId", "126508"));
        URI encoding = client("already%2Bencoded%3D%3D").buildUri("/detailCommon2", Map.of("contentId", "1"));

        assertThat(decoding.getRawQuery())
                .startsWith("serviceKey=decoding%2Bkey%2Fabc%3D%3D&MobileOS=ETC&MobileApp=Glople&_type=json")
                .endsWith("&contentId=126508");
        assertThat(encoding.getRawQuery()).startsWith("serviceKey=already%2Bencoded%3D%3D&");
    }

    @Test
    void refusesToBuildRequestWithoutServiceKey() {
        TourApiClient unconfigured = client("  ");

        assertThat(unconfigured.isConfigured()).isFalse();
        assertThatThrownBy(() -> unconfigured.buildUri("/areaBasedList2", Map.of()))
                .isInstanceOf(IllegalStateException.class);
    }

    @Test
    void mapsHttp429ToQuotaException() throws IOException {
        TourApiClient local = localClient(429, "text/plain", "Too Many Requests");

        assertThatThrownBy(() -> local.fetchOverview("126508"))
                .isInstanceOf(TourApiClient.QuotaExceededException.class);
    }

    @Test
    void readsGatewayXmlReturnedWithHttp200() throws IOException {
        String quota = "<OpenAPI_ServiceResponse><cmmMsgHeader>"
                + "<returnAuthMsg>LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR</returnAuthMsg>"
                + "<returnReasonCode>22</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>";
        String unregistered = "<OpenAPI_ServiceResponse><cmmMsgHeader>"
                + "<returnAuthMsg>SERVICE_KEY_IS_NOT_REGISTERED_ERROR</returnAuthMsg>"
                + "<returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>";

        assertThatThrownBy(() -> localClient(200, "text/xml", quota).fetchAreaBasedPage("12", 1, 10, "A"))
                .isInstanceOf(TourApiClient.QuotaExceededException.class);
        stopServer();
        assertThatThrownBy(() -> localClient(200, "text/xml", unregistered).fetchOverview("1"))
                .isInstanceOf(IllegalStateException.class)
                .isNotInstanceOf(TourApiClient.QuotaExceededException.class)
                .hasMessageContaining("SERVICE_KEY_IS_NOT_REGISTERED_ERROR");
    }

    @Test
    void readsGatewayJsonReturnedWithHttp403() throws IOException {
        // 2026-10-17 실제 KorService2가 미등록 키에 돌려준 응답 형식
        String unregistered = """
                {
                  "OpenAPI_ServiceResponse": {
                    "cmmMsgHeader": {
                      "errMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
                      "returnAuthMsg": "등록되지 않은 서비스키",
                      "returnReasonCode": "30"
                    }
                  }
                }
                """;
        String quota = """
                {"OpenAPI_ServiceResponse":{"cmmMsgHeader":{"errMsg":"LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
                 "returnAuthMsg":"LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR","returnReasonCode":"22"}}}
                """;

        assertThatThrownBy(() -> localClient(403, "application/json", unregistered).fetchAreaBasedPage("12", 1, 10, "A"))
                .isInstanceOf(IllegalStateException.class)
                .isNotInstanceOf(TourApiClient.QuotaExceededException.class)
                .hasMessage("TourAPI 게이트웨이 오류: 30 SERVICE_KEY_IS_NOT_REGISTERED_ERROR (등록되지 않은 서비스키)");
        stopServer();
        assertThatThrownBy(() -> localClient(200, "application/json", quota).fetchOverview("1"))
                .isInstanceOf(TourApiClient.QuotaExceededException.class);
    }

    @Test
    void keepsHttpErrorWhenBodyIsNotGatewayError() throws IOException {
        TourApiClient local = localClient(404, "text/plain", "Not Found");

        assertThatThrownBy(() -> local.fetchOverview("1"))
                .isInstanceOf(HttpClientErrorException.NotFound.class);
    }

    @Test
    void treatsServerErrorAsOrdinaryFailure() throws IOException {
        TourApiClient local = localClient(500, "text/plain", "error");

        // 5xx는 한도 초과가 아니므로 설명문 동기화에서 개별 실패로 센다.
        assertThatThrownBy(() -> local.fetchOverview("1"))
                .isInstanceOf(HttpServerErrorException.class);
    }

    @Test
    void sendsServiceKeyThatServerDecodesToOriginal() throws IOException {
        String body = """
                {"response":{"header":{"resultCode":"0000"},
                 "body":{"items":{"item":{"contentid":"126508","title":"해운대해수욕장"}},"totalCount":1}}}
                """;

        TourApiPage page = localClient(200, "application/json;charset=UTF-8", body).fetchAreaBasedPage("12", 1, 2, "A");

        assertThat(page.items()).extracting(TourApiItem::title).containsExactly("해운대해수욕장");
        assertThat(receivedQueries).hasSize(1);
        String rawQuery = receivedQueries.get(0);
        assertThat(rawQuery).contains("serviceKey=decoding%2Bkey%2Fabc%3D%3D", "contentTypeId=12", "arrange=A",
                "numOfRows=2", "pageNo=1", "_type=json");
        assertThat(URLDecoder.decode(rawQuery.split("&")[0].substring("serviceKey=".length()), StandardCharsets.UTF_8))
                .isEqualTo("decoding+key/abc==");
    }

    // 127.0.0.1의 임시 포트에서 정해진 응답을 돌려주는 TourAPI 대역
    private TourApiClient localClient(int status, String contentType, String body) throws IOException {
        server = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        server.createContext("/KorService2", exchange -> {
            receivedQueries.add(exchange.getRequestURI().getRawQuery());
            byte[] payload = body.getBytes(StandardCharsets.UTF_8);
            exchange.getResponseHeaders().set("Content-Type", contentType);
            exchange.sendResponseHeaders(status, payload.length);
            try (OutputStream out = exchange.getResponseBody()) {
                out.write(payload);
            }
        });
        server.start();
        String baseUrl = "http://127.0.0.1:" + server.getAddress().getPort() + "/KorService2";
        return new TourApiClient(new ObjectMapper(), baseUrl, "decoding+key/abc==", "Glople");
    }

    private static TourApiClient client(String serviceKey) {
        return new TourApiClient(new ObjectMapper(), "https://apis.example.test/KorService2", serviceKey, "Glople");
    }
}
