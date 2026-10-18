import React, { useState, useRef, useEffect, useCallback } from 'react';
import { ChevronDown, Sparkles } from 'lucide-react';
import { api } from '../api';

const DEFAULT_GEMINI_MODELS = [
    { value: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash' },
    { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro' },
    { value: 'gemini-flash-latest', label: 'Gemini Flash (latest)' },
    { value: 'gemini-flash-lite-latest', label: 'Gemini Flash Lite' },
    { value: 'gemma-4-31b-it', label: 'Gemma 4 31B IT' },
    { value: 'gemma-4-26b-a4b-it', label: 'Gemma 4 26B IT' },
];

const ModelCombobox = ({ value, onChange, label, placeholder = "Select or type model name...", className = '' }) => {
    const [isOpen, setIsOpen] = useState(false);
    const [inputValue, setInputValue] = useState(value || '');
    const [geminiModels, setGeminiModels] = useState([]);
    const [, setLoading] = useState(false);
    const containerRef = useRef(null);

    useEffect(() => {
        setInputValue(value || '');
    }, [value]);

    const dedupeModels = (models) => {
        const seen = new Set();
        return (models || []).filter(m => {
            if (!m || !m.value || seen.has(m.value)) return false;
            seen.add(m.value);
            return true;
        });
    };

    const loadModels = useCallback(async () => {
        try {
            setLoading(true);
            const { data } = await api.fetchAllModels();
            const models = dedupeModels(data.gemini || data.models || data || []);
            if (models.length > 0) {
                setGeminiModels(models);
            } else {
                setGeminiModels(DEFAULT_GEMINI_MODELS);
            }
        } catch {
            setGeminiModels(DEFAULT_GEMINI_MODELS);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        if (isOpen && geminiModels.length === 0) {
            loadModels();
        }
    }, [isOpen, geminiModels.length, loadModels]);

    useEffect(() => {
        const handler = (e) => {
            if (containerRef.current && !containerRef.current.contains(e.target)) {
                setIsOpen(false);
            }
        };
        document.addEventListener('mousedown', handler);
        return () => document.removeEventListener('mousedown', handler);
    }, []);

    const handleSelect = (modelValue) => {
        setInputValue(modelValue);
        onChange(modelValue);
        setIsOpen(false);
    };

    const handleInputChange = (e) => {
        setInputValue(e.target.value);
        setIsOpen(true);
    };

    const handleInputBlur = () => {
        const trimmed = inputValue.trim();
        if (trimmed !== (value || '')) {
            onChange(trimmed);
        }
    };

    const handleKeyDown = (e) => {
        if (e.key === 'Enter') {
            const trimmed = inputValue.trim();
            if (trimmed !== (value || '')) {
                onChange(trimmed);
            }
            setIsOpen(false);
        } else if (e.key === 'Escape') {
            setIsOpen(false);
        }
    };

    const query = (inputValue || '').toLowerCase();
    const modelsList = geminiModels.length > 0 ? geminiModels : DEFAULT_GEMINI_MODELS;
    const filteredGemini = modelsList.filter(m =>
        m.value.toLowerCase().includes(query) || (m.label && m.label.toLowerCase().includes(query))
    );

    const isCustom = inputValue && !modelsList.some(m => m.value === inputValue);

    return (
        <div ref={containerRef} className={`relative ${className}`}>
            {label && (
                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">{label}</label>
            )}
            <div className="relative">
                <input
                    type="text"
                    value={inputValue}
                    onChange={handleInputChange}
                    onFocus={() => setIsOpen(true)}
                    onBlur={handleInputBlur}
                    onKeyDown={handleKeyDown}
                    placeholder={placeholder}
                    className="w-full px-4 py-2.5 pr-10 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white focus:ring-2 focus:ring-indigo-500 outline-none transition-all text-sm"
                />
                <button
                    type="button"
                    onClick={() => setIsOpen(!isOpen)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
                >
                    <ChevronDown size={16} className={`transition-transform ${isOpen ? 'rotate-180' : ''}`} />
                </button>
            </div>

            {isOpen && (
                <div className="absolute z-50 mt-1 w-full bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-600 shadow-xl max-h-72 overflow-auto">
                    <div className="px-4 py-1.5 text-xs font-semibold text-gray-500 uppercase tracking-wider bg-gray-50 dark:bg-gray-700/50 sticky top-0 flex items-center gap-1.5">
                        <Sparkles size={12} className="text-indigo-500" />
                        Google Gemini Models
                    </div>

                    {filteredGemini.length > 0 ? (
                        filteredGemini.map(model => (
                            <button
                                key={model.value}
                                type="button"
                                onMouseDown={(e) => { e.preventDefault(); handleSelect(model.value); }}
                                className={`w-full text-left px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors ${model.value === value ? 'bg-indigo-50 dark:bg-indigo-900/30 font-semibold' : ''}`}
                            >
                                <div className="text-sm font-medium text-gray-900 dark:text-white">{model.label || model.value}</div>
                                {model.label && model.label !== model.value && (
                                    <div className="text-xs text-gray-500 dark:text-gray-400 font-mono">{model.value}</div>
                                )}
                            </button>
                        ))
                    ) : (
                        <div className="px-4 py-2.5 text-xs text-gray-400">
                            No matching models
                        </div>
                    )}

                    {inputValue.trim() && isCustom && (
                        <div className="border-t border-gray-200 dark:border-gray-600">
                            <button
                                type="button"
                                onMouseDown={(e) => { e.preventDefault(); handleSelect(inputValue.trim()); }}
                                className="w-full text-left px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700"
                            >
                                <div className="text-sm font-medium text-indigo-600 dark:text-indigo-400">Use custom model "{inputValue.trim()}"</div>
                                <div className="text-xs text-gray-500 dark:text-gray-400">Custom Gemini Model Identifier</div>
                            </button>
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default ModelCombobox;
