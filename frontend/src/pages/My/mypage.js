    import React, { useEffect, useState } from "react";
    import axios from "axios";
    import Cookies from "js-cookie";
    import {jwtDecode} from "jwt-decode";
    import TopBarComponent from "../../components/TopBar/TopBar";
    import MyReview from "../../components/MyPageDetail/MyReview";
    import MyReservation from "../../components/MyPageDetail/MyReservation";
    import MyProfile2 from '../../public/Img/forprofile/img_1.png'; // 임시 이미지
    import { Reset } from "styled-reset";
    import {
    GloplerSettingBtn, Introduce, IntroduceWrapper,
    Mbti, MyPageDetailContainer, MyProfileContainer, NameWrapper, PlanSettingBtn, PointSettingBtn,
    ProfileImg, ProfileSettingBtn, SettingContainer, SettingWrapper, Content,
    Container, Highlight, SelectorContainer, SelectorItem, SelectorWrapper,
    SelectorItem2, Highlight2, SelectorWrapper2, Wrapper
} from './mypagestyle';
    import {useNavigate} from "react-router-dom";

    const getAuthToken = () => {
        return Cookies.get('jwt');
    };

    const Mypage = () => {
        const [activeItem, setActiveItem] = useState('Left');
        const [highlightPosition, setHighlightPosition] = useState({ left: 0, width: 0 });
        const [userData, setUserData] = useState(null); // 사용자 데이터를 저장할 상태
        const token = getAuthToken();

        useEffect(() => {
            const selectedElement = document.querySelector(`div[data-item="${activeItem}"]`);
            if (selectedElement) {
                const left = selectedElement.offsetLeft;
                const width = selectedElement.offsetWidth;
                setHighlightPosition({ left, width });
            }
        }, [activeItem]);

        useEffect(() => {
            const fetchUserData = async () => {
                try {
                    if (token) {
                        // 토큰에서 사용자 아이디 추출
                        const decodedToken = jwtDecode(token);
                        const userId = decodedToken.userId;

                        const response = await axios.get(`http://localhost:8080/api/users/my-info?userId=${userId}`, {
                            headers: {
                                'Authorization': `Bearer ${token}`
                            }
                        });
                        setUserData(response.data);
                    } else {
                        console.error('No JWT token found in cookies');
                    }
                } catch (error) {
                    console.error('Failed to fetch user data', error);
                }
            };

            fetchUserData();
        }, [token]);

        function selectItem(event) {
            const target = event.target;
            const item = target.dataset.item;

            if (item === '진행 목록' || item === 'Righ') {
                setActiveItem('Righ');
            } else if (item === '게시물 확인' || item === 'Left') {
                setActiveItem('Left');
            }
        }

        const ActiveTextStyle = {
            color: 'gray' // 원하는 색상으로 변경
        };

        const InactiveTextStyle = {
            color: '#efefef' // 기본 색상으로 변경
        };

        const navigate = useNavigate();

        const handleGloplerButtonClick = () => {
            if (userData?.agent) {
                navigate('/agentperspective');
            } else {
                navigate('/RegisterAgent');
            }
        };

        const processMbti = (mbtiString) => {
            return mbtiString
                .split('/')
                .map(item => item.charAt(0))
                .join('');
        };

        const processProfilePicture = (profilePicture) => {
            if (profilePicture) {
                return `http://localhost:8080/${profilePicture.replace('static\\', '').replace(/\\/g, '/')}`;
            } else {
                return MyProfile2;
            }
        };
        //원래 introduce태그에 {userdata.statusMessage}해야함.
        return (
            <>
                <Reset />
                <div style={{ height: '55px' }}></div>
                <TopBarComponent />
                <Wrapper>
                    {userData && (
                        <MyProfileContainer>
                            <ProfileImg
                                src={processProfilePicture(userData.profilePicture)}
                                alt="프로필 이미지"
                            />
                            <NameWrapper>{userData.nickname}</NameWrapper>
                            <IntroduceWrapper>
                                <Mbti>{processMbti(userData.mbti)}</Mbti>
                                <Introduce>나는야 여행을 좋아하는 20대</Introduce>
                            </IntroduceWrapper>
                        </MyProfileContainer>
                    )}
                    <SettingWrapper>
                        <SettingContainer>
                            <ProfileSettingBtn onClick={() => navigate('/myprofile')}>
                                <svg width="21" height="20" viewBox="0 0 21 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                                    <g clipPath="url(#clip0_767_1672)">
                                        <path
                                            d="M7.3 20L6.9 16.8C6.68 16.72 6.48 16.62 6.29 16.5C6.1 16.38 5.91 16.26 5.73 16.12L2.75 17.37L0 12.63L2.58 10.68C2.56 10.56 2.55 10.45 2.55 10.34V9.66C2.55 9.55 2.55 9.44 2.58 9.32L0 7.37L2.75 2.63L5.73 3.88C5.91 3.75 6.11 3.62 6.31 3.5C6.51 3.38 6.71 3.28 6.91 3.2L7.31 0H12.81L13.21 3.2C13.43 3.28 13.63 3.38 13.82 3.5C14.01 3.62 14.2 3.74 14.38 3.88L17.36 2.63L20.11 7.38L17.53 9.33C17.55 9.45 17.56 9.56 17.56 9.67V10.35C17.56 10.46 17.54 10.57 17.51 10.69L20.09 12.64L17.34 17.39L14.39 16.14C14.21 16.27 14.01 16.4 13.81 16.52C13.61 16.64 13.41 16.74 13.21 16.82L12.81 20.02H7.31L7.3 20ZM9.05 18H11.03L11.38 15.35C11.9 15.22 12.38 15.02 12.82 14.76C13.26 14.5 13.67 14.19 14.03 13.82L16.51 14.85L17.49 13.15L15.34 11.52C15.42 11.29 15.48 11.04 15.52 10.78C15.56 10.52 15.57 10.26 15.57 9.99C15.57 9.72 15.55 9.46 15.52 9.2C15.49 8.94 15.43 8.7 15.34 8.46L17.49 6.83L16.51 5.13L14.03 6.18C13.66 5.8 13.26 5.48 12.82 5.22C12.38 4.96 11.9 4.77 11.38 4.63L11.05 1.98H9.07L8.72 4.63C8.2 4.76 7.72 4.96 7.28 5.22C6.84 5.48 6.43 5.79 6.07 6.16L3.59 5.13L2.61 6.83L4.76 8.43C4.68 8.68 4.62 8.93 4.58 9.18C4.54 9.43 4.53 9.7 4.53 9.98C4.53 10.25 4.55 10.51 4.58 10.76C4.61 11.01 4.67 11.26 4.76 11.51L2.61 13.14L3.59 14.84L6.07 13.79C6.44 14.17 6.84 14.49 7.28 14.75C7.72 15.01 8.2 15.2 8.72 15.34L9.05 17.99V18ZM10.1 13.5C11.07 13.5 11.89 13.16 12.58 12.47C13.27 11.78 13.61 10.96 13.61 9.99C13.61 9.02 13.27 8.2 12.58 7.51C11.89 6.82 11.07 6.48 10.1 6.48C9.13 6.48 8.29 6.82 7.61 7.51C6.93 8.2 6.6 9.02 6.6 9.99C6.6 10.96 6.94 11.78 7.61 12.47C8.28 13.16 9.11 13.5 10.1 13.5Z"
                                            fill="black"/>
                                    </g>
                                    <defs>
                                        <clipPath id="clip0_767_1672">
                                            <rect width="20.1" height="20" fill="white"/>
                                        </clipPath>
                                    </defs>
                                </svg>
                                <p>프로필 관리</p>
                            </ProfileSettingBtn>
                            <GloplerSettingBtn onClick={handleGloplerButtonClick}>
                                <svg width="16" height="16" viewBox="0 0 16 16" fill="none"
                                     xmlns="http://www.w3.org/2000/svg">
                                    <path
                                        d="M8 8.00047C6.9 8.00047 5.96 7.61047 5.17 6.82047C4.39 6.04047 3.99 5.09047 3.99 3.99047C3.99 2.89047 4.38 1.95047 5.17 1.16047C5.96 0.370469 6.9 -0.0195312 8 -0.0195312C9.1 -0.0195312 10.04 0.370469 10.83 1.16047C11.62 1.95047 12.01 2.89047 12.01 3.99047C12.01 5.09047 11.62 6.03047 10.83 6.82047C10.05 7.60047 9.1 8.00047 8 8.00047ZM0 16.0005V13.2005C0 12.6305 0.15 12.1105 0.44 11.6405C0.73 11.1705 1.12 10.8005 1.6 10.5505C2.63 10.0305 3.68 9.65047 4.75 9.39047C5.82 9.13047 6.9 9.00047 8 9.00047C9.1 9.00047 10.18 9.13047 11.25 9.39047C12.32 9.65047 13.37 10.0405 14.4 10.5505C14.88 10.8005 15.27 11.1605 15.56 11.6405C15.85 12.1205 16 12.6405 16 13.2005V16.0005H0ZM2 14.0005H14V13.2005C14 13.0205 13.95 12.8505 13.86 12.7005C13.77 12.5505 13.65 12.4305 13.5 12.3505C12.6 11.9005 11.69 11.5605 10.77 11.3405C9.85 11.1205 8.93 11.0005 7.99 11.0005C7.05 11.0005 6.13 11.1105 5.21 11.3405C4.29 11.5705 3.38 11.9005 2.48 12.3505C2.33 12.4305 2.21 12.5505 2.12 12.7005C2.03 12.8505 1.98 13.0205 1.98 13.2005V14.0005H2ZM8 6.00047C8.55 6.00047 9.02 5.80047 9.41 5.41047C9.8 5.02047 10 4.55047 10 4.00047C10 3.45047 9.8 2.98047 9.41 2.59047C9.02 2.20047 8.55 2.00047 8 2.00047C7.45 2.00047 6.98 2.20047 6.59 2.59047C6.2 2.98047 6 3.45047 6 4.00047C6 4.55047 6.2 5.02047 6.59 5.41047C6.98 5.80047 7.45 6.00047 8 6.00047Z"
                                        fill="black"/>
                                </svg>
                                <p>{userData?.agent ? '글로플러 설정' : '글로플러 신청'}</p>
                            </GloplerSettingBtn>
                            <PointSettingBtn>
                            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                                    <g clipPath="url(#clip0_767_1669)">
                                        <path
                                            d="M13.5 9.00047C13.92 9.00047 14.27 8.85047 14.56 8.56047C14.85 8.27047 15 7.91047 15 7.50047C15 7.09047 14.85 6.73047 14.56 6.44047C14.27 6.15047 13.91 6.00047 13.5 6.00047C13.09 6.00047 12.73 6.15047 12.44 6.44047C12.15 6.73047 12 7.09047 12 7.50047C12 7.91047 12.15 8.27047 12.44 8.56047C12.73 8.85047 13.09 9.00047 13.5 9.00047ZM6.49999 9.00047C6.91999 9.00047 7.26999 8.85047 7.55999 8.56047C7.84999 8.27047 7.99999 7.91047 7.99999 7.50047C7.99999 7.09047 7.84999 6.73047 7.55999 6.44047C7.26999 6.15047 6.90999 6.00047 6.49999 6.00047C6.08999 6.00047 5.72999 6.15047 5.43999 6.44047C5.14999 6.73047 4.99999 7.09047 4.99999 7.50047C4.99999 7.91047 5.14999 8.27047 5.43999 8.56047C5.72999 8.85047 6.08999 9.00047 6.49999 9.00047ZM9.99999 15.5005C11.13 15.5005 12.16 15.1805 13.09 14.5405C14.02 13.9005 14.69 13.0505 15.1 12.0005H13.45C13.08 12.6205 12.6 13.1005 11.99 13.4605C11.38 13.8205 10.72 14.0005 9.99999 14.0005C9.27999 14.0005 8.61999 13.8205 8.00999 13.4605C7.39999 13.1005 6.90999 12.6105 6.54999 12.0005H4.89999C5.31999 13.0505 5.98999 13.9005 6.90999 14.5405C7.82999 15.1805 8.85999 15.5005 9.99999 15.5005ZM9.99999 20.0005C8.61999 20.0005 7.31999 19.7405 6.09999 19.2105C4.87999 18.6805 3.81999 17.9705 2.91999 17.0705C2.01999 16.1705 1.30999 15.1105 0.77999 13.8905C0.24999 12.6705 -0.0100098 11.3705 -0.0100098 9.99047C-0.0100098 8.61047 0.24999 7.31047 0.77999 6.09047C1.30999 4.87047 2.01999 3.81047 2.91999 2.91047C3.81999 2.01047 4.87999 1.30047 6.09999 0.770469C7.31999 0.240469 8.61999 -0.0195312 9.99999 -0.0195312C11.38 -0.0195312 12.68 0.240469 13.9 0.770469C15.12 1.30047 16.18 2.01047 17.08 2.91047C17.98 3.81047 18.69 4.87047 19.22 6.09047C19.75 7.31047 20.01 8.61047 20.01 9.99047C20.01 11.3705 19.75 12.6705 19.22 13.8905C18.69 15.1105 17.98 16.1705 17.08 17.0705C16.18 17.9705 15.12 18.6805 13.9 19.2105C12.68 19.7405 11.38 20.0005 9.99999 20.0005ZM9.99999 18.0005C12.23 18.0005 14.13 17.2205 15.68 15.6705C17.23 14.1205 18.01 12.2305 18.01 9.99047C18.01 7.75047 17.23 5.86047 15.68 4.31047C14.13 2.76047 12.24 1.98047 9.99999 1.98047C7.75999 1.98047 5.86999 2.76047 4.31999 4.31047C2.76999 5.86047 1.98999 7.75047 1.98999 9.99047C1.98999 12.2305 2.76999 14.1205 4.31999 15.6705C5.86999 17.2205 7.75999 18.0005 9.99999 18.0005Z"
                                            fill="black"/>
                                    </g>
                                    <defs>
                                        <clipPath id="clip0_767_1669">
                                            <rect width="20" height="20" fill="white"/>
                                        </clipPath>
                                    </defs>
                                </svg>
                                <p>포인트 관리</p>
                            </PointSettingBtn>
                            <PlanSettingBtn onClick={() => navigate('/my/plans')}>
                                <svg width="20" height="20" viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg">
                                    <path
                                        d="M5 0V2H3C1.9 2 1 2.9 1 4V18C1 19.1 1.9 20 3 20H17C18.1 20 19 19.1 19 18V4C19 2.9 18.1 2 17 2H15V0H13V2H7V0H5ZM3 7H17V18H3V7ZM5 9V11H11V9H5ZM5 13V15H14V13H5Z"
                                        fill="black"/>
                                </svg>
                                <p>내 여행 일정</p>
                            </PlanSettingBtn>
                        </SettingContainer>
                    </SettingWrapper>
                    <MyPageDetailContainer>
                        <Container>
                            <SelectorContainer>
                                <SelectorWrapper2>
                                    <Highlight2 />
                                    <SelectorItem2
                                        data-item="Left"
                                        onClick={selectItem}
                                        style={activeItem === 'Left' ? ActiveTextStyle : InactiveTextStyle}
                                    >
                                        게시물 확인
                                    </SelectorItem2>
                                    <SelectorItem2
                                        data-item="Righ"
                                        onClick={selectItem}
                                        style={activeItem === 'Righ' ? ActiveTextStyle : InactiveTextStyle}
                                    >
                                        진행 목록
                                    </SelectorItem2>
                                </SelectorWrapper2>
                            </SelectorContainer>
                            <SelectorContainer>
                                <SelectorWrapper>
                                    <Highlight left={highlightPosition.left} width={highlightPosition.width} />
                                    <SelectorItem active={activeItem === 'Left'} data-item="Left"
                                                  onClick={selectItem}>Left</SelectorItem>
                                    <SelectorItem active={activeItem === 'Righ'} data-item="Righ"
                                                  onClick={selectItem}>Righ</SelectorItem>
                                </SelectorWrapper>
                            </SelectorContainer>
                            <Content>
                                {activeItem === 'Left' && <MyReview />}
                                {activeItem === 'Righ' && <MyReservation />}
                            </Content>
                        </Container>
                    </MyPageDetailContainer>
                </Wrapper>
            </>
        );
    };

    export default Mypage;
