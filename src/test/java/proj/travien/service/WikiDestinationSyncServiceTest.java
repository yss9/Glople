package proj.travien.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.data.domain.Pageable;
import proj.travien.domain.DestinationInfo;
import proj.travien.dto.TourSyncResult;
import proj.travien.dto.WikiCandidate;
import proj.travien.dto.WikiCountry;
import proj.travien.dto.WikiDestination;
import proj.travien.dto.WikiExtract;
import proj.travien.repository.DestinationInfoRepository;

import java.time.Duration;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.doThrow;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

class WikiDestinationSyncServiceTest {

    private static final String LICENSE = "CC BY-SA 4.0 (한국어 위키백과), CC0 (Wikidata)";
    private static final WikiCountry JAPAN = new WikiCountry("Q17", "JP", "일본");
    private static final WikiCountry FRANCE = new WikiCountry("Q142", "FR", "프랑스");
    private static final WikiCountry ITALY = new WikiCountry("Q38", "IT", "이탈리아");
    private static final WikiCountry GERMANY = new WikiCountry("Q183", "DE", "독일");
    // 2026-10-17 실제 응답을 줄인 픽스처를 해석만 하는 클라이언트 (네트워크를 쓰지 않음)
    private static final WikimediaClient PARSER = new WikimediaClient(new ObjectMapper(), "https://q.example.test/sparql",
            "https://w.example.test/w/api.php", null, Duration.ZERO, 0, Duration.ZERO, duration -> { }, () -> 0L);

    // 후보 목록: 픽스처의 6곳 (운영에서는 wiki/candidate-items.txt 전체)
    private static final List<String> SEED = List.of("Q63465", "Q120730", "Q501638", "Q1073585", "Q1193480", "Q1333799");

    private final DestinationInfoRepository repository = mock(DestinationInfoRepository.class);
    private final WikimediaClient client = mock(WikimediaClient.class);
    private final WikiDestinationSyncService service = new WikiDestinationSyncService(repository, client, SEED, 2000, 30);

    @Test
    void insertsDestinationsWithSourceLicenseAndFactOnlySummary() {
        givenJapaneseFixture();

        TourSyncResult result = service.syncDestinations(List.of(), 150);

        assertThat(result).isEqualTo(new TourSyncResult(3, 3, 0, 0, false));
        // 후보 목록을 한 묶음(2,000개 이하)으로 확인하고, 세계유산은 따로 한 번 묻는다.
        verify(client).fetchCandidates(SEED);
        verify(client).fetchHeritageCandidates();
        // 사이트링크 수가 많은 순서로 상세 값을 묻는다.
        verify(client).fetchDestinations(List.of("Q501638", "Q1073585", "Q63465"));

        List<DestinationInfo> saved = captureSaveAll(1).get(0);
        assertThat(saved).extracting(DestinationInfo::getExternalId).containsExactly("Q501638", "Q1073585", "Q63465");

        DestinationInfo glover = saved.get(2);
        assertThat(glover.getSource()).isEqualTo(DestinationInfo.SOURCE_WIKIDATA);
        assertThat(glover.getCountry()).isEqualTo("일본");
        assertThat(glover.getCountryCode()).isEqualTo("JP");
        assertThat(glover.getLocation()).isEqualTo("일본 나가사키시");
        assertThat(glover.getDestName()).isEqualTo("구라바엔");
        assertThat(glover.getLatitude()).isEqualTo(32.73444444);
        assertThat(glover.getLongitude()).isEqualTo(129.86888889);
        // P31 Q33506(박물관)·Q756102(야외 박물관)·Q1107656(정원)을 P31 순서가 아니라 코드표 순서로
        assertThat(glover.getCategory()).isEqualTo("Q33506|Q1107656|Q756102");
        assertThat(glover.getWorldHeritage()).isFalse();
        assertThat(glover.getSourceUrl()).isEqualTo("https://ko.wikipedia.org/wiki/%EA%B5%AC%EB%9D%BC%EB%B0%94%EC%97%94");
        assertThat(glover.getLicense()).isEqualTo(LICENSE);
        assertThat(glover.getSummary()).isEqualTo("일본 나가사키시의 박물관입니다.");
        assertThat(glover.getInfo()).isNull();
        assertThat(glover.getSourceRevision()).isNull();
        assertThat(glover.getOverviewSyncedAt()).isNull();
        assertThat(glover.computeKeywordMask()).isEqualTo(KeywordMask.fromKeywords(List.of("박물관", "문화", "정원")));

        // 세계유산 후보로 들어온 곳. 행정구역이 두 곳이면 QID 번호가 작은 쪽. P31 Q1141462는 코드표에 없어 뺀다.
        DestinationInfo shirakami = saved.get(0);
        assertThat(shirakami.getCategory()).isEqualTo("Q46831|Q1286517");
        assertThat(shirakami.getWorldHeritage()).isTrue();
        assertThat(shirakami.getLocation()).isEqualTo("일본 아오모리현");
        assertThat(shirakami.getSummary()).isEqualTo("일본 아오모리현의 산맥입니다. 유네스코 세계유산입니다.");
        assertThat(shirakami.computeKeywordMask()).isEqualTo(KeywordMask.fromKeywords(List.of("산", "자연", "경치", "유네스코")));

        DestinationInfo karatsu = saved.get(1);
        assertThat(karatsu.getCategory()).isEqualTo("Q92026|Q23413");
        assertThat(karatsu.getSummary()).isEqualTo("일본 가라쓰시의 일본의 성입니다.");
        assertThat(karatsu.computeKeywordMask()).isEqualTo(KeywordMask.fromKeywords(List.of("성", "역사", "역사적인 장소")));
    }

