package proj.travien.service;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.client.ClientHttpResponse;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestClient;
import proj.travien.dto.WikiCandidate;
import proj.travien.dto.WikiCountry;
import proj.travien.dto.WikiDestination;
import proj.travien.dto.WikiExtract;

import java.io.IOException;
import java.io.InputStream;
import java.net.URI;
import java.net.URLDecoder;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeParseException;
import java.util.ArrayList;
import java.util.Collection;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.function.LongSupplier;
import java.util.function.Predicate;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;

/**
 * 위키데이터 쿼리 서비스(SPARQL)와 한국어 위키백과 Action API 호출과 응답 해석. 둘 다 인증키가 필요 없다.
 *
 * <p>위키미디어 이용 규칙에 맞춰 모든 요청에 연락처(저장소 주소)가 담긴 User-Agent를 붙이고, 요청은 한 번에 하나씩
 * {@code wiki.request-interval-ms} 간격을 두고 보낸다. HTTP 429(또는 Retry-After가 붙은 503)와 MediaWiki maxlag 오류는
 * Retry-After만큼 기다렸다가 다시 보내고, 기다릴 시간이 너무 길거나 재시도 횟수를 넘으면 {@link RateLimitedException}을 던진다.
 */
@Slf4j
@Component
public class WikimediaClient {

    // 연락처는 저장소 주소로 대신한다. 이메일 주소는 넣지 않는다.
    public static final String DEFAULT_USER_AGENT = "GlopleBot/0.1 (+https://github.com/yss9/Glople; personal portfolio project)";
    static final String DEFAULT_SPARQL_URL = "https://query.wikidata.org/sparql";
    static final String DEFAULT_API_URL = "https://ko.wikipedia.org/w/api.php";
    static final String KO_WIKI_ARTICLE_PREFIX = "https://ko.wikipedia.org/wiki/";
    // 위키백과 extracts는 도입부만(exintro) 받을 때 한 번에 20개 문서까지 돌려준다.
    public static final int EXTRACT_BATCH_SIZE = 20;
    // 후보 확인 쿼리 한 번에 넣을 최대 항목 수 (2026-10-17 측정: 4,900개 15~21초)
    public static final int MAX_CANDIDATE_ITEMS = 5_000;
    static final Duration DEFAULT_RETRY_AFTER = Duration.ofSeconds(5);
    // 국내(대한민국 Q884)는 TourAPI로 따로 수집하고, 북한(Q423)은 다루지 않는다.
    static final Set<String> EXCLUDED_COUNTRY_CODES = Set.of("KR", "KP");

    private static final String ENTITY_PREFIX = "http://www.wikidata.org/entity/";
    private static final String SPARQL_JSON = "application/sparql-results+json";
    private static final int MAX_CONTINUATIONS = 5;
    private static final Pattern POINT = Pattern.compile(
            "Point\\(\\s*([-+]?[0-9]*\\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\\s+([-+]?[0-9]*\\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\\s*\\)");
    private static final Pattern TRAILING_DISAMBIGUATION = Pattern.compile("\\s*\\([^()]*\\)$");
    private static final long NO_REQUEST_YET = Long.MIN_VALUE;

    @FunctionalInterface
    interface Sleeper {
        void sleep(Duration duration) throws InterruptedException;
    }

    private final RestClient restClient;
    private final ObjectMapper objectMapper;
    private final String sparqlUrl;
    private final String apiUrl;
    private final String userAgent;
    private final Duration requestInterval;
    private final int maxRetries;
    private final Duration maxRetryAfter;
    private final Sleeper sleeper;
    private final LongSupplier nanoClock;
    private long lastRequestAt = NO_REQUEST_YET;

    @Autowired
    public WikimediaClient(
            ObjectMapper objectMapper,
            @Value("${wiki.sparql-url:" + DEFAULT_SPARQL_URL + "}") String sparqlUrl,
            @Value("${wiki.api-url:" + DEFAULT_API_URL + "}") String apiUrl,
            @Value("${wiki.user-agent:" + DEFAULT_USER_AGENT + "}") String userAgent,
            @Value("${wiki.request-interval-ms:1000}") long requestIntervalMs,
            @Value("${wiki.max-retries:3}") int maxRetries,
            @Value("${wiki.max-retry-after-seconds:120}") long maxRetryAfterSeconds
    ) {
        this(objectMapper, sparqlUrl, apiUrl, userAgent, Duration.ofMillis(requestIntervalMs), maxRetries,
                Duration.ofSeconds(maxRetryAfterSeconds), duration -> Thread.sleep(duration.toMillis()), System::nanoTime);
    }

