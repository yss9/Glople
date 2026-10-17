import { useState, useEffect } from 'react';
import { useNavigate } from "react-router-dom";
import { Reset } from 'styled-reset';
import Cookies from "js-cookie";
import {jwtDecode} from "jwt-decode";
import { authFetch } from "../../../api/auth";
import React from 'react';
import {
    Explain, Horizonalline, Mbti,
    MbtiContainer, MbtiSubTitle, MbtiTitle, MbtiTitleContainer,
    WriteMbtiBtn, WriteMbtiBtnWrapper, Container, MbtiTitleWrapper, Wrapper, Explain2, GlopleCharacterImg
} from "./mbtistyle";
import TopBarComponent from "../../../components/TopBar/TopBar";
import {
    Place, PlaceContainer, PlaceName1, PlaceWrapper,
    RefreshBtn, SubTitle, SubTitleWrapper
} from "../Personal/personalstyle";
import GlopleCharacter from '../../../images/GlopleCharacter.png'

const getAuthToken = () => {
    return Cookies.get('jwt');
};

const GOOGLE_MAPS_API_KEY = 'AIzaSyCCkm0KlwV72tLvvEG9c4YuPHgo_j2_qz0';

const mbtiRecommendations = {
    ISTJ: ["워싱턴 D.C., 미국", "뮌헨, 독일", "교토, 일본", "암스테르담, 네덜란드", "취리히, 스위스", "에든버러, 스코틀랜드"],
    ISFJ: ["파리, 프랑스", "토스카나, 이탈리아", "찰스턴, 미국", "프라하, 체코", "교토, 일본", "블레드 호수, 슬로베니아"],
    INFJ: ["세도나, 미국", "산토리니, 그리스", "발리, 인도네시아", "레이캬비크, 아이슬란드", "교토, 일본", "부탄"],
    INTJ: ["베를린, 독일", "싱가포르", "두바이, 아랍에미리트", "도쿄, 일본", "스톡홀름, 스웨덴", "바르셀로나, 스페인"],
    ISTP: ["밴프 국립공원, 캐나다", "뉴질랜드", "알래스카, 미국", "아이슬란드", "스위스 알프스", "파타고니아, 아르헨티나"],
    ISFP: ["아말피 해안, 이탈리아", "발리, 인도네시아", "바르셀로나, 스페인", "피렌체, 이탈리아", "마우이, 미국", "산토리니, 그리스"],
    INFP: ["에든버러, 스코틀랜드", "애쉬빌, 미국", "프라하, 체코", "뉴올리언스, 미국", "키웨스트, 미국", "부탄"],
    INTP: ["실리콘밸리, 미국", "캠브리지, 영국","암스테르담, 네덜란드", "베를린, 독일", "레이캬비크, 아이슬란드", "도쿄, 일본"],
    ESTP: ["라스베이거스, 미국", "마이애미, 미국", "칸쿤, 멕시코", "시드니, 호주", "바르셀로나, 스페인", "리우데자네이루, 브라질"],
    ESFP: ["이비사, 스페인", "칸쿤, 멕시코", "뉴올리언스, 미국", "발리, 인도네시아", "마이애미, 미국", "시드니, 호주"],
    ENFP: ["내슈빌, 미국", "밴쿠버, 캐나다", "바르셀로나, 스페인", "레이캬비크, 아이슬란드", "암스테르담, 네덜란드", "시드니, 호주"],
    ENFJ: ["교토, 일본", "워싱턴 D.C., 미국", "파리, 프랑스", "로마, 이탈리아", "바르셀로나, 스페인", "부에노스아이레스, 아르헨티나"],
    ENTP: ["뉴욕, 미국", "베를린, 독일", "도쿄, 일본", "샌프란시스코, 미국", "오스틴, 미국", "런던, 영국"],
    ESTJ: ["뉴욕, 미국", "런던, 영국", "워싱턴 D.C., 미국", "싱가포르", "프랑크푸르트, 독일", "시드니, 호주"],
    ESFJ: ["피렌체, 이탈리아", "파리, 프랑스", "바르셀로나, 스페인", "내슈빌, 미국", "찰스턴, 미국", "몬트리올, 캐나다"],
    ENTJ: ["런던, 영국", "두바이, 아랍에미리트", "싱가포르", "샌프란시스코, 미국", "뉴욕, 미국", "도쿄, 일본",],
};


