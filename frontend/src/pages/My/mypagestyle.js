import styled  from "@emotion/styled";

export const Wrapper=styled.div`
  max-height: 84vh;
  overflow-y: scroll;
  &::-webkit-scrollbar {
    width: 8px; 
  }

  &::-webkit-scrollbar-thumb {
    background-color: gray;
    border-radius: 10px;
  }

  &::-webkit-scrollbar-track {
    background-color: #f0f0f0;
  }
`
export const MyProfileContainer=styled.div`
    height: 24em;
 // background-color: palegoldenrod;
  display: flex;
  flex-direction: column;
  justify-content: center;
  align-items: center;
  font-family: Regular;

`
export const ProfileImg=styled.img`
  width: 14em;
  height: 14em;
  border-radius: 50%;
  background-color: #d9d9d9;
  margin-top: 20px;
  object-fit: cover; /* 이미지를 가득 채우도록 설정 */

`
export const NameWrapper=styled.div`
  margin-top: 30px;
  display: flex;
  justify-content: center;
  align-items: center;
  color: #515151;
  font-family: "Regular";
  font-size: 1.7rem;
  font-weight: bold;
`
export const IntroduceWrapper=styled.div`
    display: flex;
  width: auto;
  margin-top: 20px;
`
export const Mbti=styled.div`
    width: 80px;
  height: 30px;
  background-color: black;
  display: flex;
  color: white;
  align-items: center;
  justify-content: center;
font-family: "Regular";
  margin-right: 5px;
`
export const Introduce=styled.div`
  display: flex;
  align-items: center;
  justify-content: center;
  margin-left: 5px;
  color: #515151;
`
export const SettingWrapper=styled.div`
    display: flex;
  justify-content: center;
  align-items: center;
`
export const SettingContainer=styled.div`
  width: 680px;
  display: flex;
  justify-content: space-between;
`
export const ProfileSettingBtn=styled.button`
    width: 11em;
  height: 2.1em;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border: none;
  cursor: pointer;
  background-color: white;
  svg{
    margin-left: 5px;
  }
  p{
    margin-right: 15px;
    font-family: "Regular";
    font-weight: bold;
    font-size: 1rem;
  }
`
export const GloplerSettingBtn=styled.button`
  width: 12em;
  height: 2.1em;
  display: flex;
  align-items: center;
  justify-content: space-between;
  border: none;
  cursor: pointer;
  background-color: white;
  svg{
    margin-left: 5px;
  }
  p{
    margin-right: 15px;
    font-family: "Regular";
    font-weight: bold;
    font-size: 1rem;
  }
`
export const PointSettingBtn=styled.button`
  width: 11em;
  height: 2.1em;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background-color: white;
  cursor: pointer;
  border: none;
  svg{
    margin-left: 5px;
  }
  p{
    margin-right: 15px;
    font-family: "Regular";
    font-weight: bold;
    font-size: 1rem;
  }
`
// 내 여행 일정(/my/plans)으로 가는 버튼. 다른 설정 버튼과 같은 모양
export const PlanSettingBtn=styled.button`
  width: 12em;
  height: 2.1em;
  display: flex;
  align-items: center;
  justify-content: space-between;
  background-color: white;
  cursor: pointer;
  border: none;
  svg{
    margin-left: 5px;
  }
  p{
    margin-right: 15px;
    font-family: "Regular";
    font-weight: bold;
    font-size: 1rem;
  }
`
export const MyPageDetailContainer=styled.div`
  display: flex;
  justify-content: center;
  align-items: center;
  //background-color: #61dafb;
  margin-top: 2em;
  
`
export const Container = styled.div`
  font-size: 20px;
  color: #555555;
  height: 100%;
  width: 100%;
  margin: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  font-family: Regular;
`;

export const SelectorContainer = styled.div`
`;

export const SelectorWrapper = styled.div`
  padding: 0.2rem;
  background-color: #efefef;
  width: min-content;
  border-radius: 12px;
  position: relative;
  display: flex;
  align-items: center;
`;

export const SelectorItem = styled.div`
  white-space: nowrap;
  padding: 0 20rem;
  cursor: pointer;
  z-index: 2;
  user-select: none;
  transition: color 0.2s ease;
  color: transparent;
  font-size: 0.5rem;
  ${({ active }) => active && `
    color: transparent;
  `}
`;

export const Highlight = styled.div`
  background-color: #ff9900;
  border-radius: 8px;
  position: absolute;
  left: ${({ left }) => left}px;
  width: ${({ width }) => width}px;
  height: 0.7rem;
  transition: left 0.2s ease, width 0.2s ease;
`;
export const Highlight2 = styled.div`
  background-color: transparent;
  border-radius: 8px;
  position: absolute;
  left: ${({ left }) => left}px;
  width: ${({ width }) => width}px;
  height: 0.7rem;
  transition: left 0.2s ease, width 0.2s ease;
`;
export const SelectorItem2 = styled.div`
  white-space: nowrap;
  //padding: 0 18rem;
  width: 655px;
  cursor: pointer;
  z-index: 2;
  user-select: none;
  transition: color 0.2s ease;
  color: black;
  //background-color: orange;
  font-size: 15px;
  
`;

export const SelectorWrapper2 = styled.div`
  padding: 0.2rem;
  background-color: transparent;
  width: min-content;
  border-radius: 12px;
  position: relative;
  display: flex;
  align-items: center;
`;

export const Content = styled.div`
  margin-top: 2rem;
  text-align: center;
  font-size: 1.1rem;
  color: #333;
`;
