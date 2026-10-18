import { createSseParser } from './sse';

describe('createSseParser', () => {
    test('조각이 어디서 끊겨도 이벤트를 순서대로 꺼낸다', () => {
        const events = [];
        const feed = createSseParser((name, data) => events.push([name, data]));
        const stream = 'event: meta\ndata: {"threadId": "t", "turnId": "turn-1"}\n\n'
            + ': ping\n\n'
            + 'event: card\ndata: {"card": {"cardId": "c1"}}\n\n'
            + 'event: final\r\ndata: {"text": "안녕하세요", "kind": "direct", "replaced": false}\r\n\r\n';
        for (let i = 0; i < stream.length; i += 7) {
            feed(stream.slice(i, i + 7));
        }
        expect(events).toEqual([
            ['meta', { threadId: 't', turnId: 'turn-1' }],
            ['card', { card: { cardId: 'c1' } }],
            ['final', { text: '안녕하세요', kind: 'direct', replaced: false }],
        ]);
    });

    test('끝나지 않은 이벤트는 기다리고, 읽지 못한 JSON은 버린다', () => {
        const events = [];
        const feed = createSseParser((name, data) => events.push(name));
        feed('event: token\ndata: {"text": "가"}');
        expect(events).toEqual([]);
        feed('\n\nevent: broken\ndata: {nope\n\nevent: done\ndata: {"reason": "final"}\n\n');
        expect(events).toEqual(['token', 'done']);
    });
});