export default function RecMbtiPage() {
    const token = getAuthToken();
    const navigate = useNavigate();
    const [selectedDimensions, setSelectedDimensions] = useState({
        E_I: "E",
        N_S: "N",
        T_F: "T",
        J_P: "J"
    });
    const [displayedPlaces, setDisplayedPlaces] = useState([]);
    const [placeImages, setPlaceImages] = useState({});
    const [isGoogleMapsLoaded, setIsGoogleMapsLoaded] = useState(false);
    const [previousPlaces, setPreviousPlaces] = useState([]); // 이전에 보여준 장소를 저장

    useEffect(() => {
        loadGoogleMapsScript();
    }, []);

    useEffect(() => {
        if (isGoogleMapsLoaded) {
            refreshPlaces();
        }
    }, [isGoogleMapsLoaded, selectedDimensions]);

    const loadGoogleMapsScript = () => {
        const script = document.createElement('script');
        script.src = `https://maps.googleapis.com/maps/api/js?key=${GOOGLE_MAPS_API_KEY}&libraries=places`;
        script.async = true;
        script.onload = () => setIsGoogleMapsLoaded(true);
        document.body.appendChild(script);
    };

    const refreshPlaces = () => {
        const mbtiType = `${selectedDimensions.E_I}${selectedDimensions.N_S}${selectedDimensions.T_F}${selectedDimensions.J_P}`;
        const places = mbtiRecommendations[mbtiType] || [];

        // 이전 장소를 제외한 새로운 장소 목록 생성
        const filteredPlaces = places.filter(place => !previousPlaces.includes(place));

        // 충분한 새로운 장소가 없는 경우 이전 장소 초기화
        const availablePlaces = filteredPlaces.length >= 3 ? filteredPlaces : places;

        // 새로 고른 장소 랜덤으로 선택
        const shuffled = [...availablePlaces].sort(() => 0.5 - Math.random());
        const newPlaces = shuffled.slice(0, 3);

        setDisplayedPlaces(newPlaces);
        setPreviousPlaces(newPlaces); // 새로 선택한 장소를 이전 장소로 저장
    };

    const fetchUserInfo = async () => {
        try {
            const decodedToken = jwtDecode(token);
            const userId = decodedToken.userId;
            const response = await authFetch(`http://localhost:8080/api/users/my-info?userId=${userId}`);
            const data = await response.json();

            const mbti = data.mbti;
            const dimensions = {
                E_I: mbti.startsWith("I") ? "I" : "E",
                N_S: mbti.includes("/N") ? "N" : "S",
                T_F: mbti.includes("/T") ? "T" : "F",
                J_P: mbti.includes("/P") ? "P" : "J"
            };
            setSelectedDimensions(dimensions);
        } catch (error) {
            console.error("Failed to fetch user info:", error);
        }
    };

    useEffect(() => {
        const executeFunctions = async () => {
            await fetchUserInfo(); // fetchUserInfo가 완료될 때까지 대기
            mbtiTestResult(); // fetchUserInfo가 완료된 후 실행
        };
        executeFunctions();
    }, []);

    const fetchPlacePhoto = (placeName) => {
        if (!isGoogleMapsLoaded) return;

        const service = new window.google.maps.places.PlacesService(document.createElement('div'));
        const request = {
            query: placeName,
            fields: ['place_id', 'photos']
        };

        service.findPlaceFromQuery(request, (results, status) => {
            if (status === window.google.maps.places.PlacesServiceStatus.OK && results[0]) {
                const place = results[0];
                if (place.photos && place.photos.length > 0) {
                    const photoUrl = place.photos[0].getUrl({ maxWidth: 400 });
                    setPlaceImages(prevState => ({
                        ...prevState,
                        [placeName]: photoUrl
                    }));
                }
            } else {
                console.error(`Could not fetch place photo for ${placeName}`);
            }
        });
    };

    useEffect(() => {
        if (isGoogleMapsLoaded) {
            displayedPlaces.forEach((place) => {
                if (!placeImages[place]) {
                    fetchPlacePhoto(place);
                }
            });
        }
    }, [displayedPlaces, isGoogleMapsLoaded]);

    const handleGoInformation = (placename) => {
        navigate(`/recomendation/information/${placename}`);
    };

    const toggleDimension = (dimension) => {
        setSelectedDimensions(prevState => {
            const currentValue = prevState[dimension];
            return {
                ...prevState,
                [dimension]: currentValue === "E" ? "I" : currentValue === "I" ? "E" :
                    currentValue === "N" ? "S" : currentValue === "S" ? "N" :
                        currentValue === "T" ? "F" : currentValue === "F" ? "T" :
                            currentValue === "J" ? "P" : "J"
            };
        });
    };

    const setSignupDataWithExpiry = (key, value, expiryInMinutes) => {
        const now = new Date();
        const dataWithExpiry = {
            value, // 실제 데이터
            expiry: now.getTime() + expiryInMinutes * 60 * 1000 // 만료 시간 (밀리초 단위)
        };
        localStorage.setItem(key, JSON.stringify(dataWithExpiry));
    }; // mbti 검사 페이지 이동하기 전, 현재 페이지 주소 저장하기 (돌아오기 위해)

    const getSignupDataWithExpiry = (key) => {
        const itemStr = localStorage.getItem(key);
        if (!itemStr) {
            return null; // 데이터가 없는 경우
        }
        const item = JSON.parse(itemStr);
        const now = new Date();
        if (now.getTime() > item.expiry) {
            // 만료된 경우 데이터 삭제
            localStorage.removeItem(key);
            return null;
        }
        return item.value;
    }; // mbti 검사 결과 가져오기

    const mbtiTestResult = () => {
        const mbtiSavedData = getSignupDataWithExpiry('mbtiResult');
        if (mbtiSavedData) {
            const parsedArray = JSON.parse(mbtiSavedData);
            console.log('parsedArray',parsedArray)
            const dimensions = {
                E_I: parsedArray[0].split(' ')[0],
                N_S: parsedArray[1].split(' ')[0],
                T_F: parsedArray[2].split(' ')[0],
                J_P: parsedArray[3].split(' ')[0],
            };
            console.log('dimensions',dimensions)
            setSelectedDimensions(dimensions);
        }
    }

    const onClickMBTItest = () => {
        setSignupDataWithExpiry('pageData', '/recomendation/mbti', 5);
        navigate('/MBTItest')
    } // mbti 검사하러 가기 버튼 클릭

    return (
        <>
            <Reset />
            <div style={{ height: '55px' }}></div>
            <TopBarComponent />
            <Wrapper>
                <Container>
                    <MbtiTitleContainer>
                        <MbtiTitleWrapper>
                            <MbtiTitle>MBTI 기반 추천</MbtiTitle>
                            <MbtiSubTitle>
                                <p>나와 비슷한 성격의 소유자들은 어떤 여행지를 선호하고 있을까요?</p>
                                <p>나의 MBTI와 나와 비슷한 성향의 사용자 데이터를 통해 맞춤형 여행 정보를 제공합니다.</p>
                            </MbtiSubTitle>
                            <WriteMbtiBtnWrapper>
                                <WriteMbtiBtn onClick={onClickMBTItest}>
                                    <p>MBTI 검사하기</p>
                                    <svg width="12" height="18" viewBox="0 0 12 18" fill="none"
                                         xmlns="http://www.w3.org/2000/svg">
                                        <path d="M1 1L10 9L1 17" stroke="white" stroke-width="2"/>
                                    </svg>
                                </WriteMbtiBtn>
                            </WriteMbtiBtnWrapper>
                        </MbtiTitleWrapper>
                        <Horizonalline></Horizonalline>
                        <Explain2>
                            <GlopleCharacterImg src={GlopleCharacter}/>
                            <p>MBTI를 클릭해보세요!</p>
                        </Explain2>
                        <MbtiContainer>
                            <Mbti onClick={() => toggleDimension("E_I")}>{selectedDimensions.E_I}</Mbti>
                            <Mbti onClick={() => toggleDimension("N_S")}>{selectedDimensions.N_S}</Mbti>
                            <Mbti onClick={() => toggleDimension("T_F")}>{selectedDimensions.T_F}</Mbti>
                            <Mbti onClick={() => toggleDimension("J_P")}>{selectedDimensions.J_P}</Mbti>
                            <Explain>가 선호하는 여행지를 알려드릴게요.</Explain>
                        </MbtiContainer>
                        <SubTitleWrapper>
                            <SubTitle></SubTitle>
                            <RefreshBtn onClick={refreshPlaces}>
                                <p>추천 새로고침</p>
                                <svg width="16" height="19" viewBox="0 0 16 19" fill="none"
                                     xmlns="http://www.w3.org/2000/svg">
                                    <g clip-path="url(#clip0_484_188)">
                                        <path
                                            d="M7 18.45C4.98 18.2 3.31 17.32 1.99 15.81C0.66 14.3 0 12.53 0 10.5C0 9.4 0.22 8.35 0.65 7.34C1.08 6.33 1.7 5.45 2.5 4.7L3.93 6.13C3.3 6.7 2.82 7.36 2.49 8.11C2.16 8.86 2 9.66 2 10.51C2 11.98 2.47 13.27 3.4 14.4C4.33 15.53 5.53 16.21 7 16.46V18.46V18.45ZM9 18.45V16.45C10.45 16.18 11.65 15.49 12.59 14.37C13.53 13.25 14 11.96 14 10.49C14 8.82 13.42 7.41 12.25 6.24C11.08 5.07 9.67 4.49 8 4.49H7.92L9.02 5.59L7.62 6.99L4.12 3.49L7.63 0L9.03 1.4L7.93 2.5H8.01C10.24 2.5 12.14 3.28 13.69 4.83C15.24 6.38 16.02 8.27 16.02 10.51C16.02 12.53 15.36 14.29 14.03 15.8C12.7 17.31 11.03 18.2 9.02 18.46L9 18.45Z"
                                            fill="#5F6368"/>
                                    </g>
                                    <defs>
                                        <clipPath id="clip0_484_188">
                                            <rect width="16" height="18.45" fill="white"/>
                                        </clipPath>
                                    </defs>
                                </svg>
                            </RefreshBtn>
                        </SubTitleWrapper>
                        <PlaceContainer>
                            {displayedPlaces.map(place => (
                                <PlaceWrapper key={place} onClick={() => handleGoInformation(place)}>
                                    {placeImages[place] ? (
                                        <Place src={placeImages[place]} alt={place}/>
                                    ) : (
                                        <p>Loading image...</p>
                                    )}
                                    <PlaceName1>{place}</PlaceName1>
                                </PlaceWrapper>
                            ))}
                        </PlaceContainer>
                    </MbtiTitleContainer>
                </Container>
            </Wrapper>
        </>
    );
}