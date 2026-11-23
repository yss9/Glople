// AI 여행 도우미(/assistant): LangGraph 에이전트 서비스(/ai-api/v1)와 대화한다. 기존 챗봇(/chatbot)과는 별개다.
// - 결과 카드가 먼저 오고(1~2초), 답은 나중에 흐른다(token 미리보기 → final로 확정).
// - 일정 저장 요청이면 확인 카드가 뜨고 저장/취소/고치기로 답한다(resume). 확인 전에는 아무것도 저장되지 않는다.
// - 로그인한 사용자만 쓴다(토큰은 api/auth.js가 같은 출처 요청에만 붙임).
import React, { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { Reset } from 'styled-reset';
import { useNavigate } from 'react-router-dom';
import TopBarComponent from '../../components/TopBar/TopBar';
import AssistantCard from '../../components/AssistantCards/AssistantCards';
import PlanConfirmCard, { Button } from '../../components/AssistantCards/PlanConfirmCard';
import { getAuthToken } from '../../api/auth';
import {
    answerConfirm, cancelThread, createThread, deleteThread, getThread, listThreads, sendMessage,
} from '../../api/assistant';
import { STAGE_LABELS, chatReducer, initialChat, pendingConfirmOf, turnsFromThread } from './chatState';
import {
    BotBubble, BotColumn, Center, ChatArea, ErrorText, Examples, Header, Hint, InputForm, Layout, Main, Page,
    Sidebar, Stage, ThreadItem, UserBubble, UserRow,
} from './assistantStyle';

const EXAMPLES = [
    '바다 보이는 섬 추천해줘',
    '바나힐 사람들 후기 찾아줘',
    '제주 2박 3일 성산일출봉이랑 우도 넣어서 저장해줘',
];
const MAX_TEXT = 500;

let localSeq = 0;
const localKey = () => {
    localSeq += 1;
    return `local-${Date.now()}-${localSeq}`;
};

const newMessageId = () => (window.crypto && window.crypto.randomUUID ? window.crypto.randomUUID() : localKey());

function Turn({ turn, busy, onAnswer }) {
    return (
        <>
            <UserRow><UserBubble>{turn.user}</UserBubble></UserRow>
            <BotColumn>
                {turn.cards.map((card) => <AssistantCard key={card.cardId} card={card} />)}
                {turn.confirm && (
                    <PlanConfirmCard
                        key={turn.confirm.interruptId}
                        confirm={turn.confirm}
                        disabled={busy}
                        onAnswer={(decision, draft) => onAnswer(turn, decision, draft)}
                    />
                )}
                {turn.stage && STAGE_LABELS[turn.stage] && <Stage>{STAGE_LABELS[turn.stage]}…</Stage>}
                {turn.answer ? (
                    <>
                        <BotBubble>{turn.answer}</BotBubble>
                        {turn.replaced && <Hint>미리 보인 글 대신 확인된 답으로 바꿨어요.</Hint>}
                    </>
                ) : (
                    turn.preview && <BotBubble $preview>{turn.preview}</BotBubble>
                )}
                {turn.error && <ErrorText>{turn.error}</ErrorText>}
            </BotColumn>
        </>
    );
}

export default function AssistantPage() {
    const navigate = useNavigate();
    const loggedIn = !!getAuthToken();
    const [threads, setThreads] = useState([]);
    const [threadId, setThreadId] = useState(null);
    const [chat, dispatch] = useReducer(chatReducer, initialChat);
    const [input, setInput] = useState('');
    const [busy, setBusy] = useState(false);
    const [notice, setNotice] = useState(null);
    const abortRef = useRef(null);
    const endRef = useRef(null);

    const refreshThreads = useCallback(async () => {
        try {
            const data = await listThreads();
            setThreads(data.threads || []);
        } catch (e) {
            setNotice(e.message);
        }
    }, []);

    useEffect(() => {
        if (loggedIn) {
            refreshThreads();
        }
    }, [loggedIn, refreshThreads]);

    useEffect(() => () => {
        if (abortRef.current) {
            abortRef.current.abort(); // 화면을 떠나면 스트림을 끊는다(서버가 실행을 멈춤)
        }
    }, []);

    useEffect(() => {
        if (endRef.current && endRef.current.scrollIntoView) {
            endRef.current.scrollIntoView({ block: 'end' });
        }
    }, [chat.turns]);

    const run = async (stream) => {
        setBusy(true);
        setNotice(null);
        const controller = new AbortController();
        abortRef.current = controller;
        try {
            await stream(controller.signal, (name, data) => dispatch({ type: 'event', name, data }));
        } catch (e) {
            if (e.name !== 'AbortError') {
                dispatch({ type: 'event', name: 'error', data: { message: e.message } });
            }
        } finally {
            dispatch({ type: 'event', name: 'done', data: {} });
            abortRef.current = null;
            setBusy(false);
            refreshThreads();
        }
    };

    const openThread = async (id) => {
        if (busy || id === threadId) {
            return;
        }
        try {
            const view = await getThread(id);
            setThreadId(id);
            dispatch({ type: 'load', turns: turnsFromThread(view) });
        } catch (e) {
            setNotice(e.message);
        }
    };

    const startNew = () => {
        if (busy) {
            return;
        }
        setThreadId(null);
        dispatch({ type: 'load', turns: [] });
    };

    const removeThread = async (id) => {
        if (busy || !window.confirm('이 대화를 지울까요? 저장한 일정은 그대로 남아요.')) {
            return;
        }
        try {
            await deleteThread(id);
            if (id === threadId) {
                startNew();
            }
            refreshThreads();
        } catch (e) {
            setNotice(e.message);
        }
    };

    const send = async (event) => {
        event.preventDefault();
        const text = input.trim();
        if (!text || busy) {
            return;
        }
        let id = threadId;
        if (!id) {
            try {
                id = (await createThread()).threadId;
                setThreadId(id);
            } catch (e) {
                setNotice(e.message);
                return;
            }
        }
        setInput('');
        dispatch({ type: 'start', key: localKey(), text });
        const clientMessageId = newMessageId();
        await run((signal, onEvent) => sendMessage(id, text, onEvent, { signal, clientMessageId }));
    };

    const answer = async (turn, decision, draft) => {
        if (busy || !threadId) {
            return;
        }
        const { interruptId } = turn.confirm;
        dispatch({ type: 'activate', key: turn.key });
        await run((signal, onEvent) => answerConfirm(threadId, interruptId, decision, draft, onEvent, { signal }));
    };

    const stop = async () => {
        if (threadId) {
            try {
                await cancelThread(threadId);
            } catch (e) {
                setNotice(e.message);
            }
        }
    };

    if (!loggedIn) {
        return (
            <>
                <Reset />
                <div style={{ height: '55px' }}></div>
                <TopBarComponent />
                <Center>
                    <p>AI 여행 도우미는 로그인한 뒤에 쓸 수 있어요.<br />여행지 추천·후기 찾기·일정 저장을 도와드려요.</p>
                    <Button $primary type="button" onClick={() => navigate('/login')}>로그인하기</Button>
                </Center>
            </>
        );
    }

    const pending = pendingConfirmOf(chat.turns);
    return (
        <>
            <Reset />
            <div style={{ height: '55px' }}></div>
            <TopBarComponent />
            <Page>
                <Layout>
                    <Sidebar aria-label="내 대화">
                        <Button $primary type="button" onClick={startNew} disabled={busy}>새 대화</Button>
                        {threads.map((t) => (
                            <ThreadItem key={t.threadId} $active={t.threadId === threadId} onClick={() => openThread(t.threadId)}>
                                <span>{t.title || '새 대화'}</span>
                                <button type="button" aria-label="대화 지우기"
                                        onClick={(e) => { e.stopPropagation(); removeThread(t.threadId); }}>✕</button>
                            </ThreadItem>
                        ))}
                    </Sidebar>
                    <Main>
                        <Header>
                            <h1>AI 여행 도우미</h1>
                            <p>여행지 추천, Glople 후기·글로플러 찾기, 일정 저장을 도와드려요. 카드의 설명은 출처의 원문 그대로예요.</p>
                        </Header>
                        <ChatArea>
                            {chat.turns.length === 0 && (
                                <Examples>
                                    {EXAMPLES.map((ex) => <button key={ex} type="button" onClick={() => setInput(ex)}>{ex}</button>)}
                                </Examples>
                            )}
                            {chat.turns.map((turn) => <Turn key={turn.key} turn={turn} busy={busy} onAnswer={answer} />)}
                            {notice && <ErrorText>{notice}</ErrorText>}
                            <div ref={endRef} />
                        </ChatArea>
                        <InputForm onSubmit={send}>
                            <input
                                value={input}
                                onChange={(e) => setInput(e.target.value)}
                                placeholder={pending ? '새로 말하면 떠 있는 일정 확인은 취소돼요' : '가고 싶은 곳이나 여행 취향을 말해 주세요'}
                                maxLength={MAX_TEXT}
                                aria-label="메시지"
                            />
                            {busy ? (
                                <Button type="button" onClick={stop}>멈추기</Button>
                            ) : (
                                <Button $primary type="submit" disabled={!input.trim()}>보내기</Button>
                            )}
                        </InputForm>
                    </Main>
                </Layout>
            </Page>
        </>
    );
}
