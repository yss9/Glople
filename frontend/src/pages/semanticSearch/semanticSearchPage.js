// 문장으로 여행지 찾기(/search/semantic): 파인튜닝한 임베딩 모델 검색 서버(/semantic-api/v1)에 문장을 보내 뜻이 가까운 여행지를 고른다.
// - 질의·범위는 주소(?q=&scope=)에 둔다. 상세 화면에 갔다가 뒤로 오면 같은 결과가 다시 보인다(서버가 같은 문장을 캐시).
// - 결과 카드에는 이름·지역·분류만 있다. 누르면 Spring 이름 찾기로 같은 여행지를 찾아 기존 상세 화면에 넘긴다(설명문·출처는 거기서).
// - 로그인 없이 쓴다.
import React, { useEffect, useRef, useState } from 'react';
import { Reset } from 'styled-reset';
import { useNavigate, useSearchParams } from 'react-router-dom';
import TopBarComponent from '../../components/TopBar/TopBar';
import {
    MAX_QUERY_LENGTH, SCOPES, SOURCE_LABELS, detailPathOf, lookupDetailPlace, normalizeQuery, normalizeScope,
    queryLength, searchDestinations, validateQuery,
} from '../../api/semanticSearch';
import {
    Badge, CardTop, Controls, Counter, Examples, List, Meta, Note, ResultCard, ScopeButton, ScopeGroup, SearchForm,
    Status, Sub, Wrap,
} from './semanticSearchStyle';

const EXAMPLES = [
    '조용한 바닷가 마을',
    '아이랑 가기 좋은 동물 체험',
    '단풍 보러 산에 가고 싶어',
    '경주에서 역사 유적 둘러볼 곳',
    '유럽의 오래된 성당 구경',
];

function ResultItem({ item, rank, opening, onOpen }) {
    const overseas = item.source === 'WIKIDATA';
    return (
        <ResultCard type="button" onClick={() => onOpen(item)} disabled={opening}
                    aria-label={`${item.name} 상세 보기`}>
            <CardTop>
                <Badge $overseas={overseas}>{SOURCE_LABELS[item.source] || '여행지'}</Badge>
                <span>{opening ? '여는 중…' : `${rank}위`}</span>
            </CardTop>
            <strong>{item.name}</strong>
            {item.location && <Meta>{item.location}</Meta>}
            {item.category && <Meta>{item.category}</Meta>}
        </ResultCard>
    );
}

