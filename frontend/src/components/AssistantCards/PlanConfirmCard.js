// 일정 확인 카드(에이전트 interrupt, design.md 9절): 저장 / 취소 / 고치기.
// 고친 값은 {region, days, places, title?}로 보낸다. places는 '저장할 장소'만(빠진 장소·해제된 제안은 저장되지 않음).
// 일차는 서버가 장소 순서대로 다시 나눈다(1일차부터 고르게).
import React, { useState } from 'react';
import styled from 'styled-components';

const SOURCE_LABELS = {
    USER: '말씀하신 곳',
    TOOL_RESULT: '찾은 결과',
    USER_EDIT: '고친 곳',
    MODEL_SUGGESTED: 'AI 제안',
};

const Box = styled.section`
    background: white;
    border: 2px solid #4a5fc1;
    border-radius: 12px;
    padding: 16px;
    margin-bottom: 10px;
    font-family: "Regular";
    text-align: left;
    color: #333;
`;

const Title = styled.div`
    font-size: 16px;
    font-weight: bold;
    margin-bottom: 6px;
`;

const Line = styled.div`
    font-size: 13px;
    color: #848484;
    margin-bottom: 8px;
`;

const Warn = styled.div`
    font-size: 13px;
    color: #c0392b;
    margin: 6px 0;
`;

const Day = styled.div`
    margin: 6px 0;
    font-size: 14px;
`;

const Place = styled.span`
    display: inline-block;
    margin: 3px 6px 0 0;
    padding: 3px 9px;
    border-radius: 12px;
    background: ${(props) => (props.$off ? '#f4f6f8' : '#eef0fb')};
    color: ${(props) => (props.$off ? '#aaa' : '#333')};
    text-decoration: ${(props) => (props.$off ? 'line-through' : 'none')};
    font-size: 13px;
`;

const Small = styled.span`
    color: #848484;
    font-size: 11px;
    margin-left: 4px;
`;

const Buttons = styled.div`
    display: flex;
    gap: 8px;
    margin-top: 12px;
`;

export const Button = styled.button`
    padding: 7px 16px;
    border-radius: 8px;
    border: 1px solid ${(props) => (props.$primary ? '#4a5fc1' : '#d0d4e0')};
    background: ${(props) => (props.$primary ? '#4a5fc1' : 'white')};
    color: ${(props) => (props.$primary ? 'white' : '#333')};
    font-family: "Regular";
    font-size: 14px;
    cursor: pointer;

    &:disabled {
        opacity: 0.5;
        cursor: default;
    }
`;

const Field = styled.label`
    display: block;
    font-size: 13px;
    color: #515151;
    margin: 8px 0 4px;

    input {
        display: block;
        width: 100%;
        box-sizing: border-box;
        margin-top: 4px;
        padding: 6px 8px;
        border: 1px solid #d0d4e0;
        border-radius: 6px;
        font-family: "Regular";
        font-size: 14px;
    }
`;

const Row = styled.div`
    display: flex;
    gap: 6px;
    align-items: center;
    margin-top: 4px;

    input {
        flex: 1;
        padding: 6px 8px;
        border: 1px solid #d0d4e0;
        border-radius: 6px;
        font-family: "Regular";
        font-size: 14px;
    }
`;

const Mini = styled.button`
    border: 1px solid #d0d4e0;
    background: white;
    border-radius: 6px;
    padding: 4px 8px;
    cursor: pointer;
    font-size: 12px;
`;

// 확인 카드 초안 → 고치기 칸. 장소 칸의 기본값은 선택된 장소만(해제된 제안은 '제안'으로 따로, 직접 넣어야 들어감).
// 제목 칸은 비워 두고 지금 제목은 placeholder로만 보인다: 비운 채 보내면 서버가 제목을 그대로 두거나(직접 지은 제목),
// 기본 제목('제주 2박 3일')이었으면 바뀐 일수에 맞춰 새로 짓는다(plan.PlanDraftIn.title).
export function draftToForm(draft) {
    const items = [...((draft && draft.items) || [])].sort((a, b) => a.position - b.position);
    return {
        region: (draft && draft.region) || '',
        days: draft && draft.days ? String(draft.days) : '1',
        title: '',
        currentTitle: (draft && draft.title) || '',
        places: items.filter((it) => it.selected).map((it) => it.name),
        suggestions: items.filter((it) => !it.selected).map((it) => it.name),
    };
}

// 고치기 칸 → 재개 값(PlanDraftIn). 범위(일수 1~30, 장소 1~30곳 …)는 서버가 검사해 카드에 문제로 보여 준다.
export function formToDraft(form) {
    const draft = {
        region: form.region.trim(),
        days: Number.parseInt(form.days, 10) || 0,
        places: form.places.map((p) => p.trim()).filter(Boolean),
    };
    if (form.title.trim()) {
        draft.title = form.title.trim();
    }
    return draft;
}

