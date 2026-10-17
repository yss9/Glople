package proj.travien.service;

import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.data.domain.Pageable;
import proj.travien.domain.DestinationInfo;
import proj.travien.dto.TourApiItem;
import proj.travien.dto.TourApiPage;
import proj.travien.dto.TourSyncResult;
import proj.travien.repository.DestinationInfoRepository;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

class TourDestinationSyncServiceTest {

    private final DestinationInfoRepository repository = mock(DestinationInfoRepository.class);
    private final TourApiClient client = mock(TourApiClient.class);
    private final TourDestinationSyncService service =
            new TourDestinationSyncService(repository, client, new String[]{"12"}, 2);

    @Test
    void insertsNewDestinationsPageByPage() {
        when(repository.findAllBySource(DestinationInfo.SOURCE_TOUR_API)).thenReturn(List.of());
        when(client.fetchAreaBasedPage("12", 1, 2, "A"))
                .thenReturn(new TourApiPage(3, List.of(item("1", "부산광역시 해운대구 우동", "m1"), item("2", "세종특별자치시 가람동", "m1"))));
        when(client.fetchAreaBasedPage("12", 2, 2, "A"))
                .thenReturn(new TourApiPage(3, List.of(item("3", "", "m1"))));

        TourSyncResult result = service.syncDestinations();

        assertThat(result).isEqualTo(new TourSyncResult(3, 3, 0, 0, false));
        List<List<DestinationInfo>> saved = captureSaveAll(2);
        DestinationInfo first = saved.get(0).get(0);
        assertThat(first.getSource()).isEqualTo(DestinationInfo.SOURCE_TOUR_API);
        assertThat(first.getCountry()).isEqualTo("대한민국");
        assertThat(first.getExternalId()).isEqualTo("1");
        assertThat(first.getDestName()).isEqualTo("여행지1");
        assertThat(first.getLocation()).isEqualTo("부산광역시 해운대구");
        assertThat(first.getCategory()).isEqualTo("NA040500");
        assertThat(first.getInfo()).isNull();
        // 설명문과 따로, TourAPI 필드로만 만든 소개를 함께 저장한다.
        assertThat(first.getSummary()).isEqualTo("부산광역시 해운대구의 관광지입니다. 분류: 자연관광 > 자연공원 > 생태관광지");
        assertThat(saved.get(0).get(1).getLocation()).isEqualTo("세종특별자치시");
        assertThat(saved.get(1)).extracting(DestinationInfo::getExternalId).containsExactly("3");
        assertThat(saved.get(1).get(0).getSummary()).isEqualTo("관광지입니다. 분류: 자연관광 > 자연공원 > 생태관광지");
    }

    @Test
    void updatesOnlyChangedDestinationsAndRequeuesTheirOverview() {
        DestinationInfo unchanged = existing("1", "m1");
        DestinationInfo changed = existing("2", "m1");
        changed.setOverviewFailedAt(LocalDateTime.of(2026, 1, 2, 0, 0));
        when(repository.findAllBySource(DestinationInfo.SOURCE_TOUR_API)).thenReturn(List.of(unchanged, changed));
        when(client.fetchAreaBasedPage("12", 1, 2, "A"))
                .thenReturn(new TourApiPage(2, List.of(item("1", "서울특별시 종로구", "m1"), item("2", "서울특별시 종로구", "m2"))));

        TourSyncResult result = service.syncDestinations();

        assertThat(result).isEqualTo(new TourSyncResult(2, 0, 1, 0, false));
        assertThat(captureSaveAll(1).get(0)).containsExactly(changed);
        assertThat(changed.getSourceModifiedTime()).isEqualTo("m2");
        assertThat(changed.getSummary()).isEqualTo("서울특별시 종로구의 관광지입니다. 분류: 자연관광 > 자연공원 > 생태관광지");
        assertThat(unchanged.getSummary()).isNull();
        assertThat(changed.getOverviewSyncedAt()).isNull();
        assertThat(changed.getOverviewFailedAt()).isNull();
        assertThat(unchanged.getOverviewSyncedAt()).isNotNull();
    }

