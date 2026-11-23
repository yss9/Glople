import {
    DEFAULT_LIMIT, EMPTY_QUERY_MESSAGE, SEARCH_FAILED_MESSAGE, SERVER_DOWN_MESSAGE, TOO_LONG_MESSAGE,
    buildSearchUrl, detailPathOf, errorOf, lookupDetailPlace, normalizeQuery, pickLookupMatch, queryLength,
    searchDestinations, toDetailPlace, toResultItems, validateQuery,
} from './semanticSearch';

// jsdom에는 fetch·Response가 없어서 필요한 만큼만 흉내 낸다.
const jsonResponse = (status, body) => ({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => (name.toLowerCase() === 'content-type' ? 'application/json; charset=utf-8' : null) },
    json: async () => body,
});
const textResponse = (status, text) => ({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => (name.toLowerCase() === 'content-type' ? 'text/html; charset=utf-8' : null) },
    json: async () => { throw new Error(text); },
});

const HIT = { source: 'TOUR_API', id: '127480', name: '가거도', location: '전남광주통합특별시 신안군', category: '섬', score: 0.87 };

describe('질의 검사', () => {
    test('공백을 정리하고 코드 포인트로 센다', () => {
        expect(normalizeQuery('  조용한\n바닷가\t\t마을 ')).toBe('조용한 바닷가 마을');
        expect(queryLength('🌊🌊')).toBe(2);
        expect(validateQuery('   ')).toBe(EMPTY_QUERY_MESSAGE);
        expect(validateQuery('가'.repeat(200))).toBeNull();
        expect(validateQuery('🌊'.repeat(200))).toBeNull();
        expect(validateQuery('가'.repeat(201))).toBe(TOO_LONG_MESSAGE);
    });

    test('검색 주소: q·limit·scope, 모르는 범위는 all', () => {
        const url = new URL(buildSearchUrl(' 조용한  바닷가 ', { scope: 'domestic', limit: 5 }), 'http://x');
        expect(url.pathname).toBe('/semantic-api/v1/search');
        expect(url.searchParams.get('q')).toBe('조용한 바닷가');
        expect(url.searchParams.get('limit')).toBe('5');
        expect(url.searchParams.get('scope')).toBe('domestic');
        const fallback = new URL(buildSearchUrl('섬', { scope: 'mars' }), 'http://x');
        expect(fallback.searchParams.get('scope')).toBe('all');
        expect(fallback.searchParams.get('limit')).toBe(String(DEFAULT_LIMIT));
    });
});

describe('응답 다루기', () => {
    test('결과 항목은 화면에 쓸 칸만, 이름 없는 항목은 버린다', () => {
        const items = toResultItems({
            items: [{ ...HIT, description: '원문', extra: 1 }, { source: 'WIKIDATA', id: 'Q1', name: '' }, null],
        });
        expect(items).toEqual([HIT]);
        expect(toResultItems({})).toEqual([]);
    });

    test('오류: 검색 서버 400은 첫 오류 문구, 서버에 닿지 못하면 정해진 안내', () => {
        const bad = errorOf(400, {
            status: 400, path: '/semantic-api/v1/search', message: '검색 값이 올바르지 않습니다.',
            errors: [{ field: 'limit', code: 'out_of_range', message: 'limit는 1~30 사이 정수여야 합니다.' }],
        });
        expect(bad.message).toBe('limit는 1~30 사이 정수여야 합니다.');
        expect(bad.down).toBe(false);
        expect(errorOf(503, { status: 503, code: 'SEMANTIC_SEARCH_DOWN' }).message).toBe(SERVER_DOWN_MESSAGE);
        expect(errorOf(404, null).down).toBe(true); // 프록시가 없는 개발 서버의 'Cannot GET'
        expect(errorOf(500, { status: 500, path: '/semantic-api/v1/search' }).message).toBe(SEARCH_FAILED_MESSAGE);
    });

    test('searchDestinations: 성공·서버 꺼짐·네트워크 오류·화면 검사', async () => {
        const calls = [];
        const ok = async (url) => {
            calls.push(url);
            return jsonResponse(200, { scope: 'all', limit: 12, count: 1, items: [HIT] });
        };
        await expect(searchDestinations('조용한 바닷가 마을', { scope: 'overseas' }, ok)).resolves.toEqual([HIT]);
        expect(calls[0]).toContain('scope=overseas');

        await expect(searchDestinations('섬', {}, async () => textResponse(404, 'Cannot GET')))
            .rejects.toMatchObject({ message: SERVER_DOWN_MESSAGE, down: true });
        await expect(searchDestinations('섬', {}, async () => textResponse(200, '<html>')))
            .rejects.toMatchObject({ message: SERVER_DOWN_MESSAGE });
        await expect(searchDestinations('섬', {}, async () => { throw new TypeError('Failed to fetch'); }))
            .rejects.toMatchObject({ message: SERVER_DOWN_MESSAGE });

        const never = jest.fn();
        await expect(searchDestinations('  ', {}, never)).rejects.toMatchObject({ message: EMPTY_QUERY_MESSAGE });
        expect(never).not.toHaveBeenCalled();
    });

    test('취소(AbortError)는 그대로 던진다', async () => {
        const abort = Object.assign(new Error('aborted'), { name: 'AbortError' });
        await expect(searchDestinations('섬', {}, async () => { throw abort; })).rejects.toBe(abort);
    });
});

