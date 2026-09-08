package proj.travien.domain;

import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import proj.travien.service.KeywordMask;

@NoArgsConstructor
@Getter
@Setter
@Entity
public class DestinationInfo {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String country;
    private String location;
    private String destName;
    private String info;

    @jakarta.persistence.Column(name = "keyword_mask_low", nullable = false, columnDefinition = "BIGINT DEFAULT 0")
    private long keywordMaskLow;

    @jakarta.persistence.Column(name = "keyword_mask_high", nullable = false, columnDefinition = "BIGINT DEFAULT 0")
    private long keywordMaskHigh;

    @jakarta.persistence.PrePersist
    @jakarta.persistence.PreUpdate
    public void refreshKeywordMask() {
        KeywordMask.Mask mask = KeywordMask.fromDescription(info);
        keywordMaskLow = mask.low();
        keywordMaskHigh = mask.high();
    }

}