    @Test
    void doesNotTreatMissingModifiedTimeAsChange() {
        // 저장할 때 빈 modifiedtime은 null이 되므로, 다음 동기화에서 ""와 비교해 매번 갱신하면 안 된다.
        DestinationInfo saved = existing("1", null);
        when(repository.findAllBySource(DestinationInfo.SOURCE_TOUR_API)).thenReturn(List.of(saved));
        when(client.fetchAreaBasedPage("12", 1, 2, "A"))
                .thenReturn(new TourApiPage(1, List.of(item("1", "서울특별시 종로구", ""))));

        TourSyncResult result = service.syncDestinations();

        assertThat(result).isEqualTo(new TourSyncResult(1, 0, 0, 0, false));
        assertThat(captureSaveAll(1).get(0)).isEmpty();
        assertThat(saved.getOverviewSyncedAt()).isNotNull();
    }

    @Test
    void keepsSavedPagesWhenQuotaRunsOut() {
        when(repository.findAllBySource(DestinationInfo.SOURCE_TOUR_API)).thenReturn(List.of());
        when(client.fetchAreaBasedPage("12", 1, 2, "A"))
                .thenReturn(new TourApiPage(4, List.of(item("1", "", "m1"), item("2", "", "m1"))));
        when(client.fetchAreaBasedPage("12", 2, 2, "A"))
                .thenThrow(new TourApiClient.QuotaExceededException("limit"));

        TourSyncResult result = service.syncDestinations();

        assertThat(result).isEqualTo(new TourSyncResult(2, 2, 0, 0, true));
        captureSaveAll(1);
    }

    @Test
    void storesCleanedOverviewAndMarksBlankOnesAsSynced() {
        DestinationInfo withOverview = existing("1", "m1");
        DestinationInfo withoutOverview = existing("2", "m1");
        withOverview.setOverviewSyncedAt(null);
        withoutOverview.setOverviewSyncedAt(null);
        when(repository.findBySourceAndOverviewSyncedAtIsNullOrderByOverviewFailedAtAscIdAsc(eq(DestinationInfo.SOURCE_TOUR_API), any(Pageable.class)))
                .thenReturn(List.of(withOverview, withoutOverview));
        when(client.fetchOverview("1")).thenReturn("부산의 대표 해변<br />여름&nbsp;축제 &lt;바다&gt;");
        when(client.fetchOverview("2")).thenReturn("");

        TourSyncResult result = service.syncOverviews(10);

        assertThat(result).isEqualTo(new TourSyncResult(2, 0, 2, 0, false));
        assertThat(withOverview.getInfo()).isEqualTo("부산의 대표 해변 여름 축제 <바다>");
        assertThat(withOverview.getOverviewSyncedAt()).isNotNull();
        assertThat(withoutOverview.getInfo()).isNull();
        assertThat(withoutOverview.getOverviewSyncedAt()).isNotNull();
        verify(repository, times(2)).save(any(DestinationInfo.class));
    }

    @Test
    void stopsOverviewSyncWhenQuotaRunsOut() {
        DestinationInfo first = existing("1", "m1");
        DestinationInfo second = existing("2", "m1");
        second.setOverviewSyncedAt(null);
        when(repository.findBySourceAndOverviewSyncedAtIsNullOrderByOverviewFailedAtAscIdAsc(eq(DestinationInfo.SOURCE_TOUR_API), any(Pageable.class)))
                .thenReturn(List.of(first, second));
        when(client.fetchOverview("1")).thenReturn("설명");
        when(client.fetchOverview("2")).thenThrow(new TourApiClient.QuotaExceededException("limit"));

        TourSyncResult result = service.syncOverviews(10);

        assertThat(result).isEqualTo(new TourSyncResult(1, 0, 1, 0, true));
        assertThat(second.getOverviewSyncedAt()).isNull();
    }

