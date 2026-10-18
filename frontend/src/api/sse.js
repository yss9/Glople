// SSE(text/event-stream) 읽기. axios를 import하지 않는 작은 모듈이라 Jest에서 바로 시험한다(api/sse.test.js).

// SSE 조각을 이어 붙여 빈 줄로 끝난 이벤트마다 onEvent(이름, JSON 값)를 부른다. ': ping' 같은 주석 줄은 버린다.
export function createSseParser(onEvent) {
    let buffer = "";
    return (chunk) => {
        buffer = (buffer + chunk).replace(/\r\n?/g, "\n");
        let end = buffer.indexOf("\n\n");
        while (end >= 0) {
            const block = buffer.slice(0, end);
            buffer = buffer.slice(end + 2);
            let event = "message";
            const data = [];
            block.split("\n").forEach((line) => {
                if (!line || line.startsWith(":")) {
                    return;
                }
                const colon = line.indexOf(":");
                const field = colon < 0 ? line : line.slice(0, colon);
                let value = colon < 0 ? "" : line.slice(colon + 1);
                if (value.startsWith(" ")) {
                    value = value.slice(1);
                }
                if (field === "event") {
                    event = value;
                } else if (field === "data") {
                    data.push(value);
                }
            });
            if (data.length) {
                let parsed = null;
                try {
                    parsed = JSON.parse(data.join("\n"));
                } catch (e) {
                    parsed = null; // 읽지 못한 이벤트는 버린다
                }
                if (parsed !== null) {
                    onEvent(event, parsed);
                }
            }
            end = buffer.indexOf("\n\n");
        }
    };
}
