package proj.travien.repository;

import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.transaction.annotation.Transactional;
import proj.travien.domain.DestinationInfo;
import proj.travien.dto.KeywordCandidate;

import java.time.LocalDateTime;
import java.util.Collection;
import java.util.List;

public interface DestinationInfoRepository extends JpaRepository<DestinationInfo, Long> {

    // 출처가 없는 기존 해외 행 중 마스크가 비어 있고 설명문이 있는 행 (시작 시 마스크 보정. 국내·위키데이터 행은 규칙 판으로 다시 계산)
    List<DestinationInfo> findAllBySourceIsNullAndKeywordMaskLowAndKeywordMaskHighAndInfoIsNotNull(long keywordMaskLow, long keywordMaskHigh);

    // 규칙 판(keyword_mask_version)을 기록하기 전인 행. id 순으로 lastId 다음 행부터 한 페이지씩 (시작 시 마스크 재계산)
    List<DestinationInfo> findBySourceAndKeywordMaskVersionIsNullAndIdGreaterThanOrderByIdAsc(String source, Long lastId, Pageable pageable);

    // 규칙 판이 지금 판과 다른 행 (규칙·키워드 목록·코드표가 바뀐 뒤). NULL은 위 메서드가 읽는다
    List<DestinationInfo> findBySourceAndKeywordMaskVersionNotAndIdGreaterThanOrderByIdAsc(
            String source, String keywordMaskVersion, Long lastId, Pageable pageable);

    // 태거 예측 가져오기: 출처와 외부 id로 찾는다 (idx_destination_source_external_id). 같은 외부 id 행이 여럿이면 모두
    List<DestinationInfo> findAllBySourceAndExternalIdIn(String source, Collection<String> externalIds);

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

    // 시작 시 마스크 재계산: 다시 계산한 규칙 마스크와 판만 쓴다. 그사이 다른 저장이 판을 이미 지금 판으로 바꿨으면(그 저장이 계산한 마스크가 더 새것) 쓰지 않는다.
    // (엔티티 저장을 거치지 않으므로 설명문 등 다른 열을 읽어 둔 값으로 덮어쓰지 않는다)
    @Transactional
    @Modifying
    @Query("UPDATE DestinationInfo d SET d.keywordMaskLow = :low, d.keywordMaskHigh = :high, d.keywordMaskVersion = :version"
            + " WHERE d.id = :id AND (d.keywordMaskVersion IS NULL OR d.keywordMaskVersion <> :version)")
    int updateRuleMask(@Param("id") Long id, @Param("low") long low, @Param("high") long high, @Param("version") String version);

    // 시작 시 마스크 재계산: 다시 계산해도 마스크가 같은 행은 판만 지금 판으로 바꾼다.
    @Transactional
    @Modifying
    @Query("UPDATE DestinationInfo d SET d.keywordMaskVersion = :version"
            + " WHERE d.id IN :ids AND (d.keywordMaskVersion IS NULL OR d.keywordMaskVersion <> :version)")
    int markRuleMaskVersion(@Param("ids") Collection<Long> ids, @Param("version") String version);

    // 다시 확인한 설명문이 그대로일 때 확인 시각만 바꾼다. (행 전체를 다시 쓰지 않는다)
    @Transactional
    @Modifying
    @Query("UPDATE DestinationInfo d SET d.overviewSyncedAt = :syncedAt, d.overviewFailedAt = NULL WHERE d.id IN :ids")
    int markOverviewSynced(@Param("ids") Collection<Long> ids, @Param("syncedAt") LocalDateTime syncedAt);

    // 국내 여행지 판정 (DestinationOrdering.isDomestic과 같은 규칙). source·country가 NULL인 기존 해외 행도 참·거짓이 정해지도록 COALESCE를 쓴다.
    String DOMESTIC_SQL = "(COALESCE(d.source, '') = 'TOUR_API' OR COALESCE(d.country, '') = '대한민국')";

