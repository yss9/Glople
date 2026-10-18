// 문장으로 여행지 찾기: 임베딩 검색 서버(/semantic-api/v1, training/embedding/scripts/serve_search.py) 호출.
// - 같은 출처 상대 경로라서 개발 서버 프록시(setupProxy.js)가 127.0.0.1:8096으로 넘긴다.
// - 공개·읽기 전용 검색이라 로그인 토큰을 싣지 않는다(authFetch·axios 인터셉터를 쓰지 않고 fetch 그대로).
// - 검색 결과에는 이름·지역·분류·점수만 있다. 상세(설명문·출처)는 Spring 이름 찾기 API로 찾은 여행지를
//   기존 여행지 상세 화면(/recomendation/information/:placename)에 state.place로 넘겨 보여 준다.

export const SEMANTIC_API = "/semantic-api/v1";
export const DESTINATION_LOOKUP_API = "/api/destinations/lookup";
export const DETAIL_PREFIX = "/recomendation/information/";

export const MAX_QUERY_LENGTH = 200; // 서버 q 1~200자(유니코드 코드 포인트)
export const DEFAULT_LIMIT = 12;     // 서버 limit 1~30, 기본 12

export const SCOPES = [
    { value: "all", label: "전체" },
    { value: "domestic", label: "국내" },
    { value: "overseas", label: "해외" },
];

export const SOURCE_LABELS = { TOUR_API: "국내", WIKIDATA: "해외" };

// 검색 서버가 꺼져 있거나(프록시 503·504) 프록시가 아직 없을 때(개발 서버 404) 보여 주는 안내
export const SERVER_DOWN_MESSAGE = "문장 검색 서버가 꺼져 있어요. 잠시 뒤 다시 시도해 주세요.";
export const SEARCH_FAILED_MESSAGE = "검색하지 못했어요. 잠시 뒤 다시 시도해 주세요.";
export const EMPTY_QUERY_MESSAGE = "찾고 싶은 여행을 문장으로 적어 주세요.";
export const TOO_LONG_MESSAGE = `문장은 ${MAX_QUERY_LENGTH}자까지 쓸 수 있어요.`;

export class SemanticSearchError extends Error {
    constructor(message, { status = 0, down = false } = {}) {
        super(message);
        this.status = status;
        this.down = down;
    }
}

// 서버 normalize_query와 같은 생각: 앞뒤 공백을 떼고 줄바꿈·연속 공백을 하나로.
export const normalizeQuery = (text) => (text || "").normalize("NFC").replace(/\s+/g, " ").trim();

// 글자 수는 코드 포인트로 센다(이모지 하나 = 한 글자, 서버 파이썬 len과 같음).
export const queryLength = (text) => Array.from(normalizeQuery(text)).length;

// 보내기 전에 화면에서 거르는 검사. 문제가 없으면 null.
export function validateQuery(text) {
    const length = queryLength(text);
    if (length === 0) {
        return EMPTY_QUERY_MESSAGE;
    }
    if (length > MAX_QUERY_LENGTH) {
        return TOO_LONG_MESSAGE;
    }
    return null;
}

export const normalizeScope = (scope) => (SCOPES.some((s) => s.value === scope) ? scope : "all");

export function buildSearchUrl(query, { scope = "all", limit = DEFAULT_LIMIT } = {}) {
    const params = new URLSearchParams({ q: normalizeQuery(query), limit: String(limit), scope: normalizeScope(scope) });
    return `${SEMANTIC_API}/search?${params.toString()}`;
}

const readJson = async (response) => {
    const type = response.headers.get("content-type") || "";
    if (!type.includes("application/json")) {
        return null;
    }
    try {
        return await response.json();
    } catch (e) {
        return null;
    }
};