function byDay(items) {
    const days = new Map();
    [...items].sort((a, b) => a.position - b.position).forEach((it) => {
        const key = it.dayNo || 0;
        if (!days.has(key)) {
            days.set(key, []);
        }
        days.get(key).push(it);
    });
    return [...days.entries()].sort((a, b) => (a[0] || 99) - (b[0] || 99));
}

function EditForm({ draft, disabled, onSubmit, onCancel }) {
    const [form, setForm] = useState(() => draftToForm(draft));
    const [extra, setExtra] = useState('');
    const set = (patch) => setForm((old) => ({ ...old, ...patch }));
    const setPlace = (i, value) => set({ places: form.places.map((p, j) => (i === j ? value : p)) });
    const move = (i, d) => {
        const next = [...form.places];
        const j = i + d;
        if (j < 0 || j >= next.length) {
            return;
        }
        [next[i], next[j]] = [next[j], next[i]];
        set({ places: next });
    };
    const add = (name) => {
        const value = name.trim();
        if (value) {
            set({ places: [...form.places, value], suggestions: form.suggestions.filter((s) => s !== value) });
        }
    };
    return (
        <form onSubmit={(e) => { e.preventDefault(); onSubmit(formToDraft(form)); }}>
            <Field>지역<input value={form.region} onChange={(e) => set({ region: e.target.value })} maxLength={50} /></Field>
            <Field>일수<input type="number" min={1} max={30} value={form.days} onChange={(e) => set({ days: e.target.value })} /></Field>
            <Field>
                제목(비워 두면 지금 제목, 기본 제목이면 바뀐 일수에 맞게)
                <input value={form.title} placeholder={form.currentTitle} onChange={(e) => set({ title: e.target.value })} maxLength={100} />
            </Field>
            <Field as="div">저장할 장소(순서대로 일차가 나뉘어요)</Field>
            {form.places.map((place, i) => (
                <Row key={i}>
                    <input value={place} aria-label={`장소 ${i + 1}`} onChange={(e) => setPlace(i, e.target.value)} maxLength={50} />
                    <Mini type="button" onClick={() => move(i, -1)} aria-label="위로">▲</Mini>
                    <Mini type="button" onClick={() => move(i, 1)} aria-label="아래로">▼</Mini>
                    <Mini type="button" onClick={() => set({ places: form.places.filter((_, j) => j !== i) })}>빼기</Mini>
                </Row>
            ))}
            {form.suggestions.length > 0 && (
                <Line>
                    AI 제안(넣지 않으면 저장 안 됨):{' '}
                    {form.suggestions.map((s) => <Mini key={s} type="button" onClick={() => add(s)}>+ {s}</Mini>)}
                </Line>
            )}
            <Row>
                <input value={extra} placeholder="장소 더하기" onChange={(e) => setExtra(e.target.value)} maxLength={50} />
                <Mini type="button" onClick={() => { add(extra); setExtra(''); }}>더하기</Mini>
            </Row>
            <Buttons>
                <Button $primary type="submit" disabled={disabled}>고친 일정 보내기</Button>
                <Button type="button" onClick={onCancel} disabled={disabled}>그만 고치기</Button>
            </Buttons>
        </form>
    );
}

export default function PlanConfirmCard({ confirm, disabled, onAnswer }) {
    const [editing, setEditing] = useState(false);
    const draft = confirm.draft || {};
    const items = draft.items || [];
    return (
        <Box aria-label="일정 확인">
            <Title>{confirm.message}</Title>
            {confirm.noticeText && <Warn>{confirm.noticeText}</Warn>}
            <Line>{draft.region} · {draft.days}일 · 제목 '{draft.title}'</Line>
            {byDay(items).map(([day, list]) => (
                <Day key={day}>
                    <strong>{day ? `${day}일차` : '일차 미정'}</strong>{' '}
                    {list.map((it) => (
                        <Place key={it.position} $off={!it.selected}>
                            {it.name}<Small>{SOURCE_LABELS[it.source] || it.source}{it.selected ? '' : ' · 선택 안 됨'}</Small>
                        </Place>
                    ))}
                </Day>
            ))}
            {(confirm.problems || []).map((p, i) => <Warn key={i}>{p}</Warn>)}
            {editing ? (
                <EditForm
                    draft={draft}
                    disabled={disabled}
                    onCancel={() => setEditing(false)}
                    onSubmit={(edited) => { setEditing(false); onAnswer('edit', edited); }}
                />
            ) : (
                <Buttons>
                    <Button $primary type="button" disabled={disabled} onClick={() => onAnswer('approve')}>저장</Button>
                    <Button type="button" disabled={disabled} onClick={() => onAnswer('reject')}>취소</Button>
                    <Button type="button" disabled={disabled || confirm.editsLeft === 0} onClick={() => setEditing(true)}>
                        고치기{typeof confirm.editsLeft === 'number' ? ` (${confirm.editsLeft}회 남음)` : ''}
                    </Button>
                </Buttons>
            )}
        </Box>
    );
}
