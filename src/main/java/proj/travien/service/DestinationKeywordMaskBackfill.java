package proj.travien.service;

import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Component;
import proj.travien.domain.DestinationInfo;
import proj.travien.repository.DestinationInfoRepository;

import java.util.ArrayList;
import java.util.List;
import java.util.function.LongFunction;

/**
 * 서버를 시작할 때 저장된 규칙 마스크(keyword_mask_low/high)를 지금 규칙에 맞춘다.
 *
 * <ol>
 *   <li>국내(TOUR_API)·위키데이터(WIKIDATA) 행: 규칙 판(keyword_mask_version)이 없거나 지금 판({@link KeywordMaskVersion})과 다른 행만
 *       id 순으로 {@value #PAGE_SIZE}건씩 읽어 다시 계산한다. 마스크가 바뀐 행은 마스크와 판을, 같은 행은 판만 쓴다.
 *       키워드 목록이나 코드표를 고친 뒤 서버를 다시 띄우면 이미 0이 아닌 마스크도 다시 계산된다.
 *       처음 이 판 기록이 들어간 서버를 띄울 때는 두 출처의 모든 행(약 2만 7천 행)을 한 번 읽는다.</li>
 *   <li>출처가 없는 기존 해외 행: 예전처럼 마스크가 0이고 설명문이 있는 행만 채운다. (원래 데이터의 마스크를 덮어쓰지 않는다)</li>
 * </ol>
 * 다시 계산은 엔티티 저장 대신 마스크·판 열만 바꾸는 UPDATE로 해서, 동시에 돈 동기화가 저장한 설명문 등 다른 열을 덮어쓰지 않는다.
 * 태거 예측 열(tagger_*)은 건드리지 않는다.
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class DestinationKeywordMaskBackfill {

    static final int PAGE_SIZE = 500;
    static final List<String> VERSIONED_SOURCES = List.of(DestinationInfo.SOURCE_TOUR_API, DestinationInfo.SOURCE_WIKIDATA);

    private final DestinationInfoRepository destinationInfoRepository;

    @EventListener(ApplicationReadyEvent.class)
    public void onApplicationReady() {
        recomputeOutdatedMasks();
        backfillMissingMasks();
    }

    // 1. 국내·위키데이터 행 중 규칙 판이 낡은 행 다시 계산
    public RecomputeResult recomputeOutdatedMasks() {
        int read = 0;
        int changed = 0;
        for (String source : VERSIONED_SOURCES) {
            String version = KeywordMaskVersion.forSource(source);
            RecomputeResult withoutVersion = recompute(version, lastId -> destinationInfoRepository
                    .findBySourceAndKeywordMaskVersionIsNullAndIdGreaterThanOrderByIdAsc(source, lastId, PageRequest.of(0, PAGE_SIZE)));
            RecomputeResult oldVersion = recompute(version, lastId -> destinationInfoRepository
                    .findBySourceAndKeywordMaskVersionNotAndIdGreaterThanOrderByIdAsc(source, version, lastId, PageRequest.of(0, PAGE_SIZE)));
            int sourceRead = withoutVersion.read() + oldVersion.read();
            int sourceChanged = withoutVersion.changed() + oldVersion.changed();
            if (sourceRead > 0) {
                log.info("Keyword mask recompute source={} version={} read={} maskChanged={}", source, version, sourceRead, sourceChanged);
            }
            read += sourceRead;
            changed += sourceChanged;
        }
        return new RecomputeResult(read, changed);
    }

    private RecomputeResult recompute(String version, LongFunction<List<DestinationInfo>> nextPage) {
        int read = 0;
        int changed = 0;
        long lastId = 0L;
        while (true) {
            List<DestinationInfo> page = nextPage.apply(lastId);
            if (page.isEmpty()) {
                break;
            }
            List<Long> sameMask = new ArrayList<>();
            for (DestinationInfo destination : page) {
                read++;
                KeywordMask.Mask mask = destination.computeKeywordMask();
                if (mask.low() == destination.getKeywordMaskLow() && mask.high() == destination.getKeywordMaskHigh()) {
                    sameMask.add(destination.getId());
                } else {
                    changed += destinationInfoRepository.updateRuleMask(destination.getId(), mask.low(), mask.high(), version) > 0 ? 1 : 0;
                }
            }
            if (!sameMask.isEmpty()) {
                destinationInfoRepository.markRuleMaskVersion(sameMask, version);
            }
            lastId = page.get(page.size() - 1).getId();
            if (page.size() < PAGE_SIZE) {
                break;
            }
        }
        return new RecomputeResult(read, changed);
    }

    // 2. 출처가 없는 기존 해외 행 중 마스크가 비어 있는 행 채우기 (예전 동작)
    public void backfillMissingMasks() {
        List<DestinationInfo> destinations = destinationInfoRepository
                .findAllBySourceIsNullAndKeywordMaskLowAndKeywordMaskHighAndInfoIsNotNull(0L, 0L);
        if (destinations.isEmpty()) {
            return;
        }
        destinations.forEach(DestinationInfo::refreshKeywordMask);
        destinationInfoRepository.saveAll(destinations);
    }

    // read: 다시 계산한 행 수, changed: 마스크가 바뀌어 쓴 행 수 (나머지는 판만 바꿈)
    public record RecomputeResult(int read, int changed) {
    }
}
