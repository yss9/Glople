package proj.travien.service;

import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;
import proj.travien.domain.DestinationInfo;
import proj.travien.dto.TourSyncResult;
import proj.travien.dto.WikiCandidate;
import proj.travien.dto.WikiCountry;
import proj.travien.dto.WikiDestination;
import proj.travien.dto.WikiExtract;
import proj.travien.repository.DestinationInfoRepository;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.Collection;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.TreeMap;
import java.util.TreeSet;
import java.util.function.Consumer;

/**
 * 위키데이터·한국어 위키백과의 해외 여행지를 destination_info에 맞춰 넣는다.
 *
 * <p>목록 동기화는 두 단계다. (1) 후보 목록(wiki/candidate-items.txt)의 QID를 묶음마다 넘겨 지금도 조건(한국어 위키백과
 * 문서·좌표·국가)에 맞는지와 사이트링크 수·국가 코드를 확인하고, 유네스코 세계유산은 따로 전 세계를 한 번 묻는다.
 * (2) 국가마다 사이트링크 수가 많은 순으로 상위 N곳을 골라 상세 값을 받는다. 유형(P31)부터나 국가부터 훑는 쿼리는 60초 제한에
 * 걸려서(docs/overseas-wiki-sync.md의 '쿼리 설계') 모든 쿼리가 고른 QID만 VALUES로 넘긴다. 상세 값의 P31이 더는 유형 코드표에
 * 없고 세계유산도 아니면(후보 목록을 만든 뒤 바뀐 항목) 저장하지 않는다.
 *
 * <p>설명문은 한국어 위키백과 문서 도입부를 20개 문서씩 받는다. 텍스트 라이선스(CC BY-SA 4.0)에 따라
 * 문서 주소(source_url)와 라이선스(license)를 함께 저장한다. 자세한 내용은 docs/overseas-wiki-sync.md.
 */
@Slf4j
@Service
public class WikiDestinationSyncService {

    public static final String LICENSE = "CC BY-SA 4.0 (한국어 위키백과), CC0 (Wikidata)";
    static final int CATEGORY_MAX_LENGTH = 255;
    static final int SUMMARY_MAX_LENGTH = 500;
    // info는 TEXT(65,535바이트) 컬럼이라 한글 3바이트 기준으로 넉넉히 남도록 자른다.
    static final int INFO_MAX_LENGTH = 10_000;
    static final int SUMMARY_BATCH_SIZE = 1_000;
    private static final int MAX_CONSECUTIVE_FAILURES = 3;
    private static final String DEFAULT_TYPE_NAME = "여행지";
    // 유형 코드표에도 있는 '유네스코 세계유산'(P31 = Q9259). 세계유산 문장을 따로 붙일 때는 대표 유형으로 쓰지 않는다.
    static final String WORLD_HERITAGE_TYPE = "Q9259";

    private final DestinationInfoRepository destinationInfoRepository;
    private final WikimediaClient wikimediaClient;
    private final List<String> candidateItems;
    private final int candidateBatchSize;
    private final int descriptionRefreshDays;

    @Autowired
    public WikiDestinationSyncService(
            DestinationInfoRepository destinationInfoRepository,
            WikimediaClient wikimediaClient,
            // 후보 확인 쿼리 한 번에 넣을 QID 수. 쿼리가 시간 초과로 실패하면 묶음을 반으로 나눠 다시 보낸다.
            @Value("${wiki.sync.candidate-batch-size:2000}") int candidateBatchSize,
            // 설명문을 받은 지 이 일수가 지난 행은 판 번호를 다시 확인한다. 0이면 다시 확인하지 않는다.
            @Value("${wiki.sync.description-refresh-days:30}") int descriptionRefreshDays
    ) {
        // 후보 목록과 유형 코드표를 서버 시작 때 읽어, 형식이 틀렸으면 수집을 실행하기 전에 몇 번째 줄이 틀렸는지 알리며 멈춘다.
        this(destinationInfoRepository, wikimediaClient, WikiCandidateItems.qids(), candidateBatchSize, descriptionRefreshDays);
        log.info("Wiki type table loaded types={}", WikiTypeKeywords.qids().size());
    }

