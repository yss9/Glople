import { formatDate, groupByDay } from './planUtils';

test('groupByDay: 빈 날을 포함해 일차별로, 범위 밖 일차는 따로', () => {
    const { days, unassigned } = groupByDay({
        days: 3,
        places: [
            { name: '우도', dayNo: 1, position: 2 },
            { name: '성산일출봉', dayNo: 1, position: 1 },
            { name: '오설록', dayNo: 2, position: 3 },
            { name: '어딘가', dayNo: 9, position: 4 },
            { name: '모름', dayNo: null, position: 5 },
        ],
    });
    expect(days.map((d) => [d.dayNo, d.places.map((p) => p.name)])).toEqual([
        [1, ['성산일출봉', '우도']], [2, ['오설록']], [3, []],
    ]);
    expect(unassigned.map((p) => p.name)).toEqual(['어딘가', '모름']);
});

test('formatDate', () => {
    expect(formatDate('2026-10-18T14:03:00')).toBe('2026.10.18');
    expect(formatDate(null)).toBe('');
});
