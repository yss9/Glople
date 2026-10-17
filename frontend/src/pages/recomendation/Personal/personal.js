import { Reset } from 'styled-reset';
import React, { useEffect, useState, useCallback } from 'react';
import TopBarComponent from "../../../components/TopBar/TopBar";
import {
    HashTag, HashTagContaienr,
    KeywordBtn, KeywordContainer, Place, PlaceContainer, PlaceName1, PlaceWrapper, RefreshBtn,
    SubTitle, SubTitleWrapper, Title, TitleWrapper, Wrapper,
    BestCourseContainer,
    BestCourses, BestWrapper, Notice, GaugeBar, GaugeBarWrapper, Container
} from "./personalstyle";
import BestCourse from "../../../components/BestCourse/BestCourse";
import { useLocation, useNavigate } from "react-router-dom";

const GOOGLE_MAPS_API_KEY = 'AIzaSyCCkm0KlwV72tLvvEG9c4YuPHgo_j2_qz0';

const PersonalRecData = [];
const CourseData = [];



export default function PersonalRecPage() {
    const [activeIndex, setActiveIndex] = useState(0);
    const [placeImages, setPlaceImages] = useState({});
    const [displayedPlaces, setDisplayedPlaces] = useState([]);
    const [isGoogleMapsScriptLoaded, setIsGoogleMapsScriptLoaded] = useState(false);
    const [isResultsLoaded, setIsResultsLoaded] = useState(false);
    const [previousPlaces, setPreviousPlaces] = useState([]);

    const location = useLocation();
    const { results, KeywordData = [] } = location.state || {};
    console.log(results);
    const reviewsPerPage = 4;
    const startIndex = activeIndex * reviewsPerPage;
    const visibleAgents = CourseData.slice(startIndex, startIndex + reviewsPerPage);
    const totalIndicators = Math.ceil(CourseData.length / reviewsPerPage);
    const navigate = useNavigate();

    const loadGoogleMapsScript = useCallback(() => {
        return new Promise((resolve) => {
            if (window.google && window.google.maps && window.google.maps.places) {
                resolve();
            } else {
                const script = document.createElement('script');
                script.src = `https://maps.googleapis.com/maps/api/js?key=${GOOGLE_MAPS_API_KEY}&libraries=places`;
                script.async = true;
                script.onload = () => resolve();
                document.body.appendChild(script);
            }
        });
    }, []);

    useEffect(() => {
        loadGoogleMapsScript().then(() => {
            setIsGoogleMapsScriptLoaded(true);
        });
    }, [loadGoogleMapsScript]);

    useEffect(() => {
        if (!isResultsLoaded && results) {
            // place: 상세 화면에서 설명문과 출처를 보여줄 수 있도록 추천 결과 전체를 함께 둔다.
            const updatedPersonalRecData = results.slice(0, 9).map(item => ({ placename: item.destName, place: item }));
            const updatedCourseData = results.slice(9, 21).map(item => ({ placename: item.destName, place: item }));

            setDisplayedPlaces(updatedPersonalRecData.slice(0, 3));
            PersonalRecData.push(...updatedPersonalRecData);
            CourseData.push(...updatedCourseData);
            setIsResultsLoaded(true);
        }

        if (!results || results.length === 0) {
            navigate('/keyword');
        }
    }, [results, isResultsLoaded, navigate]);

    const fetchPlacePhotos = useCallback((places) => {
        places.forEach((place) => {
            if (!placeImages[place.placename]) {
                fetchPlacePhoto(place.placename);
            }
        });
    }, [placeImages]);

    useEffect(() => {
        if (isGoogleMapsScriptLoaded && displayedPlaces.length > 0) {
            fetchPlacePhotos(displayedPlaces);
        }
    }, [displayedPlaces, isGoogleMapsScriptLoaded, fetchPlacePhotos]);

    useEffect(() => {
        if (isGoogleMapsScriptLoaded && visibleAgents.length > 0) {
            fetchPlacePhotos(visibleAgents);
        }
    }, [visibleAgents, isGoogleMapsScriptLoaded, fetchPlacePhotos]);

    const fetchPlacePhoto = (placeName) => {
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

    const refreshPlaces = () => {
        // 이전에 보여준 장소를 제외한 새로운 장소 목록 생성
        const filteredPlaces = PersonalRecData.filter(place =>
            !previousPlaces.some(prev => prev.placename === place.placename)
        );

        // 충분한 새로운 장소가 없는 경우 이전 장소 초기화
        const availablePlaces = filteredPlaces.length >= 3 ? filteredPlaces : PersonalRecData;

        // 새로 고른 장소 랜덤으로 선택
        const shuffled = [...availablePlaces].sort(() => 0.5 - Math.random());
        const newDisplayedPlaces = shuffled.slice(0, 3);

        setDisplayedPlaces(newDisplayedPlaces); // 화면에 보여줄 장소 업데이트
        setPreviousPlaces(newDisplayedPlaces); // 새로 고른 장소를 이전 장소로 저장
    };

    const handleGaugeClick = (event) => {
        const boundingRect = event.currentTarget.getBoundingClientRect();
        const clickX = event.clientX - boundingRect.left;
        const completionPercentage = (clickX / boundingRect.width) * 100;
        const newIndex = Math.floor((completionPercentage / 100) * totalIndicators);
        setActiveIndex(newIndex);
    };

    const handleGoInformation = (place) => {
        navigate(`/recomendation/information/${place.placename}`, { state: { place: place.place } });
    };

    const handleGoKeywordSelection = () => {
        PersonalRecData.length = 0;
        CourseData.length = 0;
        setIsResultsLoaded(false);
        setDisplayedPlaces([]);
        navigate('/keyword');
    };


    return (
        <>
            <Reset />
            <div style={{ height: '55px' }}></div>
            <TopBarComponent />
            <Container>
                <Wrapper>
                    <TitleWrapper>
                        <Title>나만의 맞춤 여행지</Title>
                    </TitleWrapper>
                    <SubTitleWrapper>
                        <SubTitle>키워드 성향에 따른 맞춤형 여행지를 추천해 드려요.</SubTitle>
                        <RefreshBtn id="target-button-id" onClick={refreshPlaces}>
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
                            <PlaceWrapper key={place.placename} onClick={() => handleGoInformation(place)}>
                                {placeImages[place.placename] ? (
                                    <Place src={placeImages[place.placename]} alt={place.placename}/>
                                ) : (
                                    <p>Loading image...</p>
                                )}
                                <PlaceName1>{place.placename}</PlaceName1>
                            </PlaceWrapper>
                        ))}
                    </PlaceContainer>
                    <KeywordBtn onClick={handleGoKeywordSelection}>키워드 다시 선정하기</KeywordBtn>
                    <BestWrapper>
                        <KeywordContainer>
                            <HashTagContaienr>
                                {KeywordData.length > 0 ? (
                                    KeywordData.map((place, index) => (
                                        <HashTag key={index}>{place.keyword}</HashTag>
                                    ))
                                ) : (
                                    <p>키워드를 가져오지 못했습니다.</p>
                                )}
                                <Notice>
                                    <p>관심사와 어울리는</p>
                                    <p>여행지를 가져왔어요.</p>
                                </Notice>
                            </HashTagContaienr>
                            <BestCourseContainer>
                                <BestCourses>
                                    {visibleAgents.map((course, index) => (
                                        <BestCourse
                                            key={index}
                                            review={course}
                                            image={placeImages[course.placename]}
                                        />
                                    ))}
                                </BestCourses>
                            </BestCourseContainer>
                        </KeywordContainer>
                        <GaugeBarWrapper>
                            <GaugeBar
                                completion={(activeIndex + 1) / totalIndicators * 100}
                                onClick={handleGaugeClick}
                            />
                        </GaugeBarWrapper>
                    </BestWrapper>
                </Wrapper>
            </Container>
        </>
    );
}
