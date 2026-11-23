package proj.travien.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.HttpClientErrorException;
import org.springframework.web.client.RestClient;
import proj.travien.dto.TourApiItem;
import proj.travien.dto.TourApiPage;

import java.net.URI;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 한국관광공사 국문 관광정보 서비스(TourAPI KorService2) 호출과 응답 해석.
 */
@Component
public class TourApiClient {

    private static final String DEFAULT_BASE_URL = "https://apis.data.go.kr/B551011/KorService2";
    private static final String QUOTA_REASON_CODE = "22";

    private final RestClient restClient;
    private final ObjectMapper objectMapper;
    private final String baseUrl;
    private final String serviceKey;
    private final String mobileApp;

    public TourApiClient(
            ObjectMapper objectMapper,
            @Value("${tour.api.base-url:" + DEFAULT_BASE_URL + "}") String baseUrl,
            @Value("${tour.api.service-key:}") String serviceKey,
            @Value("${tour.api.mobile-app:Glople}") String mobileApp
    ) {
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(Duration.ofSeconds(10));
        requestFactory.setReadTimeout(Duration.ofSeconds(30));

        this.restClient = RestClient.builder().requestFactory(requestFactory).build();
        this.objectMapper = objectMapper;
        this.baseUrl = baseUrl;
        this.serviceKey = serviceKey == null ? "" : serviceKey.trim();
        this.mobileApp = mobileApp;
    }

    public boolean isConfigured() {
        return !serviceKey.isEmpty();
    }

    // 지역기반 관광정보 목록. 설명문(overview)은 포함되지 않는다.
    public TourApiPage fetchAreaBasedPage(String contentTypeId, int pageNo, int numOfRows, String arrange) {
        Map<String, String> params = new LinkedHashMap<>();
        params.put("contentTypeId", contentTypeId);
        params.put("arrange", arrange);
        params.put("numOfRows", String.valueOf(numOfRows));
        params.put("pageNo", String.valueOf(pageNo));
        return parseAreaBasedPage(get("/areaBasedList2", params));
    }

    // 공통정보 조회로 여행지 한 곳의 설명문을 받는다. 호출 한 번이 일일 한도 1건을 쓴다.
    public String fetchOverview(String contentId) {
        return parseOverview(get("/detailCommon2", Map.of("contentId", contentId)));
    }

    URI buildUri(String path, Map<String, String> params) {
        if (!isConfigured()) {
            throw new IllegalStateException("tour.api.service-key가 설정되지 않았습니다.");
        }

        StringBuilder query = new StringBuilder()
                .append("serviceKey=").append(encodedServiceKey())
                .append("&MobileOS=ETC")
                .append("&MobileApp=").append(encode(mobileApp))
                .append("&_type=json");
        params.forEach((name, value) -> {
            if (value != null && !value.isBlank()) {
                query.append('&').append(name).append('=').append(encode(value));
            }
        });
        return URI.create(baseUrl + path + "?" + query);
    }

    TourApiPage parseAreaBasedPage(String body) {
        JsonNode root = readResponse(body);
        int totalCount = root.at("/response/body/totalCount").asInt(0);

        List<TourApiItem> items = new ArrayList<>();
        for (JsonNode node : itemNodes(root)) {
            TourApiItem item = toItem(node);
            if (!item.contentId().isEmpty() && !item.title().isEmpty()) {
                items.add(item);
            }
        }
        return new TourApiPage(totalCount, items);
    }

    String parseOverview(String body) {
        List<JsonNode> nodes = itemNodes(readResponse(body));
        return nodes.isEmpty() ? "" : text(nodes.get(0), "overview");
    }

    private String get(String path, Map<String, String> params) {
        try {
            return restClient.get()
                    .uri(buildUri(path, params))
                    .accept(MediaType.APPLICATION_JSON)
                    .retrieve()
                    .body(String.class);
        } catch (HttpClientErrorException.TooManyRequests e) {
            throw new QuotaExceededException("TourAPI 호출 한도를 초과했습니다 (HTTP 429).");
        } catch (HttpClientErrorException e) {
            // 미등록 키 같은 인증 오류는 HTTP 403 등과 함께 게이트웨이 오류 본문(JSON 또는 XML)으로 온다.
            RuntimeException gatewayError = gatewayErrorOf(e.getResponseBodyAsString());
            throw gatewayError != null ? gatewayError : e;
        }
    }

    private JsonNode readResponse(String body) {
        if (body == null || body.isBlank()) {
            throw new IllegalStateException("TourAPI 응답이 비어 있습니다.");
        }

        String trimmed = body.trim();
        // 인증·호출 한도 오류는 공공데이터포털 게이트웨이가 XML 또는 OpenAPI_ServiceResponse JSON으로 돌려준다.
        if (trimmed.startsWith("<")) {
            throw gatewayErrorOf(trimmed);
        }

        JsonNode root;
        try {
            root = objectMapper.readTree(trimmed);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("TourAPI 응답을 해석할 수 없습니다: " + abbreviate(trimmed));
        }
        if (root.has("OpenAPI_ServiceResponse")) {
            throw gatewayErrorOf(root);
        }

        String resultCode = firstText(root, "/response/header/resultCode", "/resultCode");
        if (!resultCode.isEmpty() && !"0000".equals(resultCode)) {
            String resultMsg = firstText(root, "/response/header/resultMsg", "/resultMsg");
            if (QUOTA_REASON_CODE.equals(resultCode)) {
                throw new QuotaExceededException("TourAPI 호출 한도를 초과했습니다: " + resultMsg);
            }
            throw new IllegalStateException("TourAPI 오류: " + resultCode + " " + resultMsg);
        }
        return root;
    }

