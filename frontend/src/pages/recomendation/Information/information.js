import React, { useState, useEffect } from 'react';
import { Reset } from 'styled-reset';
import TopBarComponent from '../../../components/TopBar/TopBar';
import {
    ConsentContainer, Container, DetailInfoWrapper,
    PlaceImg, PlaceName, Wrapper, RightWrapper,
    DetailInfoTitle, DetailInfo, ReviewContainer,
    BtnContainer, BtnTitle,
    RecomendationBtn, ReviewTitle, PlaceReviews,
    PlaceReivewContainer, GaugeBar, GaugeBarWrapper
} from "./informationstyle";
import PlaceReview from "../../../components/PlaceReview/placeReview";
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import MyProfile2 from "../../../public/Img/forprofile/img_1.png";
import axios from "axios";
import styled from "styled-components";
import SourceAttribution, {
    SOURCE_TOUR_API, SOURCE_WIKIDATA, SOURCE_WIKIPEDIA
} from "../../../components/SourceAttribution/SourceAttribution";


const LimitedTextDiv = styled.div`
    display: -webkit-box;
    -webkit-box-orient: vertical;
    overflow: hidden;
    text-overflow: ellipsis;
    -webkit-line-clamp: 4; /* 표시할 줄 수 */
    line-height: 1.5em; /* 줄 높이 설정 */
    word-break: break-word; /* 긴 단어 줄바꿈 */
`;

// DB에 저장된 설명문(소개). 공공누리 제3유형(변경금지) 글도 있어서 자르지 않고 스크롤로 전부 보여준다.
const StoredTextDiv = styled.div`
    max-height: 4.5em; /* 3줄, 출처 표시 자리를 남긴다 */
    overflow-y: auto;
    line-height: 1.5em;
    word-break: break-word;
`;

// 키워드 추천 결과에서 넘어온 여행지 중 출처를 표시할 수 있는(외부 데이터로 수집한) 설명문이 있는 여행지
function storedPlaceOf(place, placename) {
    if (!place || place.destName !== placename || !place.info) {
        return null;
    }
    return place.source === SOURCE_TOUR_API || place.source === SOURCE_WIKIDATA ? place : null;
}


