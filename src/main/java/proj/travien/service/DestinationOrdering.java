package proj.travien.service;

import proj.travien.domain.DestinationInfo;

import java.nio.charset.StandardCharsets;
import java.time.Clock;
import java.time.LocalDate;
import java.time.ZoneId;
import java.util.zip.CRC32;

/**
 * 여행지 목록(키워드 추천 /api/search, 여행지 조회 /api/destinations)의 동점 정렬과 국내·해외 구분.
 *
 * <p>동점이면 ① 실제 설명문(info)이 있는 곳 ② 대표 이미지가 있는 곳 ③ {@code CRC32(CONCAT(id, ':', seed))} 오름차순
 * ④ id 오름차순 순서다. ③은 MySQL {@code CRC32}와 같은 값을 {@link #tieHash}로 계산한다(문자열 "id:seed"의 UTF-8 바이트).
 * seed를 보내지 않으면 오늘 날짜(Asia/Seoul) 숫자 {@code yyyyMMdd}를 쓴다. 그래서 같은 날에는 같은 요청이 같은 순서이고,
 * 날이 바뀌면 동점 안의 순서가 바뀐다. 예전처럼 id 순이면 동점일 때 항상 '가'로 시작하는 국내 관광지만 나왔다.
 */
public final class DestinationOrdering {

    // 기본 seed(오늘 날짜)를 정하는 시간대
    public static final ZoneId SEED_ZONE = ZoneId.of("Asia/Seoul");

    // 국내 여행지 판정: TourAPI 행이거나 나라가 대한민국인 행. 나머지(위키데이터, 출처 없는 기존 행)는 해외로 본다.
    public static final String DOMESTIC_COUNTRY = "대한민국";

    private DestinationOrdering() {
    }

    // MySQL CRC32(CONCAT(id, ':', seed))와 같은 값 (0 ~ 2^32-1)
    public static long tieHash(long id, long seed) {
        CRC32 crc = new CRC32();
        crc.update((id + ":" + seed).getBytes(StandardCharsets.UTF_8));
        return crc.getValue();
    }

    // 오늘 날짜(Asia/Seoul) yyyyMMdd. 예: 2026-10-18 → 20261018
    public static long defaultSeed(Clock clock) {
        LocalDate today = LocalDate.now(clock.withZone(SEED_ZONE));
        return today.getYear() * 10_000L + today.getMonthValue() * 100L + today.getDayOfMonth();
    }

    // SQL의 DestinationInfoRepository.DOMESTIC_SQL과 같은 판정
    public static boolean isDomestic(String source, String country) {
        return DestinationInfo.SOURCE_TOUR_API.equals(source) || DOMESTIC_COUNTRY.equals(country);
    }

    /**
     * 지역 개수 상한에 쓰는 지역 이름. 국내는 시도(location 첫 단어, 예: '부산광역시'), 해외는 나라(country).
     * 값이 없으면 location 첫 단어, 그것도 없으면 빈 문자열(모르는 지역끼리 한 지역으로 센다).
     */
    public static String regionKey(String source, String country, String location) {
        String firstToken = firstToken(location);
        if (isDomestic(source, country)) {
            return firstToken.isEmpty() ? DOMESTIC_COUNTRY : firstToken;
        }
        if (country != null && !country.isBlank()) {
            return country.trim();
        }
        return firstToken;
    }

    private static String firstToken(String value) {
        if (value == null || value.isBlank()) {
            return "";
        }
        return value.trim().split("\\s+", 2)[0];
    }
}
