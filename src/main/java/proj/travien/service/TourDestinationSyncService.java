package proj.travien.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import org.springframework.web.util.HtmlUtils;
import proj.travien.domain.DestinationInfo;
import proj.travien.dto.TourApiItem;
import proj.travien.dto.TourApiPage;
import proj.travien.dto.TourSyncResult;
import proj.travien.repository.DestinationInfoRepository;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;

/**
 * TourAPI 국내 여행지를 destination_info에 맞춰 넣는다.
 *
 * <p>목록 동기화(areaBasedList2)는 호출 수가 적어 한 번에 끝나지만, 설명문은 여행지마다
 * detailCommon2를 한 번씩 불러야 해서 일일 호출 한도 안에서 나눠 받는다.
 * 설명문을 받기 전에도 추천에 쓸 수 있도록, 목록 동기화 때 지역·관광 타입·분류로 짧은 소개(summary)를 만든다.
 */
@Slf4j
@Service
public class TourDestinationSyncService {

    static final String COUNTRY = "대한민국";
    private static final String ARRANGE_BY_TITLE = "A";
    private static final int MAX_CONSECUTIVE_FAILURES = 5;
    static final int SUMMARY_BATCH_SIZE = 1_000;
    static final int SUMMARY_MAX_LENGTH = 500;

    // TourAPI contentTypeId별 관광 타입 이름 (수집하는 타입만. 나머지는 '여행지')
    private static final Map<String, String> CONTENT_TYPE_NAMES = Map.of(
            "12", "관광지",
            "14", "문화시설",
            "15", "축제공연행사",
            "28", "레포츠"
    );
    private static final String DEFAULT_CONTENT_TYPE_NAME = "여행지";

    private final DestinationInfoRepository destinationInfoRepository;
    private final TourApiClient tourApiClient;
    private final List<String> contentTypeIds;
    private final int numOfRows;

    public TourDestinationSyncService(
            DestinationInfoRepository destinationInfoRepository,
            TourApiClient tourApiClient,
            // 12 관광지, 14 문화시설, 15 축제공연행사, 28 레포츠 (32 숙박, 39 음식점 등은 필요할 때 추가)
            @Value("${tour.sync.content-type-ids:12,14,15,28}") String[] contentTypeIds,
            @Value("${tour.sync.num-of-rows:1000}") int numOfRows
    ) {
        this.destinationInfoRepository = destinationInfoRepository;
        this.tourApiClient = tourApiClient;
        this.contentTypeIds = List.of(contentTypeIds);
        this.numOfRows = numOfRows;
    }

    public TourSyncResult syncDestinations() {
        Map<String, DestinationInfo> existing = new HashMap<>();
        for (DestinationInfo destination : destinationInfoRepository.findAllBySource(DestinationInfo.SOURCE_TOUR_API)) {
            existing.putIfAbsent(destination.getExternalId(), destination);
        }

        int processed = 0;
        int inserted = 0;
        int updated = 0;
        try {
            for (String contentTypeId : contentTypeIds) {
                int pageNo = 1;
                int lastPage = 1;
                while (pageNo <= lastPage) {
                    TourApiPage page = tourApiClient.fetchAreaBasedPage(contentTypeId, pageNo, numOfRows, ARRANGE_BY_TITLE);
                    lastPage = (page.totalCount() + numOfRows - 1) / numOfRows;

                    List<DestinationInfo> changed = new ArrayList<>();
                    for (TourApiItem item : page.items()) {
                        processed++;
                        DestinationInfo destination = existing.get(item.contentId());
                        if (destination == null) {
                            destination = new DestinationInfo();
                            destination.setSource(DestinationInfo.SOURCE_TOUR_API);
                            destination.setCountry(COUNTRY);
                            apply(destination, item);
                            existing.put(item.contentId(), destination);
                            changed.add(destination);
                            inserted++;
                        } else if (!Objects.equals(destination.getSourceModifiedTime(), emptyToNull(item.modifiedTime()))) {
                            // 저장할 때와 같은 방식(빈 값 → null)으로 비교해야 modifiedtime이 없는 item이 매번 갱신되지 않는다.
                            apply(destination, item);
                            // 원본이 바뀌었으니 설명문도 다시 받는다.
                            destination.setOverviewSyncedAt(null);
                            destination.setOverviewFailedAt(null);
                            changed.add(destination);
                            updated++;
                        }
                    }
                    destinationInfoRepository.saveAll(changed);

                    log.info("Tour destination sync contentTypeId={} page={}/{} saved={}",
                            contentTypeId, pageNo, lastPage, changed.size());
                    pageNo++;
                }
            }
        } catch (TourApiClient.QuotaExceededException e) {
            log.warn("Tour destination sync stopped by quota: {}", e.getMessage());
            return new TourSyncResult(processed, inserted, updated, 0, true);
        }
        return new TourSyncResult(processed, inserted, updated, 0, false);
    }