    // 테스트에서 기다리는 시간을 기록하고 시계를 정할 수 있도록 Sleeper와 시계를 받는 생성자
    WikimediaClient(
            ObjectMapper objectMapper,
            String sparqlUrl,
            String apiUrl,
            String userAgent,
            Duration requestInterval,
            int maxRetries,
            Duration maxRetryAfter,
            Sleeper sleeper,
            LongSupplier nanoClock
    ) {
        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(Duration.ofSeconds(10));
        // 쿼리 서비스는 60초에 쿼리를 끊으므로 그보다 조금 길게 기다린다.
        requestFactory.setReadTimeout(Duration.ofSeconds(75));

        this.restClient = RestClient.builder().requestFactory(requestFactory).build();
        this.objectMapper = objectMapper;
        this.sparqlUrl = sparqlUrl;
        this.apiUrl = apiUrl;
        this.userAgent = checkedUserAgent(userAgent);
        this.requestInterval = requestInterval.isNegative() ? Duration.ZERO : requestInterval;
        this.maxRetries = Math.max(0, maxRetries);
        this.maxRetryAfter = maxRetryAfter;
        this.sleeper = sleeper;
        this.nanoClock = nanoClock;
    }

    String userAgent() {
        return userAgent;
    }

    // 수집 대상 국가 목록 (ISO 코드 순)
    public synchronized List<WikiCountry> fetchCountries() {
        return parseCountries(sparql(countriesQuery()));
    }

    /**
     * 후보 목록(wiki/candidate-items.txt)의 항목 중 지금도 한국어 위키백과 문서·좌표·국가가 있고 대한민국·북한 소재가 아닌 항목의
     * 사이트링크 수와 국가 코드. 한 번에 최대 {@value #MAX_CANDIDATE_ITEMS}개까지 묻는다.
     */
    public synchronized Map<String, WikiCandidate> fetchCandidates(Collection<String> itemQids) {
        if (itemQids.isEmpty()) {
            return Map.of();
        }
        if (itemQids.size() > MAX_CANDIDATE_ITEMS) {
            throw new IllegalArgumentException("한 번에 " + MAX_CANDIDATE_ITEMS + "개 항목까지만 물을 수 있습니다: " + itemQids.size());
        }
        return parseCandidates(sparql(candidatesQuery(itemQids)));
    }

    // 유네스코 세계유산이고 한국어 위키백과 문서·좌표·국가가 있는 여행지 후보 (전 세계)
    public synchronized Map<String, WikiCandidate> fetchHeritageCandidates() {
        return parseCandidates(sparql(heritageCandidatesQuery()));
    }

    // 고른 여행지의 이름·좌표·유형·세계유산 여부·행정구역·문서 주소
    public synchronized List<WikiDestination> fetchDestinations(Collection<String> qids) {
        if (qids.isEmpty()) {
            return List.of();
        }
        return parseDestinations(sparql(destinationsQuery(qids)));
    }

    /**
     * 한국어 위키백과 문서 도입부를 최대 {@value #EXTRACT_BATCH_SIZE}개씩 한 번에 받는다.
     *
     * @return 요청한 제목 → 결과. 응답에서 찾지 못한 제목은 빠진다.
     */
    public synchronized Map<String, WikiExtract> fetchExtracts(List<String> titles) {
        List<String> distinct = List.copyOf(new LinkedHashSet<>(titles));
        if (distinct.isEmpty()) {
            return Map.of();
        }
        if (distinct.size() > EXTRACT_BATCH_SIZE) {
            throw new IllegalArgumentException("한 번에 " + EXTRACT_BATCH_SIZE + "개 문서까지만 요청할 수 있습니다: " + distinct.size());
        }

        Map<String, String> params = new LinkedHashMap<>();
        params.put("action", "query");
        params.put("format", "json");
        params.put("formatversion", "2");
        params.put("prop", "extracts|revisions|info");
        params.put("exintro", "1");
        params.put("explaintext", "1");
        params.put("exlimit", String.valueOf(EXTRACT_BATCH_SIZE));
        params.put("rvprop", "ids");
        params.put("inprop", "url");
        params.put("redirects", "1");
        // 복제 지연이 5초를 넘으면 서버가 maxlag 오류를 돌려준다. 그때는 Retry-After만큼 쉬었다가 다시 보낸다.
        params.put("maxlag", "5");
        params.put("titles", String.join("|", distinct));

        ExtractResponse response = new ExtractResponse();
        JsonNode continuation = null;
        for (int i = 0; i <= MAX_CONTINUATIONS; i++) {
            Map<String, String> request = new LinkedHashMap<>(params);
            if (continuation != null) {
                continuation.fields().forEachRemaining(field -> request.put(field.getKey(), field.getValue().asText()));
            }
            JsonNode root = readMediaWiki(post(apiUrl, request, MediaType.APPLICATION_JSON_VALUE, this::isMaxlag));
            response.add(root);
            continuation = root.get("continue");
            if (continuation == null || !continuation.isObject()) {
                break;
            }
        }
        return response.resolve(distinct);
    }

