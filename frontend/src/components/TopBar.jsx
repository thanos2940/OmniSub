import { useState, useEffect, useCallback } from 'react';
import { Link, NavLink, useLocation } from 'react-router-dom';
import { LayoutGrid, Lock, Unlock, Menu, Search, X } from 'lucide-react';
import { api } from '../api';
import { usePoll } from '../hooks/usePoll';
import RunnerPill from './activity/RunnerPill';

const REVIEW_EVENTS = ['queue_updated', 'agent_submission', 'task_completed'];

const linkClass = ({ isActive }) => [
    'relative inline-flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors',
    isActive
        ? 'bg-indigo-50 text-indigo-700 dark:bg-indigo-950/50 dark:text-indigo-300'
        : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900 dark:text-gray-300 dark:hover:bg-gray-800 dark:hover:text-white',
].join(' ');

const TopBar = () => {
    const location = useLocation();
    const [menuOpen, setMenuOpen] = useState(false);
    const [reviewCount, setReviewCount] = useState(0);
    const [authEnabled, setAuthEnabled] = useState(true); // assume secured until checked, to avoid a flash
    const [updateAvailable, setUpdateAvailable] = useState(false);

    useEffect(() => { setMenuOpen(false); }, [location.pathname, location.search]);

    const fetchCount = useCallback(async ({ signal } = {}) => {
        const res = await api.getGlobalReviewCount(signal);
        setReviewCount(res.data?.count || 0);
    }, []);
    usePoll(fetchCount, { intervalMs: 60000, hiddenIntervalMs: 300000, refreshOn: REVIEW_EVENTS });

    const checkUpdates = useCallback(async ({ signal } = {}) => {
        const res = await api.getUpdateStatus(signal);
        setUpdateAvailable(!!res.data?.update_available);
    }, []);
    usePoll(checkUpdates, { intervalMs: 60000, hiddenIntervalMs: 300000 });

    useEffect(() => {
        api.getAuthStatus()
            .then(res => setAuthEnabled(!!res.data?.auth_enabled))
            .catch(() => {});
    }, []);

    const onProjectRoute = location.pathname.startsWith('/project/');
    // Library also owns the project pages that open from it.
    const libraryClass = ({ isActive }) => linkClass({ isActive: isActive || onProjectRoute });

    const links = (
        <>
            <NavLink to="/" end className={libraryClass}>Library</NavLink>
            <NavLink to="/activity" className={linkClass}>Activity</NavLink>
            <NavLink to="/review" className={linkClass}>
                Review
                {reviewCount > 0 && (
                    <span className="rounded-full bg-rose-500 px-1.5 text-xs font-semibold tabular-nums text-white">{reviewCount}</span>
                )}
            </NavLink>
            <NavLink to="/settings" className={linkClass}>Settings</NavLink>
            <NavLink to="/system" className={linkClass}>
                System
                {updateAvailable && (
                    <span className="h-2 w-2 rounded-full bg-amber-500" title="An update is available" aria-label="An update is available" />
                )}
            </NavLink>
        </>
    );

    return (
        <header className="sticky top-0 z-50 border-b border-gray-200 bg-white/90 backdrop-blur-md dark:border-gray-800 dark:bg-gray-900/90">
            <div className="mx-auto flex h-14 w-full max-w-7xl items-center gap-2 px-4 sm:px-6 lg:px-8">
                <Link to="/" className="mr-2 flex shrink-0 items-center gap-2 text-gray-900 hover:opacity-80 dark:text-white" title="Library">
                    <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-indigo-600 shadow-sm">
                        <LayoutGrid className="text-white" size={18} />
                    </span>
                    <span className="hidden text-lg font-bold tracking-tight sm:block">OmbiSub</span>
                </Link>

                <nav aria-label="Main" className="hidden items-center gap-1 md:flex">{links}</nav>

                <div className="ml-auto flex items-center gap-2">
                    <RunnerPill />
                    <button
                        type="button"
                        onClick={() => window.dispatchEvent(new CustomEvent('omnisub:open-command-palette'))}
                        className="hidden items-center gap-2 rounded-lg border border-gray-200 bg-gray-50 px-3 py-1.5 text-xs text-gray-500 transition-colors hover:bg-gray-100 dark:border-gray-700 dark:bg-gray-800 dark:text-gray-400 dark:hover:bg-gray-750 lg:flex"
                        title="Search or jump to (Ctrl+K)"
                    >
                        <Search size={14} />
                        <span>Search</span>
                        <kbd className="rounded border border-gray-200 bg-white px-1.5 py-0.5 font-mono text-[10px] dark:border-gray-700 dark:bg-gray-900">Ctrl K</kbd>
                    </button>
                    <button
                        type="button"
                        onClick={() => window.dispatchEvent(new CustomEvent('omnisub:open-command-palette'))}
                        className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-gray-500 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-800 lg:hidden"
                        aria-label="Search"
                    >
                        <Search size={17} />
                    </button>
                    <Link
                        to="/settings?section=security"
                        className={`inline-flex h-8 w-8 items-center justify-center rounded-lg hover:bg-gray-100 dark:hover:bg-gray-800 ${authEnabled ? 'text-gray-500 dark:text-gray-400' : 'text-amber-600 dark:text-amber-400'}`}
                        title={authEnabled ? 'Login is on — security settings' : 'No login set — anyone who can reach this server can use it. Open security settings.'}
                        aria-label={authEnabled ? 'Login is on' : 'No login set'}
                    >
                        {authEnabled ? <Lock size={17} /> : <Unlock size={17} />}
                    </Link>
                    <button
                        type="button"
                        onClick={() => setMenuOpen(o => !o)}
                        className="inline-flex h-8 w-8 items-center justify-center rounded-lg text-gray-600 hover:bg-gray-100 dark:text-gray-300 dark:hover:bg-gray-800 md:hidden"
                        aria-label={menuOpen ? 'Close menu' : 'Open menu'}
                        aria-expanded={menuOpen}
                    >
                        {menuOpen ? <X size={18} /> : <Menu size={18} />}
                    </button>
                </div>
            </div>

            {menuOpen && (
                <nav aria-label="Main" className="flex flex-col gap-1 border-t border-gray-200 px-4 py-3 dark:border-gray-800 md:hidden [&>a]:justify-between [&>a]:py-2.5">
                    {links}
                </nav>
            )}
        </header>
    );
};

export default TopBar;