export default function SemanticSearchPage() {
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();
    const q = normalizeQuery(searchParams.get('q') || '');
    const scope = normalizeScope(searchParams.get('scope') || 'all');
    const [input, setInput] = useState(q);
    const [state, setState] = useState({ status: 'idle', items: [], error: null });
    const [inputError, setInputError] = useState(null);
    const [openingKey, setOpeningKey] = useState(null);
    const openAbort = useRef(null);

    // 주소의 질의가 바뀌면(검색·예시·뒤로 가기) 입력칸도 맞춘다.
    useEffect(() => {
        setInput(q);
    }, [q]);

    useEffect(() => {
        if (!q || validateQuery(q)) {
            setState({ status: 'idle', items: [], error: null });
            return undefined;
        }
        const controller = new AbortController();
        setState((prev) => ({ ...prev, status: 'loading', error: null }));
        searchDestinations(q, { scope, signal: controller.signal })
            .then((items) => setState({ status: 'done', items, error: null }))
            .catch((error) => {
                if (error.name !== 'AbortError') {
                    setState({ status: 'error', items: [], error: error.message });
                }
            });
        return () => controller.abort();
    }, [q, scope]);

    useEffect(() => () => openAbort.current && openAbort.current.abort(), []);

    const runSearch = (text, nextScope = scope) => {
        const problem = validateQuery(text);
        setInputError(problem);
        if (problem) {
            return;
        }
        setSearchParams({ q: normalizeQuery(text), scope: nextScope });
    };

    const onSubmit = (e) => {
        e.preventDefault();
        runSearch(input);
    };

    const onScope = (value) => {
        if (q) {
            setSearchParams({ q, scope: value });
        } else {
            setSearchParams({ scope: value });
        }
    };

    const openDetail = async (item) => {
        const key = `${item.source}:${item.id}`;
        if (openingKey) {
            return;
        }
        setOpeningKey(key);
        const controller = new AbortController();
        openAbort.current = controller;
        try {
            const place = await lookupDetailPlace(item, { signal: controller.signal });
            navigate(detailPathOf(item.name), place ? { state: { place } } : undefined);
        } catch (error) {
            if (error.name !== 'AbortError') {
                navigate(detailPathOf(item.name));
            }
        } finally {
            setOpeningKey(null);
        }
    };

    const length = queryLength(input);
    const scopeLabel = (SCOPES.find((s) => s.value === scope) || SCOPES[0]).label;

    return (
        <>
            <Reset />
            <div style={{ height: '55px' }}></div>
            <TopBarComponent />
            <Wrap>
                <h1>문장으로 여행지 찾기</h1>
                <Sub>가고 싶은 여행을 문장으로 적어 보세요. 키워드를 고르지 않아도 뜻이 가까운 여행지를 찾아 드려요.</Sub>

                <SearchForm onSubmit={onSubmit} role="search">
                    <input
                        type="search"
                        value={input}
                        onChange={(e) => {
                            setInput(e.target.value);
                            setInputError(null);
                        }}
                        placeholder="예: 조용한 바닷가 마을"
                        aria-label="찾고 싶은 여행 문장"
                        maxLength={MAX_QUERY_LENGTH * 2}
                    />
                    <button type="submit" disabled={state.status === 'loading'}>찾기</button>
                </SearchForm>
                <Controls>
                    <ScopeGroup role="group" aria-label="찾을 범위">
                        {SCOPES.map((s) => (
                            <ScopeButton key={s.value} type="button" $active={s.value === scope}
                                         aria-pressed={s.value === scope} onClick={() => onScope(s.value)}>
                                {s.label}
                            </ScopeButton>
                        ))}
                    </ScopeGroup>
                    <Counter $over={length > MAX_QUERY_LENGTH}>{length}/{MAX_QUERY_LENGTH}자</Counter>
                </Controls>
                {inputError && <Status $error>{inputError}</Status>}

                <Examples>
                    {EXAMPLES.map((text) => (
                        <button key={text} type="button" onClick={() => runSearch(text)}>{text}</button>
                    ))}
                </Examples>

                {state.status === 'loading' && <Status>뜻이 가까운 여행지를 찾는 중…</Status>}
                {state.status === 'error' && <Status $error role="alert">{state.error}</Status>}
                {state.status === 'done' && state.items.length === 0 && (
                    <Status>{scopeLabel} 범위에서 비슷한 여행지를 찾지 못했어요. 다른 말로 적어 보세요.</Status>
                )}
                {state.items.length > 0 && (
                    <List aria-busy={state.status === 'loading'}>
                        {state.items.map((item, i) => (
                            <ResultItem
                                key={`${item.source}:${item.id}`}
                                item={item}
                                rank={i + 1}
                                opening={openingKey === `${item.source}:${item.id}`}
                                onOpen={openDetail}
                            />
                        ))}
                    </List>
                )}

                <Note>
                    문장 뜻을 숫자로 바꿔 비교하는 AI 임베딩 모델(multilingual-e5-base를 국내 여행지 라벨로 파인튜닝)이 고른 결과예요.
                    학습은 국내 관광지 위주라 해외 결과는 덜 정확할 수 있고, 설명문이 있는 여행지만 찾아요.
                    카드를 누르면 여행지 상세 화면으로 가요.
                </Note>
            </Wrap>
        </>
    );
}