describe('상세 화면으로 넘기기', () => {
    const lookup = [
        { id: 7, name: '가거도', source: 'TOUR_API', location: '다른 곳', info: '다른 글' },
        { id: 1, name: '가거도', source: 'TOUR_API', location: '전남광주통합특별시 신안군', info: '가거도(소흑산도)는 …',
          infoKind: 'DESCRIPTION', copyrightType: 'Type1', license: null, sourceUrl: null },
        { id: 9, name: '가거도 등대', source: 'TOUR_API', location: '전남광주통합특별시 신안군' },
    ];

    test('이름·출처·지역이 같은 곳을 먼저 고른다', () => {
        expect(pickLookupMatch(lookup, HIT).id).toBe(1);
        expect(pickLookupMatch(lookup, { ...HIT, location: '모르는 곳' }).id).toBe(7);
        expect(pickLookupMatch(lookup, { ...HIT, source: 'WIKIDATA' }).id).toBe(7);
        expect(pickLookupMatch(lookup, { ...HIT, name: '없는 곳' })).toBeNull();
        expect(pickLookupMatch(null, HIT)).toBeNull();
    });

    test('상세 화면 state.place 모양과 주소', () => {
        expect(toDetailPlace(lookup[1])).toEqual({
            destName: '가거도', info: '가거도(소흑산도)는 …', source: 'TOUR_API', copyrightType: 'Type1',
            sourceUrl: null, license: null, infoKind: 'DESCRIPTION',
        });
        expect(toDetailPlace(null)).toBeNull();
        expect(detailPathOf('백련사 (서울)')).toBe('/recomendation/information/%EB%B0%B1%EB%A0%A8%EC%82%AC%20(%EC%84%9C%EC%9A%B8)');
    });

    test('lookupDetailPlace: Spring이 꺼져 있거나 못 찾으면 null', async () => {
        const urls = [];
        const ok = async (url) => {
            urls.push(url);
            return jsonResponse(200, lookup);
        };
        await expect(lookupDetailPlace(HIT, {}, ok)).resolves.toMatchObject({ destName: '가거도', copyrightType: 'Type1' });
        expect(urls[0]).toBe('/api/destinations/lookup?name=%EA%B0%80%EA%B1%B0%EB%8F%84');
        await expect(lookupDetailPlace(HIT, {}, async () => jsonResponse(200, []))).resolves.toBeNull();
        await expect(lookupDetailPlace(HIT, {}, async () => textResponse(504, 'proxy'))).resolves.toBeNull();
        await expect(lookupDetailPlace(HIT, {}, async () => { throw new TypeError('down'); })).resolves.toBeNull();
    });
});
