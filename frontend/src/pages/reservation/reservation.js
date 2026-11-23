import { Reset } from "styled-reset";
import Agent from "../../components/Agent/Agent";
import Progress from "../../components/Agent/progress";
import axios from "axios";
import {
    SubTitle, SubTitleWrapper, SearchBarWrapper, SearchBarContainer,
    Magnifier, Search, Icon, SubTitle2,
    AgentContainer, Agents, GaugeBar, GaugeBarWrapper,
    SelectContainer, Select, SelectWrapper,  Agents2, DownWrapper,
    Container, Wrapper, ProgressContainer
} from './reservationstyle';
import React, { useState, useEffect } from "react";
import Agent2 from "../../components/Agent/Agent2";
import TopBarComponent from "../../components/TopBar/TopBar";
import Profile from '../../images/img_1.png';
import { useNavigate } from "react-router-dom";
import Cookies from "js-cookie";
import {jwtDecode} from "jwt-decode";
import { authFetch } from "../../api/auth";

const processProfilePicture = (profilePicture) => {
    if (profilePicture) {
        return `http://localhost:8080/${profilePicture.replace('static\\', '').replace(/\\/g, '/')}`;
    } else {
        return Profile;
    }
};


export default function ReservationPage() {
    const [agentData, setAgentData] = useState([]);
    const [agent2Data, setAgent2Data] = useState([]);
    const [activeIndex, setActiveIndex] = useState(0);
    const [selectedCountry, setSelectedCountry] = useState("");
    const [searchQuery, setSearchQuery] = useState("");
    const [latestProgressData, setLatestProgressData] = useState(null);
    const [profilePictureUrl, setProfilePictureUrl] = useState(null);
    const navigate = useNavigate();

    const reviewsPerPage = 2;
    const startIndex = activeIndex * reviewsPerPage;
    const visibleAgents = agentData.slice(startIndex, startIndex + reviewsPerPage);
    const totalIndicators = Math.ceil(agentData.length / reviewsPerPage);

    useEffect(() => {
        const fetchProgressData = async () => {
            try {
                const token = Cookies.get('jwt');
                if (!token) {
                    console.error('토큰이 없습니다.');
                    return;
                }
                const decodedToken = jwtDecode(token);
                const nickname = decodedToken.nickname;

                const response = await axios.get(`http://localhost:8080/api/booking/all-progress?nickname=${nickname}`, {
                    headers: {
                        'Authorization': `Bearer ${token}`
                    }
                });

                const progressArray = response.data;
                if (progressArray.length > 0) {
                    setLatestProgressData(progressArray[progressArray.length - 1]);
                }

                console.log(progressArray);
            } catch (error) {
                console.error('데이터를 가져오는 중 오류 발생:', error);
            }
        };

        fetchProgressData();
    }, []);


    useEffect(() => {
        axios.get("http://localhost:8080/api/users/agents")
            .then((response) => {
                const fetchedData = response.data.map((agent) => ({
                    author: agent.nickname,
                    introduce: agent.introduction || '소개 내용이 없습니다.',
                    hashtags: agent.hashtags || [],
                    spec: agent.specIntroduction || [],
                    image: processProfilePicture(agent.profilePicture),
                    score: agent.averageReviewRating,
                    number: '0',
                    agentreview: '',
                }));
                setAgentData(fetchedData);
            })
            .catch((error) => console.error("Error fetching agents:", error));
    }, []);

    useEffect(() => {
        if (selectedCountry) {
            axios.get(`http://localhost:8080/api/users/agents?country=${selectedCountry}`)
                .then((response) => {
                    const fetchedAgent2Data = response.data.map((agent) => ({
                        author: agent.nickname,
                        introduce: agent.introduction,
                        hashtags: agent.hashtags || [],
                        spec: agent.specIntroduction || [],
                        image: processProfilePicture(agent.profilePicture),
                    }));
                    setAgent2Data(fetchedAgent2Data);
                })
                .catch((error) => console.error("Error fetching Agent2 data:", error));
        }
    }, [selectedCountry]);
    const handleGaugeClick = (event) => {
        const boundingRect = event.currentTarget.getBoundingClientRect();
        const clickX = event.clientX - boundingRect.left;
        const completionPercentage = (clickX / boundingRect.width) * 100;
        const newIndex = Math.floor((completionPercentage / 100) * totalIndicators);
        setActiveIndex(newIndex);
    };

    const handleCountryChange = (event) => {
        setSelectedCountry(event.target.value);
    };

    const handleSearchInputChange = (event) => {
        setSearchQuery(event.target.value);
    };

    const handleSearchClick = () => {
        if (searchQuery) {
            // Navigate to search results page with query
            navigate(`/search-resultsforglopler?hashtag=${encodeURIComponent(searchQuery)}`);
        }
    };

    const handleKeyPress = (e) => {
        if (e.key === "Enter") {
            handleSearchClick();
        }
    };

    useEffect(() => {
        const fetchProfilePicture = async () => {
            if (latestProgressData?.agentNickname) {
                try {
                    const response = await authFetch(
                        `http://localhost:8080/api/users/profile-picture?nickname=${latestProgressData.agentNickname}`
                    );
                    const picturePath = await response.text(); // Assuming the response is the picture path as a string
                    const processedUrl = processProfilePicture(picturePath);
                    setProfilePictureUrl(processedUrl);
                } catch (error) {
                    console.error('Error fetching profile picture:', error);
                    setProfilePictureUrl(Profile); // Fallback to default picture on error
                }
            }
        };

        fetchProfilePicture();
    }, [latestProgressData?.agentNickname]);


    return (
        <>
            <Reset />
            <Container>
                <Wrapper>
                    <div style={{ height: '55px' }}></div>
                    <TopBarComponent />
                    <SubTitleWrapper>
                        <SubTitle>
                            검색을 통해 원하는 <p>글로플러</p>와 예약을 진행할 수 있어요.
                        </SubTitle>
                    </SubTitleWrapper>
                    <SearchBarWrapper>
                        <Icon>
                            <svg
                                width="30"
                                height="30"
                                viewBox="0 0 32 32"
                                fill="none"
                                xmlns="http://www.w3.org/2000/svg"
                            >
                                <g clipPath="url(#clip0_74_14)">
                                    <path
                                        d="M14.2222 32V21.3333H17.7778V24.8889H32V28.4444H17.7778V32H14.2222ZM0 28.4444V24.8889H10.6667V28.4444H0ZM7.11111 21.3333V17.7778H0V14.2222H7.11111V10.6667H10.6667V21.3333H7.11111ZM14.2222 17.7778V14.2222H32V17.7778H14.2222ZM21.3333 10.6667V0H24.8889V3.55556H32V7.11111H24.8889V10.6667H21.3333ZM0 7.11111V3.55556H17.7778V7.11111H0Z"
                                        fill="#5F6368"
                                    />
                                </g>
                                <defs>
                                    <clipPath id="clip0_74_14">
                                        <rect width="32" height="32" fill="white" />
                                    </clipPath>
                                </defs>
                            </svg>
                        </Icon>
                        <SearchBarContainer>
                            <Search
                                placeholder="여행 관련 키워드를 검색해보세요."
                                onKeyPress={handleKeyPress}
                                value={searchQuery}
                                onChange={handleSearchInputChange}
                            />
                            <Magnifier onClick={handleSearchClick}>
                                <svg
                                    width="27"
                                    height="27"
                                    viewBox="0 0 27 27"
                                    fill="none"
                                    xmlns="http://www.w3.org/2000/svg"
                                >
                                    <path
                                        d="M24.9 27L15.45 17.55C14.7 18.15 13.8375 18.625 12.8625 18.975C11.8875 19.325 10.85 19.5 9.75 19.5C7.025 19.5 4.719 18.556 2.832 16.668C0.945001 14.78 0.00100079 12.474 7.93651e-07 9.75C-0.000999206 7.026 0.943001 4.72 2.832 2.832C4.721 0.944 7.027 0 9.75 0C12.473 0 14.7795 0.944 16.6695 2.832C18.5595 4.72 19.503 7.026 19.5 9.75C19.5 10.85 19.325 11.8875 18.975 12.8625C18.625 13.8375 18.15 14.7 17.55 15.45L27 24.9L24.9 27ZM9.75 16.5C11.625 16.5 13.219 15.844 14.532 14.532C15.845 13.22 16.501 11.626 16.5 9.75C16.499 7.874 15.843 6.2805 14.532 4.9695C13.221 3.6585 11.627 3.002 9.75 3C7.873 2.998 6.2795 3.6545 4.9695 4.9695C3.6595 6.2845 3.003 7.878 3 9.75C2.997 11.622 3.6535 13.216 4.9695 14.532C6.2855 15.848 7.879 16.504 9.75 16.5Z"
                                        fill="#4E53EE"
                                    />
                                </svg>
                            </Magnifier>
                        </SearchBarContainer>
                    </SearchBarWrapper>
                    {latestProgressData ? (
                        <>
                            <SubTitleWrapper>
                                <SubTitle2>
                                    <p>{latestProgressData.agentNickname} 님</p>과 예약을 진행한 적이 있어요.
                                </SubTitle2>
                            </SubTitleWrapper>
                            <ProgressContainer>
                                <Progress
                                    currentStage={latestProgressData.progress}
                                    agentName={latestProgressData.agentNickname}
                                    profilePictureUrl={profilePictureUrl} // Pass the processed URL
                                />
                            </ProgressContainer>
                        </>
                    ) : (
                        <p>진행 데이터가 없습니다.</p>
                    )}
                    <SubTitleWrapper>
                        <SubTitle2>최근 등록된 글로플러 리스트예요.</SubTitle2>
                    </SubTitleWrapper>
                    <AgentContainer>
                        <Agents>
                            {visibleAgents.map((review, index) => (
                                <Agent key={index} review={review} />
                            ))}
                        </Agents>
                        <GaugeBarWrapper>
                            <GaugeBar
                                completion={(activeIndex + 1) / totalIndicators * 100}
                                onClick={handleGaugeClick}
                            />
                        </GaugeBarWrapper>
                    </AgentContainer>
                    <DownWrapper>
                        <SubTitleWrapper>
                            <SubTitle2>글로플러 찾아보기</SubTitle2>
                        </SubTitleWrapper>
                        <SelectWrapper>
                            <SelectContainer>
                                <Select value={selectedCountry} onChange={handleCountryChange}>
                                    <option value="">국가 선택</option>
                                    <optgroup label="America">
                                        <option value="미국">미국</option>
                                    </optgroup>
                                    <optgroup label="Europe">
                                        <option value="이탈리아">이탈리아</option>
                                    </optgroup>
                                    <optgroup label="Asia">
                                        <option value="일본">일본</option>
                                    </optgroup>
                                </Select>
                            </SelectContainer>
                        </SelectWrapper>
                        <AgentContainer>
                            <Agents2>
                                {agent2Data.slice(0, 3).map((review, index) => (
                                    <Agent2 key={index} review={review} />
                                ))}
                            </Agents2>
                            <Agents2>
                                {agent2Data.slice(3, 6).map((review, index) => (
                                    <Agent2 key={index} review={review} />
                                ))}
                            </Agents2>
                        </AgentContainer>
                    </DownWrapper>
                </Wrapper>
            </Container>
        </>

    );
}
