import React from 'react';
import styled from 'styled-components';

// 키워드 추천 결과(/api/search)의 source 값
export const SOURCE_TOUR_API = 'TOUR_API';
export const SOURCE_WIKIDATA = 'WIKIDATA';
// 화면에서 위키백과 API로 직접 불러온 설명문 (DB에 설명문이 없는 기존 여행지 등)
export const SOURCE_WIKIPEDIA = 'WIKIPEDIA';

// infoKind: 보여준 글이 실제 설명문(DESCRIPTION)인지, 수집한 필드로만 만든 소개(SUMMARY)인지
export const INFO_KIND_DESCRIPTION = 'DESCRIPTION';
export const INFO_KIND_SUMMARY = 'SUMMARY';

// 한국관광공사 국문 관광정보 서비스(TourAPI)를 내려받는 공공데이터포털 페이지
const TOUR_API_URL = 'https://www.data.go.kr/data/15101578/openapi.do';

// 공공누리 유형별 이용조건 (TourAPI cpyrhtDivCd). TourAPI는 Type1·Type3만 쓰지만 나머지 유형도 글로 표시한다.
const KOGL_TYPES = {
    type1: { label: '공공누리 제1유형: 출처표시', url: 'https://www.kogl.or.kr/info/licenseType1.do' },
    type2: { label: '공공누리 제2유형: 출처표시·상업적 이용금지', url: 'https://www.kogl.or.kr/info/licenseType2.do' },
    type3: { label: '공공누리 제3유형: 출처표시·변경금지', url: 'https://www.kogl.or.kr/info/licenseType3.do' },
    type4: { label: '공공누리 제4유형: 출처표시·상업적 이용금지·변경금지', url: 'https://www.kogl.or.kr/info/licenseType4.do' },
};

const CC_BY_SA_URL = 'https://creativecommons.org/licenses/by-sa/4.0/deed.ko';
const CC0_URL = 'https://creativecommons.org/publicdomain/zero/1.0/deed.ko';
const WIKIPEDIA_URL_PREFIX = 'https://ko.wikipedia.org/wiki/';

const Attribution = styled.div`
    margin-top: 6px;
    font-family: "Regular";
    font-size: 12px;
    line-height: 1.4;
    color: rgba(132, 132, 132, 1);
    word-break: keep-all;

    p {
        margin: 0;
    }

    a {
        color: inherit;
        text-decoration: underline;
    }
`;

export function koglType(copyrightType) {
    if (!copyrightType) {
        return null;
    }
    return KOGL_TYPES[String(copyrightType).trim().toLowerCase()] || null;
}

