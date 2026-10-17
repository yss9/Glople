import React from 'react';
import { render, screen } from '@testing-library/react';
import SourceAttribution, { wikipediaTitle, wikipediaUrl } from './SourceAttribution';

describe('SourceAttribution', () => {
    test('TourAPI 설명문: 한국관광공사 출처와 공공누리 제1유형', () => {
        const { container } = render(
            <SourceAttribution source="TOUR_API" copyrightType="Type1" infoKind="DESCRIPTION" />
        );

        expect(container).toHaveTextContent('출처: 한국관광공사 TourAPI(국문 관광정보 서비스) · 공공누리 제1유형: 출처표시');
        expect(container).not.toHaveTextContent('분류 정보');
        expect(screen.getByText('공공누리 제1유형: 출처표시').closest('a'))
            .toHaveAttribute('href', 'https://www.kogl.or.kr/info/licenseType1.do');
        expect(container.querySelector('img')).toBeNull();
    });

    test('TourAPI 소개: 공공누리 제3유형과 분류 정보로 만든 글이라는 안내', () => {
        const { container } = render(
            <SourceAttribution source="TOUR_API" copyrightType="Type3" infoKind="SUMMARY" />
        );

        expect(container).toHaveTextContent('공공누리 제3유형: 출처표시·변경금지');
        expect(container).toHaveTextContent('이 소개는 TourAPI 분류 정보(지역·관광 타입·분류)로 만든 글입니다.');
    });

    test('TourAPI 저작권 유형이 없으면 출처만 표시', () => {
        const { container } = render(<SourceAttribution source="TOUR_API" copyrightType={null} />);

        expect(container).toHaveTextContent('출처: 한국관광공사 TourAPI(국문 관광정보 서비스)');
        expect(container).not.toHaveTextContent('공공누리');
    });

    test('위키데이터 설명문: 위키백과 문서 링크, CC BY-SA 4.0, 위키데이터 CC0', () => {
        const url = 'https://ko.wikipedia.org/wiki/%EA%B3%A0%EB%8C%80_%EA%B5%90%ED%86%A0%EC%9D%98_%EC%97%AD%EC%82%AC_%EA%B8%B0%EB%85%90%EB%AC%BC';
        const { container } = render(
            <SourceAttribution source="WIKIDATA" sourceUrl={url} title="교토" infoKind="DESCRIPTION" />
        );

        expect(container).toHaveTextContent(
            '출처: 한국어 위키백과 「고대 교토의 역사 기념물」 문서 도입부 발췌 (CC BY-SA 4.0) · 위키데이터 (CC0)'
        );
        expect(screen.getByText(/한국어 위키백과 「고대 교토의 역사 기념물」/).closest('a')).toHaveAttribute('href', url);
        expect(screen.getByText('CC BY-SA 4.0').closest('a'))
            .toHaveAttribute('href', 'https://creativecommons.org/licenses/by-sa/4.0/deed.ko');
        expect(screen.getByText('위키데이터 (CC0)').closest('a'))
            .toHaveAttribute('href', 'https://creativecommons.org/publicdomain/zero/1.0/deed.ko');
    });

    test('위키데이터 소개: 위키데이터 값으로 만든 글이라고 표시하고 위키백과 문서는 관련 문서로만 링크', () => {
        const url = 'https://ko.wikipedia.org/wiki/에펠탑';
        const { container } = render(
            <SourceAttribution source="WIKIDATA" sourceUrl={url} title="에펠탑" infoKind="SUMMARY" />
        );

        expect(container).toHaveTextContent('출처: 위키데이터 (CC0) 항목 정보로 만든 소개');
        expect(container).toHaveTextContent('관련 문서: 한국어 위키백과 「에펠탑」 (CC BY-SA 4.0)');
        expect(container).not.toHaveTextContent('도입부 발췌');
    });

    test('화면에서 불러온 위키백과 글: 문서 링크와 CC BY-SA 4.0', () => {
        const { container } = render(<SourceAttribution source="WIKIPEDIA" title="구라바엔" />);

        expect(container).toHaveTextContent('출처: 한국어 위키백과 「구라바엔」 문서 발췌 (CC BY-SA 4.0)');
        expect(screen.getByText(/「구라바엔」/).closest('a'))
            .toHaveAttribute('href', 'https://ko.wikipedia.org/wiki/' + encodeURIComponent('구라바엔'));
    });

    test('기존 여행지(source 없음)는 아무것도 표시하지 않는다', () => {
        const { container } = render(<SourceAttribution source={null} copyrightType={null} />);

        expect(container).toBeEmptyDOMElement();
    });

    test('http(s)가 아닌 주소는 링크로 걸지 않는다', () => {
        render(<SourceAttribution source="WIKIDATA" sourceUrl="javascript:alert(1)" title="에펠탑" />);

        expect(screen.getByText(/한국어 위키백과 「에펠탑」/).closest('a')).toBeNull();
    });
});

describe('wikipediaTitle', () => {
    test('문서 주소에서 제목을 읽고, 읽지 못하면 fallback', () => {
        expect(wikipediaTitle('https://ko.wikipedia.org/wiki/%EC%97%90%ED%8E%A0%ED%83%91', 'x')).toBe('에펠탑');
        expect(wikipediaTitle('https://ko.wikipedia.org/wiki/%E0%A4%A', '에펠탑')).toBe('에펠탑');
        expect(wikipediaTitle(null, '에펠탑')).toBe('에펠탑');
        expect(wikipediaUrl('고대 교토')).toBe('https://ko.wikipedia.org/wiki/' + encodeURIComponent('고대_교토'));
    });
});