    // ---- SPARQL 쿼리 ----

    // 주권국가(Q3624078)와, 덴마크(Q35)처럼 ISO 코드가 구성국에 붙어 있고 주권국가(덴마크 왕국)에는 없는 경우의 구성국.
    // 대한민국(Q884)과 북한(Q423), 해체된 나라(P576)는 뺀다.
    static String countriesQuery() {
        return """
                SELECT DISTINCT ?country ?iso ?koLabel ?enLabel WHERE {
                  {
                    ?country wdt:P31 wd:Q3624078 .
                  } UNION {
                    ?country wdt:P17 ?sovereign .
                    ?sovereign wdt:P31 wd:Q3624078 .
                    FILTER NOT EXISTS { ?sovereign wdt:P297 ?sovereignIso . }
                  }
                  ?country wdt:P297 ?iso .
                  FILTER NOT EXISTS { ?country wdt:P576 ?dissolved . }
                  FILTER(?country NOT IN (wd:Q884, wd:Q423))
                  OPTIONAL { ?country rdfs:label ?koLabel . FILTER(LANG(?koLabel) = "ko") }
                  OPTIONAL { ?country rdfs:label ?enLabel . FILTER(LANG(?enLabel) = "en") }
                }
                ORDER BY ?iso
                """;
    }

    /*
     * 주어진 항목(VALUES) → 사이트링크 수 → 한국어 문서 → 좌표 → 국가 순서로 고정해서 확인한다(hint:optimizer "None").
     * 유형(P31)부터 훑는 쿼리는 산·강·거리·집처럼 위키데이터 전체에 항목이 아주 많은 유형에서 60초 제한에 걸리고,
     * 한국어 문서 사이트링크(약 122만 개) 전체부터 훑는 쿼리도 제한에 걸려서(2026-10-17 측정, 문서 참고) 후보 목록의 QID를 나눠 넘긴다.
     * 국가의 ISO 코드가 없으면(네덜란드 Q55 등) 그 국가의 P17(네덜란드 왕국, NL)에서 코드를 가져온다. 조건은 CANDIDATE_TAIL 참고.
     */
    static String candidatesQuery(Collection<String> itemQids) {
        if (itemQids.isEmpty()) {
            throw new IllegalArgumentException("항목이 하나 이상 있어야 합니다.");
        }
        String values = itemQids.stream().map(WikimediaClient::entity).collect(Collectors.joining(" "));
        return """
                SELECT DISTINCT ?item ?sitelinks ?countryCode WHERE {
                  hint:Query hint:optimizer "None" .
                  VALUES ?item { %s }
                %s}
                """.formatted(values, CANDIDATE_TAIL);
    }

    // 해제(P582 종료 시점)되거나 폐기 등급(deprecated)인 세계유산 지정은 세지 않는다.
    static String heritageCandidatesQuery() {
        return """
                SELECT DISTINCT ?item ?sitelinks ?countryCode WHERE {
                  hint:Query hint:optimizer "None" .
                  ?heritageStatement ps:P1435 wd:Q9259 .
                  ?item p:P1435 ?heritageStatement .
                  FILTER NOT EXISTS { ?heritageStatement pq:P582 ?heritageEnd . }
                  FILTER NOT EXISTS { ?heritageStatement wikibase:rank wikibase:DeprecatedRank . }
                %s}
                """.formatted(CANDIDATE_TAIL);
    }

