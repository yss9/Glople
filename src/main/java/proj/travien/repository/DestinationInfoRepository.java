package proj.travien.repository;

import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import proj.travien.domain.DestinationInfo;
import proj.travien.dto.KeywordResultProjection;

import java.util.List;

public interface DestinationInfoRepository extends JpaRepository<DestinationInfo, Long> {

    List<DestinationInfo> findAllByKeywordMaskLowAndKeywordMaskHigh(long keywordMaskLow, long keywordMaskHigh);

    @Query(value = """
            SELECT d.id AS id,
                   d.country AS country,
                   d.location AS location,
                   d.dest_name AS destName,
                   d.info AS info
            FROM destination_info d
            WHERE (d.keyword_mask_low & :lowMask) <> 0
               OR (d.keyword_mask_high & :highMask) <> 0
            ORDER BY (
                BIT_COUNT(d.keyword_mask_low & :lowMask)
                + BIT_COUNT(d.keyword_mask_high & :highMask)
            ) DESC,
            d.id ASC
            LIMIT 21
            """, nativeQuery = true)
    List<KeywordResultProjection> findTop21ByKeywordMask(
            @Param("lowMask") long lowMask,
            @Param("highMask") long highMask
    );
}