    // 테스트에서 후보 목록을 직접 정할 수 있도록 받는 생성자 (운영에서는 wiki/candidate-items.txt 전체)
    WikiDestinationSyncService(
            DestinationInfoRepository destinationInfoRepository,
            WikimediaClient wikimediaClient,
            List<String> candidateItems,
            int candidateBatchSize,
            int descriptionRefreshDays
    ) {
        this.destinationInfoRepository = destinationInfoRepository;
        this.wikimediaClient = wikimediaClient;
        this.candidateItems = List.copyOf(candidateItems);
        this.candidateBatchSize = Math.min(WikimediaClient.MAX_CANDIDATE_ITEMS, Math.max(1, candidateBatchSize));
        this.descriptionRefreshDays = Math.max(0, descriptionRefreshDays);
        log.info("Wiki candidate items loaded items={} batchSize={}", this.candidateItems.size(), this.candidateBatchSize);
    }

    /**
     * @param countryCodes    수집할 국가의 ISO 3166-1 alpha-2 코드. 비어 있으면 전체 국가
     * @param limitPerCountry 국가마다 저장할 최대 여행지 수 (사이트링크 수가 많은 순)
     * @return processed: 상세 값을 받은 여행지 수, failed: 실패한 요청 수(후보 조회 묶음·국가별 상세 조회)
     */
    public TourSyncResult syncDestinations(Collection<String> countryCodes, int limitPerCountry) {
        if (limitPerCountry < 1) {
            throw new IllegalArgumentException("국가별 최대 개수는 1 이상이어야 합니다: " + limitPerCountry);
        }

        Counter counter = new Counter();
        try {
            Map<String, WikiCountry> countries = new TreeMap<>();
            for (WikiCountry country : wikimediaClient.fetchCountries()) {
                countries.put(country.isoCode(), country);
            }
            Set<String> targets = targetCodes(countryCodes, countries.keySet());
            Map<String, DestinationInfo> existing = existingRows();
            Map<String, List<WikiCandidate>> byHome = groupByHomeCountry(collectCandidates(counter), countries.keySet(), targets, existing);

            for (String code : targets) {
                List<WikiCandidate> picked = top(byHome.getOrDefault(code, List.of()), limitPerCountry);
                if (picked.isEmpty()) {
                    continue;
                }
                syncCountry(countries.get(code), picked, existing, counter);
            }
        } catch (WikimediaClient.RateLimitedException e) {
            log.warn("Wiki destination sync stopped by rate limit: {}", e.getMessage());
            return counter.result(true);
        }
        return counter.result(false);
    }

    private void syncCountry(WikiCountry country, List<WikiCandidate> picked, Map<String, DestinationInfo> existing, Counter counter) {
        List<WikiDestination> details;
        try {
            details = wikimediaClient.fetchDestinations(picked.stream().map(WikiCandidate::qid).toList());
            counter.succeeded();
        } catch (WikimediaClient.RateLimitedException e) {
            throw e;
        } catch (RuntimeException e) {
            log.warn("Wiki destination details failed country={}: {}", country.isoCode(), e.getMessage());
            counter.failed(e);
            return;
        }

        Map<String, WikiDestination> byQid = new HashMap<>();
        details.forEach(destination -> byQid.put(destination.qid(), destination));

        List<DestinationInfo> changed = new ArrayList<>();
        // 후보 순서(사이트링크 수가 많은 순)대로 저장한다.
        for (WikiCandidate candidate : picked) {
            WikiDestination destination = byQid.get(candidate.qid());
            if (destination == null) {
                // 후보를 모은 뒤 한국어 문서 연결이 사라진 경우
                continue;
            }
            if (!destination.worldHeritage() && WikiTypeKeywords.categoryOf(destination.instanceOf(), CATEGORY_MAX_LENGTH) == null) {
                // 후보 목록을 만든 뒤 P31이 바뀌어 유형 코드표에 있는 유형이 없어졌고 세계유산도 아닌 경우
                log.info("Wiki destination skipped without listed type qid={} instanceOf={}", candidate.qid(), destination.instanceOf());
                continue;
            }
            counter.processed++;
            DestinationInfo row = existing.get(candidate.qid());
            if (row == null) {
                row = new DestinationInfo();
                row.setSource(DestinationInfo.SOURCE_WIKIDATA);
                row.setExternalId(candidate.qid());
                apply(row, country, destination);
                existing.put(candidate.qid(), row);
                changed.add(row);
                counter.inserted++;
            } else if (apply(row, country, destination)) {
                changed.add(row);
                counter.updated++;
            }
        }
        destinationInfoRepository.saveAll(changed);
        log.info("Wiki destination sync country={} picked={} saved={}", country.isoCode(), picked.size(), changed.size());
    }

