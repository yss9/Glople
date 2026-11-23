import { Stomp } from '@stomp/stompjs';
import SockJS from 'sockjs-client';
import axios from 'axios';
import { authHeaders } from '../../api/auth';

const WebSocketService = (() => {
    let stompClient = null;
    let currentSubscription = null;  // 현재 구독 정보를 저장하는 변수
    let currentRoomId = '';

    // 구독 연결 함수
    const connect = (roomId, onMessageReceived) => {
        // 이전 구독이 있으면 해제
        if (currentSubscription) {
            currentSubscription.unsubscribe();
        }

        // 새로운 WebSocket 연결 설정
        const socket = new SockJS('http://localhost:8080/ws', null, { withCredentials: true });
        stompClient = Stomp.over(socket);

        // STOMP CONNECT 헤더에 쿠키의 JWT를 싣는다(토큰이 없으면 헤더 없이 연결 → 서버가 비공개 방 구독을 거부)
        stompClient.connect(authHeaders(), async () => {
            currentRoomId = roomId;

            // 새로운 채팅방에 대한 구독 설정
            currentSubscription = stompClient.subscribe(`/topic/public/${roomId}`, onMessageReceived);

            // 채팅 기록 가져오기
            try {
                console.log(`Fetching chat history for room: ${roomId}`);
                // Authorization 헤더는 공통 axios 인터셉터(src/api/auth.js)가 붙인다. 쿠키는 쓰지 않으므로 withCredentials도 필요 없음
                const response = await axios.get(`http://localhost:8080/chat/history/${roomId}`);

                if (response.status !== 200) {
                    throw new Error('Network response was not ok');
                }

                const messages = response.data;
                console.log('Chat history messages:', messages);
                messages.forEach(message => onMessageReceived({ body: JSON.stringify(message) }));
            } catch (error) {
                console.error('Failed to fetch chat history:', error);
            }
        });

        // 구독 해제 함수를 반환
        return () => {
            if (currentSubscription) {
                currentSubscription.unsubscribe();
            }
        };
    };

    // 메시지 전송 함수
    const sendMessage = (message) => {
        if (stompClient && stompClient.connected) {
            stompClient.send(`/app/chat.sendMessage/${currentRoomId}`, {}, JSON.stringify(message));
        }
    };

    // 사용자를 추가하는 함수
    const addUser = (user) => {
        if (stompClient && stompClient.connected) {
            stompClient.send(`/app/chat.addUser/${currentRoomId}`, {}, JSON.stringify(user));
        }
    };

    return {
        connect,
        sendMessage,
        addUser
    };
})();

export default WebSocketService;
