// 여행 도우미 에이전트 서비스(/ai-api/v1) 호출.
// - 같은 출처 상대 경로라서 axios 인터셉터·authFetch(api/auth.js)가 jwt 쿠키 토큰을 붙이고, 토큰을 보냈는데 401이면 로그인으로 보낸다.
// - 메시지·확인 카드 답은 SSE(text/event-stream)라서 fetch 스트림으로 읽는다(EventSource는 POST·헤더를 못 씀).
import axios from "axios";
import { authFetch } from "./auth";
import { createSseParser } from "./sse";

export const AI_API = "/ai-api/v1";

// 서버 오류 JSON({status, error, message, path, code})을 Error로. 화면에는 message만 보여 준다.
export class AssistantApiError extends Error {
    constructor(status, code, message) {
        super(message || "요청을 처리하지 못했어요.");
        this.status = status;
        this.code = code;
    }
}

const fromAxios = (error) => {
    const data = error.response && error.response.data;
    if (data && typeof data === "object") {
        return new AssistantApiError(data.status || error.response.status, data.code, data.message);
    }
    const status = error.response ? error.response.status : 0;
    const down = !status || status >= 502; // 개발 서버 프록시가 에이전트(8095)에 닿지 못하면 504 글자 응답
    return new AssistantApiError(status, "NETWORK", down ? "도우미 서버에 연결하지 못했어요." : "요청을 처리하지 못했어요.");
};

const call = async (request) => {
    try {
        const response = await request();
        return response.data;
    } catch (error) {
        throw fromAxios(error);
    }
};

export const createThread = () => call(() => axios.post(`${AI_API}/threads`));
export const listThreads = () => call(() => axios.get(`${AI_API}/threads`));
export const getThread = (threadId) => call(() => axios.get(`${AI_API}/threads/${encodeURIComponent(threadId)}`));
export const deleteThread = (threadId) => call(() => axios.delete(`${AI_API}/threads/${encodeURIComponent(threadId)}`));
export const cancelThread = (threadId) => call(() => axios.post(`${AI_API}/threads/${encodeURIComponent(threadId)}/cancel`));
export const reviewPlan = (planId) => call(() => axios.post(`${AI_API}/plans/${encodeURIComponent(planId)}/review`));

const readError = async (response) => {
    try {
        const data = await response.json();
        return new AssistantApiError(data.status || response.status, data.code, data.message);
    } catch (e) {
        return new AssistantApiError(response.status, "HTTP_" + response.status, "도우미 서버에 연결하지 못했어요.");
    }
};

// POST 한 번을 SSE로 받는다. 이벤트: meta·node·card·token·final·confirm·error·done(agent/src/glople_agent/service/app.py).
async function streamPost(path, body, onEvent, signal) {
    const response = await authFetch(`${AI_API}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify(body),
        signal,
    });
    const type = response.headers.get("content-type") || "";
    if (!response.ok || !type.startsWith("text/event-stream")) {
        throw await readError(response);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder("utf-8");
    const feed = createSseParser(onEvent);
    for (;;) {
        const { value, done } = await reader.read();
        if (done) {
            break;
        }
        feed(decoder.decode(value, { stream: true }));
    }
    feed(decoder.decode());
}

export const sendMessage = (threadId, text, onEvent, { signal, clientMessageId } = {}) =>
    streamPost(`/threads/${encodeURIComponent(threadId)}/messages`,
        clientMessageId ? { text, clientMessageId } : { text }, onEvent, signal);

// decision: 'approve' | 'reject' | 'edit'. edit이면 draft = {region, days, places, title?}
export const answerConfirm = (threadId, interruptId, decision, draft, onEvent, { signal } = {}) =>
    streamPost(`/threads/${encodeURIComponent(threadId)}/resume`,
        draft ? { interruptId, decision, draft } : { interruptId, decision }, onEvent, signal);
