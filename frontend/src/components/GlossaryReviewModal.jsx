import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Check, Plus, AlertCircle, Trash2, ChevronDown, ChevronUp, Merge } from 'lucide-react';

const TYPE_COLORS = {
    person: "text-blue-700 bg-blue-100 dark:bg-blue-900/30 dark:text-blue-300",
    location: "text-emerald-700 bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-300",
    organization: "text-orange-700 bg-orange-100 dark:bg-orange-900/30 dark:text-orange-300",
    event: "text-red-700 bg-red-100 dark:bg-red-900/30 dark:text-red-300",
    object: "text-yellow-700 bg-yellow-100 dark:bg-yellow-900/30 dark:text-yellow-300",
    technique: "text-fuchsia-700 bg-fuchsia-100 dark:bg-fuchsia-900/30 dark:text-fuchsia-300",
    other: "text-gray-700 bg-gray-100 dark:bg-gray-700 dark:text-gray-300"
};

const stem = (word) => {
    if (!word || word.length <= 3) return word;
    if (word.endsWith('ies')) return word.slice(0, -3) + 'y';
    if (word.endsWith('es')) return word.slice(0, -2);
    if (word.endsWith('s')) return word.slice(0, -1);
    return word;
};

const levenshteinDistance = (a, b) => {
    if (a.length === 0) return b.length;
    if (b.length === 0) return a.length;

    const matrix = [];
    for (let i = 0; i <= b.length; i++) {
        matrix[i] = [i];
    }
    for (let j = 0; j <= a.length; j++) {
        matrix[0][j] = j;
    }

    for (let i = 1; i <= b.length; i++) {
        for (let j = 1; j <= a.length; j++) {
            if (b.charAt(i - 1) === a.charAt(j - 1)) {
                matrix[i][j] = matrix[i - 1][j - 1];
            } else {
                matrix[i][j] = Math.min(
                    matrix[i - 1][j - 1] + 1, // substitution
                    Math.min(
                        matrix[i][j - 1] + 1,     // insertion
                        matrix[i - 1][j] + 1      // deletion
                    )
                );
            }
        }
    }
    return matrix[b.length][a.length];
};

const getSimilarityRatio = (a, b) => {
    const aLower = stem(a.toLowerCase());
    const bLower = stem(b.toLowerCase());
    if (aLower === bLower) return 1.0;

    const distance = levenshteinDistance(aLower, bLower);
    const maxLength = Math.max(a.length, b.length);
    if (maxLength === 0) return 1.0;
    return 1 - (distance / maxLength);
};

const findSimilarTerms = (newTermObj, existingTerms) => {
    if (!newTermObj || !existingTerms || existingTerms.length === 0) return [];

    const lowerNew = (newTermObj.term || '').trim().toLowerCase();
    const lowerNewTrans = (newTermObj.translation || '').trim().toLowerCase();

    return existingTerms.filter(existing => {
        const lowerExisting = (existing.term || '').trim().toLowerCase();
        const lowerExistingTrans = (existing.translation || '').trim().toLowerCase();

        // 1. Exact match on term
        if (lowerNew && lowerExisting && lowerNew === lowerExisting) return true;

        // 2. Exact match on translation (prevents mapping two different english words to same translation)
        if (lowerNewTrans && lowerExistingTrans && lowerNewTrans === lowerExistingTrans) return true;

        // 3. Levenshtein ratio >= 0.7 for catching typos
        if (lowerNew && lowerExisting && getSimilarityRatio(lowerNew, lowerExisting) >= 0.7) return true;

        // 4. Word boundary substring inclusion (either way)
        const escapeRegExp = (string) => string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

        const isWordIncluded = (fullString, subString) => {
            if (!fullString || !subString) return false;
            const pattern = new RegExp('(^|[^\\p{L}\\p{N}])' + escapeRegExp(subString) + '([^\\p{L}\\p{N}]|$)', 'iu');
            return pattern.test(fullString);
        };

        if (lowerNew && lowerExisting && (isWordIncluded(lowerNew, lowerExisting) || isWordIncluded(lowerExisting, lowerNew))) {
            return true;
        }

        // 5. Check translation similarity (tighter threshold of 0.8 to avoid false positives on short words)
        if (lowerNewTrans && lowerExistingTrans && getSimilarityRatio(lowerNewTrans, lowerExistingTrans) >= 0.8) {
            return true;
        }

        return false;
    });
};