    @Test
    void keepsWorldHeritageWithoutListedTypeAsGenericDestination() {
        // 코드표에 있는 유형이 없어도 세계유산이면 수집한다. (교토부 Q120730의 P31은 행정구역이라 코드표에 없음)
        WikiDestination kyoto = PARSER.parseDestinations(WikimediaClientTest.fixture("sparql-destinations.json")).stream()
                .filter(destination -> destination.qid().equals("Q120730"))
                .findFirst()
                .orElseThrow();
        DestinationInfo row = new DestinationInfo();
        row.setSource(DestinationInfo.SOURCE_WIKIDATA);

        assertThat(WikiDestinationSyncService.apply(row, JAPAN, kyoto)).isTrue();

        assertThat(row.getCategory()).isNull();
        assertThat(row.getWorldHeritage()).isTrue();
        assertThat(row.getSummary()).isEqualTo("일본의 여행지입니다. 유네스코 세계유산입니다.");
        assertThat(row.computeKeywordMask()).isEqualTo(KeywordMask.fromKeywords(List.of("유네스코")));
    }

    @Test
    void doesNotRewriteUnchangedRows() {
        givenJapaneseFixture();
        service.syncDestinations(List.of("JP"), 150);
        List<DestinationInfo> stored = captureSaveAll(1).get(0);
        // 저장할 때 @PrePersist가 마스크를 채운 상태를 흉내 낸다.
        stored.forEach(DestinationInfo::refreshKeywordMask);
        when(repository.findAllBySource(DestinationInfo.SOURCE_WIKIDATA)).thenReturn(stored);

        TourSyncResult second = service.syncDestinations(List.of("JP"), 150);

        assertThat(second).isEqualTo(new TourSyncResult(3, 0, 0, 0, false));
        assertThat(captureSaveAll(2).get(1)).isEmpty();
    }

    @Test
    void updatesChangedRowsAndRefetchesDescriptionWhenArticleChanges() {
        givenJapaneseFixture();
        DestinationInfo moved = storedCopy("Q63465", "https://ko.wikipedia.org/wiki/%EC%98%9B_%EB%AC%B8%EC%84%9C");
        DestinationInfo relocated = storedCopy("Q1073585", null);
        relocated.setLocation("일본 사가현");
        when(repository.findAllBySource(DestinationInfo.SOURCE_WIKIDATA)).thenReturn(List.of(moved, relocated));

        TourSyncResult result = service.syncDestinations(List.of("JP"), 150);

        assertThat(result).isEqualTo(new TourSyncResult(3, 1, 2, 0, false));
        assertThat(captureSaveAll(1).get(0)).contains(moved, relocated);
        // 위키데이터가 다른 문서를 가리키면 예전 설명문을 지우고 다시 받도록 대기열에 넣는다.
        assertThat(moved.getSourceUrl()).isEqualTo("https://ko.wikipedia.org/wiki/%EA%B5%AC%EB%9D%BC%EB%B0%94%EC%97%94");
        assertThat(moved.getInfo()).isNull();
        assertThat(moved.getSourceRevision()).isNull();
        assertThat(moved.getOverviewSyncedAt()).isNull();
        assertThat(moved.getOverviewFailedAt()).isNull();
        // 문서가 그대로면 설명문과 수집 기록을 유지한다.
        assertThat(relocated.getLocation()).isEqualTo("일본 가라쓰시");
        assertThat(relocated.getInfo()).isEqualTo("예전에 받은 설명문");
        assertThat(relocated.getSourceRevision()).isEqualTo(7L);
        assertThat(relocated.getOverviewSyncedAt()).isNotNull();
    }

