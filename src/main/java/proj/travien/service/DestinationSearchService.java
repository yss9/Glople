package proj.travien.service;

import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import proj.travien.domain.DestinationInfo;
import proj.travien.dto.DestinationDetailDTO;
import proj.travien.dto.DestinationItemDTO;
import proj.travien.dto.KeywordResultDTO;
import proj.travien.repository.DestinationInfoRepository;

import java.time.Clock;
import java.util.ArrayList;
import java.util.List;
import java.util.Optional;

/**
 * 여행지 읽기 API (GET /api/destinations, /lookup, /{id}). 모두 공개이고 읽기만 한다.
 *
 * <ul>
 *   <li>목록: region(RegionPatterns), category(DestinationCategory), query(이름·소개 LIKE), limit 1~{@value #MAX_LIMIT}(기본
 *       {@value #DEFAULT_LIMIT}), seed(동점 정렬, 기본 오늘 날짜). 조건은 모두 선택이다.</li>
 *   <li>이름 찾기: 이름이 같은 곳, 이름 뒤에 괄호 구분이 붙은 곳, 공백을 뺀 이름이 같은 곳. 최대 {@value #LOOKUP_LIMIT}곳.
 *       여러 곳이면 호출하는 쪽이 location으로 되묻는다.</li>
 *   <li>상세: id 하나. 없으면 빈 값(컨트롤러가 404).</li>
 * </ul>
 * 틀린 값은 고쳐 쓰지 않고 {@link InvalidRequestException}(400)으로 알린다.
 */
@Service
public class DestinationSearchService {

    public static final int DEFAULT_LIMIT = 20;
    public static final int MAX_LIMIT = 50;
    public static final int LOOKUP_LIMIT = 10;
    public static final int MAX_QUERY_LENGTH = 50;
    public static final int MAX_NAME_LENGTH = 100;

    private final DestinationInfoRepository repository;
    private final Clock clock;

    @Autowired
    public DestinationSearchService(DestinationInfoRepository repository) {
        this(repository, Clock.system(DestinationOrdering.SEED_ZONE));
    }

    DestinationSearchService(DestinationInfoRepository repository, Clock clock) {
        this.repository = repository;
        this.clock = clock;
    }

    @Transactional(readOnly = true)
    public List<DestinationItemDTO> search(String region, String category, String query, Integer limit, Long seed) {
        List<InvalidRequestException.Problem> problems = new ArrayList<>();

        List<String> regionPatterns = List.of();
        try {
            regionPatterns = RegionPatterns.patterns(region);
        } catch (InvalidRequestException e) {
            problems.addAll(e.getProblems());
        }

        DestinationCategory chosen = null;
        if (category != null && !category.isBlank()) {
            chosen = DestinationCategory.fromLabel(category).orElse(null);
            if (chosen == null) {
                problems.add(new InvalidRequestException.Problem("category", "unknown_value",
                        "category는 " + String.join(", ", DestinationCategory.labels()) + " 중 하나여야 합니다."));
            }
        }

        String text = query == null ? "" : query.trim();
        if (text.codePointCount(0, text.length()) > MAX_QUERY_LENGTH) {
            problems.add(new InvalidRequestException.Problem("query", "too_long",
                    "query는 " + MAX_QUERY_LENGTH + "자까지 쓸 수 있습니다."));
        }

        int size = limit == null ? DEFAULT_LIMIT : limit;
        if (size < 1 || size > MAX_LIMIT) {
            problems.add(new InvalidRequestException.Problem("limit", "out_of_range",
                    "limit는 1~" + MAX_LIMIT + " 사이여야 합니다."));
        }

        if (!problems.isEmpty()) {
            throw new InvalidRequestException("여행지 조회 값이 올바르지 않습니다.", problems);
        }

        long tieSeed = seed == null ? DestinationOrdering.defaultSeed(clock) : seed;
        KeywordMask.Mask categoryMask = chosen == null ? KeywordMask.fromKeywords(List.of()) : chosen.overseasMask();
        List<DestinationInfo> rows = repository.searchDestinations(
                regionPatterns.size(),
                patternAt(regionPatterns, 0),
                patternAt(regionPatterns, 1),
                patternAt(regionPatterns, 2),
                chosen == null ? 0 : 1,
                chosen == null ? "" : chosen.contentTypeId(),
                chosen != null && chosen.overseasAll() ? 1 : 0,
                categoryMask.low(),
                categoryMask.high(),
                text,
                "%" + escapeLike(text) + "%",
                tieSeed,
                size);
        return rows.stream().map(DestinationSearchService::toItem).toList();
    }

