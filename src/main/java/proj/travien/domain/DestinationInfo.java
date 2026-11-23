package proj.travien.domain;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;
import proj.travien.service.KeywordMask;
import proj.travien.service.KeywordMaskVersion;
import proj.travien.service.TourCategories;
import proj.travien.service.WikiTypeKeywords;

import java.nio.charset.StandardCharsets;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Objects;
import java.util.zip.CRC32;

@NoArgsConstructor
@Getter
@Setter
@Entity
@Table(indexes = @Index(name = "idx_destination_source_external_id", columnList = "source, external_id"))
public class DestinationInfo {

    // 한국관광공사 TourAPI에서 수집한 국내 여행지의 source 값
    public static final String SOURCE_TOUR_API = "TOUR_API";

    // 위키데이터(목록)와 한국어 위키백과(설명문)에서 수집한 해외 여행지의 source 값
    public static final String SOURCE_WIKIDATA = "WIKIDATA";

    private static final KeywordMask.Mask WORLD_HERITAGE_MASK = KeywordMask.fromKeywords(List.of("유네스코"));

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String country;
    private String location;
    private String destName;

    @Column(columnDefinition = "TEXT")
    private String info;

    // 수집한 필드(TourAPI 지역·관광 타입·분류 코드, 위키데이터 국가·행정구역·유형·세계유산)로만 만든 짧은 소개.
    // 실제 설명문(info)과 따로 두고, 설명문이 없을 때 추천 결과에 대신 보여준다.
    @Column(length = 500)
    private String summary;

    @jakarta.persistence.Column(name = "keyword_mask_low", nullable = false, columnDefinition = "BIGINT DEFAULT 0")
    private long keywordMaskLow;

    @jakarta.persistence.Column(name = "keyword_mask_high", nullable = false, columnDefinition = "BIGINT DEFAULT 0")
    private long keywordMaskHigh;

    // 위 규칙 마스크를 계산한 규칙의 판(KeywordMaskVersion). 국내·위키데이터 행은 서버를 시작할 때 판이 다르면 다시 계산한다. null = 판 기록 전
    @Column(name = "keyword_mask_version", length = 40)
    private String keywordMaskVersion;

    // 키워드 태거(training/keyword_tagger) 예측 마스크. 규칙 마스크와 따로 두어 저장할 때 규칙 계산이 덮어쓰지 않는다.
    // 추천 쿼리가 설정(keyword.recommend.mask-mode)에 따라 규칙 마스크와 합친다. (docs/keyword-tagger-serving.md)
    @Column(name = "tagger_mask_low", nullable = false, columnDefinition = "BIGINT DEFAULT 0")
    private long taggerMaskLow;

    @Column(name = "tagger_mask_high", nullable = false, columnDefinition = "BIGINT DEFAULT 0")
    private long taggerMaskHigh;

    // 예측을 만든 모델(outputs/<run_name>의 run_name, 예: roberta-base-v3). null이면 예측 없음(위 두 값은 0)
    @Column(name = "tagger_model", length = 100)
    private String taggerModel;

    // 예측에 쓴 설명문(info)의 CRC32(UTF-8). 설명문이 바뀌거나 지워지면 저장할 때 예측을 지운다.
    @Column(name = "tagger_info_crc32")
    private Long taggerInfoCrc32;

    // 예측을 가져온 시각 (POST /api/admin/keyword-masks/import)
    private LocalDateTime taggerUpdatedAt;

    // 아래는 외부 데이터로 수집한 여행지에만 채워지는 값 (기존 해외 여행지는 null)
    private String source;

    @Column(name = "external_id")
    private String externalId;

    private String contentTypeId;

    // 법정동 시도·시군구 코드 (TourAPI lDongRegnCd, lDongSignguCd)
    private String regionCode;
    private String sigunguCode;

    // 분류체계 3Depth 코드 (TourAPI lclsSystm3). 위키데이터 여행지는 유형 코드표(wiki/type-keywords.csv)에 있는 P31 QID를 '|'로 이은 값
    private String category;

    // ISO 3166-1 alpha-2 국가 코드 (위키데이터 P297). 위키데이터 여행지에만 채운다.
    @Column(length = 2)
    private String countryCode;

    // 유네스코 세계유산(위키데이터 P1435 = Q9259, 해제되지 않은 지정) 여부. 위키데이터 여행지에만 채운다.
    private Boolean worldHeritage;

    // 설명문을 가져온 문서 주소와 라이선스. 화면에 설명문을 보여줄 때 함께 표시해야 한다. (docs/overseas-wiki-sync.md)
    @Column(length = 1000)
    private String sourceUrl;

    private String license;

    // 설명문을 가져온 위키백과 판 번호(revision id). 판이 바뀌었을 때만 설명문을 다시 저장한다.
    private Long sourceRevision;

    @Column(length = 500)
    private String address;

    private Double latitude;
    private Double longitude;

    @Column(length = 1000)
    private String imageUrl;

    private String tel;

    // TourAPI 저작권 유형 (Type1: 출처표시, Type3: 출처표시 + 변경금지)
    private String copyrightType;

    // TourAPI modifiedtime(yyyyMMddHHmmss). 바뀌면 상세 설명을 다시 받는다.
    private String sourceModifiedTime;

