package proj.travien.repository;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;
import proj.travien.domain.DestinationInfo;
import proj.travien.dto.KeywordResultProjection;

import java.time.LocalDateTime;
import java.util.Collection;
import java.util.List;

public interface DestinationInfoRepository extends JpaRepository<DestinationInfo, Long> {

    // 마스크가 비어 있고 설명문이 있는 행 (해외 여행지, 설명문을 받은 국내 여행지)
    List<DestinationInfo> findAllByKeywordMaskLowAndKeywordMaskHighAndInfoIsNotNull(long keywordMaskLow, long keywordMaskHigh);

    // 마스크가 비어 있고 설명문은 없지만, 키워드가 정해진 분류 코드를 가진 행 (설명 수집 전인 국내 여행지)
    List<DestinationInfo> findAllByKeywordMaskLowAndKeywordMaskHighAndInfoIsNullAndCategoryIn(
            long keywordMaskLow, long keywordMaskHigh, Collection<String> categories);

    List<DestinationInfo> findAllBySource(String source);

    // 설명문이 없는 행 중 실패한 적 없는 행(overview_failed_at NULL, MySQL 오름차순에서 앞)부터, 그다음 오래전에 실패한 행 순서
    List<DestinationInfo> findBySourceAndOverviewSyncedAtIsNullOrderByOverviewFailedAtAscIdAsc(String source, Pageable pageable);

    // 소개·마스크 재계산용: id 순으로 lastId 다음 행부터 한 페이지씩 읽는다. (offset 없이 읽어 뒤 페이지도 빠르다)
    List<DestinationInfo> findBySourceAndIdGreaterThanOrderByIdAsc(String source, Long lastId, Pageable pageable);

    // 설명문을 받은 지 오래된 행 (위키백과 판 번호 재확인용). 실패한 적 없는 행부터, 그다음 오래전에 받은 순서
    List<DestinationInfo> findBySourceAndOverviewSyncedAtBeforeOrderByOverviewFailedAtAscOverviewSyncedAtAscIdAsc(
            String source, LocalDateTime syncedBefore, Pageable pageable);

    // 설명문 수집 실패 시각만 기록한다. (엔티티 저장을 거치지 않으므로 저장 실패한 값이 다시 쓰이지 않는다)
    @Transactional
    @Modifying
    @Query("UPDATE DestinationInfo d SET d.overviewFailedAt = :failedAt WHERE d.id = :id")
    int markOverviewFailed(@Param("id") Long id, @Param("failedAt") LocalDateTime failedAt);

    // 다시 확인한 설명문이 그대로일 때 확인 시각만 바꾼다. (행 전체를 다시 쓰지 않는다)
    @Transactional
    @Modifying
    @Query("UPDATE DestinationInfo d SET d.overviewSyncedAt = :syncedAt, d.overviewFailedAt = NULL WHERE d.id IN :ids")
    int markOverviewSynced(@Param("ids") Collection<Long> ids, @Param("syncedAt") LocalDateTime syncedAt);

    // 설명문(info)이 없는 국내 여행지는 TourAPI 필드로 만든 소개(summary)를 대신 돌려준다. 실제 설명문이 있으면 설명문이 우선이다.
    @Query(value = """
            SELECT d.id AS id,
                   d.country AS country,
                   d.location AS location,
                   d.dest_name AS destName,
                   COALESCE(d.info, d.summary) AS info,
                   d.source AS source,
                   d.copyright_type AS copyrightType,
                   d.source_url AS sourceUrl,
                   d.license AS license,
                   CASE WHEN d.info IS NOT NULL THEN 'DESCRIPTION'
                        WHEN d.summary IS NOT NULL THEN 'SUMMARY'
                   END AS infoKind
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
