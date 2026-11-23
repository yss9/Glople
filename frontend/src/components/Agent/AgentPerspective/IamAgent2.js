import React, { useEffect, useState } from 'react';
import styled from 'styled-components';
import MyProfile2 from "../../../public/Img/forprofile/img.png";
import Cookies from "js-cookie";
import {jwtDecode} from "jwt-decode";
import { authFetch } from "../../../api/auth";
const getAuthToken = () => {
    return Cookies.get('jwt');
};
const BlogContainer = styled.div`
    width: 800px;
    height: 380px;
    background-color: white;
    box-shadow: 0 0 15px rgba(0, 0, 0, 0.1);
    border-radius: 15px;
    display: flex;
    justify-content: space-between;
    margin: 10px 10px 40px 10px;
`;

const ContentWrapper = styled.div`
    width:400px;
    height: 380px;
    border-radius: 15px 0 0 15px;
    align-items: center;
    justify-content: center;
    display: flex;
`;
const ContentWrapper2 = styled.div`
    width:400px;
    height: 380px;
    background-color: #f0f0f0;
    flex-direction: column;
    border-radius: 15px 15px 15px 15px;
    align-items: center;
    justify-content: space-around;
    display: flex;
`;

const Content = styled.div`
    width: 92%;
    height: 95%;
`;

const Profile = styled.div`
    width: 100%;
    height: 35%;
    display: flex;
    align-items: center;
`;

const PImg = styled.img`
    width: 120px;
    height: 120px;
    border-radius: 50%;
    background-color: #7d7d7d;
    object-fit: cover;
`;

const PContainer = styled.div`
    display: flex;
    flex-direction: column;
    width: 15rem;
    height: 100%;
    margin-left: 10px;
`;

const Pname = styled.div`
    height: 35%;
    display: flex;
    align-items: center;
    font-size: 20px;
    font-family: Title;
    margin-left: 3px;
    color: #515151;
`;

const Read = styled.div`
    width: 100%;
    height: 50%;
    display: flex;
    flex-direction: column;
    margin-top: 5%;
    font-size: 16px;
    text-align: left;
    font-family: "Regular";
    p {
        font-weight: bold;
    }
`;

const ReadTitle = styled.div`
    width: 100%;
    height: 15%;
    font-size: 20px;
    color: #515151;
    margin: 0.8rem 0 0.4rem 0.8rem;
    font-weight: bold;
    font-style: normal;
    line-height: 25px;
    background: linear-gradient(
            270deg,
            rgba(78, 83, 238, 0.2) -15.09%,
            rgba(78, 83, 238, 0.4) 11.32%,
            rgba(78, 83, 238, 0.379114) 21.37%,
            rgba(78, 83, 238, 0.6) 35.56%,
            rgba(78, 83, 238, 0.8) 59.12%,
            #4e53ee 90.24%
    );
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
`;

const HashTags = styled.div`
    height: 15%;
    text-align: left;
`;

const HashTag = styled.span`
    margin: 7px;
    font-family: Regular;
    font-size: 15px;
    color: #9d9d9d;
`;

const GoChatBtnWrapper = styled.div`
    width: 100%;
    height: 45%;
    display: flex;
    align-items: center;
    justify-content: center;
`;

const GoChatBtn = styled.button`
    width: 100%;
    height: 65%;
    justify-content: center;
    border-radius: 30px;
    align-items: center;
    font-size: 15px;
    color: white;
    background-color: #4e53ed;
    border: none;
    display: flex;
    p {
        margin-right: 0.5rem;
    }
`;

const NameWrapper = styled.div`
    height: 35%;
    display: flex;
    align-items: center;
    width: 100%;
    justify-content: space-between;
`;

const Specs = styled.ul`
    margin: 0.5rem 0 0 1rem;
    font-size: 14px;
`;

const Spec = styled.li`
    list-style: inside;
    margin: 0 0 0.6rem 0;
    color: #5f5f5f;
`;

const Input = styled.input`
    width: 300px;
    padding: 5px;
    margin: 5px 0;
    border: 1px solid #ccc;
    border-radius: 5px;
`;