    /*
     * 후보 조건 (후보 목록·세계유산 쿼리 공통):
     * - 국가(P17) 값이 해체된 나라(P576, 예: 일본 제국 Q188712)면 그 값은 세지 않는다. 한 단계 위 코드를 가져오는 규칙 때문에
     *   일제강점기 한반도의 신사(P17 = 일본 제국 → 일본)가 일본 여행지로 들어가는 일을 막는다.
     * - 소재 행정구역(P131)이 대한민국·북한에 속하면 국가 값과 상관없이 뺀다. 예: 독도 천연보호구역(Q21709690)은
     *   P17 = 일본으로 잘못 적혀 있지만 P131 = 울릉읍이다. (국가에 대한민국·북한이 들어간 항목은 동기화 단계에서 뺀다)
     */
    private static final String CANDIDATE_TAIL = """
              ?item wikibase:sitelinks ?sitelinks .
              FILTER(?sitelinks > 0)
              ?article schema:about ?item .
              ?article schema:isPartOf <https://ko.wikipedia.org/> .
              ?item wdt:P625 ?anyCoord .
              ?item wdt:P17 ?country .
              FILTER NOT EXISTS { ?country wdt:P576 ?countryEnd . }
              OPTIONAL { ?country wdt:P297 ?ownCode . }
              OPTIONAL {
                ?country wdt:P17 ?parent .
                ?parent wdt:P297 ?parentCode .
                FILTER NOT EXISTS { ?parent wdt:P576 ?parentEnd . }
              }
              BIND(COALESCE(?ownCode, ?parentCode) AS ?countryCode)
              FILTER(BOUND(?countryCode))
              FILTER NOT EXISTS {
                ?item wdt:P131 ?koreanArea .
                ?koreanArea wdt:P17 ?koreanState .
                FILTER(?koreanState IN (wd:Q884, wd:Q423))
              }
            """;

    // 고른 여행지(국가 하나 분량)의 상세 값. P31·좌표·행정구역이 여러 개면 행이 여러 개로 나오고, 해석할 때 합친다.
    // 행정구역마다 그 국가 코드(adminIso)도 받아, 국경에 걸친 여행지의 소재지를 고른 나라의 행정구역으로 적는다.
    static String destinationsQuery(Collection<String> qids) {
        String values = qids.stream().map(WikimediaClient::entity).collect(Collectors.joining(" "));
        return """
                SELECT ?item ?article ?koLabel ?enLabel ?coord ?type ?heritage ?admin ?adminLabel ?adminIso WHERE {
                  hint:Query hint:optimizer "None" .
                  VALUES ?item { %s }
                  ?article schema:about ?item .
                  ?article schema:isPartOf <https://ko.wikipedia.org/> .
                  OPTIONAL { ?item rdfs:label ?koLabel . FILTER(LANG(?koLabel) = "ko") }
                  OPTIONAL { ?item rdfs:label ?enLabel . FILTER(LANG(?enLabel) = "en") }
                  OPTIONAL { ?item wdt:P625 ?coord . }
                  OPTIONAL { ?item wdt:P31 ?type . }
                  OPTIONAL {
                    ?item p:P1435 ?heritageStatement .
                    ?heritageStatement ps:P1435 wd:Q9259 .
                    FILTER NOT EXISTS { ?heritageStatement pq:P582 ?heritageEnd . }
                    FILTER NOT EXISTS { ?heritageStatement wikibase:rank wikibase:DeprecatedRank . }
                    BIND(true AS ?heritage)
                  }
                  OPTIONAL {
                    ?item wdt:P131 ?admin .
                    ?admin rdfs:label ?adminLabel .
                    FILTER(LANG(?adminLabel) = "ko")
                    OPTIONAL { ?admin wdt:P17 ?adminCountry . ?adminCountry wdt:P297 ?adminIso . }
                  }
                }
                """.formatted(values);
    }

    private static String entity(String qid) {
        String trimmed = qid == null ? "" : qid.trim();
        if (!trimmed.matches("Q[1-9][0-9]*")) {
            throw new IllegalArgumentException("위키데이터 QID 형식이 아닙니다: '" + qid + "'");
        }
        return "wd:" + trimmed;
    }

    // ---- 응답 해석 ----

    List<WikiCountry> parseCountries(String body) {
        Map<String, WikiCountry> byIso = new TreeMap<>();
        for (JsonNode row : bindings(body)) {
            String qid = entityId(row, "country");
            String iso = literal(row, "iso").toUpperCase();
            if (qid == null || !iso.matches("[A-Z]{2}") || EXCLUDED_COUNTRY_CODES.contains(iso)) {
                continue;
            }
            String name = firstNonBlank(literal(row, "koLabel"), literal(row, "enLabel"), qid);
            // 같은 ISO 코드를 가진 국가가 둘이면 QID 번호가 작은 쪽(먼저 만들어진 항목)을 쓴다.
            byIso.merge(iso, new WikiCountry(qid, iso, name),
                    (kept, other) -> qidNumber(kept.qid()) <= qidNumber(other.qid()) ? kept : other);
        }
        return List.copyOf(byIso.values());
    }

