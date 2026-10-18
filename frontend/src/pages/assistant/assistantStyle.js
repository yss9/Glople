import styled from 'styled-components';

export const Page = styled.div`
    display: flex;
    justify-content: center;
    padding: 16px;
    font-family: "Regular";
    color: #333;
`;

export const Layout = styled.div`
    display: flex;
    gap: 16px;
    width: 100%;
    max-width: 1200px;
    height: calc(100vh - 160px);
    min-height: 480px;
`;

export const Sidebar = styled.aside`
    width: 240px;
    flex-shrink: 0;
    display: flex;
    flex-direction: column;
    background: #f4f6f8;
    border-radius: 16px;
    padding: 12px;
    overflow-y: auto;
`;

export const ThreadItem = styled.div`
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 6px;
    padding: 8px 10px;
    margin-top: 6px;
    border-radius: 10px;
    cursor: pointer;
    background: ${(props) => (props.$active ? 'white' : 'transparent')};
    font-size: 14px;

    span {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    button {
        border: none;
        background: none;
        color: #aaa;
        cursor: pointer;
    }
`;

export const Main = styled.main`
    flex: 1;
    display: flex;
    flex-direction: column;
    background: #f4f6f8;
    border-radius: 16px;
    overflow: hidden;
`;

export const Header = styled.div`
    padding: 16px 20px 8px;

    h1 {
        font-size: 20px;
        font-weight: bold;
        margin: 0;
        color: #222;
    }

    p {
        margin: 4px 0 0;
        font-size: 13px;
        color: #848484;
    }
`;

export const ChatArea = styled.div`
    flex: 1;
    overflow-y: auto;
    padding: 8px 20px 16px;
`;

export const UserRow = styled.div`
    display: flex;
    justify-content: flex-end;
    margin: 14px 0 8px;
`;

export const UserBubble = styled.div`
    max-width: 70%;
    padding: 10px 14px;
    border-radius: 14px;
    background: #4a5fc1;
    color: white;
    font-size: 15px;
    line-height: 1.5;
    white-space: pre-wrap;
    word-break: break-word;
`;

export const BotColumn = styled.div`
    max-width: 82%;
`;

export const BotBubble = styled.div`
    display: inline-block;
    padding: 10px 14px;
    border-radius: 14px;
    background: white;
    font-size: 15px;
    line-height: 1.6;
    white-space: pre-wrap;
    word-break: break-word;
    color: ${(props) => (props.$preview ? '#848484' : '#222')};
`;

export const Stage = styled.div`
    font-size: 13px;
    color: #4a5fc1;
    margin: 4px 0 8px;
`;

export const ErrorText = styled.div`
    font-size: 13px;
    color: #c0392b;
    margin: 6px 0;
`;

export const Hint = styled.div`
    font-size: 12px;
    color: #848484;
    margin-top: 4px;
`;

export const InputForm = styled.form`
    display: flex;
    gap: 8px;
    padding: 12px 16px 16px;

    input {
        flex: 1;
        padding: 12px 14px;
        border-radius: 12px;
        border: 1px solid #d0d4e0;
        font-family: "Regular";
        font-size: 15px;
        outline: none;
    }
`;

export const Examples = styled.div`
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin-top: 12px;

    button {
        border: 1px solid #d0d4e0;
        background: white;
        border-radius: 16px;
        padding: 6px 12px;
        font-family: "Regular";
        font-size: 13px;
        cursor: pointer;
    }
`;

export const Center = styled.div`
    max-width: 480px;
    margin: 80px auto;
    padding: 32px;
    text-align: center;
    background: #f4f6f8;
    border-radius: 16px;
    font-family: "Regular";

    p {
        margin: 0 0 16px;
        color: #515151;
        line-height: 1.6;
    }
`;