    // 설명문(TourAPI overview, 위키백과 도입부)을 마지막으로 받은(확인한) 시각. null이면 다음 설명문 수집 대상이다.
    private LocalDateTime overviewSyncedAt;

    // 설명문 수집에 마지막으로 실패한 시각. 실패한 행은 다음 수집 때 대기열 뒤로 보낸다.
    private LocalDateTime overviewFailedAt;

    // 저장할 때마다 규칙 마스크와 그 판을 다시 계산하고, 설명문이 바뀐 행의 태거 예측을 지운다. (태거 마스크 자체는 다시 계산하지 않는다)
    @jakarta.persistence.PrePersist
    @jakarta.persistence.PreUpdate
    public void refreshKeywordMask() {
        KeywordMask.Mask mask = computeKeywordMask();
        keywordMaskLow = mask.low();
        keywordMaskHigh = mask.high();
        keywordMaskVersion = KeywordMaskVersion.forSource(source);
        if (taggerModel != null && !Objects.equals(taggerInfoCrc32, infoCrc32(info))) {
            clearTaggerPrediction();
        }
    }

    // 태거 예측을 기록한다. infoCrc32는 예측에 쓴 설명문의 CRC32(지금 설명문과 같아야 한다. 확인은 가져오는 쪽에서 한다)
    public void applyTaggerPrediction(KeywordMask.Mask mask, String model, long infoCrc32, LocalDateTime at) {
        taggerMaskLow = mask.low();
        taggerMaskHigh = mask.high();
        taggerModel = model;
        taggerInfoCrc32 = infoCrc32;
        taggerUpdatedAt = at;
    }

    public void clearTaggerPrediction() {
        taggerMaskLow = 0L;
        taggerMaskHigh = 0L;
        taggerModel = null;
        taggerInfoCrc32 = null;
        taggerUpdatedAt = null;
    }

    public KeywordMask.Mask taggerMask() {
        return new KeywordMask.Mask(taggerMaskLow, taggerMaskHigh);
    }

    /**
     * 설명문의 CRC32 (UTF-8 바이트). training/keyword_tagger/scripts/predict_all.py의 info_crc32
     * (zlib.crc32(overview.encode("utf-8")))와 같은 값이다. 설명문이 없으면 null.
     */
    public static Long infoCrc32(String info) {
        if (info == null) {
            return null;
        }
        CRC32 crc = new CRC32();
        crc.update(info.getBytes(StandardCharsets.UTF_8));
        return crc.getValue();
    }

    // 지금 값으로 계산한 키워드 마스크. 저장된 마스크는 바꾸지 않는다. (동기화에서 마스크만 낡은 행을 찾을 때 쓴다)
    public KeywordMask.Mask computeKeywordMask() {
        if (SOURCE_TOUR_API.equals(source)) {
            // 국내 설명문은 지명·일반 단어 속 한 글자 키워드('부산'의 '산' 등)를 걸러서 계산하고,
            // 분류 코드표(tour/category-keywords.csv)에 정해 둔 분류별 키워드를 더한다. 그래서 설명문이 없는 여행지도 분류로 추천된다.
            // summary는 분류명을 이어 붙인 글이라 다시 훑지 않는다. ('문화관광'의 '문화', '자연경관(산)'의 '산'처럼
            // 코드표에서 일부러 뺀 키워드가 켜지므로, 분류에서 나오는 키워드는 코드표 한 곳에서만 정한다)
            return KeywordMask.fromKoreanDescription(info).union(TourCategories.keywordMask(category));
        }
        if (SOURCE_WIKIDATA.equals(source)) {
            // 위키백과 한국어 설명문 ∪ 유형 코드표(wiki/type-keywords.csv)의 유형별 키워드 ∪ 세계유산이면 '유네스코'.
            // summary는 유형명·지명을 이어 붙인 글이라 국내와 같은 이유로 훑지 않는다.
            KeywordMask.Mask mask = KeywordMask.fromKoreanDescription(withoutMisleadingWords(info))
                    .union(WikiTypeKeywords.keywordMask(category));
            return Boolean.TRUE.equals(worldHeritage) ? mask.union(WORLD_HERITAGE_MASK) : mask;
        }
        return KeywordMask.fromDescription(info);
    }

    // 키워드가 다른 낱말 속에 들어 있어 잘못 켜지는 낱말을 빼고 훑는다. 저장된 설명문은 바꾸지 않는다.
    // - '문화어': 한국어 위키백과가 외국 지명 뒤에 북한 표기를 '(프랑스어: la Seine, 문화어: 쌘느강)'처럼 적는다. '문화'가 켜짐.
    // - '모스크바': 도시 이름 속 '모스크'가 켜져 크렘린의 종·대포가 모스크로 추천됐다(2026-10-18 실제 서버에서 확인).
    static final java.util.List<String> MISLEADING_WORDS = java.util.List.of("문화어", "모스크바");

    private static String withoutMisleadingWords(String info) {
        if (info == null) {
            return null;
        }
        String out = info;
        for (String word : MISLEADING_WORDS) {
            out = out.replace(word, " ");
        }
        return out;
    }

}