    @Transactional(readOnly = true)
    public List<DestinationItemDTO> lookup(String name) {
        String trimmed = name == null ? "" : name.trim().replaceAll("\\s+", " ");
        if (trimmed.isEmpty()) {
            throw new InvalidRequestException("name", "empty", "name(찾을 여행지 이름)을 보내 주세요.");
        }
        if (trimmed.codePointCount(0, trimmed.length()) > MAX_NAME_LENGTH) {
            throw new InvalidRequestException("name", "too_long", "name은 " + MAX_NAME_LENGTH + "자까지 쓸 수 있습니다.");
        }
        String escaped = escapeLike(trimmed);
        return repository.lookupByName(trimmed, escaped + "(%", escaped + " (%", trimmed.replace(" ", ""), LOOKUP_LIMIT)
                .stream()
                .map(DestinationSearchService::toItem)
                .toList();
    }

    @Transactional(readOnly = true)
    public Optional<DestinationDetailDTO> detail(Long id) {
        if (id == null) {
            return Optional.empty();
        }
        return repository.findById(id).map(DestinationSearchService::toDetail);
    }

    static DestinationItemDTO toItem(DestinationInfo destination) {
        DestinationItemDTO item = new DestinationItemDTO();
        fill(item, destination);
        return item;
    }

    static DestinationDetailDTO toDetail(DestinationInfo destination) {
        DestinationDetailDTO detail = new DestinationDetailDTO();
        fill(detail, destination);
        detail.setAddress(destination.getAddress());
        detail.setRegionCode(destination.getRegionCode());
        detail.setSigunguCode(destination.getSigunguCode());
        detail.setSummary(destination.getSummary());
        return detail;
    }

    // 국내는 관광 타입, 해외는 키워드 근사. 국내인데 관광 타입이 6개 밖(25 여행코스, 38 쇼핑 등)이거나 없으면 null
    static String categoryLabel(DestinationInfo destination) {
        if (DestinationOrdering.isDomestic(destination.getSource(), destination.getCountry())) {
            return DestinationCategory.fromContentTypeId(destination.getContentTypeId())
                    .map(DestinationCategory::label)
                    .orElse(null);
        }
        return DestinationCategory.approximateOverseas(destination.getKeywordMaskLow(), destination.getKeywordMaskHigh()).label();
    }

    // LIKE 이스케이프: 쿼리의 ESCAPE '!'와 짝. !, %, _ 앞에 !를 붙인다.
    static String escapeLike(String value) {
        return value.replace("!", "!!").replace("%", "!%").replace("_", "!_");
    }

    private static void fill(DestinationItemDTO item, DestinationInfo destination) {
        item.setId(destination.getId());
        item.setName(destination.getDestName());
        item.setCountry(destination.getCountry());
        item.setLocation(destination.getLocation());
        item.setCategory(categoryLabel(destination));
        item.setInfo(destination.getInfo() != null ? destination.getInfo() : destination.getSummary());
        item.setInfoKind(KeywordResultDTO.infoKindOf(destination.getInfo(), destination.getSummary()));
        item.setImageUrl(destination.getImageUrl());
        item.setLat(destination.getLatitude());
        item.setLng(destination.getLongitude());
        item.setSource(destination.getSource());
        item.setCopyrightType(destination.getCopyrightType());
        item.setLicense(destination.getLicense());
        item.setSourceUrl(destination.getSourceUrl());
    }

    private static String patternAt(List<String> patterns, int index) {
        return index < patterns.size() ? patterns.get(index) : RegionPatterns.MATCH_ALL;
    }
}
