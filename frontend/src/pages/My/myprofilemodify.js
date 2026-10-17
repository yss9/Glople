import styled from "styled-components";
import { Reset } from "styled-reset";
import React, { useEffect, useState } from "react";
import { jwtDecode } from "jwt-decode";
import axios from "axios";
import Cookies from "js-cookie";
import { setAuthToken } from "../../api/auth";
import MyProfile2 from "../../public/Img/forprofile/img.png";

const Wrapper = styled.div`
    width: 100%;
    height: 100vh;
    display: flex;
    justify-content: center;
`;

const Container = styled.div`
    width: 1500px;
    height: 800px;
`;

const MyProfileContainer = styled.div`
    height: 28em;
    display: flex;
    flex-direction: column;
    justify-content: center;
    align-items: center;
    font-family: Regular;
`;

const InputField = styled.input`
    width: 120px;
    height: 30px;
    background-color: black;
    color: white;
    text-align: center;
    font-family: "Regular";
    margin-right: 5px;
    border: none;
    outline: none;
`;

const Introduce = styled.input`
    text-align: center;
    color: #515151;
    margin-left: 5px;
    border: none;
    outline: none;
`;

const NameWrapper = styled.div`
    margin-top: 30px;
    display: flex;
    justify-content: center;
    align-items: center;
    color: #515151;
    font-family: "Regular"; 
    font-size: 1.7rem;
    font-weight: bold;
`;

const IntroduceWrapper = styled.div`
    display: flex;
    width: auto;
    margin-top: 20px;
`;

const ProfileImg = styled.img`
    width: 14em;
    height: 14em;
    border-radius: 50%;
    background-color: #d9d9d9;
    margin-top: 20px;
    object-fit: cover;
    cursor: pointer;
`;

const HiddenInput = styled.input`
    display: none;
`;

const getAuthToken = () => {
    return Cookies.get("jwt");
};

const Myprofilemodify = () => {
    const [userData, setUserData] = useState(null);
    const [profileImage, setProfileImage] = useState(null);
    const [profileImagePreview, setProfileImagePreview] = useState(MyProfile2);
    const [nickname, setNickname] = useState("");
    const [mbti, setMbti] = useState("");
    const [introduction, setIntroduction] = useState("");
    const [dateOfBirth, setDateOfBirth] = useState("");
    const [gender, setGender] = useState("");
    const token = getAuthToken();

    useEffect(() => {
        const fetchUserData = async () => {
            try {
                if (token) {
                    const decodedToken = jwtDecode(token);
                    const userId = decodedToken.userId;
                    const response = await axios.get(`http://localhost:8080/api/users/my-info?userId=${userId}`, {
                        headers: {
                            Authorization: `Bearer ${token}`,
                        },
                    });
                    setUserData(response.data);
                    setNickname(response.data.nickname || "");
                    setMbti(response.data.mbti || "");
                    setIntroduction(response.data.statusMessage || "");
                    setDateOfBirth(response.data.dateOfBirth || "");
                    setGender(response.data.gender || "");
                    setProfileImagePreview(processProfilePicture(response.data.profilePicture));
                } else {
                    console.error("No JWT token found in cookies");
                }
            } catch (error) {
                console.error("Failed to fetch user data", error);
            }
        };
        fetchUserData();
    }, [token]);

    const processProfilePicture = (profilePicture) => {
        if (profilePicture) {
            return `http://localhost:8080/${profilePicture.replace("static\\", "").replace(/\\/g, "/")}`;
        } else {
            return MyProfile2;
        }
    };

    const handleImageChange = (e) => {
        const file = e.target.files[0];
        if (file) {
            setProfileImage(file);
            setProfileImagePreview(URL.createObjectURL(file)); // 미리 보기 업데이트
        }
    };

    const handleNicknameChange = (e) => setNickname(e.target.value);
    const handleMbtiChange = (e) => setMbti(e.target.value);
    const handleIntroductionChange = (e) => setIntroduction(e.target.value);
    const handleDateOfBirthChange = (e) => setDateOfBirth(e.target.value);
    const handleGenderChange = (e) => setGender(e.target.value);

    const handleSubmit = async () => {
        if (!token) {
            alert("로그인이 필요합니다.");
            return;
        }

        const decodedToken = jwtDecode(token);
        const userId = decodedToken.userId;

        const userDataJson = {
            nickname,
            mbti,
            statusMessage: introduction,
            dateOfBirth,
            gender,
        };

        try {
            if (profileImage && profileImage instanceof File) {
                const formData = new FormData();
                formData.append("profilePicture", profileImage);
                // userId는 쿼리로만 보낸다. form에도 넣으면 서버에 'id,id'로 합쳐져 본인 확인이 틀어짐

                const uploadResponse = await axios.post(
                    `http://localhost:8080/api/users/upload-profile-picture?userId=${userId}`,
                    formData,
                    {
                        headers: {
                            Authorization: `Bearer ${token}`,
                            "Content-Type": "multipart/form-data",
                        },
                    }
                );

                if (uploadResponse.status === 200) {
                    console.log("프로필 이미지가 성공적으로 업로드되었습니다.");
                } else {
                    console.error("프로필 이미지 업로드 실패");
                }
            }

            const response = await axios.post(
                `http://localhost:8080/api/users/update-profile?userId=${userId}`,
                userDataJson,
                {
                    headers: {
                        "Content-Type": "application/json",
                        Authorization: `Bearer ${token}`,
                    },
                }
            );

            if (response.status === 200) {
                // 닉네임이 바뀌면 서버가 새 토큰을 내려준다 → 쿠키를 바꿔야 예약·채팅·이체가 새 닉네임으로 동작
                if (response.data && typeof response.data === "object" && response.data.token) {
                    setAuthToken(response.data.token);
                }
                console.log("프로필이 성공적으로 업데이트되었습니다.");
                alert("프로필이 성공적으로 업데이트되었습니다.");
            } else {
                console.error("프로필 업데이트에 실패했습니다.");
            }
        } catch (error) {
            console.error("프로필 업데이트 중 오류가 발생했습니다:", error);
            alert("프로필 업데이트 중 오류가 발생했습니다.");
        }
    };

    return (
        <>
            <Reset />
            <Wrapper>
                <Container>
                    {userData && (
                        <MyProfileContainer>
                            <ProfileImg
                                src={profileImagePreview}
                                alt="프로필 이미지"
                                onClick={() => document.getElementById("profileImageInput").click()}
                            />
                            <HiddenInput id="profileImageInput" type="file" onChange={handleImageChange} />
                            <NameWrapper>
                                <input type="text" value={nickname} onChange={handleNicknameChange} placeholder="닉네임" />
                            </NameWrapper>
                            <IntroduceWrapper>
                                <InputField type="text" value={mbti} onChange={handleMbtiChange} placeholder="MBTI" />
                                <Introduce type="text" value={introduction} onChange={handleIntroductionChange} placeholder="소개 메시지" />
                                <InputField type="text" value={dateOfBirth} onChange={handleDateOfBirthChange} placeholder="생년월일" />
                                <InputField type="text" value={gender} onChange={handleGenderChange} placeholder="성별" />
                            </IntroduceWrapper>
                            <button onClick={handleSubmit}>저장</button>
                        </MyProfileContainer>
                    )}
                </Container>
            </Wrapper>
        </>
    );
};

export default Myprofilemodify;