    // 후보 목록을 묶음마다, 그리고 세계유산으로 후보를 모아 QID별로 합친다.
    private Map<String, WikiCandidate> collectCandidates(Counter counter) {
        Map<String, WikiCandidate> merged = new HashMap<>();
        for (int start = 0; start < candidateItems.size(); start += candidateBatchSize) {
            collectListedCandidates(candidateItems.subList(start, Math.min(candidateItems.size(), start + candidateBatchSize)),
                    merged, counter);
        }
        try {
            merge(merged, wikimediaClient.fetchHeritageCandidates());
            counter.succeeded();
        } catch (WikimediaClient.RateLimitedException e) {
            throw e;
        } catch (RuntimeException e) {
            log.warn("Wiki world heritage candidates failed: {}", e.getMessage());
            counter.failed(e);
        }
        log.info("Wiki destination candidates={}", merged.size());
        return merged;
    }

    private void collectListedCandidates(List<String> items, Map<String, WikiCandidate> merged, Counter counter) {
        String range = items.get(0) + ".." + items.get(items.size() - 1);
        try {
            merge(merged, wikimediaClient.fetchCandidates(items));
            counter.succeeded();
        } catch (WikimediaClient.RateLimitedException e) {
            throw e;
        } catch (WikimediaClient.RequestFailedException e) {
            if (e.isServerSide() && items.size() > 1) {
                // 시간 초과 등 서버 쪽 실패는 묶음을 반으로 나눠 다시 보낸다.
                log.warn("Wiki candidate query failed, splitting items={} range={}: {}", items.size(), range, e.getMessage());
                int half = items.size() / 2;
                collectListedCandidates(items.subList(0, half), merged, counter);
                collectListedCandidates(items.subList(half, items.size()), merged, counter);
                return;
            }
            log.warn("Wiki candidate query failed items={} range={}: {}", items.size(), range, e.getMessage());
            counter.failed(e);
        } catch (RuntimeException e) {
            log.warn("Wiki candidate query failed items={} range={}: {}", items.size(), range, e.getMessage());
            counter.failed(e);
        }
    }

    private static void merge(Map<String, WikiCandidate> merged, Map<String, WikiCandidate> found) {
        found.forEach((qid, candidate) -> merged.merge(qid, candidate, (a, b) -> {
            Set<String> codes = new TreeSet<>(a.countryCodes());
            codes.addAll(b.countryCodes());
            return new WikiCandidate(qid, Math.max(a.sitelinks(), b.sitelinks()), Set.copyOf(codes));
        }));
    }

    private static Set<String> targetCodes(Collection<String> requested, Set<String> known) {
        if (requested == null || requested.isEmpty()) {
            return new TreeSet<>(known);
        }
        Set<String> targets = new TreeSet<>();
        List<String> unknown = new ArrayList<>();
        for (String raw : requested) {
            String code = raw == null ? "" : raw.trim().toUpperCase();
            if (known.contains(code)) {
                targets.add(code);
            } else {
                unknown.add(code);
            }
        }
        if (!unknown.isEmpty()) {
            throw new IllegalArgumentException("수집 대상 국가 목록에 없는 국가 코드입니다: " + String.join(",", unknown));
        }
        return targets;
    }

    private Map<String, DestinationInfo> existingRows() {
        Map<String, DestinationInfo> existing = new HashMap<>();
        for (DestinationInfo destination : destinationInfoRepository.findAllBySource(DestinationInfo.SOURCE_WIKIDATA)) {
            existing.putIfAbsent(destination.getExternalId(), destination);
        }
        return existing;
    }

