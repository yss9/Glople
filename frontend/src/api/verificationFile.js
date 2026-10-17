// 대행자 검증 서류 받기(서류 주인 본인·관리자 전용).
// 공개 정적 경로(/verification-files/**)는 서버에서 막혔으므로, 인증 엔드포인트
// GET /api/users/verification-file/{userId} 로만 받는다. Authorization은 공통 인터셉터(./auth.js)가 붙인다.
// 지금은 이 서류를 보여 주는 화면이 없다(pages/admin/admin.js는 정적 문구). 관리자·본인 화면을 만들 때 이 함수를 쓴다.
import axios from "axios";

const VERIFICATION_FILE_URL = "http://localhost:8080/api/users/verification-file";

const requestVerificationFile = (userId) =>
    axios.get(`${VERIFICATION_FILE_URL}/${encodeURIComponent(userId)}`, { responseType: "blob" });

// 미리 보기용 object URL. 다 쓰면 URL.revokeObjectURL(url)로 해제한다.
export const fetchVerificationFileUrl = async (userId) => {
    const response = await requestVerificationFile(userId);
    return URL.createObjectURL(response.data);
};

// 바로 내려받기. 교차 출처라 Content-Disposition을 못 읽으면 아이디로 파일 이름을 정한다.
export const downloadVerificationFile = async (userId) => {
    const response = await requestVerificationFile(userId);
    const disposition = response.headers["content-disposition"] || "";
    const matched = disposition.match(/filename="?([^";]+)"?/);
    const url = URL.createObjectURL(response.data);
    try {
        const link = document.createElement("a");
        link.href = url;
        link.download = matched ? matched[1] : `verification-${userId}`;
        document.body.appendChild(link);
        link.click();
        link.remove();
    } finally {
        setTimeout(() => URL.revokeObjectURL(url), 0);
    }
};