    // 게이트웨이 오류 본문이 아니면 null
    private RuntimeException gatewayErrorOf(String body) {
        if (body == null || body.isBlank()) {
            return null;
        }
        String trimmed = body.trim();
        if (trimmed.startsWith("<")) {
            return gatewayError(
                    between(trimmed, "<returnReasonCode>", "</returnReasonCode>"),
                    between(trimmed, "<errMsg>", "</errMsg>"),
                    between(trimmed, "<returnAuthMsg>", "</returnAuthMsg>"));
        }
        try {
            JsonNode root = objectMapper.readTree(trimmed);
            return root.has("OpenAPI_ServiceResponse") ? gatewayErrorOf(root) : null;
        } catch (JsonProcessingException e) {
            return null;
        }
    }

    private RuntimeException gatewayErrorOf(JsonNode root) {
        JsonNode header = root.at("/OpenAPI_ServiceResponse/cmmMsgHeader");
        return gatewayError(
                header.path("returnReasonCode").asText("").trim(),
                header.path("errMsg").asText("").trim(),
                header.path("returnAuthMsg").asText("").trim());
    }

    private RuntimeException gatewayError(String reasonCode, String errMsg, String authMsg) {
        // errMsg는 영문 코드(SERVICE_KEY_IS_NOT_REGISTERED_ERROR), returnAuthMsg는 한글 설명이거나 같은 영문 코드다.
        String message = errMsg.equals(authMsg) || errMsg.isEmpty() ? authMsg
                : authMsg.isEmpty() ? errMsg : errMsg + " (" + authMsg + ")";
        if (QUOTA_REASON_CODE.equals(reasonCode) || message.contains("LIMITED_NUMBER_OF_SERVICE_REQUESTS")) {
            return new QuotaExceededException("TourAPI 호출 한도를 초과했습니다: " + message);
        }
        return new IllegalStateException("TourAPI 게이트웨이 오류: " + reasonCode + " " + message);
    }

    // 결과가 여러 건이면 배열, 한 건이면 객체, 없으면 "items": "" 로 온다.
    private List<JsonNode> itemNodes(JsonNode root) {
        JsonNode item = root.at("/response/body/items/item");
        if (item.isMissingNode() || item.isNull()) {
            return List.of();
        }
        if (!item.isArray()) {
            return List.of(item);
        }

        List<JsonNode> nodes = new ArrayList<>();
        item.forEach(nodes::add);
        return nodes;
    }

    private TourApiItem toItem(JsonNode node) {
        return new TourApiItem(
                text(node, "contentid"),
                text(node, "contenttypeid"),
                text(node, "title"),
                text(node, "addr1"),
                text(node, "lDongRegnCd"),
                text(node, "lDongSignguCd"),
                text(node, "lclsSystm3"),
                decimal(node, "mapy"),
                decimal(node, "mapx"),
                text(node, "firstimage"),
                text(node, "tel"),
                text(node, "cpyrhtDivCd"),
                text(node, "modifiedtime")
        );
    }

    private String encodedServiceKey() {
        // 포털의 Encoding 키(이미 %인코딩됨)와 Decoding 키를 모두 받는다.
        return serviceKey.contains("%") ? serviceKey : encode(serviceKey);
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private static String text(JsonNode node, String fieldName) {
        JsonNode value = node.get(fieldName);
        if (value == null || value.isNull()) {
            return "";
        }
        String text = value.asText("").trim();
        return "null".equalsIgnoreCase(text) ? "" : text;
    }

    private static Double decimal(JsonNode node, String fieldName) {
        String value = text(node, fieldName);
        if (value.isEmpty()) {
            return null;
        }
        try {
            return Double.valueOf(value);
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static String firstText(JsonNode root, String... pointers) {
        for (String pointer : pointers) {
            String value = root.at(pointer).asText("").trim();
            if (!value.isEmpty()) {
                return value;
            }
        }
        return "";
    }

    private static String between(String source, String open, String close) {
        int start = source.indexOf(open);
        if (start < 0) {
            return "";
        }
        int end = source.indexOf(close, start + open.length());
        return end < 0 ? "" : source.substring(start + open.length(), end).trim();
    }

    private static String abbreviate(String value) {
        return value.length() <= 200 ? value : value.substring(0, 200) + "...";
    }

    public static class QuotaExceededException extends RuntimeException {
        public QuotaExceededException(String message) {
            super(message);
        }
    }
}
