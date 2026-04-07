import React, { useState, useEffect } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import ProjectList from './components/ProjectList';
import ProjectDetail from './components/ProjectDetail';
import JobProgressWidget from './components/JobProgressWidget';
import TopBar from './components/TopBar';
import SettingsPage from './components/SettingsPage';
import EpisodeView from './components/EpisodeView';
import ApiKeyModal from './components/ApiKeyModal';
import { JobProvider } from './context/JobContext';
import { ToastProvider } from './context/ToastContext';
import { api } from './api';

function App() {
    const [showApiKeyModal, setShowApiKeyModal] = useState(false);
    const [hasApiKey, setHasApiKey] = useState(null); // null = loading, true/false = checked

    // Check for API key on mount
    useEffect(() => {
        checkApiKey();
    }, []);

    const checkApiKey = async () => {
        try {
            const response = await api.getApiKeyStatus();
            setHasApiKey(response.data.has_key);
            if (!response.data.has_key) {
                setShowApiKeyModal(true);
            }
        } catch (error) {
            console.error('Failed to check API key status:', error);
            // If check fails, assume no key
            setHasApiKey(false);
            setShowApiKeyModal(true);
        }
    };

    const handleApiKeySaved = () => {
        setHasApiKey(true);
        setShowApiKeyModal(false);
    };

    return (
        <Router>
            <JobProvider>
                <ToastProvider>
                    <div className="flex flex-col min-h-screen bg-slate-50 dark:bg-gray-900 font-sans text-gray-900 dark:text-white">
                        <TopBar />
                        <div className="flex-1 relative">
                            <Routes>
                                <Route path="/" element={<ProjectList />} />
                                <Route path="/project/:projectName/*" element={<ProjectDetail />} />
                                <Route path="/project/:projectName/episode/:episodeName" element={<EpisodeView />} />

                                <Route path="/settings" element={<SettingsPage />} />
                            </Routes>
                            <JobProgressWidget />
                        </div>
                    </div>

                    <ApiKeyModal
                        isOpen={showApiKeyModal}
                        onClose={() => setShowApiKeyModal(false)}
                        onSave={handleApiKeySaved}
                        allowSkip={true}
                    />
                </ToastProvider>
            </JobProvider>
        </Router>
    );
}

export default App;
