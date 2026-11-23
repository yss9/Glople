
import React, { useState, useEffect } from 'react';
import { Reset } from "styled-reset";
import styled from '@emotion/styled';
import Cookies from 'js-cookie';
import { Client } from '@stomp/stompjs';
import SockJS from 'sockjs-client';
import TopBarComponent from "../../components/TopBar/TopBar";
import sendButton from "../../public/Img/sendButton.png";

const Container = styled.div`
    display: flex;
    flex-direction: column;
    height: 100vh;
`;

const GlloImg = styled.div`
    width: 73px;
    height: 65px;
    display: inline-block;
    position: relative;
    right: 80px;
    top: 60px;
    background-image: url("/gllo.png");

`

const MainContentArea = styled.div`
    display: flex;
    flex: 1;
    overflow: hidden;
`;

const Sidebar = styled.div`
    width: 160px;
    background-color: white;
    color: white;
    display: flex;
    flex-direction: column;
    padding: 20px;
    font-family: Regular;
`;

const MainContent = styled.div`
    flex: 1;
    display: flex;
    flex-direction: column;
    background-color: white;
    overflow: hidden;
    font-family: Regular;
    border-radius: 20px;
    height: 95%;
    
`;


const Header = styled.div`
    padding-left: 100px;
    margin-bottom: 30px;
    border-radius: 20px;
`;

const ChatBox = styled.div`
    border-top-left-radius: 20px;
    border-top-right-radius: 20px;
    flex: 1;
    padding: 20px;
    overflow-y: auto;
    background-color: #F4F6F8;
`;

const ChatMessage = styled.div`
    display: flex;
    justify-content: ${(props) => (props.isUser ? 'flex-end' : 'flex-start')};
    margin-bottom: 10px;
`;

const MessageBubble = styled.div`
    font-size: 20px;
    line-height: 1.5;
    max-width: 60%;
    padding: 10px;
    border-radius: 10px;
    background-color: ${(props) => (props.isUser ? '#e5e5ea' : '#4a5fc1')};
    color: ${(props) => (props.isUser ? 'black' : 'white')};
`;

const LoadingMessage = styled.div`
    color: #4a5fc1;
    font-size: 50px;
    margin-top: 10px;
    animation: blink 1.5s steps(5, start) infinite;
    text-align: center;

    @keyframes blink {
        0% { opacity: 0.2; }
        20% { opacity: 1; }
        100% { opacity: 0.2; }
    }
`;

const ChatInputContainer = styled.div`
    text-align: center;
    padding-bottom: 50px;
    background-color: #F4F6F8;
`;

const ChatInput = styled.input`
    padding: 10px 10px 10px 30px;
    font-size: 15px;
    width: 80%;
    height: 40px;
    border: 1px solid white;
    border-radius: 10px;
    box-shadow: 0 0 15px darkgrey;
    border-right: none;
    outline: none;
`;

const SendButton = styled.div`
    display: inline-block;
    position: relative;
    top: 15px;
    right: 70px;
    width: 40px;
    height: 40px;
    background-image: url("${sendButton}");
    background-size: cover;
    color: white;
    border: none;
    cursor: pointer;
`;

const Chatbot = () => {
    const [messages, setMessages] = useState([]);
    const [input, setInput] = useState('');
    const [username, setUsername] = useState('');
    const [connected, setConnected] = useState(false);
    const [client, setClient] = useState(null);
    const [loading, setLoading] = useState(false); // 로딩 상태 추가

    useEffect(() => {
        const token = Cookies.get('jwt');
        if (token) {
            try {
                const parts = token.split('.');
                if (parts.length !== 3) {
                    throw new Error('Invalid JWT token format');
                }
                const userPayload = JSON.parse(atob(parts[1]));
                const extractedUsername = userPayload.name;
                setUsername(extractedUsername);
                handleAddUser(extractedUsername, token);
            } catch (error) {
                console.error('Invalid token:', error);
            }
        } else {
            console.error('No JWT token found in cookies');
        }
    }, []);

    const handleAddUser = (extractedUsername, token) => {
        setConnected(true);
        const socket = new SockJS('http://localhost:8080/ws');
        const stompClient = new Client({
            webSocketFactory: () => socket,
            connectHeaders: {
                Authorization: `Bearer ${token}`
            },
            reconnectDelay: 5000,
            onConnect: () => {
                // 챗봇 답은 보낸 사람에게만 온다(서버 @SendToUser("/topic/chatbot")). 공용 /topic/public을 구독하면 남의 질문·답이 보였음
                stompClient.subscribe('/user/topic/chatbot', (message) => {
                    setLoading(false); // 응답이 오면 로딩을 해제합니다
                    const msg = JSON.parse(message.body);
                    setMessages((prevMessages) => [...prevMessages, msg]);
                });

                stompClient.publish({
                    destination: "/app/chat.addUser",
                    // 토큰은 본문에 싣지 않는다(서버가 받은 메시지를 그대로 방송하면 남에게 JWT가 샘). 인증은 CONNECT 헤더로만
                    body: JSON.stringify({ sender: extractedUsername, type: 'JOIN' })
                });
            }
        });

        stompClient.activate();
        setClient(stompClient);

        return () => {
            if (stompClient) {
                stompClient.deactivate();
            }
        };
    };

    const sendMessage = () => {
        if (client && input.trim() !== '') {
            const message = {
                sender: username,
                content: input,
                type: 'CHAT'
            };

            setMessages((prevMessages) => [...prevMessages, message]);
            setLoading(true); // 메시지 전송 후 로딩 상태 활성화

            client.publish({
                destination: "/app/chat.sendMessage",
                body: JSON.stringify(message)
            });
            setInput('');
        }
    };

    return (
        <>
            <Reset />
            <Container>
                <div style={{ height: '45px' }}></div>
                <TopBarComponent />
                <MainContentArea>
                    <Sidebar>
                    </Sidebar>
                    <MainContent>
                        {!connected ? (
                            <Header>로딩 중...</Header>
                        ) : (
                            <>
                                <Header>
                                    <GlloImg></GlloImg>
                                    <p>
                                        챗봇 글로에게 궁금한 점을 물어보세요.<br/>
                                        <br/>
                                        무엇을 도와드릴까요?
                                    </p>
                                </Header>
                                <ChatBox>
                                    {messages.map((msg, index) => (
                                        <ChatMessage key={index} isUser={msg.sender === username}>
                                            <MessageBubble isUser={msg.sender === username}>
                                                {msg.content}
                                            </MessageBubble>
                                        </ChatMessage>
                                    ))}{loading && <LoadingMessage>메시지 입력 중...</LoadingMessage>}

                                </ChatBox>
                                <ChatInputContainer>
                                    <ChatInput
                                        type="text"
                                        value={input}
                                        onChange={(e) => setInput(e.target.value)}
                                        onKeyPress={(e) => e.key === 'Enter' && sendMessage()}
                                        placeholder="챗봇에게 질문을 입력해주세요!"
                                    />
                                    <SendButton onClick={sendMessage}></SendButton>
                                </ChatInputContainer>
                            </>
                        )}
                    </MainContent>
                    <Sidebar>
                    </Sidebar>
                </MainContentArea>
            </Container>
        </>
    );
};

export default Chatbot;
