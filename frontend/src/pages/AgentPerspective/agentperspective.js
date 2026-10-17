import React, { useState, useEffect } from "react";
import { Reset } from "styled-reset";
import IamAgent from "../../components/Agent/AgentPerspective/IamAgent";
import IamAgent2 from "../../components/Agent/AgentPerspective/IamAgent2";
import {
    SubTitleWrapper,
    SubTitle2,
    AgentContainer,
    Agents,
    Container,
    Wrapper,
} from './agentperspectivestyle';
import TopBarComponent from "../../components/TopBar/TopBar";
import MyProfile2 from '../../images/도라에몽.jpeg';
import Cookies from "js-cookie";
import { jwtDecode } from "jwt-decode";
import { authFetch } from "../../api/auth";

const getAuthToken = () => {
    return Cookies.get('jwt');
};

const processProfilePicture = (profilePicture) => {
    if (profilePicture) {
        return `http://localhost:8080/${profilePicture.replace('static\\', '').replace(/\\/g, '/')}`;
    } else {
        return MyProfile2;
    }
};

export default function ReservationPage() {
    const [agentData, setAgentData] = useState(null);
    const [isEditing, setIsEditing] = useState(false);
    const token = getAuthToken();

    // fetchUserData 함수 생성
    const fetchUserData = () => {
        const decodedToken = jwtDecode(token);
        const userId = decodedToken.userId;

        authFetch(`http://localhost:8080/api/users/my-info?userId=${userId}`)
            .then(response => response.json())
            .then(data => {
                const formattedData = {
                    author: data.nickname || '익명 사용자',
                    introduce: data.agentDetails?.introduction || '자기소개가 없습니다.',
                    hashtags: data.agentDetails?.hashtags || ['#정보없음'],
                    spec: data.agentDetails?.specIntroduction || ['경력이 없습니다.'],
                    image: processProfilePicture(data.agentDetails?.profilePicture),
                    score: data.agentDetails?.averageReviewRating?.toFixed(1) || '0.0',
                    number: data.agentDetails?.reviewCount || '0',
                    agentreview: data.agentDetails?.latestReview || '리뷰가 없습니다.',
                };
                setAgentData(formattedData);
            })
            .catch(error => console.error("Error fetching user data:", error));
    };

    useEffect(() => {
        fetchUserData();
    }, []);

    const openModal = () => setIsEditing(true);
    const closeModal = () => {
        setIsEditing(false); // 모달을 닫음
        fetchUserData(); // 최신 데이터로 프로필 갱신
    };

    return (
        <>
            <Reset />
            <Container>
                <div style={{ height: '55px' }}></div>
                <TopBarComponent />
                <Wrapper>
                    <SubTitleWrapper>
                        {agentData && (
                            <SubTitle2>
                                <p>{agentData.author}</p> 님의 프로필이 보여지고 있어요.
                            </SubTitle2>
                        )}
                    </SubTitleWrapper>
                    <AgentContainer>
                        <Agents>
                            {agentData && (
                                isEditing
                                    ? <IamAgent2 key={agentData.author} review={agentData} closeModal={closeModal} />
                                    : <IamAgent key={agentData.author} review={agentData} openModal={openModal} />
                            )}
                        </Agents>
                    </AgentContainer>
                </Wrapper>
            </Container>
        </>
    );
}
