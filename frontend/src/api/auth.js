// 백엔드 호출에 로그인 토큰(JWT)을 싣는 공통 설정.
// index.js에서 앱 시작 때 한 번 import 한다. 모든 화면이 기본 axios 인스턴스를 쓰므로 여기서 한 번만 걸면 된다.
import axios from "axios";
import Cookies from "js-cookie";

const TOKEN_COOKIE = "jwt"; // loginPage.js가 로그인 성공 때 저장하는 쿠키 이름
const LOGIN_PATH = "/login";

// 절대 주소로 부르는 백엔드. 상대 경로(/api...)는 같은 출처라 setupProxy가 백엔드로 넘긴다.
const BACKEND_ORIGINS = ["http://localhost:8080", "http://127.0.0.1:8080"];

// 토큰을 싣지 않는 경로: 로그인 실패(401)를 '토큰 만료'로 오인해 쿠키를 지우지 않게 한다.
const NO_TOKEN_PATHS = ["/api/users/login"];

// 요청 인터셉터가 쿠키 토큰을 실었는지 config에 남기는 표시.
// 401 이동은 이 표시로만 판단한다(호출부가 직접 붙인 'Bearer undefined' 같은 헤더로 판단하지 않음).
const TOKEN_SENT = "__glopleAuthTokenSent";
const INSTALLED = Symbol.for("glople.authInterceptors");

// 쿠키의 토큰. 없거나 'undefined'·'null' 같은 잘못 저장된 값이면 null.
export const getAuthToken = () => {
    const token = Cookies.get(TOKEN_COOKIE);
    if (!token || token === "undefined" || token === "null") {
        return null;
    }
    return token;
};

// 서버가 새 토큰을 내려줄 때(예: 닉네임 변경 후) 쿠키를 바꾼다. 유효기간은 로그인과 같게 1일.
export const setAuthToken = (token) => {
    if (token) {
        Cookies.set(TOKEN_COOKIE, token, { expires: 1 });
    }
};

// STOMP CONNECT 헤더 등에 쓰는 인증 헤더. 토큰이 없으면 빈 객체('Bearer undefined'를 보내지 않음).
export const authHeaders = () => {
    const token = getAuthToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
};

const parseUrl = (url) => {
    try {
        return new URL(url, window.location.origin);
    } catch (e) {
        return null;
    }
};

// 백엔드 주소인지: 같은 출처(상대 경로) 또는 localhost:8080 / 127.0.0.1:8080.
// 위키백과·restcountries·pixabay 같은 외부 주소에는 절대 토큰을 싣지 않는다(토큰 유출 방지).
export const isBackendUrl = (url) => {
    const parsed = parseUrl(url);
    if (!parsed) {
        return false;
    }
    return parsed.origin === window.location.origin || BACKEND_ORIGINS.includes(parsed.origin);
};

const shouldSendToken = (url) => {
    const parsed = parseUrl(url);
    return !!parsed && !NO_TOKEN_PATHS.includes(parsed.pathname);
};

let redirecting = false;

// 토큰을 보냈는데 401 → 만료·무효 토큰. 쿠키를 지우고 로그인 화면으로 보낸다.
// 토큰 없이 받은 401(비로그인 사용자가 보는 공개 화면의 선택적 호출)은 여기로 오지 않는다.
const handleExpiredToken = () => {
    Cookies.remove(TOKEN_COOKIE);
    if (redirecting || window.location.pathname === LOGIN_PATH) {
        return;
    }
    redirecting = true;
    alert("로그인이 만료되었습니다. 다시 로그인해 주세요.");
    window.location.assign(LOGIN_PATH);
};

// fetch용 작은 래퍼. 백엔드 주소일 때만 쿠키 기준으로 Authorization을 덮어쓰거나 지운다.
export const authFetch = (url, options = {}) => {
    if (!isBackendUrl(url)) {
        return fetch(url, options);
    }
    const headers = new Headers(options.headers || {});
    const token = shouldSendToken(url) ? getAuthToken() : null;
    if (token) {
        headers.set("Authorization", `Bearer ${token}`);
    } else {
        headers.delete("Authorization");
    }
    return fetch(url, { ...options, headers }).then((response) => {
        if (response.status === 401 && token) {
            handleExpiredToken();
        }
        return response;
    });
};

if (!axios[INSTALLED]) {
    axios[INSTALLED] = true;

    // 요청: 백엔드 호출이면 호출부가 직접 넣은 Authorization도 쿠키 기준으로 덮어쓰거나 지운다.
    axios.interceptors.request.use((config) => {
        const url = axios.getUri(config);
        if (!isBackendUrl(url)) {
            return config;
        }
        const token = shouldSendToken(url) ? getAuthToken() : null;
        if (token) {
            config.headers.set("Authorization", `Bearer ${token}`);
        } else {
            config.headers.delete("Authorization");
        }
        config[TOKEN_SENT] = !!token;
        return config;
    });

    // 응답: 401이고 이 요청에 쿠키 토큰을 실었을 때만 로그인으로 보낸다. 403은 이동하지 않고 그대로 넘긴다.
    axios.interceptors.response.use(
        (response) => response,
        (error) => {
            const status = error.response ? error.response.status : null;
            if (status === 401 && error.config && error.config[TOKEN_SENT]) {
                handleExpiredToken();
            }
            return Promise.reject(error);
        }
    );
}
