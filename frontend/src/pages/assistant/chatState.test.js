import { chatReducer, initialChat, pendingConfirmOf, turnsFromThread } from './chatState';

const ev = (state, name, data) => chatReducer(state, { type: 'event', name, data });

describe('chatReducer', () => {
    test('메시지 한 턴: 카드 먼저, 미리보기 뒤 확정 답', () => {
        let s = chatReducer(initialChat, { type: 'start', key: 'k1', text: '바나힐 후기' });
        s = ev(s, 'meta', { threadId: 't', turnId: 'turn-1' });
        s = ev(s, 'node', { node: 'tools' });
        expect(s.turns[0].stage).toBe('tools');
        s = ev(s, 'card', { card: { cardId: 'c1', turnId: 'turn-1', tool: 'search_reviews', status: 'ok', data: {} } });
        s = ev(s, 'token', { text: '후기 ' });
        s = ev(s, 'token', { text: '3개' });
        expect(s.turns[0].preview).toBe('후기 3개');
        s = ev(s, 'final', { text: '후기 3개를 찾았어요.', kind: 'llm', replaced: false });
        s = ev(s, 'done', { reason: 'final' });
        const [turn] = s.turns;
        expect(turn).toMatchObject({ turnId: 'turn-1', answer: '후기 3개를 찾았어요.', kind: 'llm', preview: '', stage: null });
        expect(turn.cards.map((c) => c.cardId)).toEqual(['c1']);
        expect(s.activeKey).toBeNull();
    });

    test('확인 카드 → 재개: 같은 턴에 저장 카드가 같은 cardId로 바뀐다', () => {
        let s = chatReducer(initialChat, { type: 'start', key: 'k1', text: '제주 일정 저장해줘' });
        s = ev(s, 'meta', { turnId: 'turn-1' });
        s = ev(s, 'card', { card: { cardId: 'plan:draft:x', turnId: 'turn-1', tool: 'create_travel_plan', kind: 'plan_confirm' } });
        s = ev(s, 'confirm', { interruptId: 'i1', draft: { items: [] } });
        s = ev(s, 'done', { reason: 'confirm' });
        expect(pendingConfirmOf(s.turns).confirm.interruptId).toBe('i1');
        s = chatReducer(s, { type: 'activate', key: 'k1' });
        expect(pendingConfirmOf(s.turns)).toBeNull();
        s = ev(s, 'card', { card: { cardId: 'plan:draft:x', turnId: 'turn-1', tool: 'create_travel_plan', kind: 'plan_saved' } });
        s = ev(s, 'final', { text: "'제주' 일정을 저장했어요.", kind: 'plan' });
        expect(s.turns[0].cards).toHaveLength(1);
        expect(s.turns[0].cards[0].kind).toBe('plan_saved');
    });

    test('새 메시지는 떠 있던 확인 카드를 내리고, 취소 카드는 원래 턴에 들어간다', () => {
        let s = chatReducer(initialChat, { type: 'start', key: 'k1', text: '일정 저장' });
        s = ev(s, 'meta', { turnId: 'turn-1' });
        s = ev(s, 'confirm', { interruptId: 'i1', draft: {} });
        s = chatReducer(s, { type: 'start', key: 'k2', text: '안녕' });
        expect(pendingConfirmOf(s.turns)).toBeNull();
        s = ev(s, 'meta', { turnId: 'turn-2' });
        s = ev(s, 'card', { card: { cardId: 'plan:draft:x', turnId: 'turn-1', tool: 'create_travel_plan', status: 'error' } });
        expect(s.turns[0].cards).toHaveLength(1);
        expect(s.turns[1].cards).toHaveLength(0);
    });

    test('오류 이벤트는 그 턴에 문장으로 남는다', () => {
        let s = chatReducer(initialChat, { type: 'start', key: 'k1', text: '안녕' });
        s = ev(s, 'error', { code: 'MODEL_BUSY', message: '지금은 다른 답을 만드는 중이에요.' });
        expect(s.turns[0].error).toBe('지금은 다른 답을 만드는 중이에요.');
    });
});

test('turnsFromThread: 복원한 턴과 대기 중인 확인 카드', () => {
    const turns = turnsFromThread({
        turns: [
            { turnId: 'turn-1', user: '안녕', cards: [], answer: '안녕하세요!', kind: 'direct' },
            { turnId: 'turn-2', user: '일정 저장', cards: [], answer: null },
        ],
        pendingConfirm: { interruptId: 'i9', draft: {} },
    });
    expect(turns.map((t) => t.key)).toEqual(['turn-1', 'turn-2']);
    expect(turns[1].confirm.interruptId).toBe('i9');
    expect(turns[0].confirm).toBeNull();
});
