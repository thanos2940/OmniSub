import React, { useState, useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { LayoutGrid, Settings, ChevronRight, Home, Library, ShieldAlert, ListTodo, Plane, Gauge, HeartPulse, Lock, Search, Key, ArrowDownCircle } from 'lucide-react';
import { api } from '../api';

const TopBar = () => {
    const location = useLocation();
    const pathSegments = location.pathname.split('/').filter(Boolean);

    // Simple parsing logic based on route structure: /project/:projectName/episode/:episodeName
    const projectName = pathSegments[0] === 'project' ? decodeURIComponent(pathSegments[1]) : null;
    const episodeName = pathSegments[2] === 'episode' ? decodeURIComponent(pathSegments[3]) : null;
    const isLibrary = location.pathname === '/library';
    const isReview = location.pathname === '/review';
    const isQueue = location.pathname === '/queue';

    const [reviewCount, setReviewCount] = useState(0);
    const [authEnabled, setAuthEnabled] = useState(true); // assume secured until checked, to avoid a flash
    const [updateAvailable, setUpdateAvailable] = useState(false);

    useEffect(() => {
        const fetchCount = async () => {
            if (document.hidden) return; // don't poll a backgrounded tab
            try {
                const res = await api.getGlobalReviewCount();
                setReviewCount(res.data?.count || 0);
            } catch (e) {
                console.error("Failed to fetch review count", e);
            }
        };
        const handleVisibility = () => {
            if (!document.hidden) fetchCount();
        };
        fetchCount();
        const interval = setInterval(fetchCount, 15000);
        document.addEventListener('visibilitychange', handleVisibility);
        return () => {
            clearInterval(interval);
            document.removeEventListener('visibilitychange', handleVisibility);
        };
    }, []);

    useEffect(() => {
        const checkUpdates = () => {
            if (document.hidden) return;
            api.getUpdateStatus()
                .then(res => setUpdateAvailable(!!res.data?.update_available))
                .catch(() => {});
        };
        checkUpdates();
        const interval = setInterval(checkUpdates, 60000);
        return () => clearInterval(interval);
    }, []);

    useEffect(() => {
        api.getAuthStatus()
            .then(res => setAuthEnabled(!!res.data?.auth_enabled))
            .catch(() => {});
    }, []);

    return (
        <div className="sticky top-0 z-50 bg-white/80 dark:bg-gray-900/80 backdrop-blur-md border-b border-gray-200 dark:border-gray-800 px-3 sm:px-6 py-2 sm:py-3 flex items-center justify-between shadow-sm overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden gap-2">
            <div className="flex items-center gap-2 sm:gap-4 shrink-0 min-w-0">
                {/* Logo / Home */}
                <Link to="/" className="flex items-center gap-2 text-gray-900 dark:text-white hover:opacity-80 transition-opacity group shrink-0">
                    <div className="w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center shadow-indigo-200 dark:shadow-none shadow-md group-hover:scale-105 transition-transform">
                        <LayoutGrid className="text-white" size={18} />
                    </div>
                    <span className="text-lg font-bold tracking-tight hidden md:block">Omnisub</span>
                </Link>

                {/* Divider */}
                <div className="h-6 w-px bg-gray-200 dark:bg-gray-700 mx-1 hidden md:block"></div>

                {/* Breadcrumbs */}
                <nav className="flex items-center gap-1 sm:gap-1.5 text-xs sm:text-sm font-medium text-gray-500 dark:text-gray-400 min-w-0">
                    <Link to="/" className="p-1 sm:p-1.5 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-md hover:text-indigo-600 dark:hover:text-indigo-400 transition-all shrink-0">
                        <Home size={15} />
                    </Link>

                    {isLibrary && (
                        <>
                            <ChevronRight size={13} className="text-gray-300 dark:text-gray-600 shrink-0" />
                            <span className="px-1.5 sm:px-2 py-0.5 sm:py-1 text-gray-900 dark:text-white bg-gray-100 dark:bg-gray-800 rounded-md border border-gray-200 dark:border-gray-700 truncate">
                                Library
                            </span>
                        </>
                    )}

                    {isReview && (
                        <>
                            <ChevronRight size={13} className="text-gray-300 dark:text-gray-600 shrink-0" />
                            <span className="px-1.5 sm:px-2 py-0.5 sm:py-1 text-gray-900 dark:text-white bg-gray-100 dark:bg-gray-800 rounded-md border border-gray-200 dark:border-gray-700 truncate">
                                Review Queue
                            </span>
                        </>
                    )}

                    {isQueue && (
                        <>
                            <ChevronRight size={13} className="text-gray-300 dark:text-gray-600 shrink-0" />
                            <span className="px-1.5 sm:px-2 py-0.5 sm:py-1 text-gray-900 dark:text-white bg-gray-100 dark:bg-gray-800 rounded-md border border-gray-200 dark:border-gray-700 truncate">
                                Queue
                            </span>
                        </>
                    )}

                    {projectName && (
                        <>
                            <ChevronRight size={13} className="text-gray-300 dark:text-gray-600 shrink-0" />
                            <Link
                                to={`/project/${encodeURIComponent(projectName)}`}
                                className="px-1.5 sm:px-2 py-0.5 sm:py-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-md hover:text-indigo-600 dark:hover:text-indigo-400 transition-all truncate max-w-[80px] sm:max-w-[150px] md:max-w-xs"
                            >
                                {projectName}
                            </Link>
                        </>
                    )}

                    {episodeName && (
                        <>
                            <ChevronRight size={13} className="text-gray-300 dark:text-gray-600 shrink-0" />
                            <span className="px-1.5 sm:px-2 py-0.5 sm:py-1 text-gray-900 dark:text-white bg-gray-100 dark:bg-gray-800 rounded-md truncate max-w-[80px] sm:max-w-[150px] md:max-w-xs border border-gray-200 dark:border-gray-700">
                                {episodeName}
                            </span>
                        </>
                    )}
                </nav>
            </div>

            {/* Right Actions */}
            <div className="flex items-center gap-1 sm:gap-2 shrink-0">
                {/* Command Palette Trigger */}
                <button
                    onClick={() => window.dispatchEvent(new CustomEvent('omnisub:open-command-palette'))}
                    className="hidden sm:flex items-center gap-2 px-3 py-1.5 text-xs text-gray-500 dark:text-gray-400 bg-gray-100 dark:bg-gray-800 hover:bg-gray-200/80 dark:hover:bg-gray-700/80 border border-gray-200 dark:border-gray-700 rounded-xl transition-all mr-1 shadow-sm"
                    title="Open Command Palette (Ctrl+K or ⌘K)"
                >
                    <Search size={14} className="text-gray-400" />
                    <span>Search or jump to...</span>
                    <kbd className="font-mono text-[10px] bg-white dark:bg-gray-900 px-1.5 py-0.5 rounded border border-gray-200 dark:border-gray-700 text-gray-500 dark:text-gray-400 font-bold">
                        ⌘K
                    </kbd>
                </button>
                <button
                    onClick={() => window.dispatchEvent(new CustomEvent('omnisub:open-api-key-modal'))}
                    className="p-1.5 sm:p-2 rounded-lg text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-all flex items-center gap-1.5 text-sm font-medium shrink-0"
                    title="Configure Google Gemini API Key"
                >
                    <Key size={17} />
                    <span className="hidden lg:inline">API Key</span>
                </button>
                {!authEnabled && (
                    <Link
                        to="/settings"
                        className="p-1.5 sm:p-2 rounded-lg transition-all flex items-center gap-1.5 text-sm font-medium text-amber-600 hover:bg-amber-50 dark:hover:bg-amber-900/20 shrink-0"
                        title="This server is unsecured — click to set a username and password"
                    >
                        <Lock size={17} />
                        <span className="hidden md:inline">Unsecured</span>
                    </Link>
                )}
                <Link
                    to="/dashboard"
                    className={`p-1.5 sm:p-2 rounded-lg transition-all flex items-center gap-1.5 text-sm font-medium shrink-0 ${location.pathname === '/dashboard'
                            ? 'text-indigo-600 bg-indigo-50 dark:bg-indigo-900/20 dark:text-indigo-400'
                            : 'text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20'
                        }`}
                    title="Dashboard"
                >
                    <Gauge size={17} />
                    <span className="hidden md:inline">Dashboard</span>
                </Link>
                <Link
                    to="/review"
                    className={`p-1.5 sm:p-2 rounded-lg transition-all flex items-center gap-1.5 text-sm font-medium shrink-0 ${isReview
                            ? 'text-rose-600 bg-rose-50 dark:bg-rose-900/20 dark:text-rose-450'
                            : 'text-gray-500 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-900/20'
                        }`}
                    title="Review Queue"
                >
                    <div className="relative">
                        <ShieldAlert size={17} />
                        {reviewCount > 0 && (
                            <span className="absolute -top-1 -right-1 w-2.5 h-2.5 bg-rose-500 rounded-full border-2 border-white dark:border-gray-900 animate-pulse"></span>
                        )}
                    </div>
                    <span className="hidden md:inline">Review</span>
                    {reviewCount > 0 && (
                        <span className="px-1.5 py-0.5 text-xs font-bold bg-rose-500 text-white rounded-full">
                            {reviewCount}
                        </span>
                    )}
                </Link>
                <Link
                    to="/queue"
                    className={`p-1.5 sm:p-2 rounded-lg transition-all flex items-center gap-1.5 text-sm font-medium shrink-0 ${isQueue
                            ? 'text-indigo-600 bg-indigo-50 dark:bg-indigo-900/20 dark:text-indigo-400'
                            : 'text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20'
                        }`}
                    title="Translation Queue"
                >
                    <ListTodo size={17} />
                    <span className="hidden md:inline">Queue</span>
                </Link>
                <Link
                    to="/autopilot"
                    className={`p-1.5 sm:p-2 rounded-lg transition-all flex items-center gap-1.5 text-sm font-medium shrink-0 ${location.pathname === '/autopilot'
                            ? 'text-indigo-600 bg-indigo-50 dark:bg-indigo-900/20 dark:text-indigo-400'
                            : 'text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20'
                        }`}
                    title="Autopilot"
                >
                    <Plane size={17} />
                    <span className="hidden md:inline">Autopilot</span>
                </Link>
                <Link
                    to="/library"
                    className={`p-1.5 sm:p-2 rounded-lg transition-all flex items-center gap-1.5 text-sm font-medium shrink-0 ${isLibrary
                            ? 'text-indigo-600 bg-indigo-50 dark:bg-indigo-900/20 dark:text-indigo-400'
                            : 'text-gray-500 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20'
                        }`}
                    title="Media Library"
                >
                    <Library size={17} />
                    <span className="hidden md:inline">Library</span>
                </Link>
                <Link
                    to="/health"
                    className={`p-1.5 sm:p-2 rounded-lg transition-all flex items-center gap-1.5 text-sm font-medium shrink-0 ${location.pathname === '/health'
                            ? 'text-emerald-600 bg-emerald-50 dark:bg-emerald-900/20 dark:text-emerald-400'
                            : 'text-gray-500 hover:text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/20'
                        }`}
                    title="System Health"
                >
                    <HeartPulse size={17} />
                    <span className="hidden md:inline">Health</span>
                </Link>
                {updateAvailable && (
                    <Link
                        to="/settings"
                        state={{ tab: 'updates' }}
                        className="px-2 sm:px-2.5 py-1 rounded-lg text-xs font-semibold bg-amber-500/20 text-amber-600 dark:text-amber-300 border border-amber-500/30 flex items-center gap-1.5 hover:bg-amber-500/30 transition-all animate-pulse shrink-0"
                        title="New OmbiSub update available! Click to view and install."
                    >
                        <ArrowDownCircle size={14} />
                        <span className="hidden sm:inline">Update</span>
                    </Link>
                )}
                <Link
                    to="/settings"
                    className="p-1.5 sm:p-2 text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-lg transition-all shrink-0"
                    title="Settings"
                >
                    <Settings size={18} />
                </Link>
            </div>
        </div>
    );
};

export default TopBar;
