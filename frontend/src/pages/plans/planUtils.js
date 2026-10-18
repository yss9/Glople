// 여행 일정(Spring /api/travel-plans) 화면용 도우미.

// 일정 JSON → [{dayNo, places:[...]}] (1일차부터 days일차까지 빈 날 포함, position 순) + 일차가 없거나 범위 밖인 장소.
export function groupByDay(plan) {
    const days = Math.max(0, Math.min(30, Number(plan && plan.days) || 0));
    const groups = Array.from({ length: days }, (_, i) => ({ dayNo: i + 1, places: [] }));
    const unassigned = [];
    [...((plan && plan.places) || [])]
        .sort((a, b) => (a.position || 0) - (b.position || 0))
        .forEach((place) => {
            const day = Number(place.dayNo);
            if (Number.isInteger(day) && day >= 1 && day <= days) {
                groups[day - 1].places.push(place);
            } else {
                unassigned.push(place);
            }
        });
    return { days: groups, unassigned };
}

// '2026-10-18T14:03:00' → '2026.10.18' (서버 현지 시각, 시간대 표시 없음)
export function formatDate(value) {
    const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value || '');
    return m ? `${m[1]}.${m[2]}.${m[3]}` : '';
}

export const SOURCE_LABELS = {
    USER: '직접 말함',
    TOOL_RESULT: '추천 결과',
    USER_EDIT: '고친 곳',
};
