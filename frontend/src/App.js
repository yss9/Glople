import { BrowserRouter, Routes, Route } from 'react-router-dom';
import ReviewPage from './pages/review/review';
import LoginPage from './pages/login/loginPage';
import ReservationPage from './pages/reservation/reservation'
import AdminPage from './pages/admin/admin'
import SignupPage from './pages/signup/signupPage';
import GloplerSetPage from './pages/gloplerset/gloplesetPage';
import ChatPage from './pages/chat/chatPage';
import MainPage from './pages/Main/main'
import Write from "./pages/review/write/write";
import WriteTest from "./pages/review/write/writeTest";
import Recomendation from "./pages/route/route";
import RouteNavigation from "./pages/routeNavigation/routeNavigationPage";
import RecMbtiPage from "./pages/recomendation/MBTI/mbti";
import RecPersonalPage from "./pages/recomendation/Personal/personal";
import RouteRecomendation from "./pages/recomendation/Information/RouteRecomendation/routeRecomendation";
import MyPage from "./pages/My/mypage"
import MyProfileModify from "./pages/My/myprofilemodify"
import InformationPage from "./pages/recomendation/Information/information"
import BoardDetail from "./pages/review/detail/detail";
import BoardList from "./pages/review/list/list";
import ListByMKS from "./pages/review/listbymks/listbymks"
import BoardWrite from "./pages/review/write/write";
import Chatbotset from "./pages/chatbot/chatbotset";
import KeywordSelection from "./pages/recomendation/Personal/keyword";
import BoardEx from "./pages/board/boardDetail";
import AgentPerspective from "./pages/AgentPerspective/agentperspective"
import RecommendationCosine from "./pages/recomendation/MBTI/cosine";
import Test from "./pages/test/test"
import AgentDetail from './pages/agentDetail/agentDetail'
import Similarity from './pages/recomendation/Similarity/similarity'
import MBTItest from "./pages/MBTItest/MBTItestPage";
import SearchResult from "./pages/review/search-results"
import SearchResultforGlopler from "./pages/reservation/search-results"
import AssistantPage from "./pages/assistant/assistantPage";
import MyPlans from "./pages/plans/myPlans";
import PlanDetail from "./pages/plans/planDetail";
import SemanticSearchPage from "./pages/semanticSearch/semanticSearchPage";

function App() {
    return (
        <BrowserRouter>
            <Routes>
                <Route path="/" element={<MainPage />} />
                <Route path="/login" element={<LoginPage />} />
                <Route path="/review" element={<ReviewPage />} />
                <Route path="/board/new" element={<BoardWrite/>}/>
                <Route path="/review/write" element={<Write />}/>
                <Route path="/review/listbymks" element={<ListByMKS />}/>
                <Route path="/review/writeTest" element={<WriteTest />}/>
                <Route path="/board/:boardID" element={<BoardDetail />} />
                <Route path="/board/:boardID/edit" element={<BoardWrite isEdit = {true}/>} />
                <Route path="/admin" element={<AdminPage />} />
                <Route path="/reservation" element={<ReservationPage />} />
                <Route path="/signup" element={<SignupPage />} />
                <Route path="/chat" element={<ChatPage />} />
                <Route path="/route" element={<Recomendation />}/>
                <Route path="/routeNavigation" element={<RouteNavigation />} />
                <Route path="/recomendation/mbti" element={<RecMbtiPage />}/>
                <Route path="/recomendation/personal" element={<RecPersonalPage />}/>
                <Route path="/recomendation/similarity" element={<Similarity />}/>
                <Route path="/recomendation/information/:placename" element={<InformationPage />} />
                <Route path="/routeRec" element={<RouteRecomendation />} />
                <Route path="/my" element={<MyPage/>}/>
                <Route path="/myprofile" element={<MyProfileModify/>}/>
                <Route path="/chatbot" element={<Chatbotset/>}/>
                <Route path="/assistant" element={<AssistantPage/>}/>
                <Route path="/my/plans" element={<MyPlans/>}/>
                <Route path="/my/plans/:planId" element={<PlanDetail/>}/>
                <Route path="/search/semantic" element={<SemanticSearchPage/>}/>
                <Route path="/keyword" element={<KeywordSelection/>}/>
                <Route path="/exboard" element={<BoardEx/>}/>
                <Route path="/agentperspective" element={<AgentPerspective/>}/>
                <Route path="/cosine" element={<RecommendationCosine/>}/>
                <Route path="/test" element={<Test/>}/>
                <Route path="/agentdetail" element={<AgentDetail/>}/>
                <Route path="/mbtiTest" element={<MBTItest/>}/>
                <Route path="/review/list" element={<BoardList/>}/>
                <Route path="/search-results" element={<SearchResult/>}/>
                <Route path="/search-resultsforglopler" element={<SearchResultforGlopler/>}/>
                <Route path="/RegisterAgent" element={<GloplerSetPage/>}/>
            </Routes>
        </BrowserRouter>
    );
}
export default App;