    @Test
    void savesRowWhoseKeywordMaskIsStale() {
        // 유형 코드표만 고친 경우: 값은 그대로여도 저장된 마스크가 지금 계산과 다르면 저장한다.
        givenJapaneseFixture();
        DestinationInfo stale = storedCopy("Q1073585", null);
        stale.setKeywordMaskLow(0L);
        stale.setKeywordMaskHigh(0L);
        DestinationInfo current = storedCopy("Q63465", null);
        current.refreshKeywordMask();
        when(repository.findAllBySource(DestinationInfo.SOURCE_WIKIDATA)).thenReturn(List.of(stale, current));

        TourSyncResult result = service.syncDestinations(List.of("JP"), 150);

        assertThat(result).isEqualTo(new TourSyncResult(3, 1, 1, 0, false));
        assertThat(captureSaveAll(1).get(0)).contains(stale).doesNotContain(current);
    }

    @Test
    void picksTopItemsPerCountryAndPutsTransboundaryItemsInOneCountry() {
        when(client.fetchCountries()).thenReturn(List.of(GERMANY, FRANCE, ITALY));
        when(client.fetchCandidates(anyCollection())).thenReturn(candidates(
                candidate("Q1", 100, "FR", "IT"),
                candidate("Q2", 50, "IT"),
                candidate("Q3", 80, "IT"),
                candidate("Q4", 10, "FR"),
                candidate("Q6", 80, "IT", "KR")));
        when(client.fetchHeritageCandidates()).thenReturn(candidates(
                candidate("Q5", 70, "DE", "FR"),
                candidate("Q7", 999, "KR"),
                candidate("Q8", 500, "DE", "KP")));
        DestinationInfo storedInFrance = new DestinationInfo();
        storedInFrance.setSource(DestinationInfo.SOURCE_WIKIDATA);
        storedInFrance.setExternalId("Q5");
        storedInFrance.setCountryCode("FR");
        when(repository.findAllBySource(DestinationInfo.SOURCE_WIKIDATA)).thenReturn(List.of(storedInFrance));
        when(client.fetchDestinations(anyList())).thenAnswer(invocation -> {
            List<String> qids = invocation.getArgument(0);
            return qids.stream().map(qid -> destination(qid, "여행지" + qid, List.of("Q33506"), false, List.of())).toList();
        });

        TourSyncResult result = service.syncDestinations(List.of(), 2);

        // 국경을 걸친 Q1은 ISO 순 첫 나라(FR)에, 이미 프랑스로 저장된 Q5는 DE가 앞서도 FR에 남는다.
        // 국가에 대한민국·북한이 하나라도 있는 Q6·Q7·Q8은 다른 나라가 함께 적혀 있어도 수집하지 않는다. (예: 독도 P17 = 일본, 대한민국)
        verify(client, never()).fetchDestinations(List.of());
        verify(client).fetchDestinations(List.of("Q1", "Q5"));
        verify(client).fetchDestinations(List.of("Q3", "Q2"));
        verify(client, times(2)).fetchDestinations(anyList());
        assertThat(result).isEqualTo(new TourSyncResult(4, 3, 1, 0, false));
        assertThat(storedInFrance.getCountry()).isEqualTo("프랑스");

        // 일부 국가만 실행해도 다른 나라 소속 여행지(FR로 가는 Q1)는 옮기지 않는다.
        service.syncDestinations(List.of("it"), 5);
        verify(client, times(2)).fetchDestinations(List.of("Q3", "Q2"));
        verify(client, times(3)).fetchDestinations(anyList());
    }

    @Test
    void neverPlacesItemsInvolvingKoreaInAnotherCountry() {
        Set<String> known = Set.of("JP", "CN", "FR");

        assertThat(WikiDestinationSyncService.homeCountry(candidate("Q20317", 90, "JP", "KR"), known, null)).isNull();
        assertThat(WikiDestinationSyncService.homeCountry(candidate("Q107635", 80, "CN", "KP"), known, null)).isNull();
        assertThat(WikiDestinationSyncService.homeCountry(candidate("Q243", 200, "FR"), known, null)).isEqualTo("FR");
        assertThat(WikiDestinationSyncService.homeCountry(candidate("Q1", 1, "XX"), known, null)).isNull();
    }

    @Test
    void skipsDestinationsWhoseTypeIsNoLongerListed() {
        // 후보 목록을 만든 뒤 P31이 바뀌어 코드표에 있는 유형이 없어졌고 세계유산도 아니면 저장하지 않는다.
        when(client.fetchCountries()).thenReturn(List.of(FRANCE));
        when(client.fetchCandidates(anyCollection())).thenReturn(candidates(
                candidate("Q1", 50, "FR"), candidate("Q2", 40, "FR"), candidate("Q3", 30, "FR")));
        when(client.fetchHeritageCandidates()).thenReturn(Map.of());
        when(client.fetchDestinations(anyList())).thenReturn(List.of(
                destination("Q1", "가", List.of("Q33506"), false, List.of()),
                destination("Q2", "나", List.of("Q515"), false, List.of()),
                destination("Q3", "다", List.of("Q515"), true, List.of())));

        TourSyncResult result = service.syncDestinations(List.of(), 150);

        assertThat(result).isEqualTo(new TourSyncResult(2, 2, 0, 0, false));
        assertThat(captureSaveAll(1).get(0)).extracting(DestinationInfo::getExternalId).containsExactly("Q1", "Q3");
    }

