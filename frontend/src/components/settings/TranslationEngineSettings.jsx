import React from 'react';
import { Bot } from 'lucide-react';

// Who translates queued episodes: the configured model, or external AI agents
// (Claude Code, Antigravity) working through scripts/agent_task.py.
const TranslationEngineSettings = ({ settings, onChange }) => {
    const engine = settings.translation_engine || 'model';

    return (
        <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-4">
            <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                <Bot className="text-purple-600 dark:text-purple-400" size={20} />
                <h3 className="text-base font-bold text-gray-900 dark:text-white">Translation Engine</h3>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {[
                    { value: 'model', title: 'Model (Gemini / local LLM)', body: 'The background worker translates queued episodes with the configured model.' },
                    { value: 'agent', title: 'AI agent (Claude Code, Antigravity)', body: 'Queued episodes wait for an agent. It claims them with scripts/agent_task.py, translates the prepared work file, and Omnisub validates, post-processes and exports. Extraction and export retries keep running.' },
                ].map(opt => (
                    <label
                        key={opt.value}
                        className={`p-3 rounded-xl border cursor-pointer transition-colors ${engine === opt.value
                            ? 'border-purple-400 bg-purple-50/60 dark:border-purple-600 dark:bg-purple-950/20'
                            : 'border-gray-200 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-750/50'}`}
                    >
                        <div className="flex items-center gap-2">
                            <input
                                type="radio"
                                name="translation_engine"
                                checked={engine === opt.value}
                                onChange={() => onChange('translation_engine', opt.value)}
                                className="w-4 h-4 text-purple-600"
                            />
                            <span className="text-sm font-bold text-gray-900 dark:text-white">{opt.title}</span>
                        </div>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-1 ml-6">{opt.body}</p>
                    </label>
                ))}
            </div>
            {engine === 'agent' && (
                <div className="flex items-center gap-3">
                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Release an idle agent claim after</label>
                    <input
                        type="number"
                        min="1"
                        max="168"
                        value={settings.agent_claim_ttl_hours ?? 12}
                        onChange={(e) => onChange('agent_claim_ttl_hours', parseFloat(e.target.value) || 12)}
                        className="w-20 px-3 py-1.5 rounded-lg border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white text-sm"
                    />
                    <span className="text-sm text-gray-500 dark:text-gray-400">hours</span>
                </div>
            )}
        </div>
    );
};

export default TranslationEngineSettings;
