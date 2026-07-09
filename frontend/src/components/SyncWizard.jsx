import React, { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Globe, CheckSquare, Square, ChevronRight, Search, Check, RefreshCw } from 'lucide-react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';

const SyncWizard = ({ projectName, onImported }) => {
    const [candidates, setCandidates] = useState({ glossary: [], characters: [] });
    const [loading, setLoading] = useState(true);
    const [activeSubTab, setActiveSubTab] = useState('glossary'); // 'glossary' | 'characters'
    const [searchQuery, setSearchQuery] = useState('');
    const [selectedGlossary, setSelectedGlossary] = useState(new Set());
    const [selectedCharacters, setSelectedCharacters] = useState(new Set());
    const [syncing, setSyncing] = useState(false);
    const toast = useToast();

    const loadCandidates = async () => {
        setLoading(true);
        try {
            const res = await api.getSyncCandidates(projectName);
            setCandidates(res.data || { glossary: [], characters: [] });
            setSelectedGlossary(new Set());
            setSelectedCharacters(new Set());
        } catch (err) {
            console.error("Failed to load sync candidates", err);
            toast.error("Failed to fetch sync candidates.");
        } finally {
            setLoading(false);
        }
    };

    useEffect(() => {
        loadCandidates();
    }, [projectName]);

    // Filtering candidates
    const filteredGlossary = candidates.glossary.filter(t => 
        (t.term || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
        (t.translation || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
        (t.project || '').toLowerCase().includes(searchQuery.toLowerCase())
    );

    const filteredCharacters = candidates.characters.filter(c => 
        (c.name || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
        (c.gender || '').toLowerCase().includes(searchQuery.toLowerCase()) ||
        (c.project || '').toLowerCase().includes(searchQuery.toLowerCase())
    );

    const handleToggleSelectGlossary = (termName) => {
        setSelectedGlossary(prev => {
            const next = new Set(prev);
            if (next.has(termName)) next.delete(termName);
            else next.add(termName);
            return next;
        });
    };

    const handleToggleSelectCharacter = (charName) => {
        setSelectedCharacters(prev => {
            const next = new Set(prev);
            if (next.has(charName)) next.delete(charName);
            else next.add(charName);
            return next;
        });
    };

    const handleSelectAllGlossary = () => {
        if (selectedGlossary.size === filteredGlossary.length) {
            setSelectedGlossary(new Set());
        } else {
            setSelectedGlossary(new Set(filteredGlossary.map(t => t.term)));
        }
    };

    const handleSelectAllCharacters = () => {
        if (selectedCharacters.size === filteredCharacters.length) {
            setSelectedCharacters(new Set());
        } else {
            setSelectedCharacters(new Set(filteredCharacters.map(c => c.name)));
        }
    };

    const handleImport = async () => {
        setSyncing(true);
        try {
            const selectedTermsList = candidates.glossary.filter(t => selectedGlossary.has(t.term));
            const selectedCharsList = candidates.characters.filter(c => selectedCharacters.has(c.name));

            if (selectedTermsList.length === 0 && selectedCharsList.length === 0) {
                toast.warning("No items selected for sync.");
                return;
            }

            await api.syncImport(projectName, {
                terms: selectedTermsList,
                characters: selectedCharsList
            });

            toast.success(`Successfully elevated ${selectedTermsList.length} terms and ${selectedCharsList.length} profiles to the universe level!`);
            loadCandidates();
            // Tell the parent view to re-fetch so the elevated terms show up in its
            // Glossary tab (and the tab count) without a manual page reload.
            onImported?.();
        } catch (err) {
            console.error("Failed to sync import items", err);
            toast.error("Failed to elevate items.");
        } finally {
            setSyncing(false);
        }
    };

    if (loading) {
        return (
            <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 p-12 text-center">
                <RefreshCw className="w-8 h-8 mx-auto text-indigo-500 animate-spin mb-4" />
                <p className="text-gray-500 dark:text-gray-400 font-medium">Scanning child projects for new terms...</p>
            </div>
        );
    }

    const totalCandidates = candidates.glossary.length + candidates.characters.length;

    return (
        <div className="bg-white dark:bg-gray-800 rounded-xl border border-gray-200 dark:border-gray-700 overflow-hidden shadow-sm flex flex-col">
            {/* Header info */}
            <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex justify-between items-center">
                <div>
                    <h3 className="text-lg font-bold text-gray-900 dark:text-white flex items-center gap-2">
                        <Globe size={20} className="text-indigo-500" /> Universe Sync Wizard
                    </h3>
                    <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                        Elevate terminology and character profiles defined in child projects to make them shared across all universe shows.
                    </p>
                </div>
                <button
                    onClick={loadCandidates}
                    className="p-2 text-gray-400 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 rounded-lg transition-colors"
                    title="Scan Again"
                >
                    <RefreshCw size={18} />
                </button>
            </div>

            {/* Sub-tabs & Search */}
            <div className="border-b border-gray-100 dark:border-gray-700 px-6 py-3 bg-white dark:bg-gray-800 flex flex-col md:flex-row justify-between items-center gap-4">
                <div className="flex gap-2 w-full md:w-auto">
                    <button
                        onClick={() => { setActiveSubTab('glossary'); setSearchQuery(''); }}
                        className={`px-4 py-2 rounded-xl text-xs font-bold transition-all ${
                            activeSubTab === 'glossary'
                                ? 'bg-indigo-650 text-white shadow-md'
                                : 'bg-gray-50 hover:bg-gray-100 text-gray-650 dark:bg-gray-900 dark:text-gray-400 dark:hover:bg-gray-750'
                        }`}
                    >
                        Glossary Candidates ({candidates.glossary.length})
                    </button>
                    <button
                        onClick={() => { setActiveSubTab('characters'); setSearchQuery(''); }}
                        className={`px-4 py-2 rounded-xl text-xs font-bold transition-all ${
                            activeSubTab === 'characters'
                                ? 'bg-indigo-650 text-white shadow-md'
                                : 'bg-gray-50 hover:bg-gray-100 text-gray-650 dark:bg-gray-900 dark:text-gray-400 dark:hover:bg-gray-750'
                        }`}
                    >
                        Character Profiles ({candidates.characters.length})
                    </button>
                </div>

                <div className="relative w-full md:w-64">
                    <Search className="absolute left-3 top-2.5 h-4 w-4 text-gray-400" />
                    <input
                        type="text"
                        placeholder="Search candidates..."
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                        className="w-full pl-9 pr-4 py-2 text-xs bg-gray-50 border border-gray-200 dark:bg-gray-900 dark:border-gray-750 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    />
                </div>
            </div>

            {/* Table Area */}
            <div className="p-6 overflow-x-auto min-h-[250px]">
                {activeSubTab === 'glossary' ? (
                    filteredGlossary.length === 0 ? (
                        <div className="text-center py-12 text-gray-400 dark:text-gray-550">
                            No glossary terms found to sync.
                        </div>
                    ) : (
                        <table className="w-full text-left border-collapse">
                            <thead>
                                <tr className="border-b border-gray-100 dark:border-gray-700 text-xs font-semibold text-gray-450 uppercase tracking-wider">
                                    <th className="py-3 px-4 w-10">
                                        <button onClick={handleSelectAllGlossary} className="text-gray-400 hover:text-indigo-600">
                                            {selectedGlossary.size === filteredGlossary.length ? <CheckSquare size={16} /> : <Square size={16} />}
                                        </button>
                                    </th>
                                    <th className="py-3 px-4">Term</th>
                                    <th className="py-3 px-4">Translation</th>
                                    <th className="py-3 px-4">Type</th>
                                    <th className="py-3 px-4">Source Child Project</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-50 dark:divide-gray-750 text-xs">
                                {filteredGlossary.map((t, idx) => (
                                    <tr key={idx} className="hover:bg-slate-50/50 dark:hover:bg-slate-900/10">
                                        <td className="py-3 px-4">
                                            <button onClick={() => handleToggleSelectGlossary(t.term)} className="text-gray-400 hover:text-indigo-600">
                                                {selectedGlossary.has(t.term) ? <CheckSquare size={16} className="text-indigo-650" /> : <Square size={16} />}
                                            </button>
                                        </td>
                                        <td className="py-3 px-4 font-bold text-gray-900 dark:text-white">{t.term}</td>
                                        <td className="py-3 px-4 font-medium text-indigo-600 dark:text-indigo-400">{t.translation}</td>
                                        <td className="py-3 px-4 text-gray-500 capitalize">{t.type}</td>
                                        <td className="py-3 px-4 font-medium text-gray-650 dark:text-gray-400">{t.project}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )
                ) : (
                    filteredCharacters.length === 0 ? (
                        <div className="text-center py-12 text-gray-400 dark:text-gray-550">
                            No character profiles found to sync.
                        </div>
                    ) : (
                        <table className="w-full text-left border-collapse">
                            <thead>
                                <tr className="border-b border-gray-100 dark:border-gray-700 text-xs font-semibold text-gray-450 uppercase tracking-wider">
                                    <th className="py-3 px-4 w-10">
                                        <button onClick={handleSelectAllCharacters} className="text-gray-400 hover:text-indigo-600">
                                            {selectedCharacters.size === filteredCharacters.length ? <CheckSquare size={16} /> : <Square size={16} />}
                                        </button>
                                    </th>
                                    <th className="py-3 px-4">Character Name</th>
                                    <th className="py-3 px-4">Gender</th>
                                    <th className="py-3 px-4">Formality</th>
                                    <th className="py-3 px-4">Source Child Project</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-50 dark:divide-gray-750 text-xs">
                                {filteredCharacters.map((c, idx) => (
                                    <tr key={idx} className="hover:bg-slate-50/50 dark:hover:bg-slate-900/10">
                                        <td className="py-3 px-4">
                                            <button onClick={() => handleToggleSelectCharacter(c.name)} className="text-gray-400 hover:text-indigo-600">
                                                {selectedCharacters.has(c.name) ? <CheckSquare size={16} className="text-indigo-650" /> : <Square size={16} />}
                                            </button>
                                        </td>
                                        <td className="py-3 px-4 font-bold text-gray-900 dark:text-white">{c.name}</td>
                                        <td className="py-3 px-4 text-gray-500 capitalize">{c.gender}</td>
                                        <td className="py-3 px-4 text-gray-500 capitalize">{c.formality}</td>
                                        <td className="py-3 px-4 font-medium text-gray-650 dark:text-gray-400">{c.project}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    )
                )}
            </div>

            {/* Sync Bar Floating Action */}
            <AnimatePresence>
                {(selectedGlossary.size > 0 || selectedCharacters.size > 0) && (
                    <motion.div
                        initial={{ opacity: 0, y: 30 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: 30 }}
                        className="p-4 border-t border-gray-150 dark:border-gray-700 bg-gray-50 dark:bg-gray-850/80 backdrop-blur-md flex items-center justify-between z-20 shrink-0"
                    >
                        <div className="text-xs font-semibold text-gray-700 dark:text-gray-300">
                            Selected:{' '}
                            <span className="text-indigo-650 font-bold">
                                {selectedGlossary.size} terms
                            </span>
                            {activeSubTab === 'characters' && (
                                <span className="ml-2 border-l border-gray-350 dark:border-gray-600 pl-2 text-indigo-650 font-bold">
                                    {selectedCharacters.size} profiles
                                </span>
                            )}
                        </div>
                        <div className="flex gap-2">
                            <button
                                onClick={() => { setSelectedGlossary(new Set()); setSelectedCharacters(new Set()); }}
                                className="px-4 py-2 text-xs font-bold text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 transition-colors"
                            >
                                Clear Selection
                            </button>
                            <button
                                onClick={handleImport}
                                disabled={syncing}
                                className="px-5 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 transition-all disabled:opacity-50"
                            >
                                {syncing ? 'Elevating...' : <><Check size={14} /> Elevate Selected to Universe</>}
                            </button>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>
        </div>
    );
};

export default SyncWizard;
