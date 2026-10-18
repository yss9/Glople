import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import AssistantCard from './AssistantCards';
import PlanConfirmCard, { draftToForm, formToDraft } from './PlanConfirmCard';

const LONG_TYPE3 = 'TYPE3-SENTINEL 가거도 설명문 자리 표시자입니다. ' + '원문을 바꾸지 않고 그대로 보여 줘야 하는 글입니다. '.repeat(6);

const keywordCard = {
    cardId: 'recommend_by_keywords:c1',
    turnId: 'turn-1',
    tool: 'recommend_by_keywords',
    kind: 'destination_list',
    status: 'ok',
    data: {
        items: [{
            id: 101, name: '가거도', country: '대한민국', location: '전라남도 신안군', text: LONG_TYPE3,
            textKind: 'DESCRIPTION', textPolicy: 'VERBATIM_ONLY', source: 'TOUR_API', copyrightType: 'Type3',
            sourceUrl: null, imageUrl: 'http://tong.visitkorea.or.kr/x.jpg',
            detailPath: '/recomendation/information/%EA%B0%80%EA%B1%B0%EB%8F%84',
        }],
        notes: ['목록에 없어 반영하지 못한 키워드: 맛집'],
    },
};

const ROUTER_FUTURE = { v7_startTransition: true, v7_relativeSplatPath: true };
const renderCard = (card) => render(<MemoryRouter future={ROUTER_FUTURE}><AssistantCard card={card} /></MemoryRouter>);

describe('AssistantCard', () => {
    test('공공누리 제3유형 설명문은 접혀 있어도 DOM에 전문이 있고 출처·유형을 표시한다', () => {
        const { container } = renderCard(keywordCard);
        expect(container).toHaveTextContent(LONG_TYPE3.trim());
        expect(container).toHaveTextContent('출처: 한국관광공사 TourAPI(국문 관광정보 서비스) · 공공누리 제3유형: 출처표시·변경금지');
        expect(screen.getByText('더 보기')).toBeInTheDocument();
        expect(screen.getByRole('img')).toHaveStyle('object-fit: contain');
        expect(container).toHaveTextContent('목록에 없어 반영하지 못한 키워드: 맛집');
        expect(container.innerHTML).not.toContain('<script');
    });

    test('준비 중 결과는 이유 문장을 보여 준다', () => {
        const { container } = renderCard({
            cardId: 'd', turnId: 't', tool: 'get_destination_detail', kind: 'destination_detail',
            status: 'pending_feature', data: { items: [], notes: ['여행지 상세 정보는 아직 준비 중이에요.'], message: '여행지 상세 정보는 아직 준비 중이에요.' },
        });
        expect(container).toHaveTextContent('준비 중');
        expect(screen.getAllByText('여행지 상세 정보는 아직 준비 중이에요.')).toHaveLength(1);
    });

    test('저장한 일정 카드는 내 여행 일정으로 링크한다', () => {
        renderCard({
            cardId: 'plan:draft:x', turnId: 't', tool: 'create_travel_plan', kind: 'plan_saved', status: 'ok',
            data: { plan_id: 42, saved: { title: '제주 2박 3일' } },
        });
        expect(screen.getByText('내 여행 일정에서 보기').closest('a')).toHaveAttribute('href', '/my/plans/42');
    });
});

const confirm = {
    interruptId: 'i1',
    message: "'제주 2박 3일' 일정을 이대로 저장할까요?",
    editsLeft: 3,
    problems: [],
    draft: {
        title: '제주 2박 3일', region: '제주', days: 3,
        items: [
            { position: 1, dayNo: 1, name: '성산일출봉', source: 'USER', selected: true },
            { position: 2, dayNo: 2, name: '한라산', source: 'MODEL_SUGGESTED', selected: false },
            { position: 3, dayNo: 3, name: '우도', source: 'USER', selected: true },
        ],
    },
};

describe('PlanConfirmCard', () => {
    test('저장·취소 버튼이 결정을 넘긴다', () => {
        const onAnswer = jest.fn();
        render(<PlanConfirmCard confirm={confirm} disabled={false} onAnswer={onAnswer} />);
        expect(screen.getByText('1일차')).toBeInTheDocument();
        fireEvent.click(screen.getByText('저장'));
        fireEvent.click(screen.getByText('취소'));
        expect(onAnswer.mock.calls).toEqual([['approve'], ['reject']]);
    });

    test('고치기: 기본 장소는 선택된 것만, 제안은 넣어야 들어간다', () => {
        const form = draftToForm(confirm.draft);
        expect(form.places).toEqual(['성산일출봉', '우도']);
        expect(form.suggestions).toEqual(['한라산']);
        expect(formToDraft({ ...form, days: '2', title: '  ' })).toEqual({ region: '제주', days: 2, places: ['성산일출봉', '우도'] });

        const onAnswer = jest.fn();
        render(<PlanConfirmCard confirm={confirm} disabled={false} onAnswer={onAnswer} />);
        fireEvent.click(screen.getByText(/고치기/));
        fireEvent.click(screen.getByText('+ 한라산'));
        fireEvent.click(screen.getByText('고친 일정 보내기'));
        expect(onAnswer).toHaveBeenCalledWith('edit', { region: '제주', days: 3, places: ['성산일출봉', '우도', '한라산'] });
    });
});
