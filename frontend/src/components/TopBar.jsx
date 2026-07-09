import React, { useState, useEffect } from 'react';
import { Link, useLocation } from 'react-router-dom';
import { LayoutGrid, Settings, ChevronRight, Home, Library, ShieldAlert, ListTodo, Gauge, HeartPulse } from 'lucide-react';
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

    useEffect(() => {
        const fetchCount = async () => {
            try {
                const res = await api.getGlobalReviewQueue();
                setReviewCount(res.data?.count || 0);
            } catch (e) {
                console.error("Failed to fetch review count", e);
            }
        };
        fetchCount();
        const interval = setInterval(fetchCount, 15000);
        return () => clearInterval(interval);
    }, [location.pathname]);

    return (
        <div className="sticky top-0 z-50 bg-white/80 dark:bg-gray-900/80 backdrop-blur-md border-b border-gray-200 dark:border-gray-800 px-6 py-3 flex items-center justify-between shadow-sm">
            <div className="flex items-center gap-4">
                {/* Logo / Home */}
                <Link to="/" className="flex items-center gap-2 text-gray-900 dark:text-white hover:opacity-80 transition-opacity group">
                    <div className="w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center shadow-indigo-200 dark:shadow-none shadow-md group-hover:scale-105 transition-transform">
                        <LayoutGrid className="text-white" size={18} />
                    </div>
                    <span className="text-lg font-bold tracking-tight hidden md:block">Omnisub</span>
                </Link>

                {/* Divider */}
                <div className="h-6 w-px bg-gray-200 dark:bg-gray-700 mx-1 hidden md:block"></div>

                {/* Breadcrumbs */}
                <nav className="flex items-center gap-1.5 text-sm font-medium text-gray-500 dark:text-gray-400">
                    <Link to="/" className="p-1.5 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-md hover:text-indigo-600 dark:hover:text-indigo-400 transition-all">
                        <Home size={16} />
                    </Link>

                    {isLibrary && (
                        <>
                            <ChevronRight size={14} className="text-gray-300 dark:text-gray-600" />
                            <span className="px-2 py-1 text-gray-900 dark:text-white bg-gray-100 dark:bg-gray-800 rounded-md border border-gray-200 dark:border-gray-700">
                                Library
                            </span>
                        </>
                    )}

                    {isReview && (
                        <>
                            <ChevronRight size={14} className="text-gray-300 dark:text-gray-600" />
                            <span className="px-2 py-1 text-gray-900 dark:text-white bg-gray-100 dark:bg-gray-800 rounded-md border border-gray-200 dark:border-gray-700">
                                Review Queue
                            </span>
                        </>
                    )}

                    {isQueue && (
                        <>
                            <ChevronRight size={14} className="text-gray-300 dark:text-gray-600" />
                            <span className="px-2 py-1 text-gray-900 dark:text-white bg-gray-100 dark:bg-gray-800 rounded-md border border-gray-200 dark:border-gray-700">
                                Translation Queue
                            </span>
                        </>
                    )}

                    {projectName && (
                        <>
                            <ChevronRight size={14} className="text-gray-300 dark:text-gray-600" />
                            <Link
                                to={`/project/${encodeURIComponent(projectName)}`}
                                className="px-2 py-1 hover:bg-gray-100 dark:hover:bg-gray-800 rounded-md hover:text-indigo-600 dark:hover:text-indigo-400 transition-all truncate max-w-[150px] md:max-w-xs"
                            >
                                {projectName}
                            </Link>
                        </>
                    )}

                    {episodeName && (
                        <>
                            <ChevronRight size={14} className="text-gray-300 dark:text-gray-600" />
                            <span className="px-2 py-1 text-gray-900 dark:text-white bg-gray-100 dark:bg-gray-800 rounded-md truncate max-w-[150px] md:max-w-xs border border-gray-200 dark:border-gray-700">
                                {episodeName}
                            </span>
                        </>
                    )}
                </nav>
            </div>

            {/* Right Actions */}
            <div className="flex items-center gap-2">
                <Link
                    to="/dashboard"
                    className={`p-2 rounded-lg transition-all flex items-center gap-1.5 text-sm font-medium ${location.pathname === '/dashboard'
                            ? 'text-indigo-600 bg-indigo-50 dark:bg-indigo-900/20 dark:text-indigo-400'
                            : 'text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20'
                        }`}
                    title="Dashboard"
                >
                    <Gauge size={18} />
                    <span className="hidden md:inline">Dashboard</span>
                </Link>
                <Link
                    to="/review"
                    className={`p-2 rounded-lg transition-all flex items-center gap-1.5 text-sm font-medium ${isReview
                            ? 'text-rose-600 bg-rose-50 dark:bg-rose-900/20 dark:text-rose-450'
                            : 'text-gray-500 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-900/20'
                        }`}
                    title="Review Queue"
                >
                    <div className="relative">
                        <ShieldAlert size={18} />
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
                    className={`p-2 rounded-lg transition-all flex items-center gap-1.5 text-sm font-medium ${isQueue
                            ? 'text-indigo-600 bg-indigo-50 dark:bg-indigo-900/20 dark:text-indigo-400'
                            : 'text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20'
                        }`}
                    title="Translation Queue"
                >
                    <ListTodo size={18} />
                    <span className="hidden md:inline">Queue</span>
                </Link>
                <Link
                    to="/library"
                    className={`p-2 rounded-lg transition-all flex items-center gap-1.5 text-sm font-medium ${isLibrary
                            ? 'text-indigo-600 bg-indigo-50 dark:bg-indigo-900/20 dark:text-indigo-400'
                            : 'text-gray-500 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20'
                        }`}
                    title="Media Library"
                >
                    <Library size={18} />
                    <span className="hidden md:inline">Library</span>
                </Link>
                <Link
                    to="/health"
                    className={`p-2 rounded-lg transition-all flex items-center gap-1.5 text-sm font-medium ${location.pathname === '/health'
                            ? 'text-emerald-600 bg-emerald-50 dark:bg-emerald-900/20 dark:text-emerald-400'
                            : 'text-gray-500 hover:text-emerald-600 hover:bg-emerald-50 dark:hover:bg-emerald-900/20'
                        }`}
                    title="System Health"
                >
                    <HeartPulse size={18} />
                    <span className="hidden md:inline">Health</span>
                </Link>
                <Link
                    to="/settings"
                    className="p-2 text-gray-500 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-lg transition-all"
                    title="Settings"
                >
                    <Settings size={20} />
                </Link>
            </div>
        </div>
    );
};

export default TopBar;
