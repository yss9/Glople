// 여행 도우미가 보낸 결과 카드(도구 결과 원문·출처·이미지). 카드 data는 에이전트 tools/envelope.py가 만든 모양 그대로다.
// - 설명문·후기 발췌·글로플러 소개는 원문 그대로 보여 준다(요약·변경 없음, dangerouslySetInnerHTML 없음, 줄바꿈만 살림).
//   길면 CSS로 접고 '더 보기'로 펼친다(DOM에는 전문이 있음). 공공누리 제3유형(변경금지) 글도 같은 규칙.
// - 출처는 SourceAttribution을 그대로 쓴다. 이미지는 object-fit: contain(자르거나 바꾸지 않음).
import React, { useState } from 'react';
import styled from 'styled-components';
import { Link, useNavigate } from 'react-router-dom';
import SourceAttribution from '../SourceAttribution/SourceAttribution';

export const TOOL_LABELS = {
    recommend_by_keywords: '취향 여행지 추천',
    search_destinations: '지역별 여행지 검색',
    recommend_similar_traveler_routes: '비슷한 여행자 루트',
    get_destination_detail: '여행지 상세 정보',
    search_reviews: '여행 후기',
    search_gloplers: '글로플러 찾기',
    get_weather: '날씨',
    create_travel_plan: '여행 일정',
};

const DETAIL_PREFIX = '/recomendation/information/';

const Card = styled.section`
    background: white;
    border: 1px solid #e3e6ee;
    border-radius: 12px;
    padding: 14px 16px;
    margin-bottom: 10px;
    font-family: "Regular";
    color: #333;
    text-align: left;
`;

const CardHead = styled.div`
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
    font-size: 13px;
    color: #4a5fc1;
    font-weight: bold;
`;

const Status = styled.span`
    font-size: 12px;
    font-weight: normal;
    color: ${(props) => (props.$warn ? '#c0392b' : '#848484')};
`;

const Item = styled.div`
    padding: 10px 0;
    border-top: ${(props) => (props.$first ? 'none' : '1px solid #f0f1f5')};
`;

const ItemTitle = styled.div`
    font-size: 16px;
    font-weight: bold;
    color: #222;
`;

const Meta = styled.div`
    font-size: 13px;
    color: #848484;
    margin-top: 2px;
`;

const Picture = styled.img`
    display: block;
    width: 100%;
    max-height: 220px;
    object-fit: contain;
    background: #f4f6f8;
    border-radius: 8px;
    margin: 8px 0;
`;

const Body = styled.div`
    font-size: 14px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
    margin-top: 6px;
    max-height: ${(props) => (props.$open ? 'none' : '6.4em')};
    overflow: hidden;
`;

const TextButton = styled.button`
    background: none;
    border: none;
    padding: 0;
    margin-top: 4px;
    color: #4a5fc1;
    font-size: 13px;
    cursor: pointer;
    font-family: "Regular";
`;

const Notes = styled.ul`
    margin: 6px 0 0;
    padding: 0;
    list-style: none;
    font-size: 13px;
    color: #848484;

    li + li {
        margin-top: 2px;
    }
`;

const Tag = styled.span`
    display: inline-block;
    margin: 4px 6px 0 0;
    padding: 2px 8px;
    border-radius: 10px;
    background: #eef0fb;
    color: #4a5fc1;
    font-size: 12px;
`;

const Verbatim = styled.div`
    margin-top: 4px;
    font-size: 12px;
    color: #848484;
`;

// 원문 글: 접었다 펴기만 하고 글자는 바꾸지 않는다.
export function RawText({ text }) {
    const [open, setOpen] = useState(false);
    if (!text) {
        return null;
    }
    const long = text.length > 120;
    return (
        <>
            <Body $open={open || !long}>{text}</Body>
            {long && (
                <TextButton type="button" onClick={() => setOpen(!open)}>
                    {open ? '접기' : '더 보기'}
                </TextButton>
            )}
        </>
    );
}

function detailPathOf(item) {
    if (item.detailPath && item.detailPath.startsWith(DETAIL_PREFIX)) {
        return item.detailPath;
    }
    return DETAIL_PREFIX + encodeURIComponent(item.name || '');
}

function DestinationItem({ item, first }) {
    const navigate = useNavigate();
    // 여행지 상세 화면(information.js)은 state.place의 설명문·출처를 그대로 보여 준다.
    const openDetail = () => navigate(detailPathOf(item), {
        state: {
            place: {
                destName: item.name,
                info: item.text,
                source: item.source,
                copyrightType: item.copyrightType,
                sourceUrl: item.sourceUrl,
                license: item.license,
                infoKind: item.textKind,
            },
        },
    });
    const where = [item.location, item.country && item.country !== item.location ? item.country : null]
        .filter(Boolean).join(' · ');
    return (
        <Item $first={first}>
            <ItemTitle>{item.name}</ItemTitle>
            {(where || item.category) && <Meta>{[where, item.category].filter(Boolean).join(' · ')}</Meta>}
            {item.imageUrl && <Picture src={item.imageUrl} alt={item.name} loading="lazy" />}
            <RawText text={item.text} />
            {item.textPolicy === 'VERBATIM_ONLY' && item.text && (
                <Verbatim>설명은 원문 그대로 옮겼어요(변경금지).</Verbatim>
            )}
            <SourceAttribution
                source={item.source}
                copyrightType={item.copyrightType}
                sourceUrl={item.sourceUrl}
                title={item.name}
                infoKind={item.textKind}
            />
            {item.name && <TextButton type="button" onClick={openDetail}>자세히</TextButton>}
        </Item>
    );
}

