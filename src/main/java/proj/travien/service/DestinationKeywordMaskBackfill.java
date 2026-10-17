package proj.travien.service;

import lombok.RequiredArgsConstructor;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import proj.travien.domain.DestinationInfo;
import proj.travien.repository.DestinationInfoRepository;

import java.util.ArrayList;
import java.util.List;

@Component
@RequiredArgsConstructor
public class DestinationKeywordMaskBackfill {

    private final DestinationInfoRepository destinationInfoRepository;

    @EventListener(ApplicationReadyEvent.class)
    @Transactional
    public void backfillMissingMasks() {
        // 마스크가 비어 있는 행 중 다시 계산하면 키워드가 생길 수 있는 행만 읽는다.
        // 설명문도 없고 키워드가 없는 분류(호텔, 시민공원 등)인 행은 계산해도 빈 마스크라 시작할 때마다 다시 읽지 않는다.
        List<DestinationInfo> destinations = new ArrayList<>(
                destinationInfoRepository.findAllByKeywordMaskLowAndKeywordMaskHighAndInfoIsNotNull(0L, 0L));
        List<String> categoriesWithKeywords = TourCategories.codesWithKeywords();
        if (!categoriesWithKeywords.isEmpty()) {
            destinations.addAll(destinationInfoRepository.findAllByKeywordMaskLowAndKeywordMaskHighAndInfoIsNullAndCategoryIn(
                    0L, 0L, categoriesWithKeywords));
        }

        destinations.forEach(DestinationInfo::refreshKeywordMask);
        destinationInfoRepository.saveAll(destinations);
    }
}