// "https://ko.wikipedia.org/wiki/%EC%97%90%ED%8E%A0%ED%83%91" → "에펠탑". 주소에서 읽지 못하면 fallback.
export function wikipediaTitle(sourceUrl, fallback) {
    if (sourceUrl && sourceUrl.startsWith(WIKIPEDIA_URL_PREFIX)) {
        try {
            const title = decodeURIComponent(sourceUrl.slice(WIKIPEDIA_URL_PREFIX.length).split(/[?#]/)[0]);
            if (title) {
                return title.replace(/_/g, ' ');
            }
        } catch (e) {
            // 잘못 인코딩된 주소는 fallback을 쓴다.
        }
    }
    return fallback || null;
}

export function wikipediaUrl(title) {
    return title ? WIKIPEDIA_URL_PREFIX + encodeURIComponent(title.replace(/ /g, '_')) : null;
}

// DB에서 온 주소는 http(s) 주소일 때만 링크로 건다.
function safeUrl(url) {
    return url && /^https?:\/\//i.test(url) ? url : null;
}

function ExternalLink({ href, children }) {
    const url = safeUrl(href);
    if (!url) {
        return <>{children}</>;
    }
    // 카드처럼 클릭하면 이동하는 영역 안에 있어도 링크만 열리게 한다.
    return (
        <a href={url} target="_blank" rel="noopener noreferrer" onClick={(event) => event.stopPropagation()}>
            {children}
        </a>
    );
}

function TourApiAttribution({ copyrightType, infoKind }) {
    const kogl = koglType(copyrightType);
    return (
        <>
            <p>
                출처: <ExternalLink href={TOUR_API_URL}>한국관광공사 TourAPI(국문 관광정보 서비스)</ExternalLink>
                {kogl && (
                    <>
                        {' · '}
                        <ExternalLink href={kogl.url}>{kogl.label}</ExternalLink>
                    </>
                )}
            </p>
            {infoKind === INFO_KIND_SUMMARY && (
                <p>이 소개는 TourAPI 분류 정보(지역·관광 타입·분류)로 만든 글입니다.</p>
            )}
        </>
    );
}

function WikidataAttribution({ sourceUrl, title, infoKind }) {
    const articleTitle = wikipediaTitle(sourceUrl, title);
    const wikidata = <ExternalLink href={CC0_URL}>위키데이터 (CC0)</ExternalLink>;
    const ccBySa = <ExternalLink href={CC_BY_SA_URL}>CC BY-SA 4.0</ExternalLink>;

    if (infoKind === INFO_KIND_SUMMARY) {
        // 소개는 위키데이터 값으로만 만든 글이라 위키백과 글이 들어 있지 않다. 위키백과 문서는 참고 링크로만 둔다.
        return (
            <>
                <p>출처: {wikidata} 항목 정보로 만든 소개</p>
                {safeUrl(sourceUrl) && (
                    <p>
                        관련 문서: <ExternalLink href={sourceUrl}>한국어 위키백과 「{articleTitle}」</ExternalLink> ({ccBySa})
                    </p>
                )}
            </>
        );
    }

    return (
        <p>
            출처: <ExternalLink href={sourceUrl}>
                한국어 위키백과{articleTitle ? ` 「${articleTitle}」 문서` : ''} 도입부 발췌
            </ExternalLink> ({ccBySa}) · {wikidata}
        </p>
    );
}

function WikipediaAttribution({ sourceUrl, title }) {
    const articleTitle = wikipediaTitle(sourceUrl, title);
    return (
        <p>
            출처: <ExternalLink href={sourceUrl || wikipediaUrl(articleTitle)}>
                한국어 위키백과{articleTitle ? ` 「${articleTitle}」 문서` : ''} 발췌
            </ExternalLink> (<ExternalLink href={CC_BY_SA_URL}>CC BY-SA 4.0</ExternalLink>)
        </p>
    );
}

/**
 * 여행지 설명문 바로 아래에 두는 출처 표시.
 * - TOUR_API: 한국관광공사 TourAPI + 공공누리 유형 (소개(summary)를 보여줄 때는 분류 정보로 만든 글이라는 안내)
 * - WIKIDATA: 한국어 위키백과 문서 링크와 CC BY-SA 4.0, 위키데이터 (CC0)
 * - WIKIPEDIA: 화면에서 직접 불러온 위키백과 글의 문서 링크와 CC BY-SA 4.0
 * - 그 밖(기존 여행지 등): 아무것도 표시하지 않는다.
 * 공공누리 출처표시는 이미지 없이 글로만 표시한다.
 */
export default function SourceAttribution({ source, copyrightType, sourceUrl, title, infoKind }) {
    let content = null;
    if (source === SOURCE_TOUR_API) {
        content = <TourApiAttribution copyrightType={copyrightType} infoKind={infoKind} />;
    } else if (source === SOURCE_WIKIDATA) {
        content = <WikidataAttribution sourceUrl={sourceUrl} title={title} infoKind={infoKind} />;
    } else if (source === SOURCE_WIKIPEDIA) {
        content = <WikipediaAttribution sourceUrl={sourceUrl} title={title} />;
    }

    if (!content) {
        return null;
    }
    return <Attribution aria-label="출처">{content}</Attribution>;
}