    // 여러 나라에 걸친 여행지(국경 산맥, 여러 나라가 함께 등재한 세계유산 등)는 한 나라에만 넣는다. 대한민국·북한이 걸린 여행지는 넣지 않는다.
    private static Map<String, List<WikiCandidate>> groupByHomeCountry(
            Map<String, WikiCandidate> candidates, Set<String> known, Set<String> targets, Map<String, DestinationInfo> existing) {
        Map<String, List<WikiCandidate>> byHome = new HashMap<>();
        for (WikiCandidate candidate : candidates.values()) {
            String home = homeCountry(candidate, known, existing.get(candidate.qid()));
            if (home != null && targets.contains(home)) {
                byHome.computeIfAbsent(home, key -> new ArrayList<>()).add(candidate);
            }
        }
        return byHome;
    }

    /**
     * 여행지를 넣을 나라. 이미 저장된 나라가 아직 그 여행지의 국가(P17) 중 하나면 그대로 두고, 아니면 ISO 코드 순으로 첫 나라.
     * 그래서 일부 국가만 골라 실행해도 여러 나라에 걸친 여행지가 나라를 오가지 않는다. 수집 대상 국가가 없으면 null.
     *
     * <p>국가 중에 대한민국·북한이 하나라도 있으면 null(수집하지 않음). 독도(Q20317)처럼 위키데이터에 국가가
     * '일본, 대한민국'으로 함께 적힌 항목이 일본 여행지로 들어가지 않게 한다. 국내 여행지는 TourAPI로 따로 수집한다.
     */
    static String homeCountry(WikiCandidate candidate, Set<String> known, DestinationInfo existing) {
        if (candidate.countryCodes().stream().anyMatch(WikimediaClient.EXCLUDED_COUNTRY_CODES::contains)) {
            return null;
        }
        TreeSet<String> codes = new TreeSet<>(candidate.countryCodes());
        codes.retainAll(known);
        if (codes.isEmpty()) {
            return null;
        }
        if (existing != null && existing.getCountryCode() != null && codes.contains(existing.getCountryCode())) {
            return existing.getCountryCode();
        }
        return codes.first();
    }

    // 사이트링크 수가 많은 순, 같으면 QID 번호가 작은 순
    static List<WikiCandidate> top(List<WikiCandidate> candidates, int limit) {
        return candidates.stream()
                .sorted(Comparator.comparingInt(WikiCandidate::sitelinks).reversed()
                        .thenComparingLong(candidate -> WikimediaClient.qidNumber(candidate.qid())))
                .limit(limit)
                .toList();
    }

    // 값을 채우고, 저장할 만큼 바뀌었는지 돌려준다. (바뀐 값이 없으면 저장하지 않는다)
    static boolean apply(DestinationInfo destination, WikiCountry country, WikiDestination wiki) {
        String location = locationOf(country.name(), country.isoCode(), wiki.admins());
        String category = WikiTypeKeywords.categoryOf(wiki.instanceOf(), CATEGORY_MAX_LENGTH);

        boolean changed = false;
        changed |= update(destination.getCountry(), country.name(), destination::setCountry);
        changed |= update(destination.getCountryCode(), country.isoCode(), destination::setCountryCode);
        changed |= update(destination.getLocation(), location, destination::setLocation);
        changed |= update(destination.getDestName(), wiki.name(), destination::setDestName);
        changed |= update(destination.getLatitude(), wiki.latitude(), destination::setLatitude);
        changed |= update(destination.getLongitude(), wiki.longitude(), destination::setLongitude);
        changed |= update(destination.getCategory(), category, destination::setCategory);
        changed |= update(destination.getWorldHeritage(), wiki.worldHeritage(), destination::setWorldHeritage);
        changed |= update(destination.getLicense(), LICENSE, destination::setLicense);
        changed |= update(destination.getSummary(), summaryOf(location, category, wiki.worldHeritage()), destination::setSummary);
        if (!Objects.equals(destination.getSourceUrl(), wiki.articleUrl())) {
            // 위키데이터가 다른 문서를 가리키게 되었으면 예전 설명문을 지우고 다음 설명문 수집 때 새 문서에서 받는다.
            // (설명문과 출처 주소가 어긋난 채 보이지 않도록 설명문을 남겨 두지 않는다)
            destination.setSourceUrl(wiki.articleUrl());
            destination.setInfo(null);
            destination.setSourceRevision(null);
            destination.setOverviewSyncedAt(null);
            destination.setOverviewFailedAt(null);
            changed = true;
        }

        // 유형 코드표만 바뀐 경우에도 저장되도록 마스크를 직접 계산해 비교한다.
        KeywordMask.Mask mask = destination.computeKeywordMask();
        return changed || mask.low() != destination.getKeywordMaskLow() || mask.high() != destination.getKeywordMaskHigh();
    }