    // 동점 정렬 (DestinationOrdering 참고): 실제 설명문 → 대표 이미지 → CRC32(id:seed) → id
    String TIE_BREAK_SQL = "(d.info IS NOT NULL) DESC, (d.image_url IS NOT NULL) DESC, CRC32(CONCAT(d.id, ':', :seed)) ASC, d.id ASC";

    /*
     * 키워드 추천에 쓰는 마스크 (KeywordMaskMode, :maskMode = 'union' | 'tagger' | 'rule').
     * - 태거 예측이 없는 행(tagger_model IS NULL)이나 rule: 규칙 마스크
     * - tagger: 태거 마스크
     * - union(그 밖): 규칙 마스크 | 태거 마스크
     * 마스크 값은 비트 0~62만 써서 모두 0 이상 2^63 미만이다. 세 갈래가 같은 형(BIGINT UNSIGNED)이 되도록 열 하나짜리 갈래는 CAST한다.
     */
    String RECOMMEND_MASK_LOW_SQL = "(CASE WHEN :maskMode = 'rule' OR d.tagger_model IS NULL THEN CAST(d.keyword_mask_low AS UNSIGNED)"
            + " WHEN :maskMode = 'tagger' THEN CAST(d.tagger_mask_low AS UNSIGNED)"
            + " ELSE d.keyword_mask_low | d.tagger_mask_low END)";
    String RECOMMEND_MASK_HIGH_SQL = "(CASE WHEN :maskMode = 'rule' OR d.tagger_model IS NULL THEN CAST(d.keyword_mask_high AS UNSIGNED)"
            + " WHEN :maskMode = 'tagger' THEN CAST(d.tagger_mask_high AS UNSIGNED)"
            + " ELSE d.keyword_mask_high | d.tagger_mask_high END)";

    /**
     * 키워드 추천 후보 (POST /api/search). 고른 키워드가 하나라도 켜진 행을 일치 수 내림차순으로, 동점이면 TIE_BREAK_SQL 순으로
     * poolSize개까지 고른다. 지역 개수 상한(KeywordRecommendService)을 적용하려고 limit보다 넉넉히 읽고, 글 열은 읽지 않는다.
     * 행의 마스크는 RECOMMEND_MASK_LOW_SQL·RECOMMEND_MASK_HIGH_SQL(maskMode: union | tagger | rule)로 정한다.
     * scope: all | domestic | overseas
     */
    @Query(value = "SELECT d.id AS id,"
            + " d.country AS country,"
            + " d.location AS location,"
            + " d.source AS source,"
            + " (BIT_COUNT(" + RECOMMEND_MASK_LOW_SQL + " & :lowMask) + BIT_COUNT(" + RECOMMEND_MASK_HIGH_SQL + " & :highMask)) AS score,"
            + " (d.info IS NOT NULL) AS hasInfo,"
            + " (d.image_url IS NOT NULL) AS hasImage"
            + " FROM destination_info d"
            + " WHERE ((" + RECOMMEND_MASK_LOW_SQL + " & :lowMask) <> 0 OR (" + RECOMMEND_MASK_HIGH_SQL + " & :highMask) <> 0)"
            + " AND (:scope = 'all'"
            + " OR (:scope = 'domestic' AND " + DOMESTIC_SQL + ")"
            + " OR (:scope = 'overseas' AND NOT " + DOMESTIC_SQL + "))"
            + " ORDER BY score DESC, " + TIE_BREAK_SQL
            + " LIMIT :poolSize", nativeQuery = true)
    List<KeywordCandidate> findKeywordCandidates(
            @Param("lowMask") long lowMask,
            @Param("highMask") long highMask,
            @Param("maskMode") String maskMode,
            @Param("scope") String scope,
            @Param("seed") long seed,
            @Param("poolSize") int poolSize
    );