    @Test
    void rejectsUnknownCountryCodes() {
        when(client.fetchCountries()).thenReturn(List.of(JAPAN, FRANCE));

        assertThatThrownBy(() -> service.syncDestinations(List.of("JP", "XX"), 150))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("XX");
        verify(client, never()).fetchCandidates(anyCollection());
        verify(repository, never()).saveAll(anyList());
    }

    @Test
    void splitsCandidateBatchesWhenQueryTimesOut() {
        // 후보 5곳을 3개씩 → [Q1, Q2, Q3], [Q4, Q5]
        WikiDestinationSyncService batched = new WikiDestinationSyncService(repository, client,
                List.of("Q1", "Q2", "Q3", "Q4", "Q5"), 3, 30);
        when(client.fetchCountries()).thenReturn(List.of(JAPAN));
        when(client.fetchCandidates(List.of("Q1", "Q2", "Q3")))
                .thenThrow(new WikimediaClient.RequestFailedException(500, "java.util.concurrent.TimeoutException"));
        when(client.fetchCandidates(List.of("Q1"))).thenReturn(candidates(candidate("Q1", 10, "JP")));
        // 결과를 내보내다 60초 제한에 걸려 중간에 끊긴 200 응답도 나눠 다시 묻는다.
        when(client.fetchCandidates(List.of("Q2", "Q3")))
                .thenThrow(new WikimediaClient.RequestFailedException(200, "SPARQL 응답을 해석할 수 없습니다", true));
        when(client.fetchCandidates(List.of("Q2"))).thenReturn(candidates(candidate("Q2", 20, "JP")));
        when(client.fetchCandidates(List.of("Q3"))).thenReturn(Map.of());
        when(client.fetchCandidates(List.of("Q4", "Q5")))
                .thenThrow(new WikimediaClient.RequestFailedException(400, "MalformedQueryException"));
        when(client.fetchHeritageCandidates()).thenReturn(candidates(candidate("Q1", 30, "JP")));
        when(client.fetchDestinations(anyList())).thenReturn(List.of(
                destination("Q1", "가", List.of("Q33506"), true, List.of()),
                destination("Q2", "나", List.of("Q23413"), false, List.of())));

        TourSyncResult result = batched.syncDestinations(List.of(), 150);

        // 시간 초과한 묶음은 반으로 나눠 다시 묻고, 400(쿼리 오류)은 나누지 않고 실패로 센다.
        assertThat(result).isEqualTo(new TourSyncResult(2, 2, 0, 1, false));
        verify(client, times(6)).fetchCandidates(anyCollection());
        verify(client).fetchDestinations(List.of("Q1", "Q2"));
    }

    @Test
    void stopsAtRateLimitAndKeepsEarlierCountries() {
        when(client.fetchCountries()).thenReturn(List.of(FRANCE, JAPAN));
        when(client.fetchCandidates(anyCollection())).thenReturn(candidates(candidate("Q1", 10, "FR"), candidate("Q2", 10, "JP")));
        when(client.fetchHeritageCandidates()).thenReturn(Map.of());
        when(client.fetchDestinations(List.of("Q1"))).thenReturn(List.of(destination("Q1", "가", List.of("Q2977"), false, List.of())));
        when(client.fetchDestinations(List.of("Q2"))).thenThrow(new WikimediaClient.RateLimitedException("429"));

        TourSyncResult result = service.syncDestinations(List.of(), 150);

        assertThat(result).isEqualTo(new TourSyncResult(1, 1, 0, 0, true));
        assertThat(captureSaveAll(1).get(0)).extracting(DestinationInfo::getExternalId).containsExactly("Q1");
    }

    @Test
    void abortsAfterRepeatedRequestFailures() {
        when(client.fetchCountries()).thenReturn(List.of(GERMANY, FRANCE, ITALY, JAPAN));
        when(client.fetchCandidates(anyCollection())).thenReturn(candidates(
                candidate("Q1", 1, "DE"), candidate("Q2", 1, "FR"), candidate("Q3", 1, "IT"), candidate("Q4", 1, "JP")));
        when(client.fetchHeritageCandidates()).thenReturn(Map.of());
        when(client.fetchDestinations(anyList())).thenThrow(new IllegalStateException("위키미디어 요청 실패 (HTTP 500)"));

        assertThatThrownBy(() -> service.syncDestinations(List.of(), 150))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("연속 3회");
        verify(client, never()).fetchDestinations(List.of("Q4"));
    }

