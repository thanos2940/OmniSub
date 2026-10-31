import { useState, useEffect, lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
// Eagerly loaded: always on screen (chrome + always-mounted widgets).
import CommandPalette from './components/CommandPalette';
import TopBar from './components/TopBar';
import LoginPage from './components/LoginPage';
import ErrorBoundary from './components/common/ErrorBoundary';
import LoadingFallback from './components/common/LoadingFallback';
import { Page, PageHeader } from './components/ui';

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
const LibraryPage = lazyWithRetry(() => import('./components/library/LibraryPage'));
const ProjectDetail = lazyWithRetry(() => import('./components/ProjectDetail'));
const SettingsPage = lazyWithRetry(() => import('./components/SettingsPage'));
const EpisodeView = lazyWithRetry(() => import('./components/EpisodeView'));
const ReviewQueuePanel = lazyWithRetry(() => import('./components/ReviewQueuePanel'));
const ActivityPage = lazyWithRetry(() => import('./components/activity/ActivityPage'));
const SystemPage = lazyWithRetry(() => import('./components/system/SystemPage'));
import { JobProvider } from './context/JobContext';
import { ToastProvider } from './context/ToastContext';
import { api, AUTH_STORAGE_KEY } from './api';


function App() {
    // Auth gate (docs/PLAN_auth_security.md): resolved before anything else
    // mounts, so TopBar/JobContext/etc. never start polling — and
    // therefore never 401-storm the console — while a login is pending.
    const [authChecked, setAuthChecked] = useState(false);
    const [needsLogin, setNeedsLogin] = useState(false);

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
        window.addEventListener('omnisub:unauthorized', handleUnauthorized);
        return () => {
            window.removeEventListener('omnisub:unauthorized', handleUnauthorized);
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

    // Only probe the setup status (a protected endpoint) once we know the caller is actually
    // authenticated, otherwise it fires a doomed request before the user has seen the login form.
    // A Gemini key is optional now (only lines the agent refuses use it), so nothing asks for one here.
    useEffect(() => {
        if (authChecked && !needsLogin) {
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
            <Router basename={window.__OMBISUB_BASE__ || undefined}>
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
                                            <Route path="/" element={<LibraryPage />} />
                                            <Route path="/activity" element={<ActivityPage />} />
                                            <Route path="/review" element={
                                                <Page width="narrow">
                                                    <PageHeader title="Review" subtitle="Translated lines the quality check flagged, across all projects." />
                                                    <ReviewQueuePanel />
                                                </Page>
                                            } />
                                            <Route path="/settings" element={<SettingsPage />} />
                                            <Route path="/system" element={<SystemPage />} />
                                            <Route path="/project/:projectName/*" element={<ProjectDetail />} />
                                            <Route path="/project/:projectName/episode/:episodeName" element={<EpisodeView />} />

                                            {/* Old addresses */}
                                            <Route path="/library" element={<Navigate to="/" replace />} />
                                            <Route path="/queue" element={<Navigate to="/activity" replace />} />
                                            <Route path="/autopilot" element={<Navigate to="/activity?tab=autopilot" replace />} />
                                            <Route path="/health" element={<Navigate to="/system" replace />} />
                                            <Route path="/dashboard" element={<Navigate to="/system" replace />} />
                                        </Routes>
                                    </Suspense>
                                </ErrorBoundary>
                            </div>
                        </div>

                        <CommandPalette />
                    </ToastProvider>
                </JobProvider>
            </Router>
        </ErrorBoundary>
    );
}

export default App;