    Map<String, WikiCandidate> parseCandidates(String body) {
        Map<String, Integer> sitelinks = new LinkedHashMap<>();
        Map<String, Set<String>> codes = new HashMap<>();
        for (JsonNode row : bindings(body)) {
            String qid = entityId(row, "item");
            String code = literal(row, "countryCode").toUpperCase();
            if (qid == null || !code.matches("[A-Z]{2}")) {
                continue;
            }
            sitelinks.merge(qid, integer(literal(row, "sitelinks")), Math::max);
            codes.computeIfAbsent(qid, key -> new TreeSet<>()).add(code);
        }

        Map<String, WikiCandidate> candidates = new LinkedHashMap<>();
        sitelinks.forEach((qid, count) -> candidates.put(qid, new WikiCandidate(qid, count, Set.copyOf(codes.get(qid)))));
        return candidates;
    }

    List<WikiDestination> parseDestinations(String body) {
        Map<String, DestinationRows> byQid = new LinkedHashMap<>();
        for (JsonNode row : bindings(body)) {
            String qid = entityId(row, "item");
            String article = uri(row, "article");
            if (qid == null || article.isEmpty()) {
                continue;
            }
            DestinationRows rows = byQid.computeIfAbsent(qid, key -> new DestinationRows(key, article));
            rows.add(row);
        }

        List<WikiDestination> destinations = new ArrayList<>();
        for (DestinationRows rows : byQid.values()) {
            WikiDestination destination = rows.build();
            if (destination == null) {
                log.warn("Skipping wikidata item without readable ko.wikipedia sitelink qid={} article={}", rows.qid, rows.articleUrl);
            } else {
                destinations.add(destination);
            }
        }
        return destinations;
    }

    Map<String, WikiExtract> parseExtracts(String body, List<String> titles) {
        ExtractResponse response = new ExtractResponse();
        response.add(readMediaWiki(body));
        return response.resolve(List.copyOf(new LinkedHashSet<>(titles)));
    }

