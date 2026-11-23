import styled from 'styled-components';

export const Wrap = styled.div`
    max-width: 960px;
    margin: 0 auto;
    padding: 24px 16px 64px;
    font-family: "Regular";
    color: #333;
    text-align: left;
`;

export const TitleRow = styled.div`
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    margin-bottom: 16px;

    h1 {
        font-size: 24px;
        font-weight: bold;
        color: #222;
        margin: 0;
    }
`;

export const Sub = styled.div`
    font-size: 14px;
    color: #848484;
    margin-top: 4px;
`;

export const List = styled.div`
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
`;

export const PlanCard = styled.button`
    text-align: left;
    background: white;
    border: 1px solid #e3e6ee;
    border-radius: 14px;
    padding: 16px;
    cursor: pointer;
    font-family: "Regular";

    &:hover {
        border-color: #4a5fc1;
    }

    strong {
        display: block;
        font-size: 17px;
        color: #222;
        margin-bottom: 6px;
    }
`;

export const Days = styled.div`
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
    margin-top: 12px;
`;

export const DayCard = styled.section`
    background: #f4f6f8;
    border-radius: 14px;
    padding: 14px 16px;

    h2 {
        font-size: 16px;
        font-weight: bold;
        color: #4a5fc1;
        margin: 0 0 8px;
    }

    ol {
        margin: 0;
        padding-left: 20px;
        list-style: decimal;
        line-height: 1.8;
        font-size: 15px;
    }
`;

export const Tag = styled.span`
    margin-left: 6px;
    font-size: 11px;
    color: #848484;
`;

export const Note = styled.div`
    margin-top: 8px;
    font-size: 13px;
    line-height: 1.5;
    color: ${(props) => (props.$warn ? '#c0392b' : '#515151')};
`;

export const ReviewBox = styled.section`
    margin-top: 20px;
    background: white;
    border: 2px solid #4a5fc1;
    border-radius: 14px;
    padding: 16px;

    h2 {
        font-size: 16px;
        font-weight: bold;
        margin: 0 0 8px;
    }

    p {
        margin: 0;
        line-height: 1.7;
        font-size: 15px;
        white-space: pre-wrap;
    }
`;

export const Actions = styled.div`
    display: flex;
    gap: 8px;
`;

export const Message = styled.div`
    margin: 40px auto;
    text-align: center;
    color: #515151;
    line-height: 1.6;

    p {
        margin: 0 0 14px;
    }
`;

export const ErrorText = styled.div`
    margin-top: 12px;
    color: #c0392b;
    font-size: 14px;
`;