    @Test
    void abortsOverviewSyncAfterRepeatedFailures() {
        List<DestinationInfo> targets = List.of(
                existing("1", "m"), existing("2", "m"), existing("3", "m"),
                existing("4", "m"), existing("5", "m"), existing("6", "m"));
        when(repository.findBySourceAndOverviewSyncedAtIsNullOrderByOverviewFailedAtAscIdAsc(eq(DestinationInfo.SOURCE_TOUR_API), any(Pageable.class)))
                .thenReturn(targets);
        when(client.fetchOverview(anyString())).thenThrow(new IllegalStateException("TourAPI 게이트웨이 오류: 30"));

        assertThatThrownBy(() -> service.syncOverviews(10))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("연속 5회");
        verify(client, never()).fetchOverview("6");
        verify(repository, never()).save(any(DestinationInfo.class));
        verify(repository, times(5)).markOverviewFailed(any(), any(LocalDateTime.class));
    }

    @Test
    void retriesFailedOverviewsOnlyAfterUntriedOnes() {
        // id 1·3·5·7·9는 매번 실패하고 나머지는 성공한다.
        List<DestinationInfo> rows = new ArrayList<>();
        for (long id = 1; id <= 12; id++) {
            DestinationInfo row = existing(String.valueOf(id), "m");
            row.setId(id);
            row.setOverviewSyncedAt(null);
            rows.add(row);
        }
        // 실제 쿼리와 같은 순서: 실패 기록이 없는 행(NULL)을 id 순으로 먼저, 그다음 오래전에 실패한 행
        when(repository.findBySourceAndOverviewSyncedAtIsNullOrderByOverviewFailedAtAscIdAsc(eq(DestinationInfo.SOURCE_TOUR_API), any(Pageable.class)))
                .thenAnswer(invocation -> {
                    Pageable pageable = invocation.getArgument(1);
                    return rows.stream()
                            .filter(row -> row.getOverviewSyncedAt() == null)
                            .sorted(Comparator.comparing(DestinationInfo::getOverviewFailedAt,
                                            Comparator.nullsFirst(Comparator.<LocalDateTime>naturalOrder()))
                                    .thenComparing(DestinationInfo::getId))
                            .limit(pageable.getPageSize())
                            .toList();
                });
        when(client.fetchOverview(anyString())).thenAnswer(invocation -> {
            int contentId = Integer.parseInt(invocation.getArgument(0));
            if (contentId <= 9 && contentId % 2 == 1) {
                throw new IllegalStateException("TourAPI 오류: 99");
            }
            return "설명" + contentId;
        });

        // 1회차: 실패가 성공 사이에 흩어져 있어 끝까지 진행된다.
        assertThat(service.syncOverviews(10)).isEqualTo(new TourSyncResult(10, 0, 5, 5, false));

        // 2회차: 실패한 5건이 대기열 맨 앞에 남아 있으면 바로 '연속 5회 실패'로 멈추지만,
        // 실패 시각이 기록되어 아직 시도하지 않은 11·12번부터 받는다.
        assertThat(service.syncOverviews(6)).isEqualTo(new TourSyncResult(6, 0, 2, 4, false));
        assertThat(rows.get(10).getInfo()).isEqualTo("설명11");
        assertThat(rows.get(11).getInfo()).isEqualTo("설명12");
        assertThat(rows).filteredOn(row -> row.getOverviewSyncedAt() == null)
                .extracting(DestinationInfo::getId)
                .containsExactly(1L, 3L, 5L, 7L, 9L);
        assertThat(rows).filteredOn(row -> row.getOverviewSyncedAt() != null)
                .allSatisfy(row -> assertThat(row.getOverviewFailedAt()).isNull());
        verify(repository, times(2)).markOverviewFailed(eq(1L), any(LocalDateTime.class));
        verify(repository, times(1)).markOverviewFailed(eq(9L), any(LocalDateTime.class));
    }

