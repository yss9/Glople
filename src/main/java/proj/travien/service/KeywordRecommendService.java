package proj.travien.service;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import proj.travien.domain.DestinationInfo;
import proj.travien.dto.KeywordCandidate;
import proj.travien.dto.KeywordRequest;
import proj.travien.dto.KeywordResultDTO;
import proj.travien.repository.DestinationInfoRepository;

import java.time.Clock;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;

/**
 * 키워드 여행지 추천 (POST /api/search).
 *
 * <ol>
 *   <li>고른 키워드를 마스크로 바꾼다. 아는 키워드가 하나도 없으면 쿼리 없이 빈 목록.</li>
 *   <li>여행지 마스크: 설정 {@value KeywordMaskMode#PROPERTY}(기본 union)에 따라 규칙 마스크 ∪ 태거 예측, 태거 예측만, 규칙만
 *       ({@link KeywordMaskMode}). 합치는 것은 후보 쿼리가 한다.</li>
 *   <li>후보 쿼리: 일치 키워드 수 내림차순, 동점이면 {@link DestinationOrdering} 순(설명문 → 이미지 → CRC32(id:seed) → id)으로
 *       limit의 {@value #POOL_FACTOR}배(최대 {@value #MAX_POOL}개)를 읽는다. 순서는 Java에서 같은 규칙({@link #rankOrder})으로 다시 고정한다.</li>
 *   <li>지역 개수 상한: 한 지역(국내 시도, 해외 나라)은 최대 {@link #regionCap}개. 다른 지역 후보가 모자라 자리가 남으면
 *       상한에 걸려 빠진 후보로 순위대로 채운다. 결과는 원래 순위 순서다.</li>
 *   <li>고른 행만 전체 값을 읽어 응답 모양(KeywordResultDTO)으로 바꾼다.</li>
 * </ol>
 */
@Service
public class KeywordRecommendService {

    public static final int DEFAULT_LIMIT = 21;
    public static final int MAX_LIMIT = 50;
    static final int POOL_FACTOR = 4;
    static final int MAX_POOL = 200;

    public static final String SCOPE_ALL = "all";
    public static final String SCOPE_DOMESTIC = "domestic";
    public static final String SCOPE_OVERSEAS = "overseas";
    private static final Set<String> SCOPES = Set.of(SCOPE_ALL, SCOPE_DOMESTIC, SCOPE_OVERSEAS);

    private final DestinationInfoRepository repository;
    private final Clock clock;
    private final KeywordMaskMode maskMode;

    @Autowired
    public KeywordRecommendService(
            DestinationInfoRepository repository,
            @Value("${" + KeywordMaskMode.PROPERTY + ":" + KeywordMaskMode.DEFAULT_VALUE + "}") String maskMode
    ) {
        this(repository, Clock.system(DestinationOrdering.SEED_ZONE), KeywordMaskMode.parse(maskMode));
    }

    KeywordRecommendService(DestinationInfoRepository repository, Clock clock) {
        this(repository, clock, KeywordMaskMode.parse(KeywordMaskMode.DEFAULT_VALUE));
    }

    KeywordRecommendService(DestinationInfoRepository repository, Clock clock, KeywordMaskMode maskMode) {
        this.repository = repository;
        this.clock = clock;
        this.maskMode = maskMode;
    }

    // 예전 호출(키워드만)과 같은 결과 모양: scope=all, limit=21, 오늘 seed
    public List<KeywordResultDTO> searchByKeywords(List<String> keywords) {
        KeywordRequest request = new KeywordRequest();
        request.setKeywords(keywords);
        return search(request);
    }

    public List<KeywordResultDTO> search(KeywordRequest request) {
        Options options = Options.of(request, clock);
        KeywordMask.Mask mask = KeywordMask.fromKeywords(request.getKeywords());
        if (mask.isEmpty()) {
            return List.of();
        }

        int poolSize = Math.min(options.limit() * POOL_FACTOR, MAX_POOL);
        List<KeywordCandidate> ranked = new ArrayList<>(repository.findKeywordCandidates(
                mask.low(), mask.high(), maskMode.sqlValue(), options.scope(), options.seed(), poolSize));
        ranked.sort(rankOrder(options.seed()));

        List<Long> ids = pickWithRegionCap(ranked, options.limit()).stream().map(KeywordCandidate::getId).toList();
        if (ids.isEmpty()) {
            return List.of();
        }

        Map<Long, DestinationInfo> rows = new HashMap<>();
        for (DestinationInfo row : repository.findAllById(ids)) {
            rows.put(row.getId(), row);
        }
        return ids.stream()
                .map(rows::get)
                .filter(Objects::nonNull)
                .map(KeywordRecommendService::toDto)
                .toList();
    }

