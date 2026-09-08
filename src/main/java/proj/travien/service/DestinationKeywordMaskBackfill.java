package proj.travien.service;

import lombok.RequiredArgsConstructor;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;
import org.springframework.transaction.annotation.Transactional;
import proj.travien.domain.DestinationInfo;
import proj.travien.repository.DestinationInfoRepository;

import java.util.List;

@Component
@RequiredArgsConstructor
public class DestinationKeywordMaskBackfill {

    private final DestinationInfoRepository destinationInfoRepository;

    @EventListener(ApplicationReadyEvent.class)
    @Transactional
    public void backfillMissingMasks() {
        List<DestinationInfo> destinations =
                destinationInfoRepository.findAllByKeywordMaskLowAndKeywordMaskHigh(0L, 0L);

        destinations.forEach(DestinationInfo::refreshKeywordMask);
        destinationInfoRepository.saveAll(destinations);
    }
}
