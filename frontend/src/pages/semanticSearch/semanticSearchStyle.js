import styled from 'styled-components';

export const Wrap = styled.div`
    max-width: 960px;
    margin: 0 auto;
    padding: 24px 16px 64px;
    font-family: "Regular";
    color: #333;
    text-align: left;

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
    line-height: 1.5;
`;

export const SearchForm = styled.form`
    display: flex;
    gap: 8px;
    margin-top: 18px;

    input {
        flex: 1;
        min-width: 0;
        padding: 12px 14px;
        border-radius: 12px;
        border: 1px solid #d0d4e0;
        font-family: "Regular";
        font-size: 15px;
        outline: none;
    }

    input:focus {
        border-color: #4a5fc1;
    }

    button {
        padding: 0 20px;
        border: none;
        border-radius: 12px;
        background: #4a5fc1;
        color: white;
        font-family: "Regular";
        font-size: 15px;
        cursor: pointer;
    }

    button:disabled {
        background: #b7bfe4;
        cursor: default;
    }
`;

export const Controls = styled.div`
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-top: 10px;
`;

export const ScopeGroup = styled.div`
    display: inline-flex;
    border: 1px solid #d0d4e0;
    border-radius: 16px;
    overflow: hidden;
`;

export const ScopeButton = styled.button`
    border: none;
    padding: 6px 14px;
    font-family: "Regular";
    font-size: 13px;
    cursor: pointer;
    background: ${(props) => (props.$active ? '#4a5fc1' : 'white')};
    color: ${(props) => (props.$active ? 'white' : '#515151')};
`;

export const Counter = styled.span`
    font-size: 12px;
    color: ${(props) => (props.$over ? '#c0392b' : '#848484')};
`;

export const Examples = styled.div`
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 14px;

    button {
        border: 1px solid #d0d4e0;
        background: white;
        border-radius: 16px;
        padding: 6px 12px;
        font-family: "Regular";
        font-size: 13px;
        cursor: pointer;
    }

    button:hover {
        border-color: #4a5fc1;
        color: #4a5fc1;
    }
`;

export const Status = styled.div`
    margin-top: 24px;
    font-size: 14px;
    color: ${(props) => (props.$error ? '#c0392b' : '#515151')};
    line-height: 1.6;
`;

export const List = styled.div`
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 12px;
    margin-top: 20px;
`;

export const ResultCard = styled.button`
    position: relative;
    text-align: left;
    background: white;
    border: 1px solid #e3e6ee;
    border-radius: 14px;
    padding: 16px;
    cursor: pointer;
    font-family: "Regular";
    color: #333;

    &:hover {
        border-color: #4a5fc1;
    }

    &:disabled {
        cursor: wait;
        opacity: 0.6;
    }

    strong {
        display: block;
        font-size: 17px;
        color: #222;
        margin: 6px 0 4px;
        word-break: keep-all;
    }
`;

export const CardTop = styled.div`
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: 12px;
    color: #848484;
`;

export const Badge = styled.span`
    display: inline-block;
    padding: 2px 8px;
    border-radius: 10px;
    background: ${(props) => (props.$overseas ? '#fdf1e6' : '#eef0fb')};
    color: ${(props) => (props.$overseas ? '#b4641e' : '#4a5fc1')};
    font-size: 12px;
`;

export const Meta = styled.div`
    font-size: 13px;
    color: #848484;
    margin-top: 2px;
    word-break: keep-all;
`;

export const Note = styled.p`
    margin: 28px 0 0;
    font-size: 12px;
    color: #a0a0a0;
    line-height: 1.6;
`;