    /**
     * 후보 쿼리의 ORDER BY와 같은 순서: 일치 수↓, 설명문 있음↓, 이미지 있음↓, CRC32(id:seed)↑, id↑.
     * 쿼리가 이미 이 순서로 주지만, 순위를 정하는 규칙을 한 곳(Java)에서 시험할 수 있게 다시 정렬한다.
     */
    static Comparator<KeywordCandidate> rankOrder(long seed) {
        return Comparator.comparingLong((KeywordCandidate c) -> value(c.getScore())).reversed()
                .thenComparing(Comparator.comparingLong((KeywordCandidate c) -> value(c.getHasInfo())).reversed())
                .thenComparing(Comparator.comparingLong((KeywordCandidate c) -> value(c.getHasImage())).reversed())
                .thenComparingLong(c -> DestinationOrdering.tieHash(c.getId(), seed))
                .thenComparingLong(KeywordCandidate::getId);
    }

    // 한 지역에 줄 수 있는 최대 자리: max(2, ⌈limit / 4⌉). limit 21 → 6
    static int regionCap(int limit) {
        return Math.max(2, (limit + 3) / 4);
    }

    // 순위대로 고르되 한 지역은 regionCap개까지. 자리가 남으면 상한에 걸린 후보로 순위대로 채운다. 결과는 원래 순위 순서.
    static List<KeywordCandidate> pickWithRegionCap(List<KeywordCandidate> ranked, int limit) {
        int cap = regionCap(limit);
        boolean[] chosen = new boolean[ranked.size()];
        int count = 0;
        Map<String, Integer> perRegion = new HashMap<>();
        List<Integer> overCap = new ArrayList<>();

        for (int i = 0; i < ranked.size() && count < limit; i++) {
            KeywordCandidate candidate = ranked.get(i);
            String region = DestinationOrdering.regionKey(candidate.getSource(), candidate.getCountry(), candidate.getLocation());
            int used = perRegion.getOrDefault(region, 0);
            if (used < cap) {
                perRegion.put(region, used + 1);
                chosen[i] = true;
                count++;
            } else {
                overCap.add(i);
            }
        }
        for (int i = 0; i < overCap.size() && count < limit; i++) {
            chosen[overCap.get(i)] = true;
            count++;
        }

        List<KeywordCandidate> picked = new ArrayList<>(count);
        for (int i = 0; i < ranked.size(); i++) {
            if (chosen[i]) {
                picked.add(ranked.get(i));
            }
        }
        return picked;
    }

    static KeywordResultDTO toDto(DestinationInfo destination) {
        KeywordResultDTO dto = new KeywordResultDTO();
        dto.setId(destination.getId());
        dto.setDestName(destination.getDestName());
        dto.setCountry(destination.getCountry());
        dto.setLocation(destination.getLocation());
        // 예전 쿼리의 COALESCE(d.info, d.summary)와 같다
        dto.setInfo(destination.getInfo() != null ? destination.getInfo() : destination.getSummary());
        dto.setInfoKind(KeywordResultDTO.infoKindOf(destination.getInfo(), destination.getSummary()));
        dto.setSource(destination.getSource());
        dto.setCopyrightType(destination.getCopyrightType());
        dto.setSourceUrl(destination.getSourceUrl());
        dto.setLicense(destination.getLicense());
        dto.setImageUrl(destination.getImageUrl());
        dto.setCountryCode(destination.getCountryCode());
        dto.setContentTypeId(destination.getContentTypeId());
        return dto;
    }

    private static long value(Long number) {
        return number == null ? 0L : number;
    }

    // 요청 옵션 검사. 틀린 값은 고쳐 쓰지 않고 400으로 알린다.
    record Options(String scope, int limit, long seed) {

        static Options of(KeywordRequest request, Clock clock) {
            List<InvalidRequestException.Problem> problems = new ArrayList<>();

            String scope = request.getScope() == null ? SCOPE_ALL : request.getScope().trim().toLowerCase(Locale.ROOT);
            if (!SCOPES.contains(scope)) {
                problems.add(new InvalidRequestException.Problem("scope", "unknown_value",
                        "scope는 all, domestic, overseas 중 하나여야 합니다."));
            }

            int limit = request.getLimit() == null ? DEFAULT_LIMIT : request.getLimit();
            if (limit < 1 || limit > MAX_LIMIT) {
                problems.add(new InvalidRequestException.Problem("limit", "out_of_range",
                        "limit는 1~" + MAX_LIMIT + " 사이여야 합니다."));
            }

            if (!problems.isEmpty()) {
                throw new InvalidRequestException("추천 요청 값이 올바르지 않습니다.", problems);
            }
            long seed = request.getSeed() == null ? DestinationOrdering.defaultSeed(clock) : request.getSeed();
            return new Options(scope, limit, seed);
        }
    }
}