function ReviewItem({ item, first }) {
    const meta = [item.nickname, item.date, typeof item.likes === 'number' ? `좋아요 ${item.likes}` : null]
        .filter(Boolean).join(' · ');
    const internal = item.link && item.link.startsWith('/board/');
    return (
        <Item $first={first}>
            <ItemTitle>{internal ? <Link to={item.link}>{item.title}</Link> : item.title}</ItemTitle>
            {meta && <Meta>{meta}</Meta>}
            {item.imageUrl && <Picture src={item.imageUrl} alt="" loading="lazy" />}
            <RawText text={item.excerpt} />
        </Item>
    );
}

function GloplerItem({ item, first }) {
    const meta = [item.country, typeof item.rating === 'number' ? `★ ${item.rating}` : null].filter(Boolean).join(' · ');
    return (
        <Item $first={first}>
            <ItemTitle>{item.nickname}</ItemTitle>
            {meta && <Meta>{meta}</Meta>}
            <div>{(item.hashtags || []).map((tag) => <Tag key={tag}>#{tag}</Tag>)}</div>
            <RawText text={item.introduction} />
            {(item.specIntroduction || []).length > 0 && (
                <Notes>{item.specIntroduction.map((spec, i) => <li key={i}>{spec}</li>)}</Notes>
            )}
        </Item>
    );
}

function RouteItem({ item, first }) {
    return (
        <Item $first={first}>
            <ItemTitle>{[item.ageBand, item.gender, item.mbti].filter(Boolean).join(' ')}</ItemTitle>
            <Meta>{(item.destinations || []).join(' → ')}</Meta>
        </Item>
    );
}

// 날씨 등 모양이 정해지지 않은 항목: 키와 값을 그대로(M6에서 날씨 항목이 들어오면 이 꼴로 보임)
function GenericItem({ item, first }) {
    const entries = Object.entries(item).filter(([key, value]) =>
        value !== null && value !== undefined && typeof value !== 'object' && key !== 'attribution');
    return (
        <Item $first={first}>
            {item.name && <ItemTitle>{item.name}</ItemTitle>}
            <Meta>{entries.filter(([key]) => key !== 'name').map(([key, value]) => `${key} ${value}`).join(' · ')}</Meta>
            {item.attribution && <Verbatim>{item.attribution}</Verbatim>}
        </Item>
    );
}

const ITEM_VIEWS = {
    destination_list: DestinationItem,
    destination_detail: DestinationItem,
    review_list: ReviewItem,
    glopler_list: GloplerItem,
    traveler_routes: RouteItem,
};

const STATUS_LABELS = {
    ok: null,
    empty: '결과 없음',
    error: '오류',
    unsupported: '지원하지 않음',
    pending_feature: '준비 중',
};

function ToolCard({ card }) {
    const data = card.data || {};
    const items = Array.isArray(data.items) ? data.items : [];
    const View = ITEM_VIEWS[card.kind] || GenericItem;
    const notes = (data.notes || []).filter((note) => note && note !== data.message);
    return (
        <Card aria-label={TOOL_LABELS[card.tool] || card.tool}>
            <CardHead>
                <span>{TOOL_LABELS[card.tool] || card.tool}</span>
                {STATUS_LABELS[card.status] && <Status $warn={card.status === 'error'}>{STATUS_LABELS[card.status]}</Status>}
            </CardHead>
            {card.status !== 'ok' && data.message && <Meta>{data.message}</Meta>}
            {items.map((item, i) => <View key={i} item={item} first={i === 0} />)}
            {notes.length > 0 && <Notes>{notes.map((note, i) => <li key={i}>{note}</li>)}</Notes>}
            {data.attribution && <Verbatim>{data.attribution}</Verbatim>}
        </Card>
    );
}

function PlanResultCard({ card }) {
    const data = card.data || {};
    if (card.kind === 'plan_saved') {
        const title = (data.saved && data.saved.title) || (data.draft && data.draft.title);
        return (
            <Card aria-label="저장한 일정">
                <CardHead><span>여행 일정</span><Status>{data.already_saved ? '이미 저장됨' : '저장됨'}</Status></CardHead>
                <ItemTitle>{title}</ItemTitle>
                {data.plan_id && <Link to={`/my/plans/${data.plan_id}`}>내 여행 일정에서 보기</Link>}
            </Card>
        );
    }
    if (data.cancelled) {
        return (
            <Card aria-label="취소한 일정">
                <CardHead><span>여행 일정</span><Status>취소됨</Status></CardHead>
                <Meta>{data.draft && data.draft.title ? `'${data.draft.title}' 일정은 저장하지 않았어요.` : '일정 저장을 취소했어요.'}</Meta>
            </Card>
        );
    }
    return null; // 대기 중인 확인 카드는 PlanConfirmCard가 그린다
}

export default function AssistantCard({ card }) {
    if (!card) {
        return null;
    }
    if (card.tool === 'create_travel_plan') {
        return <PlanResultCard card={card} />;
    }
    return <ToolCard card={card} />;
}