    @Test
    void fetchesDescriptionsTwentyTitlesPerRequest() {
        List<DestinationInfo> rows = new ArrayList<>();
        for (int i = 1; i <= 25; i++) {
            rows.add(wikiRow((long) i, "문서" + i));
        }
        givenDescriptionTargets(rows, List.of());
        when(client.fetchExtracts(anyList())).thenAnswer(invocation -> {
            List<String> titles = invocation.getArgument(0);
            Map<String, WikiExtract> extracts = new LinkedHashMap<>();
            for (String title : titles) {
                extracts.put(title, new WikiExtract(title, title, 100L + titles.indexOf(title),
                        title + "(프랑스어: Nom[*], ​[nɔ̃])은  유네스코 세계유산인\n박물관이다.", "https://ko.wikipedia.org/wiki/x", false));
            }
            return extracts;
        });

        TourSyncResult result = service.syncDescriptions(100);

        assertThat(result).isEqualTo(new TourSyncResult(25, 0, 25, 0, false));
        @SuppressWarnings("unchecked")
        ArgumentCaptor<List<String>> titles = ArgumentCaptor.forClass(List.class);
        verify(client, times(2)).fetchExtracts(titles.capture());
        assertThat(titles.getAllValues().get(0)).hasSize(20).startsWith("문서1", "문서2");
        assertThat(titles.getAllValues().get(1)).containsExactly("문서21", "문서22", "문서23", "문서24", "문서25");

        DestinationInfo first = rows.get(0);
        assertThat(first.getInfo()).isEqualTo("문서1(프랑스어: Nom, [nɔ̃])은 유네스코 세계유산인 박물관이다.");
        assertThat(first.getSourceRevision()).isEqualTo(100L);
        assertThat(first.getOverviewSyncedAt()).isNotNull();
        // 설명문 키워드 ∪ 유형(성) 키워드
        assertThat(first.computeKeywordMask())
                .isEqualTo(KeywordMask.fromKeywords(List.of("유네스코", "박물관", "성", "역사", "역사적인 장소")));
        captureSaveAll(2);
    }

    @Test
    void handlesMissingPagesEmptyExtractsAndAbsentTitles() {
        DestinationInfo missing = wikiRow(1L, "없는 문서");
        DestinationInfo empty = wikiRow(2L, "빈 문서");
        DestinationInfo absent = wikiRow(3L, "응답에 없는 문서");
        DestinationInfo noUrl = wikiRow(4L, "주소 없음");
        noUrl.setSourceUrl(null);
        DestinationInfo cutOff = wikiRow(5L, "도입부 빠짐");
        givenDescriptionTargets(List.of(missing, empty, absent, noUrl, cutOff), List.of());
        when(client.fetchExtracts(anyList())).thenReturn(Map.of(
                "없는 문서", new WikiExtract("없는 문서", "없는 문서", null, null, null, true),
                "빈 문서", new WikiExtract("빈 문서", "빈 문서", 9L, "", "https://ko.wikipedia.org/wiki/x", false),
                "도입부 빠짐", new WikiExtract("도입부 빠짐", "도입부 빠짐", 9L, null, "https://ko.wikipedia.org/wiki/y", false)));

        TourSyncResult result = service.syncDescriptions(10);

        assertThat(result).isEqualTo(new TourSyncResult(5, 0, 2, 3, false));
        verify(client).fetchExtracts(List.of("없는 문서", "빈 문서", "응답에 없는 문서", "도입부 빠짐"));
        assertThat(missing.getInfo()).isNull();
        assertThat(missing.getOverviewSyncedAt()).isNotNull();
        assertThat(empty.getInfo()).isNull();
        assertThat(empty.getSourceRevision()).isEqualTo(9L);
        assertThat(empty.getOverviewSyncedAt()).isNotNull();
        // 응답에 없거나 도입부가 빠져 온 행, 문서 주소가 없는 행은 실패로 기록해 대기열 뒤로 보낸다.
        assertThat(absent.getOverviewSyncedAt()).isNull();
        verify(repository).markOverviewFailed(eq(3L), any(LocalDateTime.class));
        verify(repository).markOverviewFailed(eq(4L), any(LocalDateTime.class));
        verify(repository).markOverviewFailed(eq(5L), any(LocalDateTime.class));
    }

