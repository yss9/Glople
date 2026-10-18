// 여행 일정 상세(/my/plans/:planId): 일차별 카드, 삭제, 'AI 일정 검토'(에이전트 POST /ai-api/v1/plans/{id}/review).
// - 일정은 Spring GET /api/travel-plans/{id}(본인 것만, 남의 것·없는 것은 403 → '찾을 수 없어요').
// - 검토의 일차별 메모는 코드가 일정에서 센 값이고, 글은 AI가 일정 안의 이름·숫자만으로 쓴 것(가드에 걸리면 코드가 만든 글).
import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Reset } from 'styled-reset';
import { useNavigate, useParams } from 'react-router-dom';
import TopBarComponent from '../../components/TopBar/TopBar';
import { Button } from '../../components/AssistantCards/PlanConfirmCard';
import { getAuthToken } from '../../api/auth';
import { reviewPlan } from '../../api/assistant';
import { LoginRequired } from './myPlans';
import { SOURCE_LABELS, formatDate, groupByDay } from './planUtils';
import {
    Actions, DayCard, Days, ErrorText, Message, Note, ReviewBox, Sub, Tag, TitleRow, Wrap,
} from './planStyle';

function DayNotes({ notes }) {
    return (notes || []).map((n, i) => (
        <Note key={i} $warn={n.code !== 'ok'}>{n.message}</Note>
    ));
}

export default function PlanDetail() {
    const { planId } = useParams();
    const navigate = useNavigate();
    const loggedIn = !!getAuthToken();
    const [plan, setPlan] = useState(null);
    const [missing, setMissing] = useState(false);
    const [error, setError] = useState(null);
    const [review, setReview] = useState(null);
    const [reviewing, setReviewing] = useState(false);
    const [deleting, setDeleting] = useState(false);

    useEffect(() => {
        if (!loggedIn || !/^\d+$/.test(planId || '')) {
            setMissing(!!loggedIn);
            return undefined;
        }
        let alive = true;
        setPlan(null);
        setReview(null);
        axios.get(`/api/travel-plans/${planId}`)
            .then((res) => alive && setPlan(res.data))
            .catch((e) => {
                if (!alive) {
                    return;
                }
                if (e.response && (e.response.status === 403 || e.response.status === 404)) {
                    setMissing(true);
                } else {
                    setError('일정을 불러오지 못했어요. 잠시 뒤 다시 시도해 주세요.');
                }
            });
        return () => {
            alive = false;
        };
    }, [loggedIn, planId]);

    const remove = async () => {
        if (!window.confirm(`'${plan.title}' 일정을 지울까요? 되돌릴 수 없어요.`)) {
            return;
        }
        setDeleting(true);
        try {
            await axios.delete(`/api/travel-plans/${planId}`);
            navigate('/my/plans');
        } catch (e) {
            setError('일정을 지우지 못했어요.');
            setDeleting(false);
        }
    };

    const askReview = async () => {
        setReviewing(true);
        setError(null);
        try {
            setReview(await reviewPlan(planId));
        } catch (e) {
            setError(e.message);
        } finally {
            setReviewing(false);
        }
    };

    const body = () => {
        if (!loggedIn) {
            return <LoginRequired what="여행 일정" />;
        }
        if (missing) {
            return (
                <Message>
                    <p>일정을 찾을 수 없어요.</p>
                    <Button type="button" onClick={() => navigate('/my/plans')}>내 여행 일정으로</Button>
                </Message>
            );
        }
        if (!plan) {
            return error ? <ErrorText>{error}</ErrorText> : <Message><p>불러오는 중이에요…</p></Message>;
        }
        const { days, unassigned } = groupByDay(plan);
        const notesByDay = new Map(((review && review.dayNotes) || []).map((d) => [d.dayNo, d.notes]));
        return (
            <>
                <TitleRow>
                    <div>
                        <h1>{plan.title}</h1>
                        <Sub>{plan.region} · {plan.days}일 · {formatDate(plan.createdAt)} 저장</Sub>
                    </div>
                    <Actions>
                        <Button $primary type="button" onClick={askReview} disabled={reviewing}>
                            {reviewing ? '검토하는 중…' : 'AI 일정 검토'}
                        </Button>
                        <Button type="button" onClick={remove} disabled={deleting}>삭제</Button>
                    </Actions>
                </TitleRow>
                {error && <ErrorText>{error}</ErrorText>}
                {review && (
                    <ReviewBox aria-label="AI 일정 검토">
                        <h2>{review.kind === 'llm' ? 'AI 일정 검토' : '일정 검토'}</h2>
                        <p>{review.text}</p>
                        {review.kind !== 'llm' && <Sub>AI 답 대신 일정에서 센 결과로 만든 검토예요.</Sub>}
                        {(review.planNotes || []).filter((n) => n.code !== 'balanced').map((n, i) => (
                            <Note key={i} $warn>{n.message}</Note>
                        ))}
                    </ReviewBox>
                )}
                <Days>
                    {days.map((day) => (
                        <DayCard key={day.dayNo} aria-label={`${day.dayNo}일차`}>
                            <h2>{day.dayNo}일차</h2>
                            {day.places.length ? (
                                <ol>
                                    {day.places.map((p) => (
                                        <li key={p.position}>{p.name}<Tag>{SOURCE_LABELS[p.source] || ''}</Tag></li>
                                    ))}
                                </ol>
                            ) : <Sub>장소가 없어요.</Sub>}
                            <DayNotes notes={notesByDay.get(day.dayNo)} />
                        </DayCard>
                    ))}
                    {unassigned.length > 0 && (
                        <DayCard aria-label="일차 미정">
                            <h2>일차 미정</h2>
                            <ol>{unassigned.map((p) => <li key={p.position}>{p.name}</li>)}</ol>
                        </DayCard>
                    )}
                </Days>
            </>
        );
    };

    return (
        <>
            <Reset />
            <div style={{ height: '55px' }}></div>
            <TopBarComponent />
            <Wrap>{body()}</Wrap>
        </>
    );
}