    @Test
    void removesInlineTagsWithoutSplittingWords() {
        assertThat(TourDestinationSyncService.cleanOverview("부산을 대표하는 <b>해변</b>으로<p>여름에는</p><strong>바다</strong>&nbsp;축제"))
                .isEqualTo("부산을 대표하는 해변으로 여름에는 바다 축제");
        assertThat(TourDestinationSyncService.cleanOverview("<a href=\"http://example.test\">경복궁</a>은<br>조선의 궁궐"))
                .isEqualTo("경복궁은 조선의 궁궐");
        assertThat(TourDestinationSyncService.cleanOverview(null)).isEmpty();
    }

    @Test
    void derivesRegionFromAddress() {
        assertThat(TourDestinationSyncService.regionOf("경기도 수원시 팔달구 정조로 825")).isEqualTo("경기도 수원시");
        assertThat(TourDestinationSyncService.regionOf("제주특별자치도 서귀포시 성산읍")).isEqualTo("제주특별자치도 서귀포시");
        assertThat(TourDestinationSyncService.regionOf("세종특별자치시 가람동")).isEqualTo("세종특별자치시");
        assertThat(TourDestinationSyncService.regionOf(" ")).isNull();
    }

    @Test
    void buildsFactOnlySummary() {
        assertThat(TourDestinationSyncService.summaryOf("부산광역시 해운대구", "12", "NA020900"))
                .isEqualTo("부산광역시 해운대구의 관광지입니다. 분류: 자연관광 > 자연경관(하천‧해양) > 해변. 해수욕장");
        assertThat(TourDestinationSyncService.summaryOf("서울특별시 종로구", "14", "VE070100"))
                .isEqualTo("서울특별시 종로구의 문화시설입니다. 분류: 문화관광 > 전시시설 > 박물관");
        assertThat(TourDestinationSyncService.summaryOf("경기도 수원시", "15", "EV010200"))
                .isEqualTo("경기도 수원시의 축제공연행사입니다. 분류: 축제/공연/행사 > 축제 > 문화예술축제");
        assertThat(TourDestinationSyncService.summaryOf("강원특별자치도 평창군", "28", "LS011500"))
                .startsWith("강원특별자치도 평창군의 레포츠입니다. 분류: 레저스포츠 > 육상레저스포츠 > ");
    }

    @Test
    void leavesOutUnknownPartsOfSummary() {
        // 코드표에 없는 분류는 '분류:' 부분을, 지역이 없으면 지역 부분을 뺀다.
        assertThat(TourDestinationSyncService.summaryOf("부산광역시 해운대구", "12", "ZZ999999"))
                .isEqualTo("부산광역시 해운대구의 관광지입니다.");
        assertThat(TourDestinationSyncService.summaryOf("부산광역시 해운대구", "12", null))
                .isEqualTo("부산광역시 해운대구의 관광지입니다.");
        assertThat(TourDestinationSyncService.summaryOf(null, "12", "NA020900"))
                .isEqualTo("관광지입니다. 분류: 자연관광 > 자연경관(하천‧해양) > 해변. 해수욕장");
        assertThat(TourDestinationSyncService.summaryOf(" ", "39", "NA020900"))
                .isEqualTo("여행지입니다. 분류: 자연관광 > 자연경관(하천‧해양) > 해변. 해수욕장");
        assertThat(TourDestinationSyncService.summaryOf(null, null, "ZZ999999")).isNull();
        assertThat(TourDestinationSyncService.summaryOf("가".repeat(600), "12", "NA020900"))
                .hasSize(TourDestinationSyncService.SUMMARY_MAX_LENGTH);
    }