    @Test
    void rechecksOldDescriptionsAndKeepsUnchangedRevisions() {
        DestinationInfo fresh = wikiRow(1L, "새 문서");
        DestinationInfo sameRevision = wikiRow(2L, "그대로인 문서");
        sameRevision.setInfo("예전 설명");
        sameRevision.setSourceRevision(50L);
        sameRevision.setOverviewSyncedAt(LocalDateTime.of(2026, 1, 1, 0, 0));
        DestinationInfo newRevision = wikiRow(3L, "고친 문서");
        newRevision.setInfo("예전 설명");
        newRevision.setSourceRevision(60L);
        newRevision.setOverviewSyncedAt(LocalDateTime.of(2026, 1, 1, 0, 0));
        givenDescriptionTargets(List.of(fresh), List.of(sameRevision, newRevision));
        when(client.fetchExtracts(anyList())).thenReturn(Map.of(
                "새 문서", new WikiExtract("새 문서", "새 문서", 10L, "새 설명", null, false),
                "그대로인 문서", new WikiExtract("그대로인 문서", "그대로인 문서", 50L, "예전 설명", null, false),
                "고친 문서", new WikiExtract("고친 문서", "고친 문서", 61L, "고친 설명", null, false)));

        TourSyncResult result = service.syncDescriptions(10);

        assertThat(result).isEqualTo(new TourSyncResult(3, 0, 2, 0, false));
        // 설명문이 없는 행 다음, 받은 지 30일이 지난 행을 남은 개수만큼 묻는다.
        ArgumentCaptor<Pageable> pages = ArgumentCaptor.forClass(Pageable.class);
        verify(repository).findBySourceAndOverviewSyncedAtBeforeOrderByOverviewFailedAtAscOverviewSyncedAtAscIdAsc(
                eq(DestinationInfo.SOURCE_WIKIDATA), any(LocalDateTime.class), pages.capture());
        assertThat(pages.getValue().getPageSize()).isEqualTo(9);

        assertThat(captureSaveAll(1).get(0)).containsExactly(fresh, newRevision);
        assertThat(newRevision.getInfo()).isEqualTo("고친 설명");
        assertThat(newRevision.getSourceRevision()).isEqualTo(61L);
        // 판이 그대로면 행을 다시 쓰지 않고 확인 시각만 바꾼다.
        assertThat(sameRevision.getOverviewSyncedAt()).isEqualTo(LocalDateTime.of(2026, 1, 1, 0, 0));
        verify(repository).markOverviewSynced(eq(List.of(2L)), any(LocalDateTime.class));
    }

    @Test
    void stopsDescriptionsAtRateLimitAndAbortsAfterRepeatedFailures() {
        List<DestinationInfo> rows = new ArrayList<>();
        for (int i = 1; i <= 80; i++) {
            rows.add(wikiRow((long) i, "문서" + i));
        }
        givenDescriptionTargets(rows, List.of());
        when(client.fetchExtracts(anyList()))
                .thenReturn(Map.of("문서1", new WikiExtract("문서1", "문서1", 1L, "설명", null, false)))
                .thenThrow(new WikimediaClient.RateLimitedException("429"));

        assertThat(service.syncDescriptions(80)).isEqualTo(new TourSyncResult(20, 0, 1, 19, true));

        doThrow(new IllegalStateException("위키백과 API 오류: internal_api_error")).when(client).fetchExtracts(anyList());
        assertThatThrownBy(() -> service.syncDescriptions(80))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("연속 3회");
        // 실패한 묶음의 행은 모두 실패 시각을 남긴다. (첫 실행의 응답에 없던 19건 + 연속 실패 3묶음 60건)
        verify(repository, times(19 + 60)).markOverviewFailed(any(), any(LocalDateTime.class));
    }

