import React, { useState } from 'react';
import { Play, Sparkles, Plus, Trash2, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import { api } from '../../api';
import ModelCombobox from '../ModelCombobox';

const DEFAULT_TEST_LINES = [
    "What does it matter? It's just a curse.",
    "You don't understand the weight of this identity.",
    "The cherry blossoms are falling early this year.",
    "Wait! Don't go into the forbidden forest alone!"
];

const TestTranslators = ({ settings, toast }) => {
    const [testLines, setTestLines] = useState(DEFAULT_TEST_LINES);
    const [testModel, setTestModel] = useState('');
    const [isTesting, setIsTesting] = useState(false);
    const [testResult, setTestResult] = useState(null);
    const [testError, setTestError] = useState(null);

    const handleLineChange = (index, value) => {
        const next = [...testLines];
        next[index] = value;
        setTestLines(next);
    };

    const handleAddLine = () => {
        setTestLines([...testLines, '']);
    };

    const handleRemoveLine = (index) => {
        setTestLines(testLines.filter((_, i) => i !== index));
    };

    const handleRunTest = async () => {
        const validLines = testLines.filter(l => l.trim());
        if (validLines.length === 0) {
            if (toast?.warning) toast.warning("Add at least one test subtitle line.");
            return;
        }

        setIsTesting(true);
        setTestResult(null);
        setTestError(null);

        try {
            const projectsRes = await api.getProjects();
            const projects = projectsRes.data || [];
            const projectName = projects[0]?.name || projects[0]?.show_name;
            if (!projectName) {
                setTestError("Please create at least one project first to provide glossary & target language context.");
                return;
            }

            const res = await api.testTranslation(projectName, {
                lines: validLines,
                temperature: settings.temperature ?? 0.3,
                top_k: settings.top_k ?? 64,
                top_p: settings.top_p ?? 0.95,
                model_name: testModel || settings.default_translation_model || undefined
            });
            setTestResult(res.data);
            if (toast?.success) toast.success("Test translation completed!");
        } catch (err) {
            console.error("Test translation failed", err);
            const msg = err.response?.data?.detail || err.message || "Test translation failed";
            setTestError(msg);
            if (toast?.error) toast.error(msg);
        } finally {
            setIsTesting(false);
        }
    };

    return (
        <div className="space-y-6">
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-5">
                <div className="flex items-center justify-between border-b border-gray-100 dark:border-gray-700 pb-4">
                    <div className="flex items-center gap-2">
                        <Sparkles className="text-indigo-600 dark:text-indigo-400" size={20} />
                        <div>
                            <h3 className="text-base font-bold text-gray-900 dark:text-white">Translation Sandbox &amp; Prompt Tester</h3>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                                Send sample subtitle lines directly to Gemini with current hyperparameters and evaluate translation quality.
                            </p>
                        </div>
                    </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <ModelCombobox
                        label="Model to Test"
                        value={testModel || settings.default_translation_model}
                        onChange={setTestModel}
                        placeholder={settings.default_translation_model || "Select model..."}
                    />

                    <div className="flex items-end">
                        <button
                            type="button"
                            onClick={handleRunTest}
                            disabled={isTesting}
                            className="w-full py-2.5 px-5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-sm font-semibold flex items-center justify-center gap-2 transition-all disabled:opacity-50 shadow-md"
                        >
                            {isTesting ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                            <span>{isTesting ? "Executing Sandbox Test..." : "Run Test Translation"}</span>
                        </button>
                    </div>
                </div>

                {/* Test Subtitle Lines */}
                <div className="space-y-3 pt-2">
                    <div className="flex items-center justify-between">
                        <label className="text-xs font-bold text-gray-600 dark:text-gray-400 uppercase tracking-wider">
                            Sample Subtitle Cues ({testLines.length})
                        </label>
                        <button
                            type="button"
                            onClick={handleAddLine}
                            className="text-xs text-indigo-600 dark:text-indigo-400 hover:underline font-semibold flex items-center gap-1"
                        >
                            <Plus size={12} /> Add Line
                        </button>
                    </div>

                    <div className="space-y-2">
                        {testLines.map((line, idx) => (
                            <div key={idx} className="flex items-center gap-2">
                                <span className="w-6 text-center text-xs font-mono text-gray-400">{idx + 1}.</span>
                                <input
                                    type="text"
                                    value={line}
                                    onChange={(e) => handleLineChange(idx, e.target.value)}
                                    placeholder="Enter dialogue line..."
                                    className="flex-1 px-3.5 py-2 text-sm rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white outline-none focus:ring-2 focus:ring-indigo-500"
                                />
                                {testLines.length > 1 && (
                                    <button
                                        type="button"
                                        onClick={() => handleRemoveLine(idx)}
                                        className="p-2 text-gray-400 hover:text-rose-600 rounded-lg transition-colors"
                                    >
                                        <Trash2 size={16} />
                                    </button>
                                )}
                            </div>
                        ))}
                    </div>
                </div>

                {testError && (
                    <div className="p-4 bg-rose-50 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-900 text-rose-700 dark:text-rose-400 text-xs rounded-xl flex items-start gap-2">
                        <AlertCircle size={16} className="mt-0.5 shrink-0" />
                        <div>{testError}</div>
                    </div>
                )}

                {testResult && (
                    <div className="mt-4 p-5 bg-gray-50 dark:bg-gray-750 border border-gray-200 dark:border-gray-700 rounded-2xl space-y-4">
                        <div className="flex items-center justify-between">
                            <h4 className="text-sm font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                <CheckCircle2 className="text-emerald-500" size={16} />
                                <span>Translation Output</span>
                            </h4>
                            {testResult.model && (
                                <span className="font-mono text-[10px] px-2 py-0.5 rounded bg-indigo-100 dark:bg-indigo-950 text-indigo-700 dark:text-indigo-300">
                                    {testResult.model}
                                </span>
                            )}
                        </div>

                        <div className="space-y-2 divide-y divide-gray-200 dark:divide-gray-700">
                            {(testResult.translations || testResult.translated_lines || testResult.results || []).map((item, i) => (
                                <div key={i} className="pt-2 first:pt-0">
                                    <div className="text-xs text-gray-500 dark:text-gray-400 italic">
                                        "{typeof item === 'string' ? testLines[i] : item.source || testLines[i]}"
                                    </div>
                                    <div className="text-sm font-semibold text-indigo-600 dark:text-indigo-400 mt-0.5">
                                        {typeof item === 'string' ? item : item.target || item.translation}
                                    </div>
                                    {item.reasoning && (
                                        <div className="text-[11px] text-gray-400 dark:text-gray-500 mt-0.5">
                                            Reasoning: {item.reasoning}
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
};

export default TestTranslators;
