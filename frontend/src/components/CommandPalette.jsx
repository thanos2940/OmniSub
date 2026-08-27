import React, { useState, useEffect, useRef, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
    Search, Film, Tv, Settings, Home, Library, ShieldAlert,
    ListTodo, HeartPulse, RefreshCw, Moon, CornerDownLeft, X, Key
} from 'lucide-react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';

export default function CommandPalette() {
    const [isOpen, setIsOpen] = useState(false);
    const [query, setQuery] = useState('');
    const [selectedIndex, setSelectedIndex] = useState(0);
    const [projects, setProjects] = useState([]);
    const [loadingProjects, setLoadingProjects] = useState(false);
    const inputRef = useRef(null);
    const navigate = useNavigate();
    const toast = useToast();

    // Global Keybindings (Cmd+K / Ctrl+K / Escape)
    useEffect(() => {
        const handleKeyDown = (e) => {
            if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
                e.preventDefault();
                setIsOpen((prev) => !prev);
            } else if (e.key === 'Escape' && isOpen) {
                e.preventDefault();
                setIsOpen(false);
            }
        };

        const handleCustomOpen = () => setIsOpen(true);

        window.addEventListener('keydown', handleKeyDown);
        window.addEventListener('omnisub:open-command-palette', handleCustomOpen);
        return () => {
            window.removeEventListener('keydown', handleKeyDown);
            window.removeEventListener('omnisub:open-command-palette', handleCustomOpen);
        };
    }, [isOpen]);

    // Focus input on open & load project list
    useEffect(() => {
        if (isOpen) {
            setQuery('');
            setSelectedIndex(0);
            setTimeout(() => inputRef.current?.focus(), 50);

            setLoadingProjects(true);
            api.getProjects()
                .then((res) => {
                    setProjects(res.data || []);
                })
                .catch((err) => {
                    console.error('Failed to load projects for command palette', err);
                })
                .finally(() => setLoadingProjects(false));
        }
    }, [isOpen]);

    // Build Searchable Items
    const defaultPages = useMemo(() => [
        { id: 'page-home', title: 'Dashboard & Projects', category: 'Navigation', icon: Home, action: () => navigate('/') },
        { id: 'page-library', title: 'Media Library & Arr Sync', category: 'Navigation', icon: Library, action: () => navigate('/library') },
        { id: 'page-queue', title: 'Translation Worker Queue', category: 'Navigation', icon: ListTodo, action: () => navigate('/queue') },
        { id: 'page-review', title: 'Global Review Queue', category: 'Navigation', icon: ShieldAlert, action: () => navigate('/review') },
        { id: 'page-health', title: 'System Health & Diagnostics', category: 'Navigation', icon: HeartPulse, action: () => navigate('/health') },
        { id: 'page-settings', title: 'Global Application Settings', category: 'Navigation', icon: Settings, action: () => navigate('/settings') },
    ], [navigate]);

    const quickActions = useMemo(() => [
        {
            id: 'action-manage-api-key',
            title: 'Manage Google Gemini API Key & AI Credentials',
            category: 'Action',
            icon: Key,
            action: () => window.dispatchEvent(new CustomEvent('omnisub:open-api-key-modal'))
        },
        {
            id: 'action-sync-arr',
            title: 'Trigger Full Media Library Sync (Sonarr/Radarr/Bazarr)',
            category: 'Action',
            icon: RefreshCw,
            action: async () => {
                try {
                    await api.syncMediaLibrary();
                    toast.success('Media library sync job started!');
                } catch (e) {
                    toast.error('Failed to trigger media library sync');
                }
            }
        },
        {
            id: 'action-theme-toggle',
            title: 'Toggle Dark / Light Theme',
            category: 'Action',
            icon: Moon,
            action: () => {
                const isDark = document.documentElement.classList.contains('dark');
                if (isDark) {
                    document.documentElement.classList.remove('dark');
                    localStorage.setItem('theme', 'light');
                } else {
                    document.documentElement.classList.add('dark');
                    localStorage.setItem('theme', 'dark');
                }
                toast.info(`Switched to ${isDark ? 'Light' : 'Dark'} mode`);
            }
        }
    ], [toast]);

    const projectItems = useMemo(() => {
        return projects.map((p) => {
            const isMovie = p.type === 'movie' || p.arr_media_type === 'movie';
            return {
                id: `project-${p.name}`,
                title: p.show_name || p.name,
                subtitle: `${p.target_language ? `→ ${p.target_language}` : ''} (${p.episode_count ?? 0} eps)`,
                category: isMovie ? 'Movies' : 'Series',
                icon: isMovie ? Film : Tv,
                action: () => navigate(`/project/${encodeURIComponent(p.name)}`)
            };
        });
    }, [projects, navigate]);

    // Filter Items by query
    const filteredItems = useMemo(() => {
        const q = query.trim().toLowerCase();
        if (!q) {
            return [...defaultPages, ...quickActions, ...projectItems.slice(0, 8)];
        }
        const all = [...defaultPages, ...quickActions, ...projectItems];
        return all.filter((item) => {
            const matchTitle = item.title.toLowerCase().includes(q);
            const matchSub = item.subtitle ? item.subtitle.toLowerCase().includes(q) : false;
            const matchCat = item.category.toLowerCase().includes(q);
            return matchTitle || matchSub || matchCat;
        });
    }, [query, defaultPages, quickActions, projectItems]);

    // Reset selected index when query changes
    useEffect(() => {
        setSelectedIndex(0);
    }, [query]);

    // Handle Item Selection
    const handleSelect = (item) => {
        if (!item) return;
        setIsOpen(false);
        item.action();
    };

    // Keyboard navigation within list
    const handleInputKeyDown = (e) => {
        if (e.key === 'ArrowDown') {
            e.preventDefault();
            setSelectedIndex((prev) => (prev + 1) % Math.max(filteredItems.length, 1));
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            setSelectedIndex((prev) => (prev - 1 + filteredItems.length) % Math.max(filteredItems.length, 1));
        } else if (e.key === 'Enter') {
            e.preventDefault();
            if (filteredItems[selectedIndex]) {
                handleSelect(filteredItems[selectedIndex]);
            }
        }
    };

    return (
        <AnimatePresence>
            {isOpen && (
                <div className="fixed inset-0 z-50 flex items-start justify-center pt-20 sm:pt-28 px-4">
                    {/* Backdrop */}
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        onClick={() => setIsOpen(false)}
                        className="fixed inset-0 bg-black/60 backdrop-blur-sm"
                    />

                    {/* Modal Palette */}
                    <motion.div
                        initial={{ opacity: 0, scale: 0.96, y: -10 }}
                        animate={{ opacity: 1, scale: 1, y: 0 }}
                        exit={{ opacity: 0, scale: 0.96, y: -10 }}
                        transition={{ duration: 0.15 }}
                        className="relative w-full max-w-xl bg-white dark:bg-gray-850 rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-700 overflow-hidden z-10"
                    >
                        {/* Search Input Bar */}
                        <div className="flex items-center px-4 py-3.5 border-b border-gray-100 dark:border-gray-750 gap-3">
                            <Search className="w-5 h-5 text-gray-400 dark:text-gray-500 flex-shrink-0" />
                            <input
                                ref={inputRef}
                                type="text"
                                value={query}
                                onChange={(e) => setQuery(e.target.value)}
                                onKeyDown={handleInputKeyDown}
                                placeholder="Type a show name, route, or quick command..."
                                className="w-full text-base bg-transparent border-none outline-none text-gray-900 dark:text-white placeholder-gray-400 dark:placeholder-gray-500"
                            />
                            <button
                                onClick={() => setIsOpen(false)}
                                className="p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-lg"
                            >
                                <X size={18} />
                            </button>
                        </div>

                        {/* Items List */}
                        <div className="max-h-80 overflow-y-auto p-2 space-y-1">
                            {filteredItems.length === 0 ? (
                                <div className="text-center py-8 text-gray-400 text-sm">
                                    No results found for &ldquo;{query}&rdquo;
                                </div>
                            ) : (
                                filteredItems.map((item, idx) => {
                                    const isSelected = idx === selectedIndex;
                                    const Icon = item.icon;
                                    return (
                                        <div
                                            key={item.id}
                                            onClick={() => handleSelect(item)}
                                            onMouseEnter={() => setSelectedIndex(idx)}
                                            className={`flex items-center justify-between px-3 py-2.5 rounded-xl cursor-pointer transition-all ${
                                                isSelected
                                                    ? 'bg-indigo-600 text-white shadow-sm'
                                                    : 'hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-200'
                                            }`}
                                        >
                                            <div className="flex items-center gap-3 min-w-0">
                                                <div className={`p-1.5 rounded-lg ${
                                                    isSelected
                                                        ? 'bg-white/20 text-white'
                                                        : 'bg-gray-100 dark:bg-gray-750 text-gray-500 dark:text-gray-400'
                                                }`}>
                                                    <Icon size={16} />
                                                </div>
                                                <div className="min-w-0">
                                                    <div className="font-semibold text-sm truncate">
                                                        {item.title}
                                                    </div>
                                                    {item.subtitle && (
                                                        <div className={`text-xs truncate ${isSelected ? 'text-indigo-100' : 'text-gray-400 dark:text-gray-500'}`}>
                                                            {item.subtitle}
                                                        </div>
                                                    )}
                                                </div>
                                            </div>

                                            <div className="flex items-center gap-2 flex-shrink-0">
                                                <span className={`text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-md ${
                                                    isSelected
                                                        ? 'bg-white/20 text-white'
                                                        : 'bg-gray-100 dark:bg-gray-750 text-gray-500 dark:text-gray-400'
                                                }`}>
                                                    {item.category}
                                                </span>
                                                {isSelected && (
                                                    <CornerDownLeft size={14} className="text-white opacity-80" />
                                                )}
                                            </div>
                                        </div>
                                    );
                                })
                            )}
                        </div>

                        {/* Footer Hints */}
                        <div className="px-4 py-2 bg-gray-50 dark:bg-gray-800/80 border-t border-gray-100 dark:border-gray-750 flex items-center justify-between text-[11px] text-gray-400">
                            <div className="flex items-center gap-3">
                                <span><kbd className="font-mono bg-white dark:bg-gray-700 px-1.5 py-0.5 rounded border border-gray-200 dark:border-gray-600">↑↓</kbd> to navigate</span>
                                <span><kbd className="font-mono bg-white dark:bg-gray-700 px-1.5 py-0.5 rounded border border-gray-200 dark:border-gray-600">↵</kbd> to select</span>
                                <span><kbd className="font-mono bg-white dark:bg-gray-700 px-1.5 py-0.5 rounded border border-gray-200 dark:border-gray-600">esc</kbd> to close</span>
                            </div>
                            <span className="font-medium text-indigo-500 dark:text-indigo-400">Omnisub Command Hub</span>
                        </div>
                    </motion.div>
                </div>
            )}
        </AnimatePresence>
    );
}