    @Test
    void buildsFactOnlySummary() {
        assertThat(WikiDestinationSyncService.summaryOf("일본 나가사키시", "Q33506", false)).isEqualTo("일본 나가사키시의 박물관입니다.");
        // 대표 유형은 category 순서가 아니라 코드표에서 앞선 유형 (박물관이 성보다 앞)
        assertThat(WikiDestinationSyncService.summaryOf("프랑스 파리", "Q23413|Q33506", true))
                .isEqualTo("프랑스 파리의 박물관입니다. 유네스코 세계유산입니다.");
        assertThat(WikiDestinationSyncService.summaryOf("이탈리아", null, true)).isEqualTo("이탈리아의 여행지입니다. 유네스코 세계유산입니다.");
        assertThat(WikiDestinationSyncService.summaryOf(" ", "Q46169", null)).isEqualTo("국립공원입니다.");
        // 한국어 유형명이 없는 유형(island group Q1402592)만 있으면 '여행지'
        assertThat(WikiDestinationSyncService.summaryOf("그리스", "Q1402592", false)).isEqualTo("그리스의 여행지입니다.");
        // P31 = 세계유산(Q9259)인 항목은 세계유산 문장을 붙일 때 같은 말을 두 번 쓰지 않는다.
        assertThat(WikiDestinationSyncService.summaryOf("이탈리아 로마", "Q9259", true)).isEqualTo("이탈리아 로마의 여행지입니다. 유네스코 세계유산입니다.");
        assertThat(WikiDestinationSyncService.summaryOf("이탈리아 로마", "Q839954|Q9259", true)).isEqualTo("이탈리아 로마의 유적지입니다. 유네스코 세계유산입니다.");
        assertThat(WikiDestinationSyncService.summaryOf("이탈리아 로마", "Q9259", false)).isEqualTo("이탈리아 로마의 유네스코 세계유산입니다.");
        assertThat(WikiDestinationSyncService.summaryOf(null, "Q999", false)).isNull();
        assertThat(WikiDestinationSyncService.summaryOf("가".repeat(600), "Q33506", false))
                .hasSize(WikiDestinationSyncService.SUMMARY_MAX_LENGTH);
    }

    @Test
    void buildsLocationFromCountryAndAdministrativeArea() {
        assertThat(WikiDestinationSyncService.locationOf("일본", "JP", admins("일본", " ", "교토시"))).isEqualTo("일본 교토시");
        assertThat(WikiDestinationSyncService.locationOf("일본", "JP", admins("일본"))).isEqualTo("일본");
        assertThat(WikiDestinationSyncService.locationOf("프랑스", "FR", List.of())).isEqualTo("프랑스");

        // 국경에 걸친 몽블랑산(프랑스로 들어감)의 P131 중 이탈리아 쪽(쿠르마유르)이 QID 번호가 앞서도 프랑스 쪽 행정구역을 쓴다.
        List<WikiDestination.Admin> montBlanc = List.of(
                new WikiDestination.Admin("쿠르마유르", Set.of("IT")),
                new WikiDestination.Admin("샤모니몽블랑", Set.of("FR")));
        assertThat(WikiDestinationSyncService.locationOf("프랑스", "FR", montBlanc)).isEqualTo("프랑스 샤모니몽블랑");
        assertThat(WikiDestinationSyncService.locationOf("이탈리아", "IT", montBlanc)).isEqualTo("이탈리아 쿠르마유르");
        // 다른 나라 행정구역만 있으면 나라 이름만
        assertThat(WikiDestinationSyncService.locationOf("스위스", "CH", montBlanc)).isEqualTo("스위스");
    }

    @Test
    void cleansExtractText() {
        assertThat(WikiDestinationSyncService.cleanExtract("에펠탑(프랑스어: Tour Eiffel 투르 에펠[*], 프랑스어 발음: ​[tuʁ ɛfɛl])은\n\n파리의 탑( )이다."))
                .isEqualTo("에펠탑(프랑스어: Tour Eiffel 투르 에펠, 프랑스어 발음: [tuʁ ɛfɛl])은 파리의 탑이다.");
        assertThat(WikiDestinationSyncService.cleanExtract(null)).isEmpty();
        assertThat(WikiDestinationSyncService.cleanExtract("가".repeat(20_000))).hasSize(WikiDestinationSyncService.INFO_MAX_LENGTH);
        // 서로게이트 쌍 중간에서 자르지 않는다.
        String emoji = "가".repeat(WikiDestinationSyncService.INFO_MAX_LENGTH - 1) + "😀" + "나";
        assertThat(WikiDestinationSyncService.cleanExtract(emoji)).hasSize(WikiDestinationSyncService.INFO_MAX_LENGTH - 1);
    }

    @Test
    void rebuildsSummariesAndMasksWithoutCallingWikimedia() {
        DestinationInfo stale = wikiRow(1L, "가");
        stale.setLocation("일본 가라쓰시");
        stale.setSummary("예전 소개");
        DestinationInfo current = wikiRow(2L, "나");
        current.setLocation("일본 가라쓰시");
        current.setSummary(WikiDestinationSyncService.summaryOf("일본 가라쓰시", "Q23413", false));
        current.refreshKeywordMask();
        when(repository.findBySourceAndIdGreaterThanOrderByIdAsc(eq(DestinationInfo.SOURCE_WIKIDATA), eq(0L), any(Pageable.class)))
                .thenReturn(List.of(stale, current));

        TourSyncResult result = service.rebuildSummaries();

        assertThat(result).isEqualTo(new TourSyncResult(2, 0, 1, 0, false));
        assertThat(captureSaveAll(1).get(0)).containsExactly(stale);
        assertThat(stale.getSummary()).isEqualTo("일본 가라쓰시의 성입니다.");
        assertThat(stale.getKeywordMaskLow()).isEqualTo(KeywordMask.fromKeywords(List.of("성", "역사", "역사적인 장소")).low());
        assertThat(stale.getKeywordMaskHigh()).isEqualTo(KeywordMask.fromKeywords(List.of("성", "역사", "역사적인 장소")).high());
        verify(client, never()).fetchCountries();
    }