    private static <T> boolean update(T current, T value, Consumer<T> setter) {
        if (Objects.equals(current, value)) {
            return false;
        }
        setter.accept(value);
        return true;
    }

    // "일본 나가사키시". 행정구역 이름이 나라 이름과 같으면(P131 = 나라) 나라 이름만.
    // 국경에 걸친 여행지(몽블랑산 P131 = 샤모니몽블랑, 쿠르마유르)는 고른 나라에 속한 행정구역을 쓴다. 국가 코드를 모르는 행정구역은 받아들인다.
    static String locationOf(String countryName, String countryCode, List<WikiDestination.Admin> admins) {
        if (admins != null) {
            for (WikiDestination.Admin admin : admins) {
                String label = admin.label() == null ? "" : admin.label().trim();
                boolean sameCountry = admin.countryCodes().isEmpty() || admin.countryCodes().contains(countryCode);
                if (!label.isEmpty() && !label.equals(countryName) && sameCountry) {
                    return countryName + " " + label;
                }
            }
        }
        return countryName;
    }

    /**
     * 위키데이터 값으로만 만든 짧은 소개. 국내 소개(TourDestinationSyncService.summaryOf)처럼 지어낸 문장 없이 사실만 담고,
     * 앞말에 따라 바뀌는 조사 없이 '의'·'입니다'만 쓴다. 예: "일본 나가사키시의 박물관입니다.",
     * "일본 아오모리현의 여행지입니다. 유네스코 세계유산입니다."
     *
     * <p>대표 유형은 category의 QID 중 한국어 유형명이 있고 유형 코드표에서 가장 앞에 있는 것이다. 세계유산이면 세계유산 문장을
     * 따로 붙이므로 '유네스코 세계유산'(Q9259) 유형은 대표 유형으로 고르지 않는다. 담을 사실이 하나도 없으면 null.
     */
    static String summaryOf(String location, String category, Boolean worldHeritage) {
        String place = location == null || location.isBlank() ? null : location.trim();
        boolean heritage = Boolean.TRUE.equals(worldHeritage);
        String typeName = WikiTypeKeywords.primaryType(category, heritage ? Set.of(WORLD_HERITAGE_TYPE) : Set.of())
                .map(WikiTypeKeywords.Type::koLabel)
                .orElse(null);
        if (place == null && typeName == null && !heritage) {
            return null;
        }

        StringBuilder summary = new StringBuilder();
        if (place != null) {
            summary.append(place).append("의 ");
        }
        summary.append(typeName != null ? typeName : DEFAULT_TYPE_NAME).append("입니다.");
        if (heritage) {
            summary.append(" 유네스코 세계유산입니다.");
        }
        return summary.length() <= SUMMARY_MAX_LENGTH ? summary.toString() : summary.substring(0, SUMMARY_MAX_LENGTH);
    }