    public TourSyncResult syncOverviews(int limit) {
        List<DestinationInfo> targets = destinationInfoRepository.findBySourceAndOverviewSyncedAtIsNullOrderByOverviewFailedAtAscIdAsc(
                DestinationInfo.SOURCE_TOUR_API, PageRequest.of(0, limit));

        int processed = 0;
        int updated = 0;
        int failed = 0;
        int consecutiveFailures = 0;
        for (DestinationInfo destination : targets) {
            try {
                String overview = cleanOverview(tourApiClient.fetchOverview(destination.getExternalId()));
                destination.setInfo(overview.isEmpty() ? null : overview);
                destination.setOverviewSyncedAt(LocalDateTime.now());
                destination.setOverviewFailedAt(null);
                destinationInfoRepository.save(destination);
                updated++;
                consecutiveFailures = 0;
            } catch (TourApiClient.QuotaExceededException e) {
                log.warn("Tour overview sync stopped by quota after {} items: {}", processed, e.getMessage());
                return new TourSyncResult(processed, 0, updated, failed, true);
            } catch (RuntimeException e) {
                failed++;
                consecutiveFailures++;
                log.warn("Tour overview sync failed contentId={}: {}", destination.getExternalId(), e.getMessage());
                // 실패 시각을 남겨 다음 실행에서는 아직 시도하지 않은 여행지부터 받게 한다.
                // (기록하지 않으면 계속 실패하는 행이 id 순 대기열 맨 앞에 쌓여 매번 '연속 실패'로 멈춘다)
                markOverviewFailed(destination);
                // 키 오류처럼 매번 실패하는 상황이면 남은 건을 계속 부르지 않는다.
                if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
                    throw new IllegalStateException("설명문 수집이 연속 " + consecutiveFailures + "회 실패해 중단했습니다: "
                            + e.getMessage(), e);
                }
            }
            processed++;
        }
        return new TourSyncResult(processed, 0, updated, failed, false);
    }

    /**
     * 국내 여행지 전체의 소개(summary)와 키워드 마스크를 저장된 값과 분류 코드표로 다시 만든다. TourAPI는 부르지 않는다.
     * 분류 코드표(tour/category-keywords.csv)를 고쳤거나 summary 컬럼을 새로 만든 뒤 한 번 실행한다.
     *
     * @return processed: 읽은 행 수, updated: 소개나 마스크가 바뀌어 저장한 행 수
     */
    public TourSyncResult rebuildSummaries() {
        int processed = 0;
        int updated = 0;
        long lastId = 0L;
        while (true) {
            List<DestinationInfo> page = destinationInfoRepository.findBySourceAndIdGreaterThanOrderByIdAsc(
                    DestinationInfo.SOURCE_TOUR_API, lastId, PageRequest.of(0, SUMMARY_BATCH_SIZE));
            if (page.isEmpty()) {
                break;
            }

            List<DestinationInfo> changed = new ArrayList<>();
            for (DestinationInfo destination : page) {
                processed++;
                if (refreshSummaryAndMask(destination)) {
                    changed.add(destination);
                }
            }
            destinationInfoRepository.saveAll(changed);
            updated += changed.size();
            lastId = page.get(page.size() - 1).getId();

            log.info("Tour summary rebuild processed={} updated={} lastId={}", processed, updated, lastId);
            if (page.size() < SUMMARY_BATCH_SIZE) {
                break;
            }
        }
        return new TourSyncResult(processed, 0, updated, 0, false);
    }

    // 소개와 마스크를 다시 계산하고, 둘 중 하나라도 바뀌었는지 돌려준다.
    // (분류 코드표만 바뀐 경우에도 저장되도록 마스크를 직접 계산해 비교한다)
    private static boolean refreshSummaryAndMask(DestinationInfo destination) {
        String summary = summaryOf(destination.getLocation(), destination.getContentTypeId(), destination.getCategory());
        boolean summaryChanged = !Objects.equals(destination.getSummary(), summary);
        long oldLow = destination.getKeywordMaskLow();
        long oldHigh = destination.getKeywordMaskHigh();

        destination.setSummary(summary);
        destination.refreshKeywordMask();
        return summaryChanged
                || oldLow != destination.getKeywordMaskLow()
                || oldHigh != destination.getKeywordMaskHigh();
    }

    /**
     * TourAPI 필드로만 만든 짧은 소개. 지어낸 문장 없이 사실만 담고, 조사가 앞말에 따라 바뀌지 않도록
     * '의'·'입니다'만 쓴다. 예: "부산광역시 해운대구의 관광지입니다. 분류: 자연관광 > 자연경관(하천‧해양) > 해변. 해수욕장"
     *
     * <p>지역이 없으면 지역 부분을, 코드표에 없는 분류면 '분류:' 부분을 뺀다. 담을 사실이 하나도 없으면 null.
     */
    static String summaryOf(String location, String contentTypeId, String category) {
        String place = emptyToNull(location);
        String typeName = contentTypeId == null ? null : CONTENT_TYPE_NAMES.get(contentTypeId.trim());
        Optional<TourCategories.Category> found = TourCategories.find(category);
        if (place == null && typeName == null && found.isEmpty()) {
            return null;
        }

        StringBuilder summary = new StringBuilder();
        if (place != null) {
            summary.append(place.trim()).append("의 ");
        }
        summary.append(typeName != null ? typeName : DEFAULT_CONTENT_TYPE_NAME).append("입니다.");
        found.ifPresent(c -> summary.append(" 분류: ")
                .append(c.depth1()).append(" > ").append(c.depth2()).append(" > ").append(c.depth3()));
        return summary.length() <= SUMMARY_MAX_LENGTH ? summary.toString() : summary.substring(0, SUMMARY_MAX_LENGTH);
    }

    private void markOverviewFailed(DestinationInfo destination) {
        LocalDateTime failedAt = LocalDateTime.now();
        try {
            destinationInfoRepository.markOverviewFailed(destination.getId(), failedAt);
            destination.setOverviewFailedAt(failedAt);
        } catch (RuntimeException e) {
            log.warn("Could not record tour overview failure contentId={}: {}", destination.getExternalId(), e.getMessage());
        }
    }

    private void apply(DestinationInfo destination, TourApiItem item) {
        destination.setExternalId(item.contentId());
        destination.setDestName(item.title());
        destination.setLocation(regionOf(item.addr1()));
        destination.setAddress(emptyToNull(item.addr1()));
        destination.setContentTypeId(item.contentTypeId());
        destination.setRegionCode(emptyToNull(item.regionCode()));
        destination.setSigunguCode(emptyToNull(item.sigunguCode()));
        destination.setCategory(emptyToNull(item.category()));
        destination.setLatitude(item.latitude());
        destination.setLongitude(item.longitude());
        destination.setImageUrl(emptyToNull(item.firstImage()));
        destination.setTel(emptyToNull(item.tel()));
        destination.setCopyrightType(emptyToNull(item.copyrightType()));
        destination.setSourceModifiedTime(emptyToNull(item.modifiedTime()));
        destination.setSummary(summaryOf(destination.getLocation(), destination.getContentTypeId(), destination.getCategory()));
    }

    // "부산광역시 해운대구 우동 ..." → "부산광역시 해운대구", "세종특별자치시 가람동 ..." → "세종특별자치시"
    static String regionOf(String address) {
        if (address == null || address.isBlank()) {
            return null;
        }

        String[] tokens = address.trim().split("\\s+");
        if (tokens.length >= 2 && tokens[1].matches(".+[시군구]")) {
            return tokens[0] + " " + tokens[1];
        }
        return tokens[0];
    }

    // overview에는 <br>, &nbsp; 같은 HTML이 섞여 온다.
    static String cleanOverview(String overview) {
        if (overview == null) {
            return "";
        }

        // 줄을 나누는 태그는 공백으로 바꾸고, <b>·<strong> 같은 글자 꾸밈 태그는 그냥 지운다.
        // (공백으로 바꾸면 "<b>해변</b>으로"가 "해변 으로"처럼 단어가 갈라진다)
        String text = overview
                .replaceAll("(?i)<br\\s*/?>", " ")
                .replaceAll("(?i)</?(p|div|li|ul|ol|table|tr|td|th|h[1-6])\\b[^>]*>", " ")
                .replaceAll("<[^>]+>", "");
        return HtmlUtils.htmlUnescape(text).replace(' ', ' ').replaceAll("\\s+", " ").trim();
    }

    private static String emptyToNull(String value) {
        return value == null || value.isBlank() ? null : value;
    }
}
