import React, { useState, useRef, useEffect } from 'react';
import { ChevronDown } from 'lucide-react';

const MODEL_PRESETS = [
    { value: 'gemini-2.5-flash', label: 'Gemini 2.5 Flash', desc: 'Recommended — fast & capable' },
    { value: 'gemini-2.5-pro', label: 'Gemini 2.5 Pro', desc: 'Highest quality' },
    { value: 'gemini-2.0-flash', label: 'Gemini 2.0 Flash', desc: 'Previous generation' },
    { value: 'gemini-2.0-flash-lite', label: 'Gemini 2.0 Flash Lite', desc: 'Fastest, lower quality' },
];

const ModelCombobox = ({ value, onChange, label, className = '' }) => {
    const [isOpen, setIsOpen] = useState(false);
    const [inputValue, setInputValue] = useState(value || '');
    const [localModels, setLocalModels] = useState([]);
    const [loadingLocal, setLoadingLocal] = useState(false);
    const containerRef = useRef(null);

    useEffect(() => {
        setInputValue(value || '');
    }, [value]);

    useEffect(() => {
        if (isOpen) {
            loadLocalModels();
        }
    }, [isOpen]);

    const loadLocalModels = async () => {
        try {
            setLoadingLocal(true);
            const { api } = await import('../api');
            const response = await api.fetchLocalModels();
            if (response.data && response.data.models) {
                setLocalModels(response.data.models);
            }
        } catch (err) {
            console.warn('Failed to fetch local models:', err);
        } finally {
            setLoadingLocal(false);
        }
    };

    useEffect(() => {
        const handleClickOutside = (e) => {
            if (containerRef.current && !containerRef.current.contains(e.target)) {
                setIsOpen(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
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
        // Commit the typed value on blur
        if (inputValue.trim() && inputValue !== value) {
            onChange(inputValue.trim());
        }
    };

    const handleKeyDown = (e) => {
        if (e.key === 'Enter') {
            if (inputValue.trim()) {
                onChange(inputValue.trim());
                setIsOpen(false);
            }
        } else if (e.key === 'Escape') {
            setIsOpen(false);
        }
    };

    // Filter presets based on input
    const filtered = MODEL_PRESETS.filter(m =>
        m.value.toLowerCase().includes(inputValue.toLowerCase()) ||
        m.label.toLowerCase().includes(inputValue.toLowerCase())
    );

    const isCustom = inputValue && !MODEL_PRESETS.some(m => m.value === inputValue);

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
                    placeholder="Select or type model name..."
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
                <div className="absolute z-50 mt-1 w-full bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-600 shadow-xl max-h-60 overflow-auto">
                    {filtered.map(model => (
                        <button
                            key={model.value}
                            type="button"
                            onMouseDown={(e) => { e.preventDefault(); handleSelect(model.value); }}
                            className={`w-full text-left px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors ${model.value === value ? 'bg-indigo-50 dark:bg-indigo-900/30' : ''}`}
                        >
                            <div className="text-sm font-medium text-gray-900 dark:text-white">{model.label}</div>
                            <div className="text-xs text-gray-500 dark:text-gray-400">{model.desc}</div>
                        </button>
                    ))}
                    {localModels.length > 0 && (
                        <div className="border-t border-gray-200 dark:border-gray-600">
                            <div className="px-4 py-2 text-xs font-semibold text-gray-500 uppercase tracking-wider bg-gray-50 dark:bg-gray-700/50">Local Models</div>
                            {localModels.map(model => (
                                <button
                                    key={model.value}
                                    type="button"
                                    onMouseDown={(e) => { e.preventDefault(); handleSelect(model.value); }}
                                    className={`w-full text-left px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors ${model.value === value ? 'bg-indigo-50 dark:bg-indigo-900/30' : ''}`}
                                >
                                    <div className="text-sm font-medium text-gray-900 dark:text-white">{model.label}</div>
                                    <div className="text-xs text-gray-500 dark:text-gray-400">Local model (via LM Studio)</div>
                                </button>
                            ))}
                        </div>
                    )}
                    {/* Custom and Manually added local models */}
                    {inputValue.trim() && (
                        <div className="border-t border-gray-200 dark:border-gray-600">
                            {/* Option 1: Literal custom value */}
                            {isCustom && !localModels.some(m => m.value === inputValue) && (
                                <button
                                    type="button"
                                    onMouseDown={(e) => { e.preventDefault(); handleSelect(inputValue.trim()); }}
                                    className="w-full text-left px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors"
                                >
                                    <div className="text-sm font-medium text-indigo-600 dark:text-indigo-400">Use "{inputValue.trim()}"</div>
                                    <div className="text-xs text-gray-500 dark:text-gray-400">Custom model name</div>
                                </button>
                            )}

                            {/* Option 2: Suggest as local model if not already local/ */}
                            {!inputValue.startsWith('local/') && !localModels.some(m => m.value === `local/${inputValue}`) && (
                                <button
                                    type="button"
                                    onMouseDown={(e) => { e.preventDefault(); handleSelect(`local/${inputValue.trim()}`); }}
                                    className="w-full text-left px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors border-t border-gray-100 dark:border-gray-700"
                                >
                                    <div className="text-sm font-medium text-purple-600 dark:text-purple-400">Use as Local Model</div>
                                    <div className="text-xs text-gray-500 dark:text-gray-400">Will use "local/{inputValue.trim()}"</div>
                                </button>
                            )}
                        </div>
                    )}

                    {filtered.length === 0 && localModels.length === 0 && !inputValue.trim() && (
                        <div className="px-4 py-3 text-sm text-gray-500 dark:text-gray-400">No matching models</div>
                    )}
                    {loadingLocal && (
                        <div className="px-4 py-2 text-xs text-indigo-500 animate-pulse">Scanning for local models...</div>
                    )}
                </div>
            )}
        </div>
    );
};

export default ModelCombobox;
export { MODEL_PRESETS };