    // ---- 도우미 ----

    // 실제 응답 픽스처: 구라바엔(박물관), 가라쓰성(성), 시라카미 산지(세계유산), 그 밖에 후보가 아닌 3곳
    private void givenJapaneseFixture() {
        when(client.fetchCountries()).thenReturn(List.of(FRANCE, JAPAN));
        when(client.fetchCandidates(anyCollection()))
                .thenReturn(candidates(candidate("Q63465", 14, "JP"), candidate("Q1073585", 30, "JP")));
        when(client.fetchHeritageCandidates()).thenReturn(candidates(candidate("Q501638", 53, "JP")));
        List<WikiDestination> fixture = PARSER.parseDestinations(WikimediaClientTest.fixture("sparql-destinations.json"));
        when(client.fetchDestinations(anyList())).thenReturn(fixture);
    }

    // 픽스처 값으로 한 번 저장된 것과 같은 행 (설명문까지 받은 상태)
    private static DestinationInfo storedCopy(String qid, String sourceUrl) {
        WikiDestination wiki = PARSER.parseDestinations(WikimediaClientTest.fixture("sparql-destinations.json")).stream()
                .filter(destination -> destination.qid().equals(qid))
                .findFirst()
                .orElseThrow();
        DestinationInfo row = new DestinationInfo();
        row.setSource(DestinationInfo.SOURCE_WIKIDATA);
        row.setExternalId(qid);
        WikiDestinationSyncService.apply(row, JAPAN, wiki);
        if (sourceUrl != null) {
            row.setSourceUrl(sourceUrl);
        }
        row.setInfo("예전에 받은 설명문");
        row.setSourceRevision(7L);
        row.setOverviewSyncedAt(LocalDateTime.of(2026, 10, 1, 0, 0));
        row.refreshKeywordMask();
        return row;
    }

    private void givenDescriptionTargets(List<DestinationInfo> unsynced, List<DestinationInfo> old) {
        when(repository.findBySourceAndOverviewSyncedAtIsNullOrderByOverviewFailedAtAscIdAsc(eq(DestinationInfo.SOURCE_WIKIDATA), any(Pageable.class)))
                .thenReturn(unsynced);
        when(repository.findBySourceAndOverviewSyncedAtBeforeOrderByOverviewFailedAtAscOverviewSyncedAtAscIdAsc(
                eq(DestinationInfo.SOURCE_WIKIDATA), any(LocalDateTime.class), any(Pageable.class)))
                .thenReturn(old);
    }

    private static DestinationInfo wikiRow(Long id, String title) {
        DestinationInfo row = new DestinationInfo();
        row.setId(id);
        row.setSource(DestinationInfo.SOURCE_WIKIDATA);
        row.setExternalId("Q" + (1000 + id));
        row.setCategory("Q23413");
        row.setWorldHeritage(false);
        row.setSourceUrl("https://ko.wikipedia.org/wiki/" + java.net.URLEncoder.encode(title, java.nio.charset.StandardCharsets.UTF_8)
                .replace("+", "_"));
        return row;
    }

    private static WikiCandidate candidate(String qid, int sitelinks, String... codes) {
        return new WikiCandidate(qid, sitelinks, Set.of(codes));
    }

    private static Map<String, WikiCandidate> candidates(WikiCandidate... candidates) {
        Map<String, WikiCandidate> map = new LinkedHashMap<>();
        for (WikiCandidate candidate : candidates) {
            map.put(candidate.qid(), candidate);
        }
        return map;
    }

    private static WikiDestination destination(String qid, String name, List<String> types, boolean heritage, List<String> admins) {
        return new WikiDestination(qid, name, name, "https://ko.wikipedia.org/wiki/" + qid, 1.0, 2.0, types, heritage,
                admins(admins.toArray(String[]::new)));
    }

    // 국가 코드를 모르는 행정구역들
    private static List<WikiDestination.Admin> admins(String... labels) {
        return java.util.Arrays.stream(labels).map(label -> new WikiDestination.Admin(label, Set.of())).toList();
    }

    @SuppressWarnings("unchecked")
    private List<List<DestinationInfo>> captureSaveAll(int expectedCalls) {
        ArgumentCaptor<List<DestinationInfo>> captor = ArgumentCaptor.forClass(List.class);
        verify(repository, times(expectedCalls)).saveAll(captor.capture());
        return captor.getAllValues();
    }
}