    /**
     * 여행지 조회 (GET /api/destinations). 조건은 모두 선택이고, 쓰지 않는 조건은 켜짐 값(regionCount, categoryOn, query)으로 끈다.
     * <ul>
     *   <li>지역: "나라 location"(예: '대한민국 부산광역시 해운대구', '일본 일본 교토부')에 regionN 정규식이 모두 맞아야 한다.
     *       정규식은 RegionPatterns가 단어마다 만든다. 쓰지 않는 칸에는 모든 글에 맞는 '^'를 넣는다(빈 패턴은 MySQL 8에서 오류).</li>
     *   <li>분류: 국내는 content_type_id, 해외는 키워드 마스크 근사(overseasAll=1이면 해외 전체).</li>
     *   <li>검색어: 이름·소개(summary) LIKE. queryLike는 '!'로 %, _, !를 이스케이프한 '%검색어%'.</li>
     * </ul>
     * 정렬: 이름이 검색어와 같은 곳 → 이름에 검색어가 든 곳 → TIE_BREAK_SQL.
     */
    @Query(value = "SELECT d.*"
            + " FROM destination_info d"
            + " WHERE (:regionCount < 1 OR CONCAT_WS(' ', d.country, d.location) REGEXP :region1)"
            + " AND (:regionCount < 2 OR CONCAT_WS(' ', d.country, d.location) REGEXP :region2)"
            + " AND (:regionCount < 3 OR CONCAT_WS(' ', d.country, d.location) REGEXP :region3)"
            + " AND (:categoryOn = 0"
            + " OR (" + DOMESTIC_SQL + " AND d.content_type_id = :contentTypeId)"
            + " OR (NOT " + DOMESTIC_SQL + " AND (:overseasAll = 1"
            + " OR (d.keyword_mask_low & :categoryLow) <> 0"
            + " OR (d.keyword_mask_high & :categoryHigh) <> 0)))"
            + " AND (:query = '' OR d.dest_name LIKE :queryLike ESCAPE '!' OR d.summary LIKE :queryLike ESCAPE '!')"
            + " ORDER BY (d.dest_name = :query) DESC, (d.dest_name LIKE :queryLike ESCAPE '!') DESC, " + TIE_BREAK_SQL
            + " LIMIT :limit", nativeQuery = true)
    List<DestinationInfo> searchDestinations(
            @Param("regionCount") int regionCount,
            @Param("region1") String region1,
            @Param("region2") String region2,
            @Param("region3") String region3,
            @Param("categoryOn") int categoryOn,
            @Param("contentTypeId") String contentTypeId,
            @Param("overseasAll") int overseasAll,
            @Param("categoryLow") long categoryLow,
            @Param("categoryHigh") long categoryHigh,
            @Param("query") String query,
            @Param("queryLike") String queryLike,
            @Param("seed") long seed,
            @Param("limit") int limit
    );

    /**
     * 이름으로 여행지 찾기 (GET /api/destinations/lookup). 이름이 같은 곳, 이름 뒤에 괄호 구분이 붙은 곳
     * ('마니산(강화)', '백련사 (서울)'), 공백을 뺀 이름이 같은 곳. 이름이 정확히 같은 곳이 먼저다.
     */
    @Query(value = """
            SELECT d.*
            FROM destination_info d
            WHERE d.dest_name = :name
               OR d.dest_name LIKE :parenLike ESCAPE '!'
               OR d.dest_name LIKE :spacedParenLike ESCAPE '!'
               OR REPLACE(d.dest_name, ' ', '') = :compactName
            ORDER BY (d.dest_name = :name) DESC,
                     (d.info IS NOT NULL) DESC,
                     (d.image_url IS NOT NULL) DESC,
                     d.id ASC
            LIMIT :limit
            """, nativeQuery = true)
    List<DestinationInfo> lookupByName(
            @Param("name") String name,
            @Param("parenLike") String parenLike,
            @Param("spacedParenLike") String spacedParenLike,
            @Param("compactName") String compactName,
            @Param("limit") int limit
    );
}
