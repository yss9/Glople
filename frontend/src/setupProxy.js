const { createProxyMiddleware } = require("http-proxy-middleware");

// 환경 변수 또는 기본값을 사용하여 백엔드 URL 설정
const backendUrl = process.env.BACKEND_URL ||
    (process.env.NODE_ENV === 'production' ? "http://backend:8080" : "http://localhost:8080");

const proxyConfig = {
    target: backendUrl, // 환경에 맞는 백엔드 URL 사용
    changeOrigin: true,
    onProxyReq: (proxyReq, req, res) => {
        proxyReq.setHeader('Origin', 'http://localhost:3000');
        proxyReq.setHeader('withCredentials', 'true');
    },
    onProxyRes: (proxyRes, req, res) => {
        if (proxyRes.headers) {
            proxyRes.headers['Access-Control-Allow-Origin'] = 'http://localhost:3000';
            proxyRes.headers['Access-Control-Allow-Credentials'] = 'true';
        }
    }
};

// 여행 도우미 에이전트 서비스(agent/, FastAPI 127.0.0.1:8095). 브라우저는 같은 출처 /ai-api/v1/...로 부르고
// api/auth.js의 authFetch·axios 인터셉터가 같은 출처라서 jwt 쿠키 토큰을 붙인다. 에이전트는 CORS를 켜지 않는다.
const agentUrl = process.env.AGENT_URL || "http://127.0.0.1:8095";

// 문장으로 여행지 찾기 검색 서버(training/embedding/scripts/serve_search.py, 127.0.0.1:8096, 읽기 전용, 토큰 필요 없음).
// 질의 문장이 쿼리 문자열(q)에 실려서, 서버가 꺼져 있을 때 프록시 오류 로그에 질의가 찍히지 않게 한다.
// http-proxy-middleware는 onError 다음에 오류 로그(req.url 포함)를 찍으므로, 여기서 먼저 req.url의 쿼리 문자열을 지운다.
const semanticSearchUrl = process.env.SEMANTIC_SEARCH_URL || "http://127.0.0.1:8096";

const semanticProxyConfig = {
    target: semanticSearchUrl,
    changeOrigin: true,
    onError: (err, req, res) => {
        req.url = (req.url || "").split("?")[0];
        if (!res.headersSent) {
            res.writeHead(503, { "Content-Type": "application/json; charset=utf-8" });
        }
        res.end(JSON.stringify({
            status: 503, error: "SERVICE_UNAVAILABLE", code: "SEMANTIC_SEARCH_DOWN",
            message: "문장 검색 서버에 연결하지 못했어요.",
        }));
    },
};

module.exports = (app) => {
    app.use("/api", createProxyMiddleware(proxyConfig));
    app.use("/ai-api", createProxyMiddleware({ target: agentUrl, changeOrigin: true }));
    app.use("/semantic-api", createProxyMiddleware(semanticProxyConfig));
};
