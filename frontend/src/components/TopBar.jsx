import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { LayoutGrid, Settings, ChevronRight, Home, Library } from 'lucide-react';

const TopBar = () => {
    const location = useLocation();
    const pathSegments = location.pathname.split('/').filter(Boolean);

    // Simple parsing logic based on route structure: /project/:projectName/episode/:episodeName
    const projectName = pathSegments[0] === 'project' ? decodeURIComponent(pathSegments[1]) : null;
    const episodeName = pathSegments[2] === 'episode' ? decodeURIComponent(pathSegments[3]) : null;
    const isLibrary = location.pathname === '/library';

    return (
        <div className="sticky top-0 z-50 bg-white/80 dark:bg-gray-900/80 backdrop-blur-md border-b border-gray-200 dark:border-gray-800 px-6 py-3 flex items-center justify-between shadow-sm">
            <div className="flex items-center gap-4">
                {/* Logo / Home */}
                <Link to="/" className="flex items-center gap-2 text-gray-900 dark:text-white hover:opacity-80 transition-opacity group">
                    <div className="w-8 h-8 bg-indigo-600 rounded-lg flex items-center justify-center shadow-indigo-200 dark:shadow-none shadow-md group-hover:scale-105 transition-transform">
                        <LayoutGrid className="text-white" size={18} />
                    </div>
                    <span className="text-lg font-bold tracking-tight hidden md:block">OmbiSub</span>
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
                    to="/library"
                    className={`p-2 rounded-lg transition-all flex items-center gap-1.5 text-sm font-medium ${
                        isLibrary
                            ? 'text-indigo-600 bg-indigo-50 dark:bg-indigo-900/20 dark:text-indigo-400'
                            : 'text-gray-500 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20'
                    }`}
                    title="Bazarr Library"
                >
                    <Library size={18} />
                    <span className="hidden md:inline">Library</span>
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