const CompleteButton = styled.button`
    width: 360px;
    height: 40px;
    border: none;
    border-radius: 10px;
    background-color: #4e53ed;
    color: white;
    font-size: 16px;
    margin-top: 10px;
`;

const Index=styled.div`
    font-family: Regular;
    font-size: 16px;
    text-align: left;
    width: 300px;
    margin-top: 20px;
`;

const Info=styled.div`
    font-family: Title;
    font-size: 20px;
    text-align: left;
    width: 360px;
`;

const IamAgent = ({ review, closeModal }) => {
    const [newIntroduce, setNewIntroduce] = useState("");
    const [newHashtags, setNewHashtags] = useState("");
    const [newSpecs, setNewSpecs] = useState("");
    const token = getAuthToken(); // JWT 토큰 가져오기


    useEffect(() => {
        if (review) {
            setNewIntroduce(review.introduce || "");
            setNewHashtags(review.hashtags.map(tag => tag.replace('#', '')).join(", ") || "");
            setNewSpecs(review.spec.join(", ") || "");
        }
    }, [review]);

    const handleComplete = async () => {
        const updatedHashtags = newHashtags.split(",").map(tag => `#${tag.trim()}`);
        const updatedSpecs = newSpecs.split(",").map(spec => spec.trim());
        const decodedToken = jwtDecode(token);
        const userId = decodedToken.userId;

        const requestBody = {
            introduction: newIntroduce,
            hashtags: updatedHashtags,
            specIntroduction: updatedSpecs,
            // 다른 필요 데이터들 (예: agentCountry 등) 필요에 따라 추가
        };

        try {
            const response = await authFetch(`http://localhost:8080/api/users/update-agent-details?userId=${userId}`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': '*/*',
                },
                body: JSON.stringify(requestBody),
            });

            if (response.ok) {
                alert("프로필 정보가 성공적으로 업데이트되었습니다.");
                closeModal(); // 모달을 닫아 초기 화면으로 복귀
            } else {
                alert("프로필 업데이트에 실패했습니다.");
            }
        } catch (error) {
            console.error("Error updating profile:", error);
            alert("서버 오류로 인해 프로필 업데이트에 실패했습니다.");
        }
    };

    return (
        <BlogContainer>
            <ContentWrapper>
                <Content>
                    {review && (
                        <Profile>
                            <PImg src={review.image || MyProfile2} />
                            <PContainer>
                                <NameWrapper>
                                    <Pname>{review.author}</Pname>
                                </NameWrapper>
                                <HashTags>
                                    {newHashtags.split(",").map((tag, index) => (
                                        <HashTag key={index}>#{tag.trim()}</HashTag>
                                    ))}
                                </HashTags>
                                <GoChatBtnWrapper>
                                    <GoChatBtn>
                                        <p>채팅하기</p>
                                    </GoChatBtn>
                                </GoChatBtnWrapper>
                            </PContainer>
                        </Profile>
                    )}
                    <Read>
                        <p>소개</p>
                        <ReadTitle>"{newIntroduce}"</ReadTitle>
                        <Specs>
                            {newSpecs.split(",").map((spec, index) => (
                                <Spec key={index}>{spec.trim()}</Spec>
                            ))}
                        </Specs>
                    </Read>
                </Content>
            </ContentWrapper>
            <ContentWrapper2>
                <Info>정보를 입력하시면 왼쪽 프로필이 수정됩니다.</Info>
                <div>
                    <Index>해시태그 (쉼표로 구분. '#' 입력x)</Index>
                    <Input
                        placeholder="해시태그 입력 (쉼표로 구분)"
                        value={newHashtags}
                        onChange={(e) => setNewHashtags(e.target.value)}
                    />
                    <Index>소개</Index>
                    <Input
                        placeholder="소개 입력"
                        value={newIntroduce}
                        onChange={(e) => setNewIntroduce(e.target.value)}
                    />
                    <Index>스펙 입력 (쉼표로 구분)</Index>
                    <Input
                        placeholder="스펙 입력 (쉼표로 구분)"
                        value={newSpecs}
                        onChange={(e) => setNewSpecs(e.target.value)}
                    />
                </div>
                <CompleteButton onClick={handleComplete}>저장 하기</CompleteButton>
            </ContentWrapper2>
        </BlogContainer>
    );
};

export default IamAgent;