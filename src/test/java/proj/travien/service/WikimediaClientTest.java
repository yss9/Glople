package proj.travien.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import proj.travien.dto.WikiCandidate;
import proj.travien.dto.WikiCountry;
import proj.travien.dto.WikiDestination;
import proj.travien.dto.WikiExtract;

import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.UncheckedIOException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.URLDecoder;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayDeque;
import java.util.Deque;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.concurrent.atomic.AtomicLong;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

/**
 * 픽스처(src/test/resources/wiki)는 2026-10-17 실제 위키데이터 쿼리 서비스·한국어 위키백과 API 응답을 줄인 것이다.
 * 위키백과 설명문 발췌는 CC BY-SA 4.0, 위키데이터 값은 CC0.
 */
class WikimediaClientTest {

    private static final String ENTITY = "http://www.wikidata.org/entity/";

    private final List<Duration> sleeps = new CopyOnWriteArrayList<>();
    private final List<Request> requests = new CopyOnWriteArrayList<>();
    private final Deque<Reply> replies = new ArrayDeque<>();
    private final WikimediaClient client = client("https://query.example.test/sparql", "https://ko.example.test/w/api.php", 3);
    private HttpServer server;

    @AfterEach
    void stopServer() {
        if (server != null) {
            server.stop(0);
        }
    }

    @Test
    void parsesCountriesWithIsoCodes() {
        List<WikiCountry> countries = client.parseCountries(fixture("sparql-countries.json"));

        // ISO 코드 순. 덴마크(Q35)는 주권국가가 아니라 덴마크 왕국의 구성국이지만 DK 코드가 붙어 있어 목록에 들어온다.
        assertThat(countries).extracting(WikiCountry::isoCode).containsExactly("DK", "FR", "JP", "NL", "US");
        assertThat(countries.get(0)).isEqualTo(new WikiCountry("Q35", "DK", "덴마크"));
        assertThat(countries.get(1)).isEqualTo(new WikiCountry("Q142", "FR", "프랑스"));
        assertThat(countries.get(3).name()).isEqualTo("네덜란드 왕국");
    }

    @Test
    void fallsBackToEnglishCountryNameAndSkipsDomesticOrInvalidCodes() {
        String body = sparql(
                row(Map.of("country", uri("Q99"), "iso", literal("zz"), "enLabel", literal("Zedland"))),
                row(Map.of("country", uri("Q884"), "iso", literal("KR"), "koLabel", literal("대한민국"))),
                row(Map.of("country", uri("Q5"), "iso", literal("ZZ"), "koLabel", literal("다른 항목"))),
                row(Map.of("country", uri("Q7"), "iso", literal("Z1"), "koLabel", literal("잘못된 코드"))));

        List<WikiCountry> countries = client.parseCountries(body);

        // 같은 ISO 코드면 QID 번호가 작은 항목을 쓴다. 대한민국·북한은 국내 수집 대상이라 뺀다.
        assertThat(countries).containsExactly(new WikiCountry("Q5", "ZZ", "다른 항목"));
        assertThat(client.parseCountries(sparql(row(Map.of("country", uri("Q99"), "iso", literal("ZY"),
                "enLabel", literal("Zedland")))))).containsExactly(new WikiCountry("Q99", "ZY", "Zedland"));
    }

    @Test
    void mergesCandidateRowsByItem() {
        Map<String, WikiCandidate> candidates = client.parseCandidates(fixture("sparql-candidates.json"));

        assertThat(candidates).containsOnlyKeys("Q63465", "Q187702", "Q224124", "Q501638", "Q1333799");
        // 여러 나라에 걸친 항목은 국가(P17)마다 행이 따로 온다.
        assertThat(candidates.get("Q187702")).isEqualTo(new WikiCandidate("Q187702", 76, java.util.Set.of("IL", "PS")));
        // 네덜란드 항목의 P17(네덜란드 Q55)에는 ISO 코드가 없어 쿼리가 상위 국가(네덜란드 왕국)의 NL을 붙인다.
        assertThat(candidates.get("Q224124").countryCodes()).containsExactly("NL");
        assertThat(candidates.get("Q63465").sitelinks()).isEqualTo(14);
    }

