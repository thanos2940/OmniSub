import React, { useState, useEffect, lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import { RefreshCw } from 'lucide-react';
// Eagerly loaded: always on screen (chrome + always-mounted widgets).
import JobProgressWidget from './components/JobProgressWidget';
import TopBar from './components/TopBar';
import ApiKeyModal from './components/ApiKeyModal';
// Route components are code-split so the initial bundle stays small and each page
// only downloads when first visited.
const ProjectList = lazy(() => import('./components/ProjectList'));
const ProjectDetail = lazy(() => import('./components/ProjectDetail'));
const SettingsPage = lazy(() => import('./components/SettingsPage'));
const EpisodeView = lazy(() => import('./components/EpisodeView'));
const MediaLibrary = lazy(() => import('./components/MediaLibrary'));
const ReviewQueuePanel = lazy(() => import('./components/ReviewQueuePanel'));
const TranslationQueuePanel = lazy(() => import('./components/TranslationQueuePanel'));
const Dashboard = lazy(() => import('./components/Dashboard'));
const HealthPage = lazy(() => import('./components/HealthPage'));
import { JobProvider } from './context/JobContext';
import { ToastProvider } from './context/ToastContext';
import { api } from './api';

const RouteFallback = () => (
    <div className="p-10 text-center text-gray-400">
        <RefreshCw className="w-6 h-6 animate-spin mx-auto" />
    </div>
);

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
                            <Suspense fallback={<RouteFallback />}>
                            <Routes>
                                <Route path="/" element={<ProjectList />} />
                                <Route path="/project/:projectName/*" element={<ProjectDetail />} />
                                <Route path="/project/:projectName/episode/:episodeName" element={<EpisodeView />} />

                                <Route path="/settings" element={<SettingsPage />} />
                                <Route path="/library" element={<MediaLibrary />} />
                                <Route path="/dashboard" element={<Dashboard />} />
                                <Route path="/health" element={<HealthPage />} />
                                <Route path="/review" element={
                                    <div className="max-w-4xl mx-auto p-8 space-y-6">
                                        <header className="mb-6">
                                            <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Global Review Queue</h1>
                                            <p className="text-gray-500 dark:text-gray-400">Resolve translations flagged by the AI quality control across all projects.</p>
                                        </header>
                                        <ReviewQueuePanel />
                                    </div>
                                } />
                                <Route path="/queue" element={
                                    <div className="max-w-6xl mx-auto p-8 space-y-6">
                                        <TranslationQueuePanel />
                                    </div>
                                } />
                            </Routes>
                            </Suspense>
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
