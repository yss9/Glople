package proj.travien.service;

import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import proj.travien.dto.KeywordResultDTO;
import proj.travien.dto.KeywordResultProjection;
import proj.travien.repository.DestinationInfoRepository;

import java.util.List;

@Service
@RequiredArgsConstructor
public class KeywordRecommendService {

    private final DestinationInfoRepository repository;

    public List<KeywordResultDTO> searchByKeywords(List<String> keywords) {
        KeywordMask.Mask mask = KeywordMask.fromKeywords(keywords);
        if (mask.isEmpty()) {
            return List.of();
        }

        return repository.findTop21ByKeywordMask(mask.low(), mask.high()).stream()
                .map(this::convertToDTO)
                .toList();
    }

    private KeywordResultDTO convertToDTO(KeywordResultProjection entity) {
        KeywordResultDTO dto = new KeywordResultDTO();
        dto.setId(entity.getId());
        dto.setDestName(entity.getDestName());
        dto.setCountry(entity.getCountry());
        dto.setLocation(entity.getLocation());
        dto.setInfo(entity.getInfo());
        return dto;
    }

}