    @Test
    void rebuildsSummariesAndMasksPageByPageWithoutCallingTourApi() {
        // 1,001건 → 1,000건 + 1건 두 페이지. 짝수 id는 이미 최신 소개·마스크라 저장하지 않는다.
        List<DestinationInfo> rows = new ArrayList<>();
        for (long id = 1; id <= TourDestinationSyncService.SUMMARY_BATCH_SIZE + 1; id++) {
            DestinationInfo row = existing(String.valueOf(id), "m");
            row.setId(id);
            row.setLocation("부산광역시 해운대구");
            row.setContentTypeId("12");
            row.setCategory("NA020900");
            if (id % 2 == 0) {
                row.setSummary(TourDestinationSyncService.summaryOf("부산광역시 해운대구", "12", "NA020900"));
                row.refreshKeywordMask();
            }
            rows.add(row);
        }
        when(repository.findBySourceAndIdGreaterThanOrderByIdAsc(eq(DestinationInfo.SOURCE_TOUR_API), any(), any(Pageable.class)))
                .thenAnswer(invocation -> {
                    long lastId = invocation.getArgument(1);
                    Pageable pageable = invocation.getArgument(2);
                    return rows.stream()
                            .filter(row -> row.getId() > lastId)
                            .limit(pageable.getPageSize())
                            .toList();
                });

        TourSyncResult result = service.rebuildSummaries();

        assertThat(result).isEqualTo(new TourSyncResult(1001, 0, 501, 0, false));
        verify(repository).findBySourceAndIdGreaterThanOrderByIdAsc(eq(DestinationInfo.SOURCE_TOUR_API), eq(0L), any(Pageable.class));
        verify(repository).findBySourceAndIdGreaterThanOrderByIdAsc(eq(DestinationInfo.SOURCE_TOUR_API), eq(1000L), any(Pageable.class));
        List<List<DestinationInfo>> saved = captureSaveAll(2);
        assertThat(saved.get(0)).hasSize(500).allSatisfy(row -> assertThat(row.getId() % 2).isEqualTo(1L));
        assertThat(saved.get(1)).extracting(DestinationInfo::getId).containsExactly(1001L);
        assertThat(rows).allSatisfy(row -> {
            assertThat(row.getSummary()).isEqualTo("부산광역시 해운대구의 관광지입니다. 분류: 자연관광 > 자연경관(하천‧해양) > 해변. 해수욕장");
            assertThat(row.getKeywordMaskLow()).isEqualTo(TourCategories.keywordMask("NA020900").low());
        });
        verifyNoInteractions(client);
    }

    @Test
    void rebuildSavesRowsWhoseMaskIsStaleEvenIfSummaryIsCurrent() {
        // 분류 코드표만 고친 경우: 소개는 같아도 마스크가 달라졌으면 저장한다.
        DestinationInfo stale = existing("1", "m");
        stale.setId(1L);
        stale.setLocation("부산광역시 해운대구");
        stale.setContentTypeId("12");
        stale.setCategory("NA020900");
        stale.setSummary(TourDestinationSyncService.summaryOf("부산광역시 해운대구", "12", "NA020900"));
        when(repository.findBySourceAndIdGreaterThanOrderByIdAsc(eq(DestinationInfo.SOURCE_TOUR_API), eq(0L), any(Pageable.class)))
                .thenReturn(List.of(stale));

        assertThat(service.rebuildSummaries()).isEqualTo(new TourSyncResult(1, 0, 1, 0, false));
        assertThat(captureSaveAll(1).get(0)).containsExactly(stale);
        assertThat(stale.getKeywordMaskLow()).isNotZero();
    }

    @SuppressWarnings("unchecked")
    private List<List<DestinationInfo>> captureSaveAll(int expectedCalls) {
        ArgumentCaptor<List<DestinationInfo>> captor = ArgumentCaptor.forClass(List.class);
        verify(repository, times(expectedCalls)).saveAll(captor.capture());
        return captor.getAllValues();
    }

    private static TourApiItem item(String contentId, String address, String modifiedTime) {
        return new TourApiItem(contentId, "12", "여행지" + contentId, address, "26", "350", "NA040500",
                35.1, 129.1, "", "", "Type1", modifiedTime);
    }

    private static DestinationInfo existing(String contentId, String modifiedTime) {
        DestinationInfo destination = new DestinationInfo();
        destination.setSource(DestinationInfo.SOURCE_TOUR_API);
        destination.setExternalId(contentId);
        destination.setSourceModifiedTime(modifiedTime);
        destination.setOverviewSyncedAt(LocalDateTime.of(2026, 1, 1, 0, 0));
        return destination;
    }
}