    /**
     * 설명문이 없는 위키데이터 여행지(먼저)와, 설명문을 받은 지 오래된 여행지(그다음)의 한국어 위키백과 도입부를 최대 limit건 받는다.
     * 요청 한 번에 {@value WikimediaClient#EXTRACT_BATCH_SIZE}개 문서씩 묻는다. 다시 확인한 문서의 판 번호가 그대로면
     * 설명문을 다시 쓰지 않고 확인 시각만 바꾼다.
     *
     * @return processed: 처리한 여행지 수, updated: 설명문을 저장한 수, failed: 받지 못한 수
     */
    public TourSyncResult syncDescriptions(int limit) {
        LocalDateTime now = LocalDateTime.now();
        List<DestinationInfo> targets = new ArrayList<>(
                destinationInfoRepository.findBySourceAndOverviewSyncedAtIsNullOrderByOverviewFailedAtAscIdAsc(
                        DestinationInfo.SOURCE_WIKIDATA, PageRequest.of(0, limit)));
        if (targets.size() < limit && descriptionRefreshDays > 0) {
            targets.addAll(destinationInfoRepository.findBySourceAndOverviewSyncedAtBeforeOrderByOverviewFailedAtAscOverviewSyncedAtAscIdAsc(
                    DestinationInfo.SOURCE_WIKIDATA, now.minusDays(descriptionRefreshDays), PageRequest.of(0, limit - targets.size())));
        }

        int processed = 0;
        int updated = 0;
        int failed = 0;
        int consecutiveFailures = 0;
        for (int start = 0; start < targets.size(); start += WikimediaClient.EXTRACT_BATCH_SIZE) {
            List<DestinationInfo> batch = targets.subList(start, Math.min(targets.size(), start + WikimediaClient.EXTRACT_BATCH_SIZE));
            Map<String, List<DestinationInfo>> byTitle = new LinkedHashMap<>();
            for (DestinationInfo destination : batch) {
                String title = WikimediaClient.titleFromArticleUrl(destination.getSourceUrl());
                if (title == null) {
                    log.warn("Wiki description skipped without ko.wikipedia url qid={}", destination.getExternalId());
                    processed++;
                    failed++;
                    markOverviewFailed(destination);
                } else {
                    byTitle.computeIfAbsent(title, key -> new ArrayList<>()).add(destination);
                }
            }
            if (byTitle.isEmpty()) {
                continue;
            }

            Map<String, WikiExtract> extracts;
            try {
                extracts = wikimediaClient.fetchExtracts(new ArrayList<>(byTitle.keySet()));
                consecutiveFailures = 0;
            } catch (WikimediaClient.RateLimitedException e) {
                log.warn("Wiki description sync stopped by rate limit after {} items: {}", processed, e.getMessage());
                return new TourSyncResult(processed, 0, updated, failed, true);
            } catch (RuntimeException e) {
                consecutiveFailures++;
                log.warn("Wiki description batch failed titles={}: {}", byTitle.keySet(), e.getMessage());
                for (List<DestinationInfo> rows : byTitle.values()) {
                    for (DestinationInfo destination : rows) {
                        processed++;
                        failed++;
                        markOverviewFailed(destination);
                    }
                }
                if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
                    throw new IllegalStateException("설명문 수집이 연속 " + consecutiveFailures + "회 실패해 중단했습니다: "
                            + e.getMessage(), e);
                }
                continue;
            }

            List<DestinationInfo> changed = new ArrayList<>();
            List<Long> unchanged = new ArrayList<>();
            for (Map.Entry<String, List<DestinationInfo>> entry : byTitle.entrySet()) {
                WikiExtract extract = extracts.get(entry.getKey());
                for (DestinationInfo destination : entry.getValue()) {
                    processed++;
                    if (extract == null || (!extract.missing() && extract.extract() == null)) {
                        // 응답에 문서가 없거나 도입부가 빠져 온 경우. 다음 실행에서 아직 시도하지 않은 행부터 받는다.
                        failed++;
                        markOverviewFailed(destination);
                    } else if (!extract.missing() && destination.getOverviewSyncedAt() != null
                            && extract.revisionId() != null && extract.revisionId().equals(destination.getSourceRevision())) {
                        // 다시 확인했는데 판이 그대로면 행을 다시 쓰지 않는다.
                        unchanged.add(destination.getId());
                    } else {
                        String text = extract.missing() ? "" : cleanExtract(extract.extract());
                        destination.setInfo(text.isEmpty() ? null : text);
                        destination.setSourceRevision(extract.missing() ? null : extract.revisionId());
                        destination.setOverviewSyncedAt(now);
                        destination.setOverviewFailedAt(null);
                        changed.add(destination);
                        updated++;
                    }
                }
            }
            destinationInfoRepository.saveAll(changed);
            if (!unchanged.isEmpty()) {
                destinationInfoRepository.markOverviewSynced(unchanged, now);
            }
            log.info("Wiki description sync batch processed={} updated={} failed={}", processed, updated, failed);
        }
        return new TourSyncResult(processed, 0, updated, failed, false);
    }

    /**
     * 위키데이터 여행지 전체의 소개(summary)와 키워드 마스크를 저장된 값과 유형 코드표로 다시 만든다. 위키미디어는 부르지 않는다.
     * 유형 코드표(wiki/type-keywords.csv)를 고친 뒤 한 번 실행한다.
     *
     * @return processed: 읽은 행 수, updated: 소개나 마스크가 바뀌어 저장한 행 수
     */
    public TourSyncResult rebuildSummaries() {
        int processed = 0;
        int updated = 0;
        long lastId = 0L;
        while (true) {
            List<DestinationInfo> page = destinationInfoRepository.findBySourceAndIdGreaterThanOrderByIdAsc(
                    DestinationInfo.SOURCE_WIKIDATA, lastId, PageRequest.of(0, SUMMARY_BATCH_SIZE));
            if (page.isEmpty()) {
                break;
            }

            List<DestinationInfo> changed = new ArrayList<>();
            for (DestinationInfo destination : page) {
                processed++;
                String summary = summaryOf(destination.getLocation(), destination.getCategory(), destination.getWorldHeritage());
                boolean summaryChanged = !Objects.equals(destination.getSummary(), summary);
                destination.setSummary(summary);
                KeywordMask.Mask mask = destination.computeKeywordMask();
                if (summaryChanged || mask.low() != destination.getKeywordMaskLow() || mask.high() != destination.getKeywordMaskHigh()) {
                    destination.refreshKeywordMask();
                    changed.add(destination);
                }
            }
            destinationInfoRepository.saveAll(changed);
            updated += changed.size();
            lastId = page.get(page.size() - 1).getId();

            log.info("Wiki summary rebuild processed={} updated={} lastId={}", processed, updated, lastId);
            if (page.size() < SUMMARY_BATCH_SIZE) {
                break;
            }
        }
        return new TourSyncResult(processed, 0, updated, 0, false);
    }

    /**
     * explaintext 도입부를 한 줄 평문으로 정리한다. 한국어 문서가 없는 언어 간 링크 표시 "[*]", 폭 없는 공백,
     * 틀이 빠지며 남은 빈 괄호를 지우고 공백을 하나로 줄인다. 그 밖의 글은 바꾸지 않는다.
     */
    static String cleanExtract(String extract) {
        if (extract == null) {
            return "";
        }
        String text = extract
                .replace("[*]", "")
                .replaceAll("[\\u200B\\uFEFF]", "")
                .replaceAll("\\(\\s*[,;]?\\s*\\)", "")
                .replaceAll("\\s+", " ")
                .trim();
        if (text.length() <= INFO_MAX_LENGTH) {
            return text;
        }
        int end = Character.isHighSurrogate(text.charAt(INFO_MAX_LENGTH - 1)) ? INFO_MAX_LENGTH - 1 : INFO_MAX_LENGTH;
        return text.substring(0, end);
    }

    private void markOverviewFailed(DestinationInfo destination) {
        LocalDateTime failedAt = LocalDateTime.now();
        try {
            destinationInfoRepository.markOverviewFailed(destination.getId(), failedAt);
            destination.setOverviewFailedAt(failedAt);
        } catch (RuntimeException e) {
            log.warn("Could not record wiki description failure qid={}: {}", destination.getExternalId(), e.getMessage());
        }
    }

    // 목록 동기화 집계. 요청이 연속으로 실패하면(서버 장애 등) 남은 요청을 보내지 않고 멈춘다.
    private static final class Counter {
        private int processed;
        private int inserted;
        private int updated;
        private int failed;
        private int consecutiveFailures;

        private void succeeded() {
            consecutiveFailures = 0;
        }

        private void failed(RuntimeException cause) {
            failed++;
            consecutiveFailures++;
            if (consecutiveFailures >= MAX_CONSECUTIVE_FAILURES) {
                throw new IllegalStateException("위키데이터 요청이 연속 " + consecutiveFailures + "회 실패해 중단했습니다: "
                        + cause.getMessage(), cause);
            }
        }

        private TourSyncResult result(boolean stoppedByRateLimit) {
            return new TourSyncResult(processed, inserted, updated, failed, stoppedByRateLimit);
        }
    }
}
