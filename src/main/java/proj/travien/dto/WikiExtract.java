package proj.travien.dto;

/**
 * 한국어 위키백과 Action API(prop=extracts|revisions|info)로 받은 문서 도입부.
 *
 * @param requestedTitle 요청한 제목
 * @param title          정규화·넘겨주기를 거친 실제 문서 제목
 * @param revisionId     설명문을 가져온 판 번호 (없으면 null)
 * @param extract        도입부 평문. 응답에 없으면 null, 도입부가 비어 있으면 ""
 * @param url            실제 문서 주소 (fullurl)
 * @param missing        문서가 없거나(missing) 잘못된 제목(invalid)인지
 */
public record WikiExtract(
        String requestedTitle,
        String title,
        Long revisionId,
        String extract,
        String url,
        boolean missing
) {
}