export default function Information() {
    const { placename } = useParams();
    const [activeIndex, setActiveIndex] = useState(0);
    const [currentPlace, setCurrentPlace] = useState({});
    const [placeImage, setPlaceImage] = useState("");
    const [reviews, setReviews] = useState([]);
    const [isGoogleMapsScriptLoaded, setIsGoogleMapsScriptLoaded] = useState(false);
    const [extractHtml, setExtractHtml] = useState('');
    const [wikiTitle, setWikiTitle] = useState('');
    const navigate = useNavigate();
    const location = useLocation();
    // 키워드 추천 화면에서 넘겨준 여행지 (MBTI 추천이나 주소로 바로 들어오면 없다)
    const storedPlace = storedPlaceOf(location.state && location.state.place, placename);

    const GOOGLE_MAPS_API_KEY = 'AIzaSyCCkm0KlwV72tLvvEG9c4YuPHgo_j2_qz0';

    const loadGoogleMapsScript = () => {
        return new Promise((resolve) => {
            if (window.google && window.google.maps && window.google.maps.places) {
                resolve();
            } else {
                const script = document.createElement('script');
                script.src = `https://maps.googleapis.com/maps/api/js?key=${GOOGLE_MAPS_API_KEY}&libraries=places`;
                script.async = true;
                script.onload = () => {
                    resolve();
                };
                document.body.appendChild(script);
            }
        });
    };

    useEffect(() => {
        loadGoogleMapsScript().then(() => {
            setIsGoogleMapsScriptLoaded(true);
        });
    }, []);

    useEffect(() => {
        if (isGoogleMapsScriptLoaded) {
            fetchPlaceDetails(placename);
        }
    }, [placename, isGoogleMapsScriptLoaded]);

    const fetchPlaceDetails = (placeName) => {
        const service = new window.google.maps.places.PlacesService(document.createElement('div'));
        const request = {
            query: placeName,
            fields: ['place_id']
        };

        service.findPlaceFromQuery(request, (results, status) => {
            if (status === window.google.maps.places.PlacesServiceStatus.OK && results[0]) {
                const placeId = results[0].place_id;
                const detailsRequest = {
                    placeId: placeId,
                    fields: ['name', 'photos', 'formatted_address']
                };

                service.getDetails(detailsRequest, (placeDetails, detailsStatus) => {
                    if (detailsStatus === window.google.maps.places.PlacesServiceStatus.OK && placeDetails) {
                        if (placeDetails.photos && placeDetails.photos.length > 0) {
                            const photoUrl = placeDetails.photos[0].getUrl({ maxWidth: 400 });
                            setPlaceImage(photoUrl);
                        }

                        setCurrentPlace({
                            name: placeDetails.name,
                            address: placeDetails.formatted_address || ""
                        });

                        fetchReviews(placeDetails.name);
                    } else {
                        console.error(`Could not fetch details for ${placeName}`);
                    }
                });
            } else {
                console.error(`Could not fetch place ID for ${placeName}`);
            }
        });
    };

    const fetchReviews = (placeName) => {
        fetch(`http://localhost:8080/api/posts/titles/${placeName}`)
            .then((response) => {
                if (!response.ok) {
                    throw new Error('Failed to fetch reviews');
                }
                return response.json();
            })
            .then((data) => {
                setReviews(data);
            })
            .catch((error) => {
                console.error('Error fetching reviews:', error);
            });
    };

    const reviewsPerPage = 2;
    const startIndex = activeIndex * reviewsPerPage;
    const visibleReviews = reviews.slice(startIndex, startIndex + reviewsPerPage);
    const totalIndicators = Math.ceil(reviews.length / reviewsPerPage);

    const handleGaugeClick = (event) => {
        const boundingRect = event.currentTarget.getBoundingClientRect();
        const clickX = event.clientX - boundingRect.left;
        const completionPercentage = (clickX / boundingRect.width) * 100;
        const newIndex = Math.floor((completionPercentage / 100) * totalIndicators);
        setActiveIndex(newIndex);
    };

    const handleGoRouteRec = () => {
        navigate('/routeRec', { state: { placename: placename } });
    };

    useEffect(() => {
        // DB 설명문을 보여줄 때는 위키백과를 따로 불러오지 않는다.
        if (storedPlace) {
            return;
        }
        const fetchData = async () => {
            try {
                console.log(placename);
                const firstPart = placename.split(',')[0].trim();
                const response = await axios.get(
                    `https://ko.wikipedia.org/w/api.php`, {
                        params: {
                            action: 'query',
                            format: 'json',
                            titles: firstPart,
                            prop: 'extracts',
                            exintro: false, // 전체 내용 가져오기
                            explaintext: true, // 텍스트만 가져오기 (HTML 태그 제외)
                            origin: '*' // CORS 문제 해결
                        }
                    }
                );

                const pages = response.data.query.pages;
                const page = pages[Object.keys(pages)[0]];
                if (page.extract) {
                    setExtractHtml(page.extract);
                    setWikiTitle(page.title || firstPart);
                } else {
                    console.error('No content found for this page');
                }
            } catch (error) {
                console.error('Error fetching full article:', error);
            }
        };
        fetchData();
    }, [storedPlace]);

    return (
        <>
            <Reset />
            <div style={{ height: '55px' }}></div>
            <TopBarComponent />
            <Wrapper>
                <PlaceName>{placename}</PlaceName>
                <Container>
                    <ConsentContainer>
                        {placeImage ? (
                            <PlaceImg src={placeImage} alt={placename} />
                        ) : (
                            <p>Loading image...</p>
                        )}
                        <RightWrapper>
                            <DetailInfoWrapper>
                                <DetailInfoTitle>{currentPlace.name}</DetailInfoTitle>
                                <DetailInfo>
                                    {storedPlace ? (
                                        <>
                                            <StoredTextDiv>{storedPlace.info}</StoredTextDiv>
                                            <SourceAttribution
                                                source={storedPlace.source}
                                                copyrightType={storedPlace.copyrightType}
                                                sourceUrl={storedPlace.sourceUrl}
                                                title={storedPlace.destName}
                                                infoKind={storedPlace.infoKind}
                                            />
                                        </>
                                    ) : (
                                        <>
                                            <LimitedTextDiv
                                                dangerouslySetInnerHTML={{__html: extractHtml}}/>
                                            {extractHtml && (
                                                <SourceAttribution source={SOURCE_WIKIPEDIA} title={wikiTitle}/>
                                            )}
                                        </>
                                    )}
                                </DetailInfo>
                            </DetailInfoWrapper>
                            <ReviewContainer>
                                <ReviewTitle>리뷰</ReviewTitle>
                                <PlaceReivewContainer>
                                    <PlaceReviews>
                                        {visibleReviews.map((review) => (
                                            <PlaceReview
                                                boardID ={review.boardID}
                                                review={review.title}
                                                profilePicture={
                                                    review.profilePicture
                                                        ? `http://localhost:8080/${review.profilePicture.replace('static\\', '').replace(/\\/g, '/')}`
                                                        : MyProfile2
                                                }
                                            />
                                        ))}
                                    </PlaceReviews>
                                    <GaugeBarWrapper>
                                        <GaugeBar
                                            completion={(activeIndex + 1) / totalIndicators * 100}
                                            onClick={handleGaugeClick}
                                        />
                                    </GaugeBarWrapper>
                                </PlaceReivewContainer>
                            </ReviewContainer>
                        </RightWrapper>
                    </ConsentContainer>
                </Container>
                <BtnContainer>
                    <BtnTitle>해당 여행지를 포함한 여행이 궁금하다면?</BtnTitle>
                    <RecomendationBtn onClick={handleGoRouteRec}>루트 추천</RecomendationBtn>
                </BtnContainer>
            </Wrapper>
        </>
    );
}