    @Test
    void aggregatesDestinationRows() {
        Map<String, WikiDestination> byQid = new LinkedHashMap<>();
        client.parseDestinations(fixture("sparql-destinations.json")).forEach(d -> byQid.put(d.qid(), d));

        assertThat(byQid).containsOnlyKeys("Q120730", "Q63465", "Q501638", "Q1073585", "Q1333799", "Q1193480");

        // P31이 여러 개면 행이 여러 개로 오고, QID 번호 순으로 모은다.
        WikiDestination glover = byQid.get("Q63465");
        assertThat(glover.name()).isEqualTo("구라바엔");
        assertThat(glover.instanceOf()).containsExactly("Q33506", "Q756102", "Q1107656");
        assertThat(glover.worldHeritage()).isFalse();
        assertThat(glover.adminLabels()).containsExactly("나가사키시");
        assertThat(glover.latitude()).isEqualTo(32.73444444);
        assertThat(glover.longitude()).isEqualTo(129.86888889);
        assertThat(glover.articleTitle()).isEqualTo("구라바엔");
        assertThat(glover.articleUrl()).isEqualTo("https://ko.wikipedia.org/wiki/%EA%B5%AC%EB%9D%BC%EB%B0%94%EC%97%94");

        // 세계유산, 좌표 두 개(정렬상 첫 값), 행정구역 두 곳(QID 번호 순)
        WikiDestination shirakami = byQid.get("Q501638");
        assertThat(shirakami.worldHeritage()).isTrue();
        assertThat(shirakami.latitude()).isEqualTo(40.47);
        assertThat(shirakami.longitude()).isEqualTo(140.13);
        assertThat(shirakami.adminLabels()).containsExactly("아오모리현", "아키타현");
        assertThat(shirakami.articleTitle()).isEqualTo("시라카미 산지");

        // 레이블과 문서 제목이 다른 경우: 이름은 레이블, 제목은 사이트링크 주소에서 %인코딩·밑줄을 풀어 꺼낸다.
        WikiDestination horyuji = byQid.get("Q1333799");
        assertThat(horyuji.name()).isEqualTo("호류지 지역의 불교 건조물");
        assertThat(horyuji.articleTitle()).isEqualTo("호류사 지역의 불교기념물");
        assertThat(horyuji.instanceOf()).containsExactly("Q5393308", "Q18247357");
        assertThat(horyuji.worldHeritage()).isTrue();

        assertThat(byQid.get("Q1193480").adminLabels()).isEmpty();
        assertThat(byQid.get("Q1193480").articleTitle()).isEqualTo("가톨릭 세키구치 교회");
        assertThat(byQid.get("Q120730").adminLabels()).containsExactly("일본");
    }

    @Test
    void namesItemWithoutKoreanLabelAfterArticleTitle() {
        String article = "https://ko.wikipedia.org/wiki/%EC%98%A4%EB%85%B8%EC%84%B1_(%ED%9B%84%EC%BF%A0%EC%9D%B4%ED%98%84)";
        String body = sparql(
                row(Map.of("item", uri("Q1"), "article", articleUri(article), "enLabel", literal("Ono Castle"),
                        "coord", literal("<http://www.wikidata.org/entity/Q111> Point(10 10)"))),
                row(Map.of("item", uri("Q2"), "article", articleUri("https://en.wikipedia.org/wiki/Nowhere"),
                        "koLabel", literal("영어 문서만"))));

        List<WikiDestination> destinations = client.parseDestinations(body);

        // 한국어 레이블이 없으면 한국어 문서 제목(끝의 동음이의 구분 괄호 제외)을 이름으로 쓴다. 다른 천체 좌표는 버린다.
        assertThat(destinations).hasSize(1);
        assertThat(destinations.get(0).name()).isEqualTo("오노성");
        assertThat(destinations.get(0).articleTitle()).isEqualTo("오노성 (후쿠이현)");
        assertThat(destinations.get(0).latitude()).isNull();
        assertThat(destinations.get(0).instanceOf()).isEmpty();
    }

