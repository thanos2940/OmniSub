import React, { useState, useRef, useEffect } from 'react';
import { ChevronDown, Wifi, WifiOff } from 'lucide-react';

const ModelCombobox = ({ value, onChange, label, className = '' }) => {
    const [isOpen, setIsOpen] = useState(false);
    const [inputValue, setInputValue] = useState(value || '');
    const [geminiModels, setGeminiModels] = useState([]);
    const [localModels, setLocalModels] = useState([]);
    const [localOnline, setLocalOnline] = useState(null); // null = unknown
    const [loading, setLoading] = useState(false);
    const containerRef = useRef(null);

    useEffect(() => {
        setInputValue(value || '');
    }, [value]);

    useEffect(() => {
        if (isOpen && geminiModels.length === 0) {
            loadModels();
        }
    }, [isOpen]);

    const loadModels = async () => {
        try {
            setLoading(true);
            const { api } = await import('../api');
            const { data } = await api.fetchAllModels();
            setGeminiModels(data.gemini || []);
            setLocalModels(data.local || []);
            setLocalOnline(data.local_online ?? false);
        } catch {
            // fall back to minimal hardcoded list
            setGeminiModels([
                { value: 'gemini-2.5-flash',      label: 'Gemini 2.5 Flash' },
                { value: 'gemini-flash-latest',    label: 'Gemini Flash (latest)' },
                { value: 'gemini-flash-lite-latest', label: 'Gemini Flash Lite' },
            ]);
        } finally {
            setLoading(false);
        }
    };

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
        if (inputValue.trim() && inputValue !== value) {
            onChange(inputValue.trim());
        }
    };

    const handleKeyDown = (e) => {
        if (e.key === 'Enter' && inputValue.trim()) {
            onChange(inputValue.trim());
            setIsOpen(false);
        } else if (e.key === 'Escape') {
            setIsOpen(false);
        }
    };

    const query = inputValue.toLowerCase();
    const filteredGemini = geminiModels.filter(m =>
        m.value.toLowerCase().includes(query) || m.label.toLowerCase().includes(query)
    );
    const filteredLocal = localModels.filter(m =>
        m.value.toLowerCase().includes(query) || m.label.toLowerCase().includes(query)
    );

    const allKnown = [...geminiModels, ...localModels];
    const isCustom = inputValue && !allKnown.some(m => m.value === inputValue);

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
                <div className="absolute z-50 mt-1 w-full bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-600 shadow-xl max-h-72 overflow-auto">

                    {/* Gemini section */}
                    {filteredGemini.length > 0 && (
                        <>
                            <div className="px-4 py-1.5 text-xs font-semibold text-gray-500 uppercase tracking-wider bg-gray-50 dark:bg-gray-700/50 sticky top-0">
                                ☁ Gemini (Cloud)
                            </div>
                            {filteredGemini.map(model => (
                                <button
                                    key={model.value}
                                    type="button"
                                    onMouseDown={(e) => { e.preventDefault(); handleSelect(model.value); }}
                                    className={`w-full text-left px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors ${model.value === value ? 'bg-indigo-50 dark:bg-indigo-900/30' : ''}`}
                                >
                                    <div className="text-sm font-medium text-gray-900 dark:text-white">{model.label}</div>
                                </button>
                            ))}
                        </>
                    )}

                    {/* Local section */}
                    <div className={`${filteredGemini.length > 0 ? 'border-t border-gray-200 dark:border-gray-600' : ''}`}>
                        <div className="px-4 py-1.5 text-xs font-semibold text-gray-500 uppercase tracking-wider bg-gray-50 dark:bg-gray-700/50 sticky top-0 flex items-center gap-1.5">
                            {localOnline === true
                                ? <Wifi size={11} className="text-green-500" />
                                : <WifiOff size={11} className="text-gray-400" />}
                            Local Models
                            {localOnline === false && <span className="text-gray-400 normal-case font-normal">(server offline)</span>}
                        </div>
                        {filteredLocal.length > 0 ? filteredLocal.map(model => (
                            <button
                                key={model.value}
                                type="button"
                                onMouseDown={(e) => { e.preventDefault(); handleSelect(model.value); }}
                                className={`w-full text-left px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 transition-colors ${model.value === value ? 'bg-indigo-50 dark:bg-indigo-900/30' : ''}`}
                            >
                                <div className="text-sm font-medium text-gray-900 dark:text-white">{model.label}</div>
                                <div className="text-xs text-gray-500 dark:text-gray-400">Local</div>
                            </button>
                        )) : (
                            <div className="px-4 py-2.5 text-xs text-gray-400">
                                {loading ? 'Scanning...' : localOnline === false ? 'Start LM Studio / Ollama to see models' : 'No local models found'}
                            </div>
                        )}
                    </div>

                    {/* Custom / manual entry */}
                    {inputValue.trim() && isCustom && (
                        <div className="border-t border-gray-200 dark:border-gray-600">
                            <button
                                type="button"
                                onMouseDown={(e) => { e.preventDefault(); handleSelect(inputValue.trim()); }}
                                className="w-full text-left px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700"
                            >
                                <div className="text-sm font-medium text-indigo-600 dark:text-indigo-400">Use "{inputValue.trim()}"</div>
                                <div className="text-xs text-gray-500 dark:text-gray-400">Custom model name</div>
                            </button>
                            {!inputValue.startsWith('local/') && (
                                <button
                                    type="button"
                                    onMouseDown={(e) => { e.preventDefault(); handleSelect(`local/${inputValue.trim()}`); }}
                                    className="w-full text-left px-4 py-2.5 hover:bg-gray-50 dark:hover:bg-gray-700 border-t border-gray-100 dark:border-gray-700"
                                >
                                    <div className="text-sm font-medium text-purple-600 dark:text-purple-400">Use as Local: "local/{inputValue.trim()}"</div>
                                </button>
                            )}
                        </div>
                    )}
                </div>
            )}
        </div>
    );
};

export default ModelCombobox;
