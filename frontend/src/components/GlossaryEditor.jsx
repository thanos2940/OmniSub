import React, { useState, useRef } from 'react';
import { motion } from 'framer-motion';
import { Save, Edit2, Check, X, Globe, User, Book, Shield, Sparkles, Download, Upload, Trash2, Plus } from 'lucide-react';

const GlossaryEditor = ({ glossary, onSave, onCancel, isSaving, readOnly = false, onChange, hideSaveButton = false }) => {
    const [terms, setTerms] = useState(glossary.terms || []);

    const [filter, setFilter] = useState('');
    const [activeTab, setActiveTab] = useState('All');
    const [customCategories, setCustomCategories] = useState([]);
    const fileInputRef = useRef(null);
    const isInternalUpdateRef = useRef(false);
    const debounceTimerRef = useRef(null);

    // Derive available types from defaults + existing terms + custom added
    const defaultTypes = ['Person', 'Location', 'Item', 'Concept', 'Other'];
    const termTypes = [...new Set(terms.map(t => t.type))].map(t => t.charAt(0).toUpperCase() + t.slice(1));
    const allTypes = [...new Set([...defaultTypes, ...termTypes, ...customCategories])];

    const handleAddCategory = () => {
        const name = prompt("Enter new category name:");
        if (name) {
            const formatted = name.charAt(0).toUpperCase() + name.slice(1);
            if (!allTypes.includes(formatted)) {
                setCustomCategories([...customCategories, formatted]);
                setActiveTab(formatted);
            }
        }
    };

    // Debounced sync with parent to prevent rapid re-renders during fast typing
    React.useEffect(() => {
        if (onChange && isInternalUpdateRef.current) {
            // Clear any pending debounce timer
            if (debounceTimerRef.current) {
                clearTimeout(debounceTimerRef.current);
            }

            // Set a new debounce timer - only notify parent after 300ms of no changes
            debounceTimerRef.current = setTimeout(() => {
                onChange({ ...glossary, terms });
                isInternalUpdateRef.current = false;
            }, 300);
        }

        // Cleanup function to clear timer on unmount
        return () => {
            if (debounceTimerRef.current) {
                clearTimeout(debounceTimerRef.current);
            }
        };
    }, [terms]);

    // Track the last glossary terms prop we received to detect external changes
    const prevGlossaryTermsRef = useRef(glossary.terms);

    // Sync with prop changes (e.g. when switching projects or reloading)
    React.useEffect(() => {
        // Simple length check or reference check might be enough, but let's be safe.
        // If the prop reference changes AND it's different from what we have, update.
        if (glossary.terms !== prevGlossaryTermsRef.current) {
            // Only update if we are NOT currently editing (internal update)
            // OR if the terms are drastically different (like switching projects)
            if (!isInternalUpdateRef.current) {
                setTerms(glossary.terms || []);
            }
            prevGlossaryTermsRef.current = glossary.terms;
        }
    }, [glossary.terms]);

    const handleTermChange = (index, field, value) => {
        if (readOnly) return;
        isInternalUpdateRef.current = true;
        const newTerms = [...terms];
        newTerms[index] = { ...newTerms[index], [field]: value };
        setTerms(newTerms);
    };

    const handleRemoveTerm = (index) => {
        if (readOnly) return;
        if (window.confirm("Are you sure you want to remove this term?")) {
            isInternalUpdateRef.current = true;
            const newTerms = terms.filter((_, i) => i !== index);
            setTerms(newTerms);
        }
    };

    const handleAddTerm = () => {
        const newTerm = {
            term: '',
            translation: '',
            type: activeTab !== 'All' ? activeTab.toLowerCase() : 'other',
            gender: 'neuter',
            description: '',
            keep_original: false,
            case_sensitive: false
        };
        // Add to the beginning of the list so the user sees it immediately
        isInternalUpdateRef.current = true;
        setTerms([newTerm, ...terms]);
    };

    const handleSave = () => {
        onSave({ ...glossary, terms });
    };

    const handleExportGlossary = () => {
        const dataStr = JSON.stringify({ ...glossary, terms }, null, 2);
        const blob = new Blob([dataStr], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = "glossary.json";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const handleImportClick = () => {
        fileInputRef.current.click();
    };

    const handleImportFile = (e) => {
        const file = e.target.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = (event) => {
            try {
                const importedData = JSON.parse(event.target.result);
                if (importedData.terms && Array.isArray(importedData.terms)) {
                    isInternalUpdateRef.current = true;
                    setTerms(importedData.terms);
                    alert("Glossary imported successfully!");
                } else {
                    alert("Invalid glossary format.");
                }
            } catch (err) {
                console.error(err);
                alert("Error parsing JSON file.");
            }
        };
        reader.readAsText(file);
        e.target.value = null; // Reset input
    };

    const toggleKeepAllOriginal = () => {
        const allKeep = terms.every(t => t.type !== 'person' || t.keep_original);
        isInternalUpdateRef.current = true;
        const newTerms = terms.map(t => {
            if (t.type === 'person') {
                return { ...t, keep_original: !allKeep };
            }
            return t;
        });
        setTerms(newTerms);
    };

    const filteredTerms = terms.filter(term => {
        const matchesFilter = term.term.toLowerCase().includes(filter.toLowerCase()) ||
            term.description?.toLowerCase().includes(filter.toLowerCase());

        const matchesTab = activeTab === 'All' || term.type.toLowerCase() === activeTab.toLowerCase();

        return matchesFilter && matchesTab;
    });

    return (
        <div className="h-full flex flex-col bg-white/50 backdrop-blur-xl rounded-3xl shadow-xl border border-white/60 overflow-hidden">
            {/* Header */}
            <div className="p-6 border-b border-gray-200/50 bg-white/40 flex justify-between items-center">
                <div>
                    <h2 className="text-2xl font-bold text-gray-800 flex items-center gap-2">
                        <Book className="w-6 h-6 text-indigo-600" />
                        Glossary Editor
                    </h2>
                    <p className="text-sm text-gray-500">Review and edit detected terms before translation.</p>
                </div>
                <div className="flex gap-3">
                    <input
                        type="file"
                        ref={fileInputRef}
                        onChange={handleImportFile}
                        accept=".json"
                        className="hidden"
                    />
                    <button
                        onClick={handleImportClick}
                        className="px-3 py-2 rounded-xl text-indigo-600 bg-indigo-50 hover:bg-indigo-100 transition-all flex items-center gap-2"
                        title="Import Glossary"
                    >
                        <Upload className="w-4 h-4" /> Import
                    </button>
                    <button
                        onClick={handleExportGlossary}
                        className="px-3 py-2 rounded-xl text-indigo-600 bg-indigo-50 hover:bg-indigo-100 transition-all flex items-center gap-2"
                        title="Export Glossary"
                    >
                        <Download className="w-4 h-4" /> Export
                    </button>

                    {!readOnly && !hideSaveButton && (
                        <>
                            <button
                                onClick={onCancel}
                                className="px-4 py-2 rounded-xl text-gray-600 hover:bg-gray-100 transition-all"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleSave}
                                disabled={isSaving}
                                className="px-6 py-2 rounded-xl bg-indigo-600 text-white font-semibold shadow-lg hover:bg-indigo-700 transition-all flex items-center gap-2"
                            >
                                {isSaving ? 'Saving...' : <><Save className="w-4 h-4" /> Save & Continue</>}
                            </button>
                        </>
                    )}
                </div>
            </div>

            <div className="flex-1 overflow-y-auto custom-scrollbar">


                {/* Tabs */}
                <div className="px-6 pt-4 bg-gray-50/50 border-b border-gray-200/50 flex items-center gap-2 overflow-x-auto custom-scrollbar">
                    {['All', ...allTypes].map(type => (
                        <button
                            key={type}
                            onClick={() => setActiveTab(type)}
                            className={`px-4 py-2 rounded-t-lg text-sm font-medium transition-colors border-b-2 ${activeTab === type
                                ? 'border-indigo-600 text-indigo-600 bg-indigo-50/50'
                                : 'border-transparent text-gray-500 hover:text-gray-700 hover:bg-gray-100'
                                }`}
                        >
                            {type}
                        </button>
                    ))}
                    {!readOnly && (
                        <button
                            onClick={handleAddCategory}
                            className="px-3 py-1 ml-2 rounded-full bg-gray-100 hover:bg-gray-200 text-gray-600 transition-colors"
                            title="Add Category"
                        >
                            <Plus className="w-4 h-4" />
                        </button>
                    )}
                </div>

                {/* Filter & Actions */}
                <div className="p-4 bg-gray-50/50 border-b border-gray-200/50 flex gap-4 items-center sticky top-0 z-10 backdrop-blur-sm">
                    <input
                        type="text"
                        placeholder="Filter terms..."
                        value={filter}
                        onChange={(e) => setFilter(e.target.value)}
                        className="flex-1 px-4 py-2 rounded-xl border border-gray-200 bg-white focus:ring-2 focus:ring-indigo-500 outline-none"
                    />
                    {!readOnly && (
                        <>
                            <button
                                onClick={handleAddTerm}
                                className="px-4 py-2 rounded-xl bg-indigo-600 text-white hover:bg-indigo-700 text-sm font-medium flex items-center gap-2 shadow-sm"
                            >
                                <Plus className="w-4 h-4" />
                                Add Term
                            </button>
                            <button
                                onClick={toggleKeepAllOriginal}
                                className="px-4 py-2 rounded-xl bg-white border border-gray-200 text-gray-700 hover:bg-gray-50 text-sm font-medium flex items-center gap-2"
                            >
                                <Shield className="w-4 h-4" />
                                Keep all character names as original
                            </button>
                        </>
                    )}
                </div>

                {/* Terms List */}
                <div className="p-4 space-y-4">
                    {filteredTerms.map((term, index) => (
                        <motion.div
                            key={index}
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            className="bg-white rounded-xl p-4 shadow-sm border border-gray-100 hover:shadow-md transition-all"
                        >
                            <div className="grid grid-cols-1 md:grid-cols-12 gap-4 items-start">
                                {/* Term Name */}
                                <div className="md:col-span-3">
                                    <label className="text-xs font-semibold text-gray-500 uppercase mb-1 block">Term</label>
                                    <input
                                        type="text"
                                        value={term.term}
                                        onChange={(e) => handleTermChange(index, 'term', e.target.value)}
                                        disabled={readOnly}
                                        className="w-full px-3 py-2 rounded-lg border border-gray-200 focus:border-indigo-500 outline-none text-gray-800 font-medium disabled:bg-gray-50"
                                    />
                                </div>

                                {/* Translation */}
                                <div className="md:col-span-3">
                                    <label className="text-xs font-semibold text-gray-500 uppercase mb-1 block">Translation</label>
                                    <input
                                        type="text"
                                        value={term.translation || ''}
                                        onChange={(e) => handleTermChange(index, 'translation', e.target.value)}
                                        placeholder="Auto-translate if empty"
                                        disabled={readOnly}
                                        className="w-full px-3 py-2 rounded-lg border border-indigo-100 bg-indigo-50/30 focus:border-indigo-500 outline-none text-gray-800 disabled:opacity-70"
                                    />
                                </div>

                                {/* Type */}
                                <div className="md:col-span-2">
                                    <label className="text-xs font-semibold text-gray-500 uppercase mb-1 block">Type</label>
                                    <select
                                        value={term.type}
                                        onChange={(e) => handleTermChange(index, 'type', e.target.value)}
                                        disabled={readOnly}
                                        className="w-full px-3 py-2 rounded-lg border border-gray-200 focus:border-indigo-500 outline-none text-sm disabled:bg-gray-50 capitalize"
                                    >
                                        {allTypes.map(t => (
                                            <option key={t} value={t.toLowerCase()}>{t}</option>
                                        ))}
                                    </select>
                                </div>

                                {/* Gender */}
                                <div className="md:col-span-2">
                                    <label className="text-xs font-semibold text-gray-500 uppercase mb-1 block flex items-center gap-1">
                                        Gender <User className="w-3 h-3" />
                                    </label>
                                    <select
                                        value={term.gender || 'neuter'}
                                        onChange={(e) => handleTermChange(index, 'gender', e.target.value)}
                                        disabled={readOnly}
                                        className="w-full px-3 py-2 rounded-lg border border-gray-200 focus:border-indigo-500 outline-none text-sm disabled:bg-gray-50"
                                    >
                                        <option value="masculine">Masculine</option>
                                        <option value="feminine">Feminine</option>
                                        <option value="neuter">Neuter</option>
                                        <option value="n/a">N/A</option>
                                    </select>
                                </div>

                                {/* Options: Keep Original & Case Sensitive & Remove */}
                                <div className="md:col-span-2 flex flex-col justify-center gap-2 h-full pt-5">
                                    <label className="flex items-center gap-2 cursor-pointer select-none" title="Do not translate this term">
                                        <input
                                            type="checkbox"
                                            checked={term.keep_original || false}
                                            onChange={(e) => handleTermChange(index, 'keep_original', e.target.checked)}
                                            disabled={readOnly}
                                            className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300 disabled:opacity-50"
                                        />
                                        <span className="text-xs text-gray-700 font-medium flex items-center gap-1">
                                            <Shield className="w-3 h-3" /> Keep Original
                                        </span>
                                    </label>
                                    <label className="flex items-center gap-2 cursor-pointer select-none" title="Enforce exact capitalization">
                                        <input
                                            type="checkbox"
                                            checked={term.case_sensitive || false}
                                            onChange={(e) => handleTermChange(index, 'case_sensitive', e.target.checked)}
                                            disabled={readOnly}
                                            className="w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300 disabled:opacity-50"
                                        />
                                        <span className="text-xs text-gray-700 font-medium flex items-center gap-1">
                                            <span className="font-serif italic">Aa</span> Case Sensitive
                                        </span>
                                    </label>
                                    {!readOnly && (
                                        <button
                                            onClick={() => handleRemoveTerm(index)}
                                            className="flex items-center gap-2 text-xs text-red-600 hover:text-red-800 font-medium mt-1"
                                            title="Remove term"
                                        >
                                            <Trash2 className="w-3 h-3" /> Remove Term
                                        </button>
                                    )}
                                </div>

                                {/* Description */}
                                <div className="md:col-span-12">
                                    <label className="text-xs font-semibold text-gray-500 uppercase mb-1 block">Context / Description</label>
                                    <textarea
                                        value={term.description || ''}
                                        onChange={(e) => handleTermChange(index, 'description', e.target.value)}
                                        rows={1}
                                        disabled={readOnly}
                                        className="w-full px-3 py-2 text-gray-700 rounded-lg border border-gray-200 focus:border-indigo-500 outline-none text-sm resize-none focus:h-20 transition-all disabled:bg-gray-50"
                                    />
                                </div>
                            </div>
                        </motion.div>
                    ))}

                    {filteredTerms.length === 0 && (
                        <div className="text-center py-12 text-gray-400">
                            No terms found matching your filter.
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default GlossaryEditor;
