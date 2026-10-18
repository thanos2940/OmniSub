import React from 'react';
import { BarChart3, Database, Layers, Sparkles } from 'lucide-react';

const StatsTab = ({ tokenSummary, episodes = [] }) => {
    const totalEpisodes = episodes.length;
    const translatedEpisodes = episodes.filter(e => e.translated).length;
    const translationRate = totalEpisodes > 0 ? Math.round((translatedEpisodes / totalEpisodes) * 100) : 0;

    return (
        <div className="space-y-6">
            {/* Top Metrics Cards */}
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="bg-white dark:bg-gray-800 p-5 border border-gray-200 dark:border-gray-700 rounded-2xl shadow-sm space-y-1">
                    <div className="text-xs font-bold text-gray-400 uppercase tracking-wider">Episode Translation Progress</div>
                    <div className="text-2xl font-black text-indigo-600 dark:text-indigo-400">
                        {translatedEpisodes} / {totalEpisodes} <span className="text-sm font-normal text-gray-400">({translationRate}%)</span>
                    </div>
                </div>

                <div className="bg-white dark:bg-gray-800 p-5 border border-gray-200 dark:border-gray-700 rounded-2xl shadow-sm space-y-1">
                    <div className="text-xs font-bold text-gray-400 uppercase tracking-wider">Static Context Base Load</div>
                    <div className="text-2xl font-black text-emerald-600 dark:text-emerald-400">
                        {tokenSummary?.total_static_tokens || 0} <span className="text-sm font-normal text-gray-400">tokens</span>
                    </div>
                </div>

                <div className="bg-white dark:bg-gray-800 p-5 border border-gray-200 dark:border-gray-700 rounded-2xl shadow-sm space-y-1">
                    <div className="text-xs font-bold text-gray-400 uppercase tracking-wider">Est. Avg. Scene Chunk</div>
                    <div className="text-2xl font-black text-purple-600 dark:text-purple-400">
                        {(tokenSummary?.total_static_tokens || 0) + 6500} <span className="text-sm font-normal text-gray-400">tokens</span>
                    </div>
                </div>
            </div>

            {/* Context Budget Breakdown */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-4">
                <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                    <BarChart3 className="text-indigo-600 dark:text-indigo-400" size={20} />
                    <h3 className="text-base font-bold text-gray-900 dark:text-white">Context Window Allocation Analysis</h3>
                </div>

                {tokenSummary ? (
                    <div className="space-y-3">
                        <div className="flex justify-between items-center text-sm p-3 rounded-xl bg-gray-50 dark:bg-gray-750">
                            <span className="text-gray-600 dark:text-gray-300 font-medium">Glossary Terms Load</span>
                            <span className="font-mono font-bold text-indigo-600 dark:text-indigo-400">{tokenSummary.glossary_tokens} tokens</span>
                        </div>
                        <div className="flex justify-between items-center text-sm p-3 rounded-xl bg-gray-50 dark:bg-gray-750">
                            <span className="text-gray-600 dark:text-gray-300 font-medium">Context Guide &amp; Lore</span>
                            <span className="font-mono font-bold text-indigo-600 dark:text-indigo-400">{tokenSummary.context_tokens} tokens</span>
                        </div>
                        <div className="flex justify-between items-center text-sm p-3 rounded-xl bg-gray-50 dark:bg-gray-750">
                            <span className="text-gray-600 dark:text-gray-300 font-medium">System Instructions &amp; Grammar Guardrails</span>
                            <span className="font-mono font-bold text-indigo-600 dark:text-indigo-400">{tokenSummary.base_instruction_tokens} tokens</span>
                        </div>
                        <div className="flex justify-between items-center text-sm p-3 rounded-xl bg-indigo-50 dark:bg-indigo-950/40 border border-indigo-100 dark:border-indigo-900/50">
                            <span className="text-indigo-900 dark:text-indigo-200 font-bold">Total Persistent Context Payload</span>
                            <span className="font-mono font-black text-indigo-600 dark:text-indigo-400">{tokenSummary.total_static_tokens} tokens</span>
                        </div>
                    </div>
                ) : (
                    <p className="text-sm text-gray-400 italic text-center py-6">Calculating token budget...</p>
                )}
            </div>
        </div>
    );
};

export default StatsTab;