    @Test
    void keepsCountryCodesOfAdministrativeAreas() {
        String article = "https://ko.wikipedia.org/wiki/%EB%AA%BD%EB%B8%94%EB%9E%91%EC%82%B0";
        String body = sparql(
                row(Map.of("item", uri("Q583"), "article", articleUri(article), "koLabel", literal("몽블랑산"),
                        "admin", uri("Q242617"), "adminLabel", literal("쿠르마유르"), "adminIso", literal("IT"))),
                row(Map.of("item", uri("Q583"), "article", articleUri(article),
                        "admin", uri("Q273946"), "adminLabel", literal("샤모니몽블랑"), "adminIso", literal("fr"))),
                row(Map.of("item", uri("Q583"), "article", articleUri(article),
                        "admin", uri("Q273946"), "adminLabel", literal("샤모니몽블랑"))),
                row(Map.of("item", uri("Q583"), "article", articleUri(article),
                        "admin", uri("Q999999999"), "adminLabel", literal("코드 없는 곳"))));

        WikiDestination montBlanc = client.parseDestinations(body).get(0);

        assertThat(montBlanc.admins()).containsExactly(
                new WikiDestination.Admin("쿠르마유르", java.util.Set.of("IT")),
                new WikiDestination.Admin("샤모니몽블랑", java.util.Set.of("FR")),
                new WikiDestination.Admin("코드 없는 곳", java.util.Set.of()));
        assertThat(montBlanc.adminLabels()).containsExactly("쿠르마유르", "샤모니몽블랑", "코드 없는 곳");
        assertThat(WikimediaClient.destinationsQuery(List.of("Q583"))).contains("?adminCountry wdt:P297 ?adminIso");
    }

    @Test
    void extractsTitleFromSitelinkUrl() {
        assertThat(WikimediaClient.titleFromArticleUrl("https://ko.wikipedia.org/wiki/%EC%97%90%ED%8E%A0%ED%83%91"))
                .isEqualTo("에펠탑");
        assertThat(WikimediaClient.titleFromArticleUrl("https://ko.wikipedia.org/wiki/%EB%85%B8%ED%8A%B8%EB%A5%B4%EB%8B%B4_%EB%8C%80%EC%84%B1%EB%8B%B9_(%ED%8C%8C%EB%A6%AC)"))
                .isEqualTo("노트르담 대성당 (파리)");
        // '+'는 공백이 아니라 제목 글자다.
        assertThat(WikimediaClient.titleFromArticleUrl("https://ko.wikipedia.org/wiki/C%2B%2B")).isEqualTo("C++");
        assertThat(WikimediaClient.titleFromArticleUrl("https://ko.wikipedia.org/wiki/A+B")).isEqualTo("A+B");
        assertThat(WikimediaClient.titleFromArticleUrl("https://ko.wikipedia.org/wiki/%EC%84%B1#%EC%97%AD%EC%82%AC")).isEqualTo("성");
        assertThat(WikimediaClient.titleFromArticleUrl("https://en.wikipedia.org/wiki/Eiffel_Tower")).isNull();
        assertThat(WikimediaClient.titleFromArticleUrl("https://ko.wikipedia.org/wiki/%ZZ")).isNull();
        assertThat(WikimediaClient.titleFromArticleUrl("https://ko.wikipedia.org/wiki/")).isNull();
        assertThat(WikimediaClient.titleFromArticleUrl(null)).isNull();
    }