    /**
     * 한국어 위키백과 문서 주소에서 문서 제목을 꺼낸다. %인코딩을 풀고 밑줄을 공백으로 바꾼다.
     * '+'는 공백이 아니라 제목에 들어간 글자로 본다. 한국어 위키백과 문서 주소가 아니거나 인코딩이 깨졌으면 null.
     */
    public static String titleFromArticleUrl(String url) {
        if (url == null || !url.startsWith(KO_WIKI_ARTICLE_PREFIX)) {
            return null;
        }
        String encoded = url.substring(KO_WIKI_ARTICLE_PREFIX.length());
        int query = encoded.indexOf('?');
        if (query >= 0) {
            encoded = encoded.substring(0, query);
        }
        int fragment = encoded.indexOf('#');
        if (fragment >= 0) {
            encoded = encoded.substring(0, fragment);
        }
        try {
            String title = URLDecoder.decode(encoded.replace("+", "%2B"), StandardCharsets.UTF_8).replace('_', ' ').trim();
            return title.isEmpty() ? null : title;
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    // ---- HTTP ----

    private String sparql(String query) {
        return post(sparqlUrl, Map.of("query", query), SPARQL_JSON, response -> false);
    }

    private String post(String url, Map<String, String> form, String accept, Predicate<Response> retryableBody) {
        byte[] body = formBody(form).getBytes(StandardCharsets.UTF_8);
        for (int attempt = 0; ; attempt++) {
            Response response = send(url, body, accept);
            boolean throttled = response.status() == 429
                    || (response.status() == 503 && response.retryAfter() != null)
                    || (response.status() == 200 && retryableBody.test(response));
            if (!throttled) {
                if (response.status() < 200 || response.status() >= 300) {
                    throw new RequestFailedException(response.status(), "위키미디어 요청 실패 (HTTP " + response.status() + "): "
                            + abbreviate(response.body()));
                }
                return response.body();
            }

            Duration wait = retryAfterOf(response.retryAfter(), Instant.now());
            if (attempt >= maxRetries) {
                throw new RateLimitedException("위키미디어 요청 한도에 걸려 " + (attempt + 1) + "번 시도 후 멈췄습니다 (HTTP "
                        + response.status() + ").");
            }
            if (wait.compareTo(maxRetryAfter) > 0) {
                throw new RateLimitedException("위키미디어가 " + wait.toSeconds() + "초 뒤에 다시 요청하라고 해서 멈췄습니다 (HTTP "
                        + response.status() + ", 최대 대기 " + maxRetryAfter.toSeconds() + "초).");
            }
            log.warn("Wikimedia throttled status={} attempt={} retryAfter={}s", response.status(), attempt + 1, wait.toSeconds());
            sleep(wait);
        }
    }

    // 요청은 한 번에 하나씩, 앞 요청이 끝난 뒤 requestInterval이 지나야 보낸다.
    private Response send(String url, byte[] body, String accept) {
        if (lastRequestAt != NO_REQUEST_YET) {
            long remaining = requestInterval.toNanos() - (nanoClock.getAsLong() - lastRequestAt);
            if (remaining > 0) {
                sleep(Duration.ofNanos(remaining));
            }
        }
        try {
            return restClient.post()
                    .uri(URI.create(url))
                    .header(HttpHeaders.USER_AGENT, userAgent)
                    .header(HttpHeaders.ACCEPT, accept)
                    .contentType(MediaType.APPLICATION_FORM_URLENCODED)
                    .body(body)
                    .exchange((request, response) -> new Response(
                            response.getStatusCode().value(),
                            response.getHeaders().getFirst(HttpHeaders.RETRY_AFTER),
                            readBody(response)));
        } catch (ResourceAccessException e) {
            throw new RequestFailedException(0, "위키미디어 서버에 연결하지 못했습니다: " + e.getMessage());
        } finally {
            lastRequestAt = nanoClock.getAsLong();
        }
    }

    private void sleep(Duration duration) {
        try {
            sleeper.sleep(duration);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("위키미디어 요청을 기다리다 중단되었습니다.", e);
        }
    }

    private boolean isMaxlag(Response response) {
        try {
            return "maxlag".equals(objectMapper.readTree(response.body()).path("error").path("code").asText(""));
        } catch (JsonProcessingException e) {
            return false;
        }
    }

    /**
     * Retry-After 헤더(초 또는 HTTP 날짜)를 기다릴 시간으로 바꾼다. 없거나 읽을 수 없으면 {@link #DEFAULT_RETRY_AFTER}.
     */
    static Duration retryAfterOf(String header, Instant now) {
        if (header == null || header.isBlank()) {
            return DEFAULT_RETRY_AFTER;
        }
        String value = header.trim();
        if (value.matches("[0-9]{1,9}")) {
            return Duration.ofSeconds(Long.parseLong(value));
        }
        try {
            Duration until = Duration.between(now, ZonedDateTime.parse(value, DateTimeFormatter.RFC_1123_DATE_TIME).toInstant());
            return until.isNegative() ? Duration.ZERO : until;
        } catch (DateTimeParseException e) {
            return DEFAULT_RETRY_AFTER;
        }
    }

    private static String formBody(Map<String, String> form) {
        return form.entrySet().stream()
                .map(entry -> encode(entry.getKey()) + "=" + encode(entry.getValue()))
                .collect(Collectors.joining("&"));
    }

    private static String readBody(ClientHttpResponse response) {
        try (InputStream input = response.getBody()) {
            return new String(input.readAllBytes(), StandardCharsets.UTF_8);
        } catch (IOException e) {
            // 본문 없는 오류 응답(예: 빈 429)은 읽을 스트림이 없다.
            return "";
        }
    }

    private static String checkedUserAgent(String userAgent) {
        String value = userAgent == null || userAgent.isBlank() ? DEFAULT_USER_AGENT : userAgent.trim();
        if (value.contains("@")) {
            throw new IllegalArgumentException("wiki.user-agent에 이메일 주소를 넣지 않습니다. 연락처는 저장소 주소로 대신합니다.");
        }
        return value;
    }

    // ---- JSON 도우미 ----

    private List<JsonNode> bindings(String body) {
        JsonNode root;
        try {
            root = readJson(body, "SPARQL");
        } catch (IllegalStateException e) {
            // 쿼리 서비스는 결과를 내보내는 도중 60초 제한에 걸리면 HTTP 200 본문을 TimeoutException 메시지와 함께 중간에 끊는다.
            // 그래서 해석할 수 없는 200 응답은 시간 초과처럼 '쿼리를 나누면 통과할 수 있는 실패'로 다룬다.
            throw new RequestFailedException(200, e.getMessage(), true);
        }
        JsonNode bindings = root.at("/results/bindings");
        if (!bindings.isArray()) {
            throw new IllegalStateException("SPARQL 응답에 results.bindings가 없습니다: " + abbreviate(body));
        }
        List<JsonNode> rows = new ArrayList<>();
        bindings.forEach(rows::add);
        return rows;
    }

    private JsonNode readMediaWiki(String body) {
        JsonNode root = readJson(body, "위키백과 API");
        JsonNode error = root.get("error");
        if (error != null && error.isObject()) {
            throw new IllegalStateException("위키백과 API 오류: " + error.path("code").asText("") + " " + error.path("info").asText(""));
        }
        return root;
    }

    private JsonNode readJson(String body, String source) {
        if (body == null || body.isBlank()) {
            throw new IllegalStateException(source + " 응답이 비어 있습니다.");
        }
        try {
            return objectMapper.readTree(body);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException(source + " 응답을 해석할 수 없습니다: " + abbreviate(body));
        }
    }

    private static String literal(JsonNode row, String name) {
        return row.path(name).path("value").asText("").trim();
    }

    private static String uri(JsonNode row, String name) {
        JsonNode value = row.path(name);
        return "uri".equals(value.path("type").asText()) ? value.path("value").asText("").trim() : "";
    }

    // http://www.wikidata.org/entity/Q142 → Q142. 빈 노드(_:b0)나 QID가 아닌 값은 null
    private static String entityId(JsonNode row, String name) {
        String value = uri(row, name);
        if (!value.startsWith(ENTITY_PREFIX)) {
            return null;
        }
        String id = value.substring(ENTITY_PREFIX.length());
        return id.matches("Q[1-9][0-9]*") ? id : null;
    }

    static long qidNumber(String qid) {
        try {
            return Long.parseLong(qid.substring(1));
        } catch (RuntimeException e) {
            return Long.MAX_VALUE;
        }
    }

    private static int integer(String value) {
        try {
            return Integer.parseInt(value);
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    private static String firstNonBlank(String... values) {
        for (String value : values) {
            if (value != null && !value.isBlank()) {
                return value.trim();
            }
        }
        return null;
    }

    private static String encode(String value) {
        return URLEncoder.encode(value, StandardCharsets.UTF_8);
    }

    private static String abbreviate(String value) {
        if (value == null) {
            return "";
        }
        String trimmed = value.trim();
        return trimmed.length() <= 300 ? trimmed : trimmed.substring(0, 300) + "...";
    }

    private record Response(int status, String retryAfter, String body) {
    }

    // 한 여행지에 대해 P31·좌표·행정구역 수만큼 여러 행으로 오는 SPARQL 결과를 모은다.
    private static final class DestinationRows {
        private final String qid;
        private final String articleUrl;
        private String koLabel;
        private String enLabel;
        private final TreeSet<String> coordinates = new TreeSet<>();
        private final TreeMap<Long, String> types = new TreeMap<>();
        private final TreeMap<Long, String> admins = new TreeMap<>();
        private final Map<Long, Set<String>> adminCodes = new HashMap<>();
        private boolean worldHeritage;

        private DestinationRows(String qid, String articleUrl) {
            this.qid = qid;
            this.articleUrl = articleUrl;
        }

        private void add(JsonNode row) {
            koLabel = firstNonBlank(koLabel, literal(row, "koLabel"));
            enLabel = firstNonBlank(enLabel, literal(row, "enLabel"));
            String coordinate = literal(row, "coord");
            // 다른 천체의 좌표는 "<http://www.wikidata.org/entity/Q111> Point(...)" 꼴이라 건너뛴다.
            if (coordinate.startsWith("Point(")) {
                coordinates.add(coordinate);
            }
            String type = entityId(row, "type");
            if (type != null) {
                types.put(qidNumber(type), type);
            }
            if ("true".equals(literal(row, "heritage"))) {
                worldHeritage = true;
            }
            String admin = entityId(row, "admin");
            String adminLabel = literal(row, "adminLabel");
            if (admin != null && !adminLabel.isEmpty()) {
                long key = qidNumber(admin);
                admins.putIfAbsent(key, adminLabel);
                Set<String> codes = adminCodes.computeIfAbsent(key, k -> new TreeSet<>());
                String iso = literal(row, "adminIso").toUpperCase();
                if (iso.matches("[A-Z]{2}")) {
                    codes.add(iso);
                }
            }
        }

        private WikiDestination build() {
            String title = titleFromArticleUrl(articleUrl);
            if (title == null) {
                return null;
            }

            Double latitude = null;
            Double longitude = null;
            // 좌표가 여러 개면 문자열 정렬상 첫 값을 써서, 실행할 때마다 같은 값이 나오게 한다.
            for (String coordinate : coordinates) {
                Matcher matcher = POINT.matcher(coordinate);
                if (matcher.matches()) {
                    double lon = Double.parseDouble(matcher.group(1));
                    double lat = Double.parseDouble(matcher.group(2));
                    if (Math.abs(lat) <= 90 && Math.abs(lon) <= 180) {
                        latitude = lat;
                        longitude = lon;
                        break;
                    }
                }
            }

            // 한국어 레이블이 없으면 한국어 문서 제목(끝의 '(동음이의 구분)' 제외), 그것도 못 쓰면 영어 레이블
            String titleName = TRAILING_DISAMBIGUATION.matcher(title).replaceFirst("").trim();
            String name = firstNonBlank(koLabel, titleName, title, enLabel, qid);
            List<WikiDestination.Admin> adminList = admins.entrySet().stream()
                    .map(entry -> new WikiDestination.Admin(entry.getValue(), Set.copyOf(adminCodes.get(entry.getKey()))))
                    .toList();
            return new WikiDestination(qid, name, title, articleUrl, latitude, longitude,
                    List.copyOf(types.values()), worldHeritage, adminList);
        }
    }

    // normalized → redirects → pages를 따라가 요청한 제목마다 결과를 붙인다. 이어받기(continue) 응답도 합친다.
    private static final class ExtractResponse {
        private final Map<String, String> normalized = new HashMap<>();
        private final Map<String, String> redirects = new HashMap<>();
        private final Map<String, ObjectNode> pages = new LinkedHashMap<>();

        private void add(JsonNode root) {
            JsonNode query = root.path("query");
            query.path("normalized").forEach(node -> normalized.put(node.path("from").asText(), node.path("to").asText()));
            query.path("redirects").forEach(node -> redirects.put(node.path("from").asText(), node.path("to").asText()));
            for (JsonNode page : query.path("pages")) {
                if (!page.isObject() || !page.hasNonNull("title")) {
                    continue;
                }
                ObjectNode kept = pages.get(page.get("title").asText());
                if (kept == null) {
                    pages.put(page.get("title").asText(), ((ObjectNode) page).deepCopy());
                } else {
                    page.fields().forEachRemaining(field -> {
                        if (!kept.has(field.getKey())) {
                            kept.set(field.getKey(), field.getValue());
                        }
                    });
                }
            }
        }

        private Map<String, WikiExtract> resolve(List<String> titles) {
            Map<String, WikiExtract> result = new LinkedHashMap<>();
            for (String requested : titles) {
                String title = normalized.getOrDefault(requested, requested);
                title = redirects.getOrDefault(title, title);
                ObjectNode page = pages.get(title);
                if (page == null) {
                    continue;
                }

                boolean missing = page.has("missing") || page.has("invalid");
                String url = page.path("fullurl").asText("");
                Long revisionId = null;
                JsonNode revid = page.path("revisions").path(0).path("revid");
                if (revid.canConvertToLong()) {
                    revisionId = revid.asLong();
                } else if (page.path("lastrevid").canConvertToLong()) {
                    revisionId = page.get("lastrevid").asLong();
                }
                String extract = page.has("extract") ? page.get("extract").asText("") : null;
                result.put(requested, new WikiExtract(requested, page.path("title").asText(title),
                        missing ? null : revisionId, missing ? null : extract, url.isEmpty() ? null : url, missing));
            }
            return result;
        }
    }

    // 429·maxlag가 계속되거나 Retry-After가 너무 길어 멈춘 경우. 지금까지 저장한 결과는 그대로 두고 다음 실행에서 이어 간다.
    public static class RateLimitedException extends RuntimeException {
        public RateLimitedException(String message) {
            super(message);
        }
    }

    // 429가 아닌 HTTP 오류나 연결 실패, 중간에 끊긴 SPARQL 응답. status 0은 연결 실패(시간 초과 포함)
    public static class RequestFailedException extends IllegalStateException {
        private final int status;
        private final boolean truncated;

        public RequestFailedException(int status, String message) {
            this(status, message, false);
        }

        RequestFailedException(int status, String message, boolean truncated) {
            super(message);
            this.status = status;
            this.truncated = truncated;
        }

        public int status() {
            return status;
        }

        // 쿼리 서비스의 60초 제한(500 TimeoutException, 또는 200 응답이 중간에 끊김), 게이트웨이 시간 초과(502·504),
        // 읽기 시간 초과처럼 쿼리를 작게 나누면 통과할 수 있는 실패인지
        public boolean isServerSide() {
            return truncated || status == 0 || status >= 500;
        }
    }
}
