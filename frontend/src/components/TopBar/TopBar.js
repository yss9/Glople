import React from 'react';
import styled from 'styled-components';
import { useNavigate } from "react-router-dom";

const Container=styled.div`
  display: flex;
  align-items: center;
  justify-content: center;
`

const TopBar = styled.div`
  display: flex;
  align-items: center;
  justify-content: flex-start;
  width: 1500px;
  height: 60px;
  //margin-top: 3vh;
 // background-color: gray;
`;

const LogoWrapper = styled.div`
  width: 25%;
    display: flex;
    justify-content: center; 
   // background-color: #61dafb;
`;

const Logo = styled.div`
    background-image: url("/GlopleLogo.png");
    background-size: cover;
    background-position: center;
    width: 300px;
    height: 80px;
  margin-bottom: 1.5em;
  cursor: pointer;
 // background-color: pink;
    
`;

const MenuContainer = styled.div`
  width: 56%;
  display: flex;
  align-items: center;
  justify-content: space-between;
   // background-color: yellowgreen;
    margin-left: 14%;
 
`;

const Recomendation = styled.div`
  position: relative; /* 추가 */
  font-weight: bolder;
  font-size: 14px;
  cursor: pointer;
  height: 2.5em;
  display: flex;
  align-items: center;
  z-index: 2; /* 적당한 z-index 값 추가 */
`;

const RecomendationList = styled.ul`
  background-color: #d9d9d9;
  border: none;
  font-weight: bolder;
  font-size: 14px;
  cursor: pointer;
  position: absolute;
  top: 100%;
  left: 50%;
  transform: translateX(-50%);
  width: 140px;
  opacity: 0;
  transition: opacity 0.8s;
  pointer-events: none;
  z-index: 10; /* svg보다 높은 값 설정 */
  
  :hover {
    background-color: white;
  }

  ${Recomendation}:hover & {
    opacity: 1;
    pointer-events: auto;
  }
`;

const RecomendationItem = styled.li`
  text-align: center;
  width: 100%;
  height: 3em;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10; /* 필요시 추가 */
`;


const RecomendationDropdown = ({ children }) => {
    return <RecomendationList>{children}</RecomendationList>;
};

const Community = styled.button`
  background-color: transparent;
  border: none;
  font-weight: bolder;
  font-size: 14px;
  cursor: pointer;
    z-index: 3;
    
`;

const MyPage = styled.button`
  background-color: transparent;
  border: none;
  font-weight: bolder;
  font-size: 14px;
  cursor: pointer;
    z-index: 3;
`;


export default function TopBarComponent({ fontColor }) {
    const navigate = useNavigate();

    const handleGoHome = () => {
        navigate('/');
    };

    const handleGoReservation = () => {
        navigate('/reservation');
    };

    const handleGoReview = () => {
        navigate('/review');
    };

    const handleGoRoute = () => {
        navigate('/route');
    };

    const handleGoMbti = () => {
        navigate('/recomendation/mbti');
    };

    const handleGoPersonal = () => {
        navigate('/recomendation/personal');
    };
    const handleGoSimilarity = () => {
        navigate('/recomendation/similarity');
    };
    const handleGoSemanticSearch = () => {
        navigate('/search/semantic');
    };

    const handleGoMyPage = () => {
        navigate('/my');
    };

    const handleGoChatBot = () =>{
        navigate('/chatbot');
    };

    const handleGoAssistant = () => {
        navigate('/assistant');
    };

    const handleGoChat = () =>{
        navigate('/chat');
    };

    return (
        <Container>
            <TopBar>
                <LogoWrapper>
                    <Logo onClick={handleGoHome}></Logo>
                </LogoWrapper>
                <MenuContainer style={{color:fontColor}}>
                    <Recomendation>
                        여행지 예약
                        <RecomendationDropdown>
                            <RecomendationItem style={{color:"black"}} onClick={handleGoReservation}>글로플러 찾기</RecomendationItem>
                            <RecomendationItem style={{color:"black"}} onClick={handleGoChat}>채팅 목록</RecomendationItem>
                        </RecomendationDropdown>
                    </Recomendation>
                    <Recomendation>
                        여행지 추천
                        <RecomendationDropdown>
                            <RecomendationItem style={{color:"black"}} onClick={(e) => { e.stopPropagation(); handleGoMbti(); }}>MBTI 기반 추천</RecomendationItem>
                            <RecomendationItem style={{color:"black"}} onClick={(e) => { e.stopPropagation(); handleGoPersonal(); }}>키워드 추천</RecomendationItem>
                            <RecomendationItem style={{color:"black"}} onClick={(e) => { e.stopPropagation(); handleGoSimilarity(); }}>사용자 기반 추천</RecomendationItem>
                            <RecomendationItem style={{color:"black"}} onClick={(e) => { e.stopPropagation(); handleGoSemanticSearch(); }}>문장으로 찾기</RecomendationItem>
                        </RecomendationDropdown>
                    </Recomendation>
                    <Community style={{color:fontColor}} onClick={handleGoRoute}>루트 탐색</Community>
                    <Community style={{color:fontColor}} onClick={handleGoReview}>커뮤니티</Community>
                    <Community style={{color:fontColor}} onClick={handleGoChatBot}>챗봇</Community>
                    <Community style={{color:fontColor}} onClick={handleGoAssistant}>AI 도우미</Community>
                    <MyPage style={{color:fontColor}} onClick={handleGoMyPage}>마이페이지</MyPage>
                </MenuContainer>
            </TopBar>
        </Container>
    );
}
