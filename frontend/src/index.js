// 백엔드 호출마다 JWT를 싣는 axios 인터셉터를 앱 시작 때 한 번 건다(다른 import보다 먼저).
import './api/auth';
import React from 'react'
import ReactDOM from 'react-dom/client';
import App from './App';
import './App.css';

const root = ReactDOM.createRoot(document.getElementById('root'));
root.render(<App />);


