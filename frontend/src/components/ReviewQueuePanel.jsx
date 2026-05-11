import React, { useState, useEffect } from 'react';
import { ShieldAlert, Check, CheckCircle, RefreshCw, AlertTriangle } from 'lucide-react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';

const ReviewQueuePanel = ({ projectName }) => {
    const [queue, setQueue] = useState([]);
    const [loading, setLoading] = useState(true);
    const [resolvingId, setResolvingId] = useState(null);
    const [resolvingAll, setResolvingAll] = useState(false);
    const toast = useToast();

    const loadQueue = async () => {
        setLoading(true);
        try {
            const res = await api.getReviewQueue(projectName);
            // Backend returns { items: [...], count: N }
            setQueue(res.data?.items || []);
        } catch (err) {
            console.error("Failed to load review queue", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadQueue();
    }, [projectName]);

    const handleResolve = async (episode, index) => {
        setResolvingId(`${episode}-${index}`);
        try {
            await api.resolveReviewItem(projectName, episode, index);
            toast.success("Item resolved");
            setQueue(prev => prev.filter(item => !(item.episode === episode && item.index === index)));
        } catch (err) {
            console.error("Failed to resolve item", err);
            toast.error("Failed to resolve item.");
        } finally {
            setResolvingId(null);
        }
    };

    const handleResolveAll = async () => {
        if (!window.confirm("Are you sure you want to dismiss all pending review items?")) return;
        setResolvingAll(true);
        try {
            await api.resolveAllReviewItems(projectName);
            toast.success("All items resolved");
            setQueue([]);
        } catch (err) {
            console.error("Failed to resolve all", err);
            toast.error("Failed to resolve all items.");
        } finally {
            setResolvingAll(false);
        }
    };

    if (loading) {
        return (
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-8 text-center">
                <RefreshCw className="w-8 h-8 mx-auto text-rose-400 animate-spin mb-4" />
                <p className="text-gray-500">Loading Review Queue...</p>
            </div>
        );
    }

    return (
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden shadow-sm">
            <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex justify-between items-center">
                <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                    <ShieldAlert size={20} className="text-rose-500" /> Review Queue
                    <span className="text-sm font-normal text-gray-500 ml-2">({queue.length} pending)</span>
                </h3>
                <div className="flex items-center gap-2">
                    {queue.length > 0 && (
                        <button
                            onClick={handleResolveAll}
                            disabled={resolvingAll}
                            className="flex items-center gap-1.5 px-3 py-1.5 text-sm bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-300 rounded-lg font-medium transition-colors disabled:opacity-50"
                        >
                            <CheckCircle size={14} /> Resolve All
                        </button>
                    )}
                    <button
                        onClick={loadQueue}
                        className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-lg transition-colors"
                        title="Refresh Queue"
                    >
                        <RefreshCw size={16} />
                    </button>
                </div>
            </div>

            <div className="p-0">
                {queue.length === 0 ? (
                    <div className="text-center py-12">
                        <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-emerald-50 dark:bg-emerald-900/20 mb-4">
                            <CheckCircle size={32} className="text-emerald-500" />
                        </div>
                        <h4 className="text-lg font-medium text-gray-900 dark:text-white mb-1">You're all caught up!</h4>
                        <p className="text-gray-500 text-sm">No items currently need manual review.</p>
                    </div>
                ) : (
                    <div className="divide-y divide-gray-100 dark:divide-gray-700 max-h-[600px] overflow-y-auto">
                        {queue.map((item, i) => (
                            <div key={`${item.episode}-${item.index}-${i}`} className="p-4 hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors">
                                <div className="flex justify-between items-start mb-3">
                                    <div className="flex items-center gap-2">
                                        <span className="text-xs font-semibold px-2 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                                            {item.episode}
                                        </span>
                                        <span className="text-xs font-mono text-gray-400">Idx: {item.index}</span>
                                    </div>
                                    <button
                                        onClick={() => handleResolve(item.episode, item.index)}
                                        disabled={resolvingId === `${item.episode}-${item.index}`}
                                        className="flex items-center gap-1 text-xs font-medium text-emerald-600 hover:text-emerald-700 bg-emerald-50 hover:bg-emerald-100 dark:bg-emerald-900/20 dark:hover:bg-emerald-900/40 px-2 py-1 rounded transition-colors disabled:opacity-50"
                                    >
                                        <Check size={14} /> Resolve
                                    </button>
                                </div>
                                <div className="grid grid-cols-2 gap-4 mb-3">
                                    <div className="bg-gray-50 dark:bg-gray-900/50 p-3 rounded-lg border border-gray-100 dark:border-gray-800">
                                        <div className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-1">Original</div>
                                        <div className="text-sm text-gray-700 dark:text-gray-300">{item.original}</div>
                                    </div>
                                    <div className="bg-indigo-50/50 dark:bg-indigo-900/10 p-3 rounded-lg border border-indigo-100 dark:border-indigo-800/30">
                                        <div className="text-[10px] font-bold text-indigo-400 uppercase tracking-wider mb-1">Translated</div>
                                        <div className="text-sm text-gray-900 dark:text-white font-medium">{item.translated}</div>
                                    </div>
                                </div>
                                <div className="flex items-start gap-2 bg-rose-50 dark:bg-rose-900/10 p-3 rounded-lg border border-rose-100 dark:border-rose-900/30">
                                    <AlertTriangle size={16} className="text-rose-500 mt-0.5 flex-shrink-0" />
                                    <div>
                                        <div className="text-[10px] font-bold text-rose-500 uppercase tracking-wider mb-0.5">AI Flag Reason</div>
                                        <div className="text-sm text-rose-700 dark:text-rose-400">{item.review_issues || "Unknown issue"}</div>
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};

export default ReviewQueuePanel;