// 실패 응답 -> 화면에 보일 Error.
// 검색 서버가 직접 보낸 JSON 오류(400·404·405·500)는 path가 /semantic-api로 시작한다. 그 밖은 서버에 닿지 못한 것으로 본다.
export function errorOf(status, body) {
    const fromServer = body && typeof body === "object" && typeof body.path === "string"
        && body.path.startsWith(SEMANTIC_API);
    if (!fromServer || status === 502 || status === 503 || status === 504) {
        return new SemanticSearchError(SERVER_DOWN_MESSAGE, { status, down: true });
    }
    if (status === 400) {
        const first = Array.isArray(body.errors) && body.errors.length > 0 ? body.errors[0].message : null;
        return new SemanticSearchError(first || body.message || SEARCH_FAILED_MESSAGE, { status });
    }
    return new SemanticSearchError(SEARCH_FAILED_MESSAGE, { status });
}

// 응답 항목 중 화면에 쓸 칸만, 빠진 칸은 null. 이름이 없는 항목은 버린다.
export function toResultItems(body) {
    const items = body && Array.isArray(body.items) ? body.items : [];
    return items
        .filter((item) => item && typeof item.name === "string" && item.name.trim())
        .map((item) => ({
            source: item.source || null,
            id: item.id == null ? null : String(item.id),
            name: item.name.trim(),
            location: item.location || null,
            category: item.category || null,
            score: typeof item.score === "number" ? item.score : null,
        }));
}

export async function searchDestinations(query, { scope, limit, signal } = {}, fetchImpl = fetch) {
    const problem = validateQuery(query);
    if (problem) {
        throw new SemanticSearchError(problem, { status: 400 });
    }
    let response;
    try {
        response = await fetchImpl(buildSearchUrl(query, { scope, limit }), {
            headers: { Accept: "application/json" },
            signal,
        });
    } catch (error) {
        if (error && error.name === "AbortError") {
            throw error;
        }
        throw new SemanticSearchError(SERVER_DOWN_MESSAGE, { down: true });
    }
    const body = await readJson(response);
    if (!response.ok) {
        throw errorOf(response.status, body);
    }
    if (!body || !Array.isArray(body.items)) {
        throw new SemanticSearchError(SERVER_DOWN_MESSAGE, { status: response.status, down: true });
    }
    return toResultItems(body);
}

// Spring 이름 찾기 결과 중 검색 결과와 같은 여행지: 이름·출처·지역이 모두 같은 곳 → 이름·출처 → 이름. 없으면 null.
export function pickLookupMatch(candidates, hit) {
    if (!Array.isArray(candidates) || !hit) {
        return null;
    }
    const sameName = candidates.filter((c) => c && typeof c.name === "string" && c.name.trim() === hit.name);
    const sameSource = sameName.filter((c) => (c.source || null) === (hit.source || null));
    return sameSource.find((c) => (c.location || null) === (hit.location || null))
        || sameSource[0] || sameName[0] || null;
}

// 여행지 상세 화면(information.js)이 읽는 state.place 모양(키워드 추천 결과와 같은 칸 이름)
export function toDetailPlace(destination) {
    if (!destination) {
        return null;
    }
    return {
        destName: destination.name,
        info: destination.info,
        source: destination.source,
        copyrightType: destination.copyrightType,
        sourceUrl: destination.sourceUrl,
        license: destination.license,
        infoKind: destination.infoKind,
    };
}

export const detailPathOf = (name) => DETAIL_PREFIX + encodeURIComponent(name || "");

// 카드를 눌렀을 때 상세 화면으로 넘길 state.place. 찾지 못하거나 Spring이 꺼져 있으면 null
// (상세 화면은 이름만으로도 열리고, 그때는 위키백과 소개를 불러온다).
export async function lookupDetailPlace(hit, { signal } = {}, fetchImpl = fetch) {
    try {
        const response = await fetchImpl(`${DESTINATION_LOOKUP_API}?${new URLSearchParams({ name: hit.name })}`, {
            headers: { Accept: "application/json" },
            signal,
        });
        if (!response.ok) {
            return null;
        }
        const body = await readJson(response);
        return toDetailPlace(pickLookupMatch(body, hit));
    } catch (error) {
        if (error && error.name === "AbortError") {
            throw error;
        }
        return null;
    }
}
