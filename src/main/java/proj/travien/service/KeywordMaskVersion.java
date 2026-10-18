package proj.travien.service;

import proj.travien.domain.DestinationInfo;

import java.nio.charset.StandardCharsets;
import java.util.zip.CRC32;

/**
 * 규칙 마스크({@code destination_info.keyword_mask_low/high})를 계산한 규칙의 판.
 *
 * <p>마스크를 계산할 때(@PrePersist·@PreUpdate) 행의 {@code keyword_mask_version}에 함께 저장하고, 서버를 시작할 때
 * {@link DestinationKeywordMaskBackfill}이 저장된 판이 지금 판과 다른 국내(TOUR_API)·위키데이터(WIKIDATA) 행만 다시 계산한다.
 * 출처가 없는 기존 해외 행은 판을 보지 않는다(예전처럼 마스크가 0인 행만 채움).
 *
 * <p>판 = {@code r<RULE_REVISION>-k<키워드·조사 지문>}에 국내는 {@code -t<분류 코드표 지문>}, 위키데이터는 {@code -w<유형 코드표 지문>}을 붙인 값.
 * 예: {@code r1-k1a2b3c4d-t5e6f7a8b}. 지문은 CRC32(16진수 8자리)다.
 * <ul>
 *   <li>키워드 목록(순서), 조사 목록, 코드표의 코드별 키워드는 지문으로 자동 반영된다. (코드표의 분류명만 바꾸면 판은 그대로)</li>
 *   <li>계산 코드({@link KeywordMask}의 매칭 방식, {@link DestinationInfo#computeKeywordMask()})를 바꾸면 {@link #RULE_REVISION}을 올린다.</li>
 * </ul>
 */
public final class KeywordMaskVersion {

    // 마스크 계산 코드를 바꿀 때 올린다. 1 = fromKoreanDescription ∪ 분류 코드표(국내), '문화어' 제외 + 유형 코드표 + 세계유산(위키데이터)
    // 2 = 위키데이터 설명문에서 '모스크바'도 빼고 훑음(모스크 오탐, DestinationInfo.MISLEADING_WORDS)
    static final int RULE_REVISION = 2;

    private KeywordMaskVersion() {
    }

    // 이 출처의 행에 지금 규칙으로 계산한 마스크의 판
    public static String forSource(String source) {
        if (DestinationInfo.SOURCE_TOUR_API.equals(source)) {
            return Holder.TOUR_API;
        }
        if (DestinationInfo.SOURCE_WIKIDATA.equals(source)) {
            return Holder.WIKIDATA;
        }
        return Holder.BASE;
    }

    // 처음 쓸 때 한 번만 계산한다. 코드표 형식이 틀리면 여기서 TourCategories·WikiTypeKeywords의 예외가 난다(서버 시작 단계에서 멈춤).
    private static final class Holder {
        private static final String BASE = "r" + RULE_REVISION + "-k" + crc32(KeywordMask.ruleFingerprintSource());
        private static final String TOUR_API = BASE + "-t" + crc32(tourCategoryFingerprintSource());
        private static final String WIKIDATA = BASE + "-w" + crc32(wikiTypeFingerprintSource());
    }

    static String tourCategoryFingerprintSource() {
        StringBuilder text = new StringBuilder();
        for (TourCategories.Category category : TourCategories.all()) {
            text.append(category.code()).append('=').append(String.join("|", category.keywords())).append('\n');
        }
        return text.toString();
    }

    static String wikiTypeFingerprintSource() {
        StringBuilder text = new StringBuilder();
        for (WikiTypeKeywords.Type type : WikiTypeKeywords.all()) {
            text.append(type.qid()).append('=').append(String.join("|", type.keywords())).append('\n');
        }
        return text.toString();
    }

    static String crc32(String text) {
        CRC32 crc = new CRC32();
        crc.update(text.getBytes(StandardCharsets.UTF_8));
        return String.format("%08x", crc.getValue());
    }
}