const GlossaryReviewModal = ({ isOpen, onClose, onConfirm, newTerms, existingTerms, onDelete }) => {
    const [editableTerms, setEditableTerms] = useState([]);
    const [selectedTerms, setSelectedTerms] = useState(new Set());
    const [similarities, setSimilarities] = useState({});
    const [mergeTargets, setMergeTargets] = useState({}); // { idx: existingTermName }
    const [expandedWarnings, setExpandedWarnings] = useState({});

    useEffect(() => {
        if (isOpen && newTerms) {
            const initialTerms = JSON.parse(JSON.stringify(newTerms));
            setEditableTerms(initialTerms);
            
            // Automatically select terms that don't have exact name matches,
            // but keep exact matches unselected by default so users don't accidentally duplicate
            const existingNames = new Set(existingTerms.map(t => (t.term || '').toLowerCase()));
            const initialSelection = new Set();
            initialTerms.forEach((term, idx) => {
                if (!existingNames.has((term.term || '').toLowerCase())) {
                    initialSelection.add(idx);
                }
            });
            setSelectedTerms(initialSelection);
            
            setMergeTargets({});
            setExpandedWarnings({});
        }
    }, [isOpen, newTerms]);

    useEffect(() => {
        if (isOpen && newTerms) {
            const sims = {};
            newTerms.forEach((term, idx) => {
                sims[idx] = findSimilarTerms(term, existingTerms);
            });
            setSimilarities(sims);
        }
    }, [isOpen, newTerms, existingTerms]);

    const handleTermChange = (index, field, value) => {
        const updated = [...editableTerms];
        updated[index] = { ...updated[index], [field]: value };
        setEditableTerms(updated);
    };

    const handleTermBlur = (idx) => {
        setSimilarities(prev => ({
            ...prev,
            [idx]: findSimilarTerms(editableTerms[idx], existingTerms)
        }));
    };

    const toggleTerm = (idx) => {
        const newSelection = new Set(selectedTerms);
        if (newSelection.has(idx)) {
            newSelection.delete(idx);
        } else {
            newSelection.add(idx);
        }
        setSelectedTerms(newSelection);
    };

    const setMergeTarget = (idx, existingTermName) => {
        setMergeTargets(prev => ({
            ...prev,
            [idx]: existingTermName
        }));
        // Auto-select if merged
        const newSelection = new Set(selectedTerms);
        newSelection.add(idx);
        setSelectedTerms(newSelection);
    };

    const toggleExpandedWarning = (idx, e) => {
        e.stopPropagation();
        setExpandedWarnings(prev => ({
            ...prev,
            [idx]: !prev[idx]
        }));
    };

    const handleBulkChange = (field, value) => {
        const updated = [...editableTerms];
        selectedTerms.forEach(idx => {
            updated[idx] = { ...updated[idx], [field]: value };
        });
        setEditableTerms(updated);
    };

    const handleConfirm = () => {
        const addedTerms = [];
        const updatedTerms = [];

        editableTerms.forEach((term, idx) => {
            if (selectedTerms.has(idx)) {
                const targetName = mergeTargets[idx];
                if (targetName) {
                    updatedTerms.push({ ...term, original_term_name: targetName });
                } else {
                    addedTerms.push(term);
                }
            }
        });

        onConfirm(addedTerms, updatedTerms);
    };

    if (!isOpen) return null;

    return (
        <AnimatePresence>
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50 backdrop-blur-sm">
                <motion.div
                    initial={{ opacity: 0, scale: 0.95 }}
                    animate={{ opacity: 1, scale: 1 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl max-w-4xl w-full max-h-[80vh] flex flex-col"
                >
                    {/* Header */}
                    <div className="px-6 py-4 border-b border-gray-200 dark:border-gray-700 flex items-center justify-between">
                        <div>
                            <h2 className="text-xl font-bold text-gray-900 dark:text-white">
                                Review New Glossary Terms
                            </h2>
                            <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                                {editableTerms.length} new {editableTerms.length === 1 ? 'term' : 'terms'} found • Select which ones to add
                            </p>
                        </div>
                        <button
                            onClick={onClose}
                            className="p-2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 rounded-lg hover:bg-gray-100 dark:hover:bg-gray-700 transition-colors"
                        >
                            <X size={20} />
                        </button>
                    </div>

                    {/* Actions & Bulk */}
                    <div className="px-6 py-3 border-b border-gray-200 dark:border-gray-700 flex flex-wrap items-center justify-between gap-4">
                        <div className="flex items-center gap-4">
                            <button
                                onClick={() => setSelectedTerms(new Set(editableTerms.map((_, idx) => idx)))}
                                className="text-sm font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400"
                            >
                                Select All
                            </button>
                            <span className="text-gray-300">|</span>
                            <button
                                onClick={() => setSelectedTerms(new Set())}
                                className="text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400"
                            >
                                Select None
                            </button>
                            <span className="text-xs text-gray-400 ml-2">
                                {selectedTerms.size} selected
                            </span>
                        </div>
                        
                        {selectedTerms.size > 0 && (
                            <div className="flex items-center gap-2 text-sm">
                                <span className="text-gray-500 dark:text-gray-400 text-xs font-medium uppercase tracking-wider">Bulk Edit:</span>
                                <select
                                    onChange={(e) => handleBulkChange('type', e.target.value)}
                                    className="px-2 py-1 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 border-none outline-none cursor-pointer"
                                    defaultValue=""
                                >
                                    <option value="" disabled>Type...</option>
                                    <option value="person">Person</option>
                                    <option value="location">Location</option>
                                    <option value="organization">Organization</option>
                                    <option value="event">Event</option>
                                    <option value="object">Object</option>
                                    <option value="technique">Technique</option>
                                    <option value="other">Other</option>
                                </select>
                                <select
                                    onChange={(e) => handleBulkChange('gender', e.target.value)}
                                    className="px-2 py-1 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 border-none outline-none cursor-pointer"
                                    defaultValue=""
                                >
                                    <option value="" disabled>Gender...</option>
                                    <option value="masculine">Masculine</option>
                                    <option value="feminine">Feminine</option>
                                    <option value="neuter">Neuter</option>
                                    <option value="n/a">N/A</option>
                                </select>
                            </div>
                        )}
                    </div>

                    {/* Terms List */}
                    <div className="flex-1 overflow-y-auto p-6 space-y-3">
                        {editableTerms.length === 0 ? (
                            <div className="text-center py-12 text-gray-500 dark:text-gray-400">
                                <p className="text-lg font-medium mb-2">No new terms found</p>
                                <p className="text-sm">The AI didn't discover any new terminology in the selected episodes.</p>
                            </div>
                        ) : (
                            editableTerms.map((term, idx) => {
                                const isMerged = !!mergeTargets[idx];
                                const typeColorClass = TYPE_COLORS[term.type] || TYPE_COLORS.other;
                                
                                return (
                                    <div
                                        key={idx}
                                        onClick={() => toggleTerm(idx)}
                                        className={`p-4 rounded-lg border-2 cursor-pointer transition-all ${selectedTerms.has(idx)
                                            ? (isMerged ? 'border-emerald-500 bg-emerald-50 dark:bg-emerald-900/10' : 'border-blue-500 bg-blue-50 dark:bg-blue-900/20')
                                            : 'border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-700'
                                            }`}
                                    >
                                        <div className="flex items-start gap-3">
                                            <div className={`mt-1 w-5 h-5 rounded border-2 flex items-center justify-center transition-colors shrink-0 ${selectedTerms.has(idx)
                                                ? (isMerged ? 'border-emerald-500 bg-emerald-500' : 'border-blue-500 bg-blue-500')
                                                : 'border-gray-300 dark:border-gray-600'
                                                }`}>
                                                {selectedTerms.has(idx) && (
                                                    <Check size={14} className="text-white" />
                                                )}
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                {isMerged && (
                                                    <div className="text-xs font-medium text-emerald-600 dark:text-emerald-400 mb-1 flex items-center gap-1">
                                                        <Merge size={12} />
                                                        Merging into: {mergeTargets[idx]}
                                                    </div>
                                                )}
                                                <div className="flex flex-wrap items-center gap-2 mb-2">
                                                    <input
                                                        type="text"
                                                        value={term.term}
                                                        onChange={(e) => handleTermChange(idx, 'term', e.target.value)}
                                                        onBlur={() => handleTermBlur(idx)}
                                                        onClick={(e) => e.stopPropagation()}
                                                        className="font-bold text-gray-900 dark:text-white bg-transparent border-b border-gray-300 dark:border-gray-600 focus:border-blue-500 outline-none px-1 py-0.5 min-w-[120px]"
                                                        placeholder="Term"
                                                    />
                                                    <select
                                                        value={term.type}
                                                        onChange={(e) => handleTermChange(idx, 'type', e.target.value)}
                                                        onClick={(e) => e.stopPropagation()}
                                                        className={`text-sm px-2 py-0.5 rounded-full border-none outline-none cursor-pointer ${typeColorClass}`}
                                                    >
                                                        <option value="person">Person</option>
                                                        <option value="location">Location</option>
                                                        <option value="organization">Organization</option>
                                                        <option value="event">Event</option>
                                                        <option value="object">Object</option>
                                                        <option value="technique">Technique</option>
                                                        <option value="other">Other</option>
                                                        {!['person', 'location', 'organization', 'event', 'object', 'technique', 'other'].includes(term.type) && (
                                                            <option value={term.type}>{term.type}</option>
                                                        )}
                                                    </select>
                                                    <select
                                                        value={term.gender || 'n/a'}
                                                        onChange={(e) => handleTermChange(idx, 'gender', e.target.value)}
                                                        onClick={(e) => e.stopPropagation()}
                                                        className="text-sm px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 border-none outline-none cursor-pointer"
                                                    >
                                                        <option value="masculine">Masculine</option>
                                                        <option value="feminine">Feminine</option>
                                                        <option value="neuter">Neuter</option>
                                                        <option value="n/a">N/A</option>
                                                    </select>
                                                </div>
                                                <textarea
                                                    value={term.description}
                                                    onChange={(e) => handleTermChange(idx, 'description', e.target.value)}
                                                    onClick={(e) => e.stopPropagation()}
                                                    className="w-full text-sm text-gray-600 dark:text-gray-400 mb-2 bg-transparent border border-gray-200 dark:border-gray-700 rounded p-2 focus:border-blue-500 outline-none resize-y"
                                                    rows={2}
                                                    placeholder="Description"
                                                />
                                                {term.context_quote && (
                                                    <div className="text-xs text-gray-500 dark:text-gray-400 italic mb-3 border-l-2 border-gray-300 dark:border-gray-600 pl-2 py-1 bg-gray-50 dark:bg-gray-800/50 rounded-r">
                                                        "{term.context_quote}"
                                                    </div>
                                                )}
                                                <div className="flex items-center gap-2 text-sm">
                                                    <span className="text-gray-500 dark:text-gray-400 shrink-0">Translation:</span>
                                                    <input
                                                        type="text"
                                                        value={term.translation}
                                                        onChange={(e) => handleTermChange(idx, 'translation', e.target.value)}
                                                        onBlur={() => handleTermBlur(idx)}
                                                        onClick={(e) => e.stopPropagation()}
                                                        className="font-medium text-indigo-600 dark:text-indigo-400 bg-transparent border-b border-gray-300 dark:border-gray-600 focus:border-indigo-500 outline-none px-1 py-0.5 flex-1"
                                                        placeholder="Translation"
                                                    />
                                                </div>
                                            </div>
                                        </div>
                                        {(() => {
                                            const similar = similarities[idx] || [];
                                            if (similar.length === 0) return null;
                                            
                                            const isExpanded = expandedWarnings[idx];
                                            const displaySimilar = isExpanded ? similar : similar.slice(0, 2);
                                            
                                            return (
                                                <div className="mt-3 p-3 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg border border-yellow-200 dark:border-yellow-700/50">
                                                    <div className="flex items-center gap-2 mb-2">
                                                        <AlertCircle size={14} className="text-yellow-600 dark:text-yellow-400" />
                                                        <span className="text-xs font-medium text-yellow-800 dark:text-yellow-200">
                                                            Similar existing terms found:
                                                        </span>
                                                    </div>
                                                    <div className="flex flex-col gap-2">
                                                        {displaySimilar.map((t, i) => (
                                                            <div key={i} className="flex items-center justify-between px-3 py-2 bg-yellow-100 dark:bg-yellow-900/40 rounded border border-yellow-200 dark:border-yellow-700/50">
                                                                <div className="flex flex-col gap-0.5">
                                                                    <span className="text-xs font-bold text-yellow-900 dark:text-yellow-100">{t.term}</span>
                                                                    {t.translation && <span className="text-[10px] text-yellow-700 dark:text-yellow-300">{t.translation}</span>}
                                                                </div>
                                                                <button
                                                                    onClick={(e) => {
                                                                        e.stopPropagation();
                                                                        setMergeTarget(idx, t.term);
                                                                    }}
                                                                    className="text-[10px] px-2 py-1 bg-yellow-200 hover:bg-yellow-300 dark:bg-yellow-700 dark:hover:bg-yellow-600 text-yellow-800 dark:text-yellow-100 rounded transition-colors font-medium flex items-center gap-1"
                                                                >
                                                                    <Merge size={10} />
                                                                    Merge
                                                                </button>
                                                            </div>
                                                        ))}
                                                        {similar.length > 2 && (
                                                            <button 
                                                                onClick={(e) => toggleExpandedWarning(idx, e)}
                                                                className="text-xs text-yellow-700 dark:text-yellow-400 hover:underline flex items-center gap-1 mt-1 self-start"
                                                            >
                                                                {isExpanded ? <><ChevronUp size={12}/> Show less</> : <><ChevronDown size={12}/> +{similar.length - 2} more</>}
                                                            </button>
                                                        )}
                                                    </div>
                                                </div>
                                            );
                                        })()}
                                    </div>
                                );
                            })
                        )}
                    </div>

                    {/* Footer */}
                    <div className="px-6 py-4 border-t border-gray-200 dark:border-gray-700 flex justify-between items-center">
                        <div>
                            {onDelete && (
                                <button
                                    onClick={() => {
                                        if (window.confirm("Delete this AI response? You'll be able to request a new one.")) {
                                            onDelete();
                                            onClose();
                                        }
                                    }}
                                    className="px-4 py-2 text-red-600 dark:text-red-400 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg transition-colors font-medium flex items-center gap-2"
                                    title="Delete this response and request a new one"
                                >
                                    <Trash2 size={16} />
                                    Delete Response
                                </button>
                            )}
                        </div>
                        <div className="flex gap-3">
                            <button
                                onClick={onClose}
                                className="px-4 py-2 text-gray-700 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 rounded-lg transition-colors font-medium"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleConfirm}
                                disabled={selectedTerms.size === 0}
                                className="px-6 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors font-medium flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                            >
                                <Plus size={18} />
                                Add {selectedTerms.size} {selectedTerms.size === 1 ? 'Term' : 'Terms'}
                            </button>
                        </div>
                    </div>
                </motion.div >
            </div >
        </AnimatePresence >
    );
};

export default GlossaryReviewModal;