    @Test
    void mapsExtractsBackThroughNormalizedTitlesAndRedirects() {
        List<String> titles = List.of("구라바엔", "기이_산지의_영지와_참배길", "고도 교토의 문화재", "에펠 탑", "글로플테스트없는문서", "응답에 없는 제목");

        Map<String, WikiExtract> extracts = client.parseExtracts(fixture("wiki-extracts.json"), titles);

        assertThat(extracts).containsOnlyKeys("구라바엔", "기이_산지의_영지와_참배길", "고도 교토의 문화재", "에펠 탑", "글로플테스트없는문서");
        WikiExtract glover = extracts.get("구라바엔");
        assertThat(glover.revisionId()).isEqualTo(41293884L);
        assertThat(glover.extract()).startsWith("구라바엔(일본어: グラバー園) 또는 글로버 가든");
        assertThat(glover.url()).isEqualTo("https://ko.wikipedia.org/wiki/%EA%B5%AC%EB%9D%BC%EB%B0%94%EC%97%94");
        assertThat(glover.missing()).isFalse();

        // 밑줄 제목은 normalized로, 넘겨주기 문서는 redirects로 실제 문서를 찾는다.
        assertThat(extracts.get("기이_산지의_영지와_참배길").title()).isEqualTo("기이 산지의 영지와 참배길");
        assertThat(extracts.get("기이_산지의_영지와_참배길").revisionId()).isEqualTo(41878545L);
        assertThat(extracts.get("고도 교토의 문화재").title()).isEqualTo("고대 교토의 역사 기념물");
        assertThat(extracts.get("에펠 탑").title()).isEqualTo("에펠탑");
        assertThat(extracts.get("에펠 탑").revisionId()).isEqualTo(42643643L);

        WikiExtract missing = extracts.get("글로플테스트없는문서");
        assertThat(missing.missing()).isTrue();
        assertThat(missing.extract()).isNull();
        assertThat(missing.revisionId()).isNull();
    }

    @Test
    void keepsEmptyExtractApartFromMissingExtractField() {
        String body = """
                {"batchcomplete":true,"query":{"pages":[
                  {"pageid":1,"ns":0,"title":"빈 도입부","extract":"","revisions":[{"revid":10,"parentid":9}]},
                  {"pageid":2,"ns":0,"title":"도입부 없음","lastrevid":20},
                  {"ns":0,"title":"잘못된|제목","invalid":true,"invalidreason":"bad"}
                ]}}
                """;

        Map<String, WikiExtract> extracts = client.parseExtracts(body, List.of("빈 도입부", "도입부 없음", "잘못된|제목"));

        assertThat(extracts.get("빈 도입부").extract()).isEmpty();
        assertThat(extracts.get("빈 도입부").revisionId()).isEqualTo(10L);
        assertThat(extracts.get("도입부 없음").extract()).isNull();
        assertThat(extracts.get("도입부 없음").revisionId()).isEqualTo(20L);
        assertThat(extracts.get("잘못된|제목").missing()).isTrue();
    }

