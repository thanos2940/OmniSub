import React, { useState, useEffect } from 'react';
import { Film, Sparkles, Edit2, Trash2, X, Check, RefreshCw } from 'lucide-react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';

const EpisodeSummariesPanel = ({ projectName, projectSettings }) => {
    const [summaries, setSummaries] = useState({});
    const [loading, setLoading] = useState(true);
    const [generatingEp, setGeneratingEp] = useState(null);
    const [editingEp, setEditingEp] = useState(null);
    const [editContent, setEditContent] = useState('');
    const toast = useToast();

    const loadSummaries = async () => {
        setLoading(true);
        try {
            const res = await api.getSummaries(projectName);
            setSummaries(res.data || {});
        } catch (err) {
            console.error("Failed to load summaries", err);
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadSummaries();
    }, [projectName]);

    const handleGenerate = async (episodeName) => {
        const epToGenerate = episodeName || prompt("Enter episode name to generate a summary for (e.g. S01E01):");
        if (!epToGenerate) return;
        
        setGeneratingEp(epToGenerate);
        try {
            const model = projectSettings?.context_model || 'gemini-flash-latest';
            const res = await api.generateSummary(projectName, epToGenerate, model);
            const jobId = res.data?.job_id;
            toast.success(`Summary generation started for ${epToGenerate}...`);
            if (jobId) {
                let attempts = 0;
                const poll = async () => {
                    attempts++;
                    const jobRes = await api.getJob(jobId);
                    if (jobRes.data?.status === 'completed') {
                        toast.success(`Summary for ${epToGenerate} ready.`);
                        loadSummaries();
                    } else if (jobRes.data?.status === 'failed') {
                        toast.error(`Summary generation failed: ${jobRes.data.message}`);
                    } else if (attempts < 15) {
                        setTimeout(poll, 2000);
                    }
                };
                setTimeout(poll, 2000);
            } else {
                setTimeout(loadSummaries, 2000);
            }
        } catch (err) {
            console.error("Failed to generate summary", err);
            toast.error("Failed to generate summary.");
        } finally {
            setGeneratingEp(null);
        }
    };

    const handleSave = async (episodeName) => {
        if (!editContent.trim()) return toast.error("Summary cannot be empty.");
        try {
            await api.updateSummary(projectName, episodeName, editContent);
            toast.success(`Summary for ${episodeName} saved.`);
            setEditingEp(null);
            loadSummaries();
        } catch (err) {
            console.error("Failed to save summary", err);
            toast.error("Failed to save summary.");
        }
    };

    const handleDelete = async (episodeName) => {
        if (!window.confirm(`Delete summary for ${episodeName}?`)) return;
        try {
            await api.deleteSummary(projectName, episodeName);
            toast.success("Summary deleted.");
            loadSummaries();
        } catch (err) {
            console.error("Failed to delete summary", err);
            toast.error("Failed to delete summary.");
        }
    };

    const openEdit = (episodeName, content) => {
        setEditingEp(episodeName);
        setEditContent(content || '');
    };

    if (loading) {
        return (
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-8 text-center">
                <RefreshCw className="w-8 h-8 mx-auto text-blue-400 animate-spin mb-4" />
                <p className="text-gray-500">Loading Episode Summaries...</p>
            </div>
        );
    }

    const epsList = Object.keys(summaries).sort();

    return (
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden shadow-sm">
            <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex justify-between items-center">
                <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                    <Film size={20} className="text-blue-500" /> Episode Summaries
                    <span className="text-sm font-normal text-gray-500 ml-2">({epsList.length} items)</span>
                </h3>
                <div className="flex items-center gap-2">
                    <button
                        onClick={() => handleGenerate(null)}
                        className="flex items-center gap-1.5 text-indigo-600 hover:text-indigo-700 px-3 py-1.5 rounded-lg hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors text-sm font-medium"
                    >
                        <Sparkles size={14} /> AI Generate
                    </button>
                    <button
                        onClick={loadSummaries}
                        className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-lg transition-colors"
                        title="Refresh Summaries"
                    >
                        <RefreshCw size={16} />
                    </button>
                </div>
            </div>

            <div className="p-0">
                {epsList.length === 0 ? (
                    <div className="text-center py-12">
                        <Film size={40} className="mx-auto text-gray-300 dark:text-gray-600 mb-3" />
                        <p className="text-gray-500">No episode summaries found.</p>
                        <p className="text-sm text-gray-400 mt-1">Generate summaries to help the AI maintain context across episodes.</p>
                    </div>
                ) : (
                    <div className="divide-y divide-gray-100 dark:divide-gray-700 max-h-[600px] overflow-y-auto">
                        {epsList.map(epName => (
                            <div key={epName} className="p-6 hover:bg-gray-50/50 dark:hover:bg-gray-800/30 transition-colors">
                                <div className="flex justify-between items-start mb-2">
                                    <h4 className="font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                        <span className="px-2 py-0.5 bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400 rounded text-sm uppercase">
                                            {epName}
                                        </span>
                                    </h4>
                                    <div className="flex items-center gap-1">
                                        {editingEp !== epName && (
                                            <button onClick={() => openEdit(epName, summaries[epName])} className="p-1.5 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded transition-colors" title="Edit">
                                                <Edit2 size={14} />
                                            </button>
                                        )}
                                        <button onClick={() => handleGenerate(epName)} disabled={generatingEp === epName} className="p-1.5 text-gray-400 hover:text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded transition-colors disabled:opacity-50" title="Regenerate">
                                            {generatingEp === epName ? <RefreshCw size={14} className="animate-spin" /> : <Sparkles size={14} />}
                                        </button>
                                        <button onClick={() => handleDelete(epName)} className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors" title="Delete">
                                            <Trash2 size={14} />
                                        </button>
                                    </div>
                                </div>
                                
                                {editingEp === epName ? (
                                    <div className="mt-3 bg-white dark:bg-gray-900 p-3 rounded-lg border border-indigo-200 dark:border-indigo-800">
                                        <textarea
                                            value={editContent}
                                            onChange={(e) => setEditContent(e.target.value)}
                                            className="w-full min-h-[120px] p-2 bg-transparent border-none focus:ring-0 resize-y text-gray-700 dark:text-gray-300 outline-none text-sm leading-relaxed"
                                            placeholder="Write summary here..."
                                        />
                                        <div className="flex justify-end gap-2 mt-2 pt-2 border-t border-gray-100 dark:border-gray-800">
                                            <button onClick={() => setEditingEp(null)} className="px-3 py-1.5 text-xs text-gray-500 hover:bg-gray-100 dark:hover:bg-gray-800 rounded transition-colors">
                                                Cancel
                                            </button>
                                            <button onClick={() => handleSave(epName)} className="flex items-center gap-1 px-3 py-1.5 text-xs bg-indigo-600 text-white rounded hover:bg-indigo-700 transition-colors">
                                                <Check size={12} /> Save
                                            </button>
                                        </div>
                                    </div>
                                ) : (
                                    <p className="text-gray-600 dark:text-gray-400 text-sm leading-relaxed whitespace-pre-wrap">
                                        {summaries[epName]}
                                    </p>
                                )}
                            </div>
                        ))}
                    </div>
                )}
            </div>
        </div>
    );
};

export default EpisodeSummariesPanel;
