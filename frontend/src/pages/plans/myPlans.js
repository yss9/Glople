// 내 여행 일정 목록(/my/plans): Spring GET /api/travel-plans(내 일정만, 최근 저장 순). AI 도우미가 확인 뒤 저장한 일정이 여기 모인다.
import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Reset } from 'styled-reset';
import { useNavigate } from 'react-router-dom';
import TopBarComponent from '../../components/TopBar/TopBar';
import { Button } from '../../components/AssistantCards/PlanConfirmCard';
import { getAuthToken } from '../../api/auth';
import { formatDate } from './planUtils';
import { ErrorText, List, Message, PlanCard, Sub, TitleRow, Wrap } from './planStyle';

export function LoginRequired({ what }) {
    const navigate = useNavigate();
    return (
        <Message>
            <p>{what}은(는) 로그인한 뒤에 볼 수 있어요.</p>
            <Button $primary type="button" onClick={() => navigate('/login')}>로그인하기</Button>
        </Message>
    );
}

export default function MyPlans() {
    const navigate = useNavigate();
    const loggedIn = !!getAuthToken();
    const [plans, setPlans] = useState(null);
    const [error, setError] = useState(null);

    useEffect(() => {
        if (!loggedIn) {
            return undefined;
        }
        let alive = true;
        axios.get('/api/travel-plans')
            .then((res) => alive && setPlans(Array.isArray(res.data) ? res.data : []))
            .catch(() => alive && setError('일정을 불러오지 못했어요. 잠시 뒤 다시 시도해 주세요.'));
        return () => {
            alive = false;
        };
    }, [loggedIn]);

    return (
        <>
            <Reset />
            <div style={{ height: '55px' }}></div>
            <TopBarComponent />
            <Wrap>
                {!loggedIn ? <LoginRequired what="내 여행 일정" /> : (
                    <>
                        <TitleRow>
                            <div>
                                <h1>내 여행 일정</h1>
                                <Sub>AI 도우미와 함께 저장한 일정이에요. 일정을 누르면 일차별로 보고 AI 검토를 받을 수 있어요.</Sub>
                            </div>
                            <Button type="button" onClick={() => navigate('/assistant')}>AI 도우미에게 일정 만들기</Button>
                        </TitleRow>
                        {error && <ErrorText>{error}</ErrorText>}
                        {plans && plans.length === 0 && (
                            <Message><p>아직 저장한 일정이 없어요. AI 도우미에게 "제주 2박 3일 일정 저장해줘"처럼 말해 보세요.</p></Message>
                        )}
                        {plans && plans.length > 0 && (
                            <List>
                                {plans.map((plan) => (
                                    <PlanCard key={plan.id} type="button" onClick={() => navigate(`/my/plans/${plan.id}`)}>
                                        <strong>{plan.title}</strong>
                                        <Sub>{plan.region} · {plan.days}일 · 장소 {plan.placeCount}곳</Sub>
                                        <Sub>{formatDate(plan.createdAt)} 저장</Sub>
                                    </PlanCard>
                                ))}
                            </List>
                        )}
                    </>
                )}
            </Wrap>
        </>
    );
}
