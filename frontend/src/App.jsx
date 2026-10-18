import React, { useState, useEffect, lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
// Eagerly loaded: always on screen (chrome + always-mounted widgets).
import JobProgressWidget from './components/JobProgressWidget';
import CommandPalette from './components/CommandPalette';
import TopBar from './components/TopBar';
import ApiKeyModal from './components/ApiKeyModal';
import LoginPage from './components/LoginPage';
import ErrorBoundary from './components/common/ErrorBoundary';
import LoadingFallback from './components/common/LoadingFallback';

// Route components are code-split with automatic reload retry on stale Vite/HMR chunks
const lazyWithRetry = (componentImport) =>
    lazy(async () => {
        try {
            const component = await componentImport();
            window.sessionStorage.removeItem('retry-lazy-refreshed');
            return component;
        } catch (error) {
            const alreadyRefreshed = window.sessionStorage.getItem('retry-lazy-refreshed');
            if (!alreadyRefreshed) {
                window.sessionStorage.setItem('retry-lazy-refreshed', 'true');
                window.location.reload();
                return new Promise(() => {});
            }
            throw error;
        }
    });

const SetupWizard = lazyWithRetry(() => import('./components/setup/SetupWizard'));
const ProjectList = lazyWithRetry(() => import('./components/ProjectList'));
const ProjectDetail = lazyWithRetry(() => import('./components/ProjectDetail'));
const SettingsPage = lazyWithRetry(() => import('./components/SettingsPage'));
const EpisodeView = lazyWithRetry(() => import('./components/EpisodeView'));
const MediaLibrary = lazyWithRetry(() => import('./components/MediaLibrary'));
const ReviewQueuePanel = lazyWithRetry(() => import('./components/ReviewQueuePanel'));
const TranslationQueuePanel = lazyWithRetry(() => import('./components/TranslationQueuePanel'));
const Dashboard = lazyWithRetry(() => import('./components/Dashboard'));
const HealthPage = lazyWithRetry(() => import('./components/HealthPage'));
const AutopilotPage = lazyWithRetry(() => import('./components/autopilot/AutopilotPage'));
import { JobProvider } from './context/JobContext';
import { ToastProvider } from './context/ToastContext';
import { api, AUTH_STORAGE_KEY } from './api';


function App() {
    // Auth gate (docs/PLAN_auth_security.md): resolved before anything else
    // mounts, so JobProgressWidget/TopBar/etc. never start polling — and
    // therefore never 401-storm the console — while a login is pending.
    const [authChecked, setAuthChecked] = useState(false);
    const [needsLogin, setNeedsLogin] = useState(false);

    const [showApiKeyModal, setShowApiKeyModal] = useState(false);
    const [hasApiKey, setHasApiKey] = useState(null); // null = loading, true/false = checked

    // First-run setup wizard (docs/PLAN_onboarding_wizard.md) — checked once
    // auth is resolved (it's a protected /api/ endpoint once auth is enabled).
    const [setupChecked, setSetupChecked] = useState(false);
    const [needsSetup, setNeedsSetup] = useState(false);

    useEffect(() => {
        checkAuthStatus();
        const handleUnauthorized = () => {
            localStorage.removeItem(AUTH_STORAGE_KEY);
            setNeedsLogin(true);
        };
        const handleOpenApiKey = () => {
            setShowApiKeyModal(true);
        };
        window.addEventListener('omnisub:unauthorized', handleUnauthorized);
        window.addEventListener('omnisub:open-api-key-modal', handleOpenApiKey);
        return () => {
            window.removeEventListener('omnisub:unauthorized', handleUnauthorized);
            window.removeEventListener('omnisub:open-api-key-modal', handleOpenApiKey);
        };
    }, []);

    const checkAuthStatus = async () => {
        try {
            const res = await api.getAuthStatus();
            setNeedsLogin(!!res.data.auth_enabled && !res.data.authenticated);
        } catch (error) {
            console.error('Failed to check auth status:', error);
            // Fail open on a broken status check rather than locking the user
            // out of a server that may not even have auth configured.
            setNeedsLogin(false);
        } finally {
            setAuthChecked(true);
        }
    };

    // Only probe the Gemini key / setup status (protected endpoints) once we
    // know the caller is actually authenticated — otherwise these fire doomed
    // requests before the user has even seen the login form.
    useEffect(() => {
        if (authChecked && !needsLogin) {
            checkApiKey();
            checkSetupStatus();
        }
    }, [authChecked, needsLogin]);

    const checkSetupStatus = async () => {
        try {
            const res = await api.getSetupStatus();
            setNeedsSetup(!res.data.setup_completed);
        } catch (error) {
            console.error('Failed to check setup status:', error);
            setNeedsSetup(false); // fail open — don't trap an existing install behind a broken check
        } finally {
            setSetupChecked(true);
        }
    };

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

    if (!authChecked || (!needsLogin && !setupChecked)) {
        return <LoadingFallback fullScreen message="Connecting to OmbiSub..." />;
    }

    if (needsLogin) {
        return (
            <ErrorBoundary title="Authentication Error">
                <LoginPage onLoggedIn={() => setNeedsLogin(false)} />
            </ErrorBoundary>
        );
    }

    return (
        <ErrorBoundary title="Application Error">
            <Router>
                <JobProvider>
                    <ToastProvider>
                        {needsSetup && (
                            <Suspense fallback={<LoadingFallback fullScreen message="Loading setup wizard..." />}>
                                <SetupWizard onFinished={() => setNeedsSetup(false)} />
                            </Suspense>
                        )}
                        <div className="flex flex-col min-h-screen bg-slate-50 dark:bg-gray-900 font-sans text-gray-900 dark:text-white">
                            <TopBar />
                            <div className="flex-1 relative">
                                <ErrorBoundary title="Page Load Error">
                                    <Suspense fallback={<LoadingFallback message="Loading view..." />}>
                                        <Routes>
                                            <Route path="/" element={<ProjectList />} />
                                            <Route path="/project/:projectName/*" element={<ProjectDetail />} />
                                            <Route path="/project/:projectName/episode/:episodeName" element={<EpisodeView />} />

                                            <Route path="/settings" element={<SettingsPage />} />
                                            <Route path="/library" element={<MediaLibrary />} />
                                            <Route path="/dashboard" element={<Dashboard />} />
                                            <Route path="/health" element={<HealthPage />} />
                                            <Route path="/autopilot" element={<AutopilotPage />} />
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
                                </ErrorBoundary>
                                <ErrorBoundary title="Background Task Widget Error">
                                    <JobProgressWidget />
                                </ErrorBoundary>
                            </div>
                        </div>

                        <ApiKeyModal
                            isOpen={showApiKeyModal}
                            onClose={() => setShowApiKeyModal(false)}
                            onSave={handleApiKeySaved}
                            allowSkip={true}
                        />

                        <CommandPalette />
                    </ToastProvider>
                </JobProvider>
            </Router>
        </ErrorBoundary>
    );
}

export default App;
