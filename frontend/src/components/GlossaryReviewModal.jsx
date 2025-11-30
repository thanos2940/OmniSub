import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { X, Check, Plus, AlertCircle, Trash2 } from 'lucide-react';

const findSimilarTerms = (newTerm, existingTerms) => {
    if (!newTerm || !existingTerms || existingTerms.length === 0) return [];

    // Split into significant words (length > 2)
    const words = newTerm.trim().split(/[\s-_]+/).filter(w => w.length > 2);

    // If no significant words, fallback to direct substring check
    if (words.length === 0) {
        const lowerNew = newTerm.toLowerCase();
        return existingTerms.filter(t =>
            t.term.toLowerCase().includes(lowerNew) || lowerNew.includes(t.term.toLowerCase())
        );
    }

    const escapeRegExp = (string) => string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const pattern = new RegExp(words.map(escapeRegExp).join('|'), 'i');

    return existingTerms.filter(existing => {
        // Check for word overlap (e.g. new="Matou Shinji", existing="Matou" -> matches)
        // Also covers new="Matou", existing="Matou Shinji" -> matches
        if (pattern.test(existing.term)) return true;

        // Check for direct substring inclusion (reverse direction)
        // If new term is "Superman", existing is "Super". Regex "Superman" might not match "Super" depending on boundaries,
        // but "Superman" includes "Super".
        if (newTerm.toLowerCase().includes(existing.term.toLowerCase())) return true;

        return false;
    });
};

const GlossaryReviewModal = ({ isOpen, onClose, onConfirm, newTerms, existingTerms, onDelete }) => {
    const [editableTerms, setEditableTerms] = useState([]);
    const [selectedTerms, setSelectedTerms] = useState(new Set());

    useEffect(() => {
        if (isOpen && newTerms) {
            setEditableTerms(JSON.parse(JSON.stringify(newTerms)));
            setSelectedTerms(new Set(newTerms.map((_, idx) => idx)));
        }
    }, [isOpen, newTerms]);

    const handleTermChange = (index, field, value) => {
        const updated = [...editableTerms];
        updated[index] = { ...updated[index], [field]: value };
        setEditableTerms(updated);
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

    const selectAll = () => {
        setSelectedTerms(new Set(editableTerms.map((_, idx) => idx)));
    };

    const selectNone = () => {
        setSelectedTerms(new Set());
    };

    const handleConfirm = () => {
        const termsToAdd = editableTerms.filter((_, idx) => selectedTerms.has(idx));
        onConfirm(termsToAdd);
        onClose();
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

                    {/* Actions */}
                    <div className="px-6 py-3 border-b border-gray-200 dark:border-gray-700 flex items-center gap-4">
                        <button
                            onClick={selectAll}
                            className="text-sm font-medium text-blue-600 hover:text-blue-700 dark:text-blue-400"
                        >
                            Select All
                        </button>
                        <span className="text-gray-300">|</span>
                        <button
                            onClick={selectNone}
                            className="text-sm font-medium text-gray-500 hover:text-gray-700 dark:text-gray-400"
                        >
                            Select None
                        </button>
                        <span className="text-xs text-gray-400 ml-auto">
                            {selectedTerms.size} selected
                        </span>
                    </div>

                    {/* Terms List */}
                    <div className="flex-1 overflow-y-auto p-6 space-y-3">
                        {editableTerms.length === 0 ? (
                            <div className="text-center py-12 text-gray-500 dark:text-gray-400">
                                <p className="text-lg font-medium mb-2">No new terms found</p>
                                <p className="text-sm">The AI didn't discover any new terminology in the selected episodes.</p>
                            </div>
                        ) : (
                            editableTerms.map((term, idx) => (
                                <div
                                    key={idx}
                                    onClick={() => toggleTerm(idx)}
                                    className={`p-4 rounded-lg border-2 cursor-pointer transition-all ${selectedTerms.has(idx)
                                        ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20'
                                        : 'border-gray-200 dark:border-gray-700 hover:border-blue-300 dark:hover:border-blue-700'
                                        }`}
                                >
                                    <div className="flex items-start gap-3">
                                        <div className={`mt-1 w-5 h-5 rounded border-2 flex items-center justify-center transition-colors shrink-0 ${selectedTerms.has(idx)
                                            ? 'border-blue-500 bg-blue-500'
                                            : 'border-gray-300 dark:border-gray-600'
                                            }`}>
                                            {selectedTerms.has(idx) && (
                                                <Check size={14} className="text-white" />
                                            )}
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <div className="flex flex-wrap items-center gap-2 mb-2">
                                                <input
                                                    type="text"
                                                    value={term.term}
                                                    onChange={(e) => handleTermChange(idx, 'term', e.target.value)}
                                                    onClick={(e) => e.stopPropagation()}
                                                    className="font-bold text-gray-900 dark:text-white bg-transparent border-b border-gray-300 dark:border-gray-600 focus:border-blue-500 outline-none px-1 py-0.5 min-w-[120px]"
                                                    placeholder="Term"
                                                />
                                                <select
                                                    value={term.type}
                                                    onChange={(e) => handleTermChange(idx, 'type', e.target.value)}
                                                    onClick={(e) => e.stopPropagation()}
                                                    className="text-sm px-2 py-0.5 rounded-full bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300 border-none outline-none cursor-pointer"
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
                                                    className="text-sm px-2 py-0.5 rounded-full bg-purple-100 dark:bg-purple-900/30 text-purple-600 dark:text-purple-300 border-none outline-none cursor-pointer"
                                                >
                                                    <option value="male">Male</option>
                                                    <option value="female">Female</option>
                                                    <option value="neutral">Neutral</option>
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
                                            <div className="flex items-center gap-2 text-sm">
                                                <span className="text-gray-500 dark:text-gray-400 shrink-0">Translation:</span>
                                                <input
                                                    type="text"
                                                    value={term.translation}
                                                    onChange={(e) => handleTermChange(idx, 'translation', e.target.value)}
                                                    onClick={(e) => e.stopPropagation()}
                                                    className="font-medium text-indigo-600 dark:text-indigo-400 bg-transparent border-b border-gray-300 dark:border-gray-600 focus:border-indigo-500 outline-none px-1 py-0.5 flex-1"
                                                    placeholder="Translation"
                                                />
                                            </div>
                                        </div>
                                    </div>
                                    {(() => {
                                        const similar = findSimilarTerms(term.term, existingTerms);
                                        if (similar.length === 0) return null;
                                        return (
                                            <div className="mt-3 p-3 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg border border-yellow-200 dark:border-yellow-700/50">
                                                <div className="flex items-center gap-2 mb-2">
                                                    <AlertCircle size={14} className="text-yellow-600 dark:text-yellow-400" />
                                                    <span className="text-xs font-medium text-yellow-800 dark:text-yellow-200">
                                                        Similar terms found in glossary:
                                                    </span>
                                                </div>
                                                <div className="flex flex-wrap gap-2">
                                                    {similar.map((t, i) => (
                                                        <div key={i} className="flex flex-col gap-0.5 px-2 py-1 bg-yellow-100 dark:bg-yellow-900/40 rounded border border-yellow-200 dark:border-yellow-700/50">
                                                            <span className="text-xs font-medium text-yellow-900 dark:text-yellow-100">{t.term}</span>
                                                            {t.translation && <span className="text-[10px] text-yellow-700 dark:text-yellow-300">{t.translation}</span>}
                                                        </div>
                                                    ))}
                                                </div>
                                            </div>
                                        );
                                    })()}
                                </div>
                            ))
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
