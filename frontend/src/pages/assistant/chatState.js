// 도우미 대화 화면 상태(useReducer). 서버 SSE 이벤트(meta·node·card·token·final·confirm·error·done)를 턴에 쌓는다.
// 턴: {key, turnId, user, cards, answer, kind, replaced, preview, stage, confirm, error}
// - 카드는 cardId로 덮어쓴다(일정 카드가 취소·저장으로 바뀌면 같은 자리에서 바뀜). card.turnId가 가리키는 턴에 넣는다.
// - 확인 카드(confirm)는 한 번에 하나만 떠 있다. 새 메시지를 보내면 서버가 이전 카드를 취소하므로 화면에서도 내린다.

export const initialChat = { turns: [], activeKey: null };

export const STAGE_LABELS = {
    start_turn: '요청을 읽고 있어요',
    route: '무엇을 할지 고르고 있어요',
    validate: '요청을 확인하고 있어요',
    tools: '찾아보고 있어요',
    collect: '결과를 정리하고 있어요',
    answer_llm: '답을 쓰고 있어요',
    // M7 '도구 결과 뒤 루프'(GLOPLE_AFTER_TOOLS=loop): 결과를 보고 답하거나 한 번 더 찾아본다
    route_after_tools: '결과를 보고 다음 할 일을 고르고 있어요',
    validate_after_tools: '더 찾아볼 내용을 확인하고 있어요',
    answer_template: '답을 쓰고 있어요',
    clarify: '되물을 말을 고르고 있어요',
    direct_reply: '답을 쓰고 있어요',
    fallback: '답을 쓰고 있어요',
    plan_prepare: '일정 초안을 만들고 있어요',
    review: '확인 카드를 띄우고 있어요',
    save_plan: '일정을 저장하고 있어요',
    finish: '마무리하고 있어요',
};

const blankTurn = (key, user) => ({
    key, turnId: null, user, cards: [], answer: null, kind: null, replaced: false, preview: '', stage: null,
    confirm: null, error: null,
});

export function turnsFromThread(view) {
    const turns = ((view && view.turns) || []).map((t) => ({
        ...blankTurn(t.turnId, t.user),
        turnId: t.turnId,
        cards: t.cards || [],
        answer: t.answer,
        kind: t.kind || null,
    }));
    if (view && view.pendingConfirm && turns.length) {
        turns[turns.length - 1].confirm = view.pendingConfirm;
    }
    return turns;
}

const mapTurn = (turns, key, fn) => turns.map((t) => (t.key === key ? fn(t) : t));

const upsertCard = (cards, card) => {
    const i = cards.findIndex((c) => c.cardId === card.cardId);
    if (i < 0) {
        return [...cards, card];
    }
    const next = [...cards];
    next[i] = card;
    return next;
};

function applyEvent(state, name, data) {
    const key = state.activeKey;
    const active = (fn) => ({ ...state, turns: mapTurn(state.turns, key, fn) });
    switch (name) {
        case 'meta':
            return active((t) => ({ ...t, turnId: t.turnId || data.turnId }));
        case 'node':
            return active((t) => ({ ...t, stage: data.node }));
        case 'card': {
            const card = data.card;
            const owner = state.turns.find((t) => t.turnId && t.turnId === card.turnId);
            const target = owner ? owner.key : key;
            return { ...state, turns: mapTurn(state.turns, target, (t) => ({ ...t, cards: upsertCard(t.cards, card) })) };
        }
        case 'token':
            return active((t) => ({ ...t, preview: t.preview + data.text }));
        case 'final':
            return active((t) => ({
                ...t, answer: data.text, kind: data.kind, replaced: !!data.replaced, preview: '', stage: null,
                confirm: null,
            }));
        case 'confirm':
            return active((t) => ({ ...t, confirm: data, preview: '', stage: null }));
        case 'error':
            return active((t) => ({ ...t, error: data.message || '답을 만들지 못했어요.', stage: null, preview: '' }));
        case 'done':
            return { ...active((t) => ({ ...t, stage: null })), activeKey: null };
        default:
            return state;
    }
}

export function chatReducer(state, action) {
    switch (action.type) {
        case 'load':
            return { turns: action.turns, activeKey: null };
        case 'start':
            return {
                turns: [...state.turns.map((t) => ({ ...t, confirm: null })), blankTurn(action.key, action.text)],
                activeKey: action.key,
            };
        case 'activate':
            return {
                ...state,
                activeKey: action.key,
                turns: mapTurn(state.turns, action.key, (t) => ({ ...t, confirm: null, error: null })),
            };
        case 'event':
            return applyEvent(state, action.name, action.data);
        default:
            return state;
    }
}

export const pendingConfirmOf = (turns) => {
    const turn = [...turns].reverse().find((t) => t.confirm);
    return turn ? { turn, confirm: turn.confirm } : null;
};