    @Test
    void reportsMediaWikiErrors() {
        String error = """
                {"error":{"code":"toomanyvalues","info":"Too many values supplied for parameter \\"titles\\"."}}
                """;

        assertThatThrownBy(() -> client.parseExtracts(error, List.of("a")))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("toomanyvalues");
        assertThatThrownBy(() -> client.parseCandidates("<html>busy</html>"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("SPARQL");
        // 쿼리 서비스가 결과를 내보내다 시간 초과로 끊은 200 응답은 쿼리를 나눠 다시 물을 수 있는 실패다.
        String truncated = "{\"head\":{\"vars\":[\"item\"]},\"results\":{\"bindings\":[{\"item\":{\"type\":\"uri\",\"va"
                + "\njava.util.concurrent.ExecutionException: java.util.concurrent.TimeoutException";
        assertThatThrownBy(() -> client.parseCandidates(truncated))
                .isInstanceOfSatisfying(WikimediaClient.RequestFailedException.class, e -> {
                    assertThat(e.status()).isEqualTo(200);
                    assertThat(e.isServerSide()).isTrue();
                });
    }

    @Test
    void sendsUserAgentAndSparqlQueryAsForm() throws IOException {
        replies.add(new Reply(200, Map.of(), fixture("sparql-countries.json")));
        WikimediaClient local = localClient(3);

        List<WikiCountry> countries = local.fetchCountries();

        assertThat(countries).hasSize(5);
        assertThat(requests).hasSize(1);
        Request request = requests.get(0);
        assertThat(request.path()).isEqualTo("/sparql");
        assertThat(request.method()).isEqualTo("POST");
        assertThat(request.userAgent())
                .isEqualTo("GlopleBot/0.1 (+https://github.com/yss9/Glople; personal portfolio project)")
                .isEqualTo(WikimediaClient.DEFAULT_USER_AGENT)
                .doesNotContain("@");
        assertThat(request.accept()).isEqualTo("application/sparql-results+json");
        assertThat(request.contentType()).startsWith("application/x-www-form-urlencoded");
        assertThat(request.form()).containsExactly(Map.entry("query", WikimediaClient.countriesQuery()));
    }

    @Test
    void sendsExtractParametersWithMaxlag() throws IOException {
        replies.add(new Reply(200, Map.of(), fixture("wiki-extracts.json")));
        WikimediaClient local = localClient(3);

        Map<String, WikiExtract> extracts = local.fetchExtracts(List.of("구라바엔", "에펠 탑", "구라바엔"));

        assertThat(extracts).containsOnlyKeys("구라바엔", "에펠 탑");
        Request request = requests.get(0);
        assertThat(request.path()).isEqualTo("/w/api.php");
        assertThat(request.userAgent()).isEqualTo(WikimediaClient.DEFAULT_USER_AGENT);
        assertThat(request.form()).containsAllEntriesOf(Map.of(
                "action", "query",
                "prop", "extracts|revisions|info",
                "exintro", "1",
                "explaintext", "1",
                "exlimit", "20",
                "rvprop", "ids",
                "inprop", "url",
                "redirects", "1",
                "formatversion", "2",
                "maxlag", "5"));
        // 같은 제목은 한 번만 묻는다.
        assertThat(request.form()).containsEntry("titles", "구라바엔|에펠 탑").containsEntry("format", "json");
    }

    @Test
    void refusesMoreThanTwentyTitles() {
        List<String> titles = java.util.stream.IntStream.rangeClosed(1, 21).mapToObj(i -> "문서" + i).toList();

        assertThatThrownBy(() -> client.fetchExtracts(titles)).isInstanceOf(IllegalArgumentException.class);
    }

    @Test
    void waitsForRetryAfterOn429AndRetries() throws IOException {
        replies.add(new Reply(429, Map.of("Retry-After", "7"), "Too Many Requests"));
        replies.add(new Reply(200, Map.of(), fixture("sparql-candidates.json")));
        WikimediaClient local = localClient(3);

        Map<String, WikiCandidate> candidates = local.fetchHeritageCandidates();

        assertThat(candidates).hasSize(5);
        assertThat(requests).hasSize(2);
        assertThat(requests).allSatisfy(request -> assertThat(request.userAgent()).isEqualTo(WikimediaClient.DEFAULT_USER_AGENT));
        assertThat(sleeps).containsExactly(Duration.ofSeconds(7));
    }

    @Test
    void stopsWhenRetryAfterIsTooLongOrRetriesRunOut() throws IOException {
        replies.add(new Reply(429, Map.of("Retry-After", "600"), ""));
        WikimediaClient local = localClient(3);

        // 최대 대기(120초)보다 길게 기다리라고 하면 기다리지 않고 멈춘다.
        assertThatThrownBy(local::fetchCountries).isInstanceOf(WikimediaClient.RateLimitedException.class)
                .hasMessageContaining("600초");
        assertThat(requests).hasSize(1);
        assertThat(sleeps).isEmpty();

        stopServer();
        requests.clear();
        for (int i = 0; i < 3; i++) {
            replies.add(new Reply(429, Map.of("Retry-After", "1"), ""));
        }
        WikimediaClient twoRetries = localClient(2);
        assertThatThrownBy(twoRetries::fetchCountries).isInstanceOf(WikimediaClient.RateLimitedException.class);
        assertThat(requests).hasSize(3);
        assertThat(sleeps).containsExactly(Duration.ofSeconds(1), Duration.ofSeconds(1));
    }

    @Test
    void retriesMaxlagErrorAfterRetryAfter() throws IOException {
        String maxlag = """
                {"error":{"code":"maxlag","info":"Waiting for 10.64.0.1: 6 seconds lagged.","host":"db1","lag":6}}
                """;
        replies.add(new Reply(200, Map.of("Retry-After", "5"), maxlag));
        replies.add(new Reply(200, Map.of(), fixture("wiki-extracts.json")));
        WikimediaClient local = localClient(3);

        Map<String, WikiExtract> extracts = local.fetchExtracts(List.of("구라바엔"));

        assertThat(extracts.get("구라바엔").revisionId()).isEqualTo(41293884L);
        assertThat(requests).hasSize(2);
        assertThat(sleeps).containsExactly(Duration.ofSeconds(5));
    }

    @Test
    void reportsServerSideFailuresSeparately() throws IOException {
        replies.add(new Reply(500, Map.of(), "java.util.concurrent.TimeoutException"));
        WikimediaClient local = localClient(3);

        assertThatThrownBy(() -> local.fetchCandidates(List.of("Q243")))
                .isInstanceOfSatisfying(WikimediaClient.RequestFailedException.class, e -> {
                    assertThat(e.status()).isEqualTo(500);
                    assertThat(e.isServerSide()).isTrue();
                    assertThat(e.getMessage()).contains("TimeoutException");
                });

        stopServer();
        replies.add(new Reply(400, Map.of(), "MalformedQueryException"));
        WikimediaClient badRequest = localClient(3);
        assertThatThrownBy(() -> badRequest.fetchCandidates(List.of("Q243")))
                .isInstanceOfSatisfying(WikimediaClient.RequestFailedException.class,
                        e -> assertThat(e.isServerSide()).isFalse());

        stopServer();
        replies.add(new Reply(503, Map.of(), "Service Unavailable"));
        WikimediaClient unavailable = localClient(3);
        // 503이라도 Retry-After가 없으면 한도 초과가 아니라 일반 실패다.
        assertThatThrownBy(unavailable::fetchHeritageCandidates)
                .isInstanceOfSatisfying(WikimediaClient.RequestFailedException.class,
                        e -> assertThat(e.status()).isEqualTo(503));
        assertThat(sleeps).isEmpty();
    }

    @Test
    void keepsIntervalBetweenRequests() throws IOException {
        replies.add(new Reply(200, Map.of(), fixture("sparql-countries.json")));
        replies.add(new Reply(200, Map.of(), fixture("sparql-countries.json")));
        AtomicLong clock = new AtomicLong();
        server = startServer();
        WikimediaClient local = new WikimediaClient(new ObjectMapper(), baseUrl() + "/sparql", baseUrl() + "/w/api.php",
                null, Duration.ofSeconds(1), 3, Duration.ofSeconds(120), duration -> {
                    sleeps.add(duration);
                    clock.addAndGet(duration.toNanos());
                }, clock::get);

        local.fetchCountries();
        clock.addAndGet(Duration.ofMillis(300).toNanos());
        local.fetchCountries();

        // 첫 요청은 바로 보내고, 두 번째 요청은 앞 요청이 끝난 뒤 1초가 되도록 남은 0.7초를 기다린다.
        assertThat(sleeps).containsExactly(Duration.ofMillis(700));
        assertThat(requests).hasSize(2);
    }

    @Test
    void refusesEmailInUserAgent() {
        assertThatThrownBy(() -> new WikimediaClient(new ObjectMapper(), "https://q.example.test", "https://w.example.test",
                "GlopleBot/0.1 (someone@example.test)", 1000, 3, 120))
                .isInstanceOf(IllegalArgumentException.class);
        assertThat(new WikimediaClient(new ObjectMapper(), "https://q.example.test", "https://w.example.test", " ", 1000, 3, 120)
                .userAgent()).isEqualTo(WikimediaClient.DEFAULT_USER_AGENT);
    }

    @Test
    void readsRetryAfterSecondsOrHttpDate() {
        Instant now = Instant.parse("2026-10-17T06:00:00Z");
        String inTenSeconds = DateTimeFormatter.RFC_1123_DATE_TIME.format(ZonedDateTime.ofInstant(now.plusSeconds(10), ZoneOffset.UTC));

        assertThat(WikimediaClient.retryAfterOf("120", now)).isEqualTo(Duration.ofSeconds(120));
        assertThat(WikimediaClient.retryAfterOf(inTenSeconds, now)).isEqualTo(Duration.ofSeconds(10));
        assertThat(WikimediaClient.retryAfterOf(null, now)).isEqualTo(WikimediaClient.DEFAULT_RETRY_AFTER);
        assertThat(WikimediaClient.retryAfterOf("soon", now)).isEqualTo(WikimediaClient.DEFAULT_RETRY_AFTER);
    }

    @Test
    void buildsQueriesWithFixedJoinOrderAndValues() {
        String candidates = WikimediaClient.candidatesQuery(List.of("Q243", "Q39231"));
        assertThat(candidates)
                .contains("hint:Query hint:optimizer \"None\" .")
                .contains("VALUES ?item { wd:Q243 wd:Q39231 }")
                .contains("?article schema:isPartOf <https://ko.wikipedia.org/> .")
                .contains("?item wdt:P625 ?anyCoord .")
                .contains("FILTER(BOUND(?countryCode))")
                // 유형부터 훑는 쿼리는 60초 제한에 걸린다. 후보 확인은 넘긴 항목만 본다.
                .doesNotContain("wdt:P31");
        // 항목이 먼저, 국가는 마지막에 확인한다.
        assertThat(candidates.indexOf("VALUES ?item")).isLessThan(candidates.indexOf("?item wdt:P17 ?country"));
        // 해체된 나라(일본 제국 등)의 P17은 세지 않고, 소재 행정구역이 대한민국·북한이면 뺀다. (세계유산 쿼리도 같은 조건)
        for (String query : List.of(candidates, WikimediaClient.heritageCandidatesQuery())) {
            assertThat(query)
                    .contains("FILTER NOT EXISTS { ?country wdt:P576 ?countryEnd . }")
                    .contains("FILTER NOT EXISTS { ?parent wdt:P576 ?parentEnd . }")
                    .contains("?item wdt:P131 ?koreanArea .")
                    .contains("FILTER(?koreanState IN (wd:Q884, wd:Q423))");
        }
        assertThat(WikimediaClient.heritageCandidatesQuery()).contains("ps:P1435 wd:Q9259", "pq:P582");
        assertThat(WikimediaClient.destinationsQuery(List.of("Q63465", "Q501638")))
                .contains("VALUES ?item { wd:Q63465 wd:Q501638 }")
                .contains("FILTER(LANG(?koLabel) = \"ko\")");
        assertThat(WikimediaClient.countriesQuery()).contains("wd:Q3624078", "wd:Q884, wd:Q423", "wdt:P297");
        assertThatThrownBy(() -> WikimediaClient.candidatesQuery(List.of("Q1 } DELETE")))
                .isInstanceOf(IllegalArgumentException.class);
        assertThatThrownBy(() -> WikimediaClient.candidatesQuery(List.of())).isInstanceOf(IllegalArgumentException.class);
        assertThat(client.fetchCandidates(List.of())).isEmpty();
        List<String> tooMany = java.util.stream.IntStream.rangeClosed(1, WikimediaClient.MAX_CANDIDATE_ITEMS + 1)
                .mapToObj(i -> "Q" + i).toList();
        assertThatThrownBy(() -> client.fetchCandidates(tooMany)).isInstanceOf(IllegalArgumentException.class);
    }

    // ---- 도우미 ----

    private WikimediaClient client(String sparqlUrl, String apiUrl, int maxRetries) {
        return new WikimediaClient(new ObjectMapper(), sparqlUrl, apiUrl, null, Duration.ZERO, maxRetries,
                Duration.ofSeconds(120), sleeps::add, System::nanoTime);
    }

    private WikimediaClient localClient(int maxRetries) throws IOException {
        server = startServer();
        return client(baseUrl() + "/sparql", baseUrl() + "/w/api.php", maxRetries);
    }

    private String baseUrl() {
        return "http://127.0.0.1:" + server.getAddress().getPort();
    }

    // 127.0.0.1의 임시 포트에서 replies에 넣어 둔 응답을 차례로 돌려주는 위키미디어 대역
    private HttpServer startServer() throws IOException {
        HttpServer local = HttpServer.create(new InetSocketAddress(InetAddress.getLoopbackAddress(), 0), 0);
        local.createContext("/", exchange -> {
            String body = new String(exchange.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            requests.add(new Request(
                    exchange.getRequestMethod(),
                    exchange.getRequestURI().getPath(),
                    exchange.getRequestHeaders().getFirst("User-Agent"),
                    exchange.getRequestHeaders().getFirst("Accept"),
                    exchange.getRequestHeaders().getFirst("Content-Type"),
                    body));
            Reply reply;
            synchronized (replies) {
                reply = replies.poll();
            }
            if (reply == null) {
                reply = new Reply(500, Map.of(), "no reply prepared");
            }
            byte[] payload = reply.body().getBytes(StandardCharsets.UTF_8);
            reply.headers().forEach((name, value) -> exchange.getResponseHeaders().set(name, value));
            exchange.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
            exchange.sendResponseHeaders(reply.status(), payload.length == 0 ? -1 : payload.length);
            if (payload.length > 0) {
                try (OutputStream out = exchange.getResponseBody()) {
                    out.write(payload);
                }
            }
            exchange.close();
        });
        local.start();
        return local;
    }

    static String fixture(String name) {
        try (InputStream input = WikimediaClientTest.class.getClassLoader().getResourceAsStream("wiki/" + name)) {
            if (input == null) {
                throw new IllegalStateException("픽스처가 없습니다: " + name);
            }
            return new String(input.readAllBytes(), StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new UncheckedIOException(e);
        }
    }

    private static String sparql(String... rows) {
        return "{\"head\":{\"vars\":[]},\"results\":{\"bindings\":[" + String.join(",", rows) + "]}}";
    }

    private static String row(Map<String, String> values) {
        StringBuilder row = new StringBuilder("{");
        values.forEach((name, value) -> row.append(row.length() > 1 ? "," : "").append('"').append(name).append("\":").append(value));
        return row.append('}').toString();
    }

    private static String uri(String qid) {
        return "{\"type\":\"uri\",\"value\":\"" + ENTITY + qid + "\"}";
    }

    private static String articleUri(String url) {
        return "{\"type\":\"uri\",\"value\":\"" + url + "\"}";
    }

    private static String literal(String value) {
        return "{\"type\":\"literal\",\"value\":\"" + value + "\"}";
    }

    private record Reply(int status, Map<String, String> headers, String body) {
    }

    private record Request(String method, String path, String userAgent, String accept, String contentType, String body) {

        Map<String, String> form() {
            Map<String, String> form = new LinkedHashMap<>();
            for (String pair : body.split("&")) {
                int eq = pair.indexOf('=');
                form.put(URLDecoder.decode(pair.substring(0, eq), StandardCharsets.UTF_8),
                        URLDecoder.decode(pair.substring(eq + 1), StandardCharsets.UTF_8));
            }
            return form;
        }
    }
}
