import React, { useState, useEffect } from 'react';
import { Database, Trash2, AlertTriangle, RefreshCw, BarChart } from 'lucide-react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';

const TMStatsPanel = ({ projectName }) => {
    const [tmStats, setTmStats] = useState(null);
    const [editStats, setEditStats] = useState(null);
    const [loading, setLoading] = useState(true);
    const [clearing, setClearing] = useState(false);
    const toast = useToast();

    const loadData = async () => {
        setLoading(true);
        try {
            const [tmRes, editRes] = await Promise.allSettled([
                api.getTmStats(projectName),
                api.getEditStats(projectName)
            ]);

            if (tmRes.status === 'fulfilled') setTmStats(tmRes.value.data);
            if (editRes.status === 'fulfilled') setEditStats(editRes.value.data);
        } catch (err) {
            console.error("Failed to load TM stats", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadData();
    }, [projectName]);

    const handleClearTm = async () => {
        if (!window.confirm("Are you sure you want to clear the Translation Memory? All past translations and user edits will be deleted permanently.")) return;
        setClearing(true);
        try {
            await api.clearTm(projectName);
            toast.success("Translation Memory cleared successfully.");
            await loadData();
        } catch (err) {
            console.error("Failed to clear TM", err);
            toast.error("Failed to clear Translation Memory.");
        } finally {
            setClearing(false);
        }
    };

    if (loading) {
        return (
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-8 text-center">
                <RefreshCw className="w-8 h-8 mx-auto text-indigo-400 animate-spin mb-4" />
                <p className="text-gray-500">Loading TM statistics...</p>
            </div>
        );
    }

    return (
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden shadow-sm">
            <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex justify-between items-center">
                <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                    <Database size={20} className="text-indigo-500" /> Translation Memory Stats
                </h3>
                <button
                    onClick={loadData}
                    className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-lg transition-colors"
                    title="Refresh Stats"
                >
                    <RefreshCw size={16} />
                </button>
            </div>
            
            <div className="p-6">
                {tmStats ? (
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
                        <div className="bg-indigo-50 dark:bg-indigo-900/10 p-5 rounded-xl border border-indigo-100 dark:border-indigo-800/30">
                            <div className="text-sm font-medium text-indigo-600 dark:text-indigo-400 mb-1">Total Pairs Stored</div>
                            <div className="text-3xl font-bold text-gray-900 dark:text-white">{tmStats.total_records || 0}</div>
                        </div>
                        <div className="bg-purple-50 dark:bg-purple-900/10 p-5 rounded-xl border border-purple-100 dark:border-purple-800/30">
                            <div className="text-sm font-medium text-purple-600 dark:text-purple-400 mb-1">Unique Episodes</div>
                            <div className="text-3xl font-bold text-gray-900 dark:text-white">{tmStats.unique_episodes || 0}</div>
                        </div>
                        <div className="bg-emerald-50 dark:bg-emerald-900/10 p-5 rounded-xl border border-emerald-100 dark:border-emerald-800/30">
                            <div className="text-sm font-medium text-emerald-600 dark:text-emerald-400 mb-1">High-Priority User Edits</div>
                            <div className="text-3xl font-bold text-gray-900 dark:text-white">{editStats?.user_edited_records || 0}</div>
                        </div>
                    </div>
                ) : (
                    <div className="text-center py-6 text-gray-500">Translation Memory data unavailable.</div>
                )}

                {editStats && editStats.top_edited_characters && editStats.top_edited_characters.length > 0 && (
                    <div className="mb-8">
                        <h4 className="text-sm font-semibold text-gray-700 dark:text-gray-300 uppercase tracking-wider mb-4 flex items-center gap-2">
                            <BarChart size={16} /> Top Characters Edited
                        </h4>
                        <div className="flex gap-2 flex-wrap">
                            {editStats.top_edited_characters.map(char => (
                                <span key={char} className="px-3 py-1 bg-gray-100 dark:bg-gray-700 text-gray-800 dark:text-gray-200 rounded-lg text-sm font-medium border border-gray-200 dark:border-gray-600">
                                    {char}
                                </span>
                            ))}
                        </div>
                    </div>
                )}

                <div className="border-t border-red-100 dark:border-red-900/30 pt-6 mt-6">
                    <div className="bg-red-50 dark:bg-red-900/10 rounded-xl p-5 border border-red-100 dark:border-red-900/30 flex justify-between items-center">
                        <div>
                            <h4 className="text-red-800 dark:text-red-400 font-semibold flex items-center gap-2">
                                <AlertTriangle size={16} /> Danger Zone
                            </h4>
                            <p className="text-red-600 dark:text-red-300 text-sm mt-1">Clearing the TM will wipe all accumulated context and user edits.</p>
                        </div>
                        <button
                            onClick={handleClearTm}
                            disabled={clearing}
                            className="px-4 py-2 bg-red-100 hover:bg-red-200 text-red-700 dark:bg-red-800/50 dark:hover:bg-red-800 dark:text-red-200 rounded-lg font-medium transition-colors text-sm flex items-center gap-2 disabled:opacity-50"
                        >
                            {clearing ? <RefreshCw className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                            Clear Memory
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default TMStatsPanel;
