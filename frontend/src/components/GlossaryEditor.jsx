import React, { useState, useRef, useEffect, useMemo } from 'react';
import { createPortal } from 'react-dom';
import { motion, AnimatePresence } from 'framer-motion';
import {
    Save, Edit2, Check, X, Globe, User, Book, Shield, Sparkles,
    Download, Upload, Trash2, Plus, RotateCcw, ArrowUpCircle,
    EyeOff, Eye, AlertCircle, ChevronDown, ChevronUp, Search,
    Filter, FileSpreadsheet, FileJson, CheckSquare, Square,
    ArrowRight, Info, Layers
} from 'lucide-react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';
import TermHarvestModal from './TermHarvestModal';

let _uidCounter = 0;
const ensureUids = (arr) => (arr || []).map(t => (t && t._uid != null) ? t : { ...t, _uid: `t${Date.now()}_${_uidCounter++}` });
const stripUids = (arr) => (arr || []).map(({ _uid, ...rest }) => rest);

const SOURCE_FILTERS = {
    ALL: 'all',
    INHERITED: 'inherited',
    OVERRIDE: 'override',
    PROJECT_ONLY: 'project_only',
    UPSTREAM_MODIFIED: 'upstream_modified',
    SUPPRESSED: 'suppressed',
};

const TYPE_BADGES = {
    person: { label: 'Character', color: 'bg-amber-100 dark:bg-amber-950/50 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-900' },
    character: { label: 'Character', color: 'bg-amber-100 dark:bg-amber-950/50 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-900' },
    location: { label: 'Location', color: 'bg-emerald-100 dark:bg-emerald-950/50 text-emerald-800 dark:text-emerald-300 border-emerald-200 dark:border-emerald-900' },
    organization: { label: 'Organization', color: 'bg-blue-100 dark:bg-blue-950/50 text-blue-800 dark:text-blue-300 border-blue-200 dark:border-blue-900' },
    item: { label: 'Item / Object', color: 'bg-purple-100 dark:bg-purple-950/50 text-purple-800 dark:text-purple-300 border-purple-200 dark:border-purple-900' },
    object: { label: 'Item / Object', color: 'bg-purple-100 dark:bg-purple-950/50 text-purple-800 dark:text-purple-300 border-purple-200 dark:border-purple-900' },
    technique: { label: 'Technique', color: 'bg-rose-100 dark:bg-rose-950/50 text-rose-800 dark:text-rose-300 border-rose-200 dark:border-rose-900' },
    concept: { label: 'Concept', color: 'bg-teal-100 dark:bg-teal-950/50 text-teal-800 dark:text-teal-300 border-teal-200 dark:border-teal-900' },
    other: { label: 'Other', color: 'bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 border-gray-200 dark:border-gray-700' },
};

const GlossaryEditor = ({
    glossary,
    onSave,
    onCancel,
    isSaving,
    readOnly = false,
    onChange,
    hideSaveButton = false,
    onUpdateTermsInScenes,
    projectName = null,
    parentProject = null,
    suppressedTerms: initialSuppressed = []
}) => {
    const [terms, setTerms] = useState(() => ensureUids(glossary.terms || []));
    const [suppressedTerms, setSuppressedTerms] = useState(() => initialSuppressed || []);
    const [showSuppressed, setShowSuppressed] = useState(false);
    const [selectedTerms, setSelectedTerms] = useState(new Set());
    const [syncDialog, setSyncDialog] = useState(null);
    const [promotingTerm, setPromotingTerm] = useState(null);
    const [harvestModalOpen, setHarvestModalOpen] = useState(false);
    const [expandedDiffUid, setExpandedDiffUid] = useState(null);

    // Facet Filters
    const [searchFilter, setSearchFilter] = useState('');
    const [sourceFilter, setSourceFilter] = useState(SOURCE_FILTERS.ALL);
    const [typeFilter, setTypeFilter] = useState('All');
    const [customCategories, setCustomCategories] = useState([]);

    const fileInputRef = useRef(null);
    const csvInputRef = useRef(null);
    const isInternalUpdateRef = useRef(false);
    const dirtyRef = useRef(false);
    const debounceTimerRef = useRef(null);
    const toast = useToast();

    // Map of original loaded terms to detect overrides and provide revert values
    const originalTermsMapRef = useRef(new Map());
    useEffect(() => {
        const map = new Map();
        (glossary.terms || []).forEach(t => {
            if (t.term) {
                map.set(t.term.toLowerCase().strip ? t.term.toLowerCase().strip() : t.term.toLowerCase(), t);
            }
        });
        originalTermsMapRef.current = map;
    }, [glossary.terms]);

    const defaultTypes = ['Person', 'Location', 'Item', 'Organization', 'Technique', 'Concept', 'Other'];
    const termTypes = [...new Set(terms.map(t => t.type || 'other'))].map(t => t.charAt(0).toUpperCase() + t.slice(1));
    const allTypes = [...new Set([...defaultTypes, ...termTypes, ...customCategories])];

    const prevGlossaryTermsRef = useRef(glossary.terms);
    useEffect(() => {
        if (glossary.terms !== prevGlossaryTermsRef.current) {
            setTerms(ensureUids(glossary.terms || []));
            dirtyRef.current = false;
            prevGlossaryTermsRef.current = glossary.terms;
        }
    }, [glossary.terms]);

    useEffect(() => {
        if (initialSuppressed) {
            setSuppressedTerms(initialSuppressed);
        }
    }, [initialSuppressed]);

    // Debounced sync with parent
    useEffect(() => {
        if (onChange && isInternalUpdateRef.current) {
            if (debounceTimerRef.current) {
                clearTimeout(debounceTimerRef.current);
            }

            debounceTimerRef.current = setTimeout(() => {
                onChange({ ...glossary, terms: stripUids(terms) }, suppressedTerms);
                isInternalUpdateRef.current = false;
            }, 300);
        }

        return () => {
            if (debounceTimerRef.current) {
                clearTimeout(debounceTimerRef.current);
            }
        };
    }, [terms, suppressedTerms]);

    const handleTermChange = (index, field, value) => {
        if (readOnly) return;
        isInternalUpdateRef.current = true;
        dirtyRef.current = true;
        const newTerms = [...terms];
        newTerms[index] = { ...newTerms[index], [field]: value };
        setTerms(newTerms);
    };

    const handleRemoveTerm = (index) => {
        if (readOnly) return;
        const targetTerm = terms[index];
        if (!targetTerm) return;

        if (targetTerm.inherited) {
            if (window.confirm(`"${targetTerm.term}" is inherited from parent universe (${targetTerm.inherited_from || 'Universe'}).\n\nSuppress this term for this project? (It won't affect the universe or sibling shows).`)) {
                isInternalUpdateRef.current = true;
                dirtyRef.current = true;
                const normName = (targetTerm.term || '').trim();
                if (normName && !suppressedTerms.includes(normName)) {
                    setSuppressedTerms([...suppressedTerms, normName]);
                }
                setTerms(terms.filter((_, i) => i !== index));
                toast?.info(`"${targetTerm.term}" suppressed for this project.`);
            }
        } else {
            if (window.confirm(`Are you sure you want to remove "${targetTerm.term || 'this term'}"?`)) {
                isInternalUpdateRef.current = true;
                dirtyRef.current = true;
                setTerms(terms.filter((_, i) => i !== index));
            }
        }
    };

    const handleUnsuppressTerm = (termName) => {
        isInternalUpdateRef.current = true;
        dirtyRef.current = true;
        setSuppressedTerms(suppressedTerms.filter(t => t.toLowerCase() !== termName.toLowerCase()));
        toast?.success(`"${termName}" restored. Click Save to apply.`);
    };

    const handleRevertToUniverse = (index) => {
        const targetTerm = terms[index];
        if (!targetTerm || !targetTerm.term) return;
        const parentDef = targetTerm.parent_term || originalTermsMapRef.current.get(targetTerm.term.toLowerCase());
        if (!parentDef) return;

        isInternalUpdateRef.current = true;
        dirtyRef.current = true;
        const newTerms = [...terms];
        newTerms[index] = {
            ...newTerms[index],
            translation: parentDef.translation || '',
            type: parentDef.type || 'other',
            gender: parentDef.gender || 'neuter',
            description: parentDef.description || '',
            keep_original: parentDef.keep_original || false,
            case_sensitive: parentDef.case_sensitive !== false,
            is_override: false,
            upstream_modified: false,
        };
        setTerms(newTerms);
        toast?.info(`Reverted "${targetTerm.term}" to universe standard.`);
    };

    const handleAdoptUpstream = (index) => {
        const targetTerm = terms[index];
        if (!targetTerm || !targetTerm.parent_term) return;
        const parentDef = targetTerm.parent_term;

        isInternalUpdateRef.current = true;
        dirtyRef.current = true;
        const newTerms = [...terms];
        newTerms[index] = {
            ...newTerms[index],
            translation: parentDef.translation || targetTerm.translation,
            type: parentDef.type || targetTerm.type,
            gender: parentDef.gender || targetTerm.gender,
            description: parentDef.description || targetTerm.description,
            keep_original: parentDef.keep_original ?? targetTerm.keep_original,
            case_sensitive: parentDef.case_sensitive ?? targetTerm.case_sensitive,
            upstream_modified: false,
        };
        setTerms(newTerms);
        toast?.success(`Adopted upstream definition for "${targetTerm.term}".`);
    };

    const handleSilenceUpstreamDiff = (index) => {
        const newTerms = [...terms];
        newTerms[index] = { ...newTerms[index], upstream_modified: false };
        setTerms(newTerms);
    };

    const handleInlinePromote = async (term) => {
        if (!projectName || !parentProject) {
            toast?.error("No parent universe linked to promote to.");
            return;
        }
        if (!term.term || !term.translation) {
            toast?.warning("Term and translation are required before promoting.");
            return;
        }

        setPromotingTerm(term.term);
        try {
            await api.promoteTerm(projectName, {
                term: term.term,
                translation: term.translation,
                type: term.type,
                gender: term.gender,
                case_sensitive: term.case_sensitive,
                keep_original: term.keep_original,
                description: term.description,
            });
            toast?.success(`"${term.term}" promoted to ${parentProject} universe!`);
            setTerms(prev => prev.map(t => (t.term.toLowerCase() === term.term.toLowerCase())
                ? { ...t, inherited: true, inherited_from: parentProject, is_override: false, upstream_modified: false }
                : t
            ));
        } catch (err) {
            console.error("Failed to promote term", err);
            toast?.error("Failed to promote term to universe.");
        } finally {
            setPromotingTerm(null);
        }
    };

    const handleAddTerm = () => {
        const newTerm = {
            _uid: `t${Date.now()}_${_uidCounter++}`,
            term: '',
            translation: '',
            type: typeFilter !== 'All' ? typeFilter.toLowerCase() : 'other',
            gender: 'neuter',
            description: '',
            keep_original: false,
            case_sensitive: false,
            inherited: false,
            is_override: false,
            upstream_modified: false,
        };
        isInternalUpdateRef.current = true;
        dirtyRef.current = true;
        setTerms([newTerm, ...terms]);
    };

    // Bulk selection handlers
    const toggleSelectTerm = (uid) => {
        setSelectedTerms(prev => {
            const next = new Set(prev);
            if (next.has(uid)) next.delete(uid);
            else next.add(uid);
            return next;
        });
    };

    const toggleSelectAllFiltered = () => {
        const visibleUids = filteredTerms.map(t => t.term._uid);
        const allSelected = visibleUids.every(uid => selectedTerms.has(uid));
        setSelectedTerms(prev => {
            const next = new Set(prev);
            if (allSelected) {
                visibleUids.forEach(uid => next.delete(uid));
            } else {
                visibleUids.forEach(uid => next.add(uid));
            }
            return next;
        });
    };

    // Bulk actions
    const handleBulkPromote = async () => {
        if (!parentProject || !projectName) return;
        const selectedList = terms.filter(t => selectedTerms.has(t._uid) && t.term);
        if (selectedList.length === 0) return;

        try {
            await api.promoteTermsBatch(projectName, selectedList);
            toast.success(`Promoted ${selectedList.length} terms to ${parentProject} universe!`);
            setTerms(prev => prev.map(t => selectedTerms.has(t._uid)
                ? { ...t, inherited: true, inherited_from: parentProject, is_override: false, upstream_modified: false }
                : t
            ));
            setSelectedTerms(new Set());
        } catch (e) {
            toast.error("Failed to batch promote terms.");
        }
    };

    const handleBulkSuppress = async () => {
        const selectedList = terms.filter(t => selectedTerms.has(t._uid) && t.term);
        if (selectedList.length === 0) return;

        const termNames = selectedList.map(t => t.term.trim());
        try {
            await api.suppressTermsBatch(projectName, termNames);
            setSuppressedTerms(prev => [...new Set([...prev, ...termNames])]);
            setTerms(prev => prev.filter(t => !selectedTerms.has(t._uid)));
            setSelectedTerms(new Set());
            toast.success(`Suppressed ${selectedList.length} terms.`);
        } catch (e) {
            toast.error("Failed to batch suppress terms.");
        }
    };

    const handleBulkRevert = async () => {
        const selectedList = terms.filter(t => selectedTerms.has(t._uid) && t.term);
        if (selectedList.length === 0) return;

        const termNames = selectedList.map(t => t.term.trim());
        try {
            await api.revertTermsBatch(projectName, termNames);
            toast.success(`Reverted overrides for ${selectedList.length} terms.`);
            // Reload or refresh locally
            setTerms(prev => prev.map(t => {
                if (selectedTerms.has(t._uid) && t.parent_term) {
                    return {
                        ...t,
                        ...t.parent_term,
                        is_override: false,
                        upstream_modified: false,
                    };
                }
                return t;
            }));
            setSelectedTerms(new Set());
        } catch (e) {
            toast.error("Failed to batch revert terms.");
        }
    };

    const handleBulkDelete = () => {
        const count = selectedTerms.size;
        if (!count) return;
        if (window.confirm(`Delete / remove ${count} selected term(s)?`)) {
            isInternalUpdateRef.current = true;
            dirtyRef.current = true;
            setTerms(prev => prev.filter(t => !selectedTerms.has(t._uid)));
            setSelectedTerms(new Set());
            toast.info(`Removed ${count} terms.`);
        }
    };

    const handleHarvestedTermsAdded = (newHarvestedTerms, directToUniverse = false) => {
        if (!newHarvestedTerms || newHarvestedTerms.length === 0) return;
        if (directToUniverse) {
            // Added to universe, mark as inherited
            const formatted = newHarvestedTerms.map(t => ({
                ...t,
                _uid: `t${Date.now()}_${_uidCounter++}`,
                inherited: true,
                inherited_from: parentProject,
                is_override: false,
            }));
            setTerms(prev => [...formatted, ...prev]);
        } else {
            const formatted = newHarvestedTerms.map(t => ({
                ...t,
                _uid: `t${Date.now()}_${_uidCounter++}`,
                inherited: false,
                is_override: false,
            }));
            setTerms(prev => [...formatted, ...prev]);
            isInternalUpdateRef.current = true;
            dirtyRef.current = true;
        }
    };

    const handleSave = () => {
        const cleanTerms = stripUids(terms);
        dirtyRef.current = false;
        onSave({ ...glossary, terms: cleanTerms }, false, suppressedTerms);
    };

    // Export Formats
    const handleExportJSON = () => {
        const dataStr = JSON.stringify({
            ...glossary,
            terms: stripUids(terms),
            suppressed_terms: suppressedTerms
        }, null, 2);
        const blob = new Blob([dataStr], { type: "application/json" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `glossary_${projectName || glossary.show_name || 'export'}.json`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const handleExportCSV = () => {
        const headers = ["term", "translation", "type", "gender", "case_sensitive", "keep_original", "description"];
        const rows = terms.map(t => [
            `"${(t.term || '').replace(/"/g, '""')}"`,
            `"${(t.translation || '').replace(/"/g, '""')}"`,
            `"${(t.type || 'other').replace(/"/g, '""')}"`,
            `"${(t.gender || 'n/a').replace(/"/g, '""')}"`,
            t.case_sensitive ? "true" : "false",
            t.keep_original ? "true" : "false",
            `"${(t.description || '').replace(/"/g, '""')}"`
        ]);

        const csvContent = [headers.join(','), ...rows.map(r => r.join(','))].join('\n');
        const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `glossary_${projectName || 'export'}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const handleImportCSV = (e) => {
        const file = e.target.files[0];
        if (!file) return;

        const reader = new FileReader();
        reader.onload = (event) => {
            try {
                const text = event.target.result;
                const lines = text.split(/\r?\n/).filter(line => line.trim());
                if (lines.length <= 1) return;

                const headers = lines[0].split(',').map(h => h.trim().replace(/^"|"$/g, '').toLowerCase());
                const imported = [];

                for (let i = 1; i < lines.length; i++) {
                    const row = lines[i];
                    // Simple CSV parser handling quotes
                    const values = row.match(/(".*?"|[^",\s]+)(?=\s*,|\s*$)/g) || row.split(',');
                    const cleanValues = values.map(v => v.trim().replace(/^"|"$/g, '').replace(/""/g, '"'));

                    const termObj = {};
                    headers.forEach((h, idx) => {
                        termObj[h] = cleanValues[idx] || '';
                    });

                    if (termObj.term && termObj.translation) {
                        imported.push({
                            term: termObj.term,
                            translation: termObj.translation,
                            type: termObj.type || 'other',
                            gender: termObj.gender || 'n/a',
                            case_sensitive: termObj.case_sensitive === 'true',
                            keep_original: termObj.keep_original === 'true',
                            description: termObj.description || '',
                        });
                    }
                }

                if (imported.length > 0) {
                    isInternalUpdateRef.current = true;
                    dirtyRef.current = true;
                    setTerms(prev => [...ensureUids(imported), ...prev]);
                    toast.success(`Imported ${imported.length} terms from CSV.`);
                }
            } catch (err) {
                console.error("CSV import error:", err);
                toast.error("Failed to parse CSV file.");
            }
        };
        reader.readAsText(file);
        e.target.value = null;
    };

    // Filter computation
    const filteredTerms = useMemo(() => {
        return terms
            .map((term, index) => ({ term, index }))
            .filter(({ term }) => {
                // Search filter
                const q = searchFilter.toLowerCase();
                const matchesSearch = !q ||
                    (term.term && term.term.toLowerCase().includes(q)) ||
                    (term.translation && term.translation.toLowerCase().includes(q)) ||
                    (term.description && term.description.toLowerCase().includes(q));

                // Source facet
                let matchesSource = true;
                if (sourceFilter === SOURCE_FILTERS.INHERITED) matchesSource = !!term.inherited;
                else if (sourceFilter === SOURCE_FILTERS.OVERRIDE) matchesSource = !!term.is_override;
                else if (sourceFilter === SOURCE_FILTERS.PROJECT_ONLY) matchesSource = !term.inherited && !term.is_override;
                else if (sourceFilter === SOURCE_FILTERS.UPSTREAM_MODIFIED) matchesSource = !!term.upstream_modified;

                // Type facet
                const matchesType = typeFilter === 'All' ||
                    (term.type && term.type.toLowerCase() === typeFilter.toLowerCase());

                return matchesSearch && matchesSource && matchesType;
            });
    }, [terms, searchFilter, sourceFilter, typeFilter]);

    const upstreamModifiedCount = terms.filter(t => t.upstream_modified).length;
    const inheritedCount = terms.filter(t => t.inherited).length;
    const overrideCount = terms.filter(t => t.is_override).length;

    return (
        <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden flex flex-col">
            {/* Top Toolbar */}
            <div className="p-4 border-b border-gray-100 dark:border-gray-700 flex flex-wrap items-center justify-between gap-3 bg-gray-50/50 dark:bg-gray-850">
                {/* Search & Source Filter Tabs */}
                <div className="flex items-center gap-3 flex-wrap flex-1 min-w-[280px]">
                    <div className="relative flex-1 max-w-sm">
                        <Search className="w-4 h-4 text-gray-400 absolute left-3 top-2.5" />
                        <input
                            type="text"
                            placeholder="Search terms, translations, notes..."
                            value={searchFilter}
                            onChange={(e) => setSearchFilter(e.target.value)}
                            className="w-full pl-9 pr-3 py-1.5 text-xs rounded-xl border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 focus:ring-1 focus:ring-indigo-500 text-gray-800 dark:text-gray-200"
                        />
                    </div>

                    {/* Source Facet Pills */}
                    <div className="flex bg-gray-100 dark:bg-gray-700 p-1 rounded-xl gap-0.5 text-xs font-semibold">
                        {[
                            { id: SOURCE_FILTERS.ALL, label: 'All', count: terms.length },
                            { id: SOURCE_FILTERS.INHERITED, label: 'Inherited', count: inheritedCount },
                            { id: SOURCE_FILTERS.OVERRIDE, label: 'Overrides', count: overrideCount },
                            { id: SOURCE_FILTERS.UPSTREAM_MODIFIED, label: 'Upstream Changed', count: upstreamModifiedCount, highlight: true },
                        ].map(({ id, label, count, highlight }) => {
                            if (highlight && count === 0) return null;
                            const isActive = sourceFilter === id;
                            return (
                                <button
                                    key={id}
                                    onClick={() => setSourceFilter(id)}
                                    className={`px-2.5 py-1 rounded-lg transition-all flex items-center gap-1.5 ${
                                        isActive
                                            ? highlight
                                                ? 'bg-amber-500 text-white shadow-sm'
                                                : 'bg-white dark:bg-gray-800 text-indigo-600 dark:text-indigo-400 shadow-sm'
                                            : highlight
                                                ? 'text-amber-600 dark:text-amber-400 hover:bg-amber-50 dark:hover:bg-amber-950/40'
                                                : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-white'
                                    }`}
                                >
                                    <span>{label}</span>
                                    <span className={`text-[10px] px-1 py-0.2 rounded-full ${isActive ? 'bg-black/15 text-current' : 'bg-gray-200 dark:bg-gray-600 text-gray-600 dark:text-gray-300'}`}>
                                        {count}
                                    </span>
                                </button>
                            );
                        })}
                    </div>
                </div>

                {/* Actions */}
                <div className="flex items-center gap-2">
                    {/* Harvest Dialogue Terms */}
                    <button
                        onClick={() => setHarvestModalOpen(true)}
                        className="px-3 py-1.5 bg-gradient-to-r from-indigo-500 to-purple-600 hover:from-indigo-600 hover:to-purple-700 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-all"
                        title="Scan translated dialogue for new named entities"
                    >
                        <Sparkles className="w-3.5 h-3.5" />
                        <span>Harvest from Dialogue</span>
                    </button>

                    {/* Import / Export Menu */}
                    <input type="file" ref={fileInputRef} onChange={(e) => {
                        const file = e.target.files[0];
                        if (file) {
                            const reader = new FileReader();
                            reader.onload = (evt) => {
                                try {
                                    const parsed = JSON.parse(evt.target.result);
                                    const imported = Array.isArray(parsed) ? parsed : (parsed.terms || []);
                                    setTerms(prev => [...ensureUids(imported), ...prev]);
                                    toast.success(`Imported ${imported.length} terms.`);
                                } catch (err) {
                                    toast.error("Invalid JSON format.");
                                }
                            };
                            reader.readAsText(file);
                        }
                    }} accept=".json" className="hidden" />
                    <input type="file" ref={csvInputRef} onChange={handleImportCSV} accept=".csv" className="hidden" />

                    <div className="flex items-center bg-gray-100 dark:bg-gray-700 rounded-xl p-0.5">
                        <button
                            onClick={() => fileInputRef.current?.click()}
                            className="p-1.5 text-gray-600 dark:text-gray-300 hover:text-indigo-600 hover:bg-white dark:hover:bg-gray-800 rounded-lg transition-colors"
                            title="Import JSON"
                        >
                            <FileJson className="w-4 h-4" />
                        </button>
                        <button
                            onClick={() => csvInputRef.current?.click()}
                            className="p-1.5 text-gray-600 dark:text-gray-300 hover:text-indigo-600 hover:bg-white dark:hover:bg-gray-800 rounded-lg transition-colors"
                            title="Import CSV"
                        >
                            <FileSpreadsheet className="w-4 h-4" />
                        </button>
                        <button
                            onClick={handleExportJSON}
                            className="p-1.5 text-gray-600 dark:text-gray-300 hover:text-indigo-600 hover:bg-white dark:hover:bg-gray-800 rounded-lg transition-colors"
                            title="Export JSON"
                        >
                            <Download className="w-4 h-4" />
                        </button>
                    </div>

                    {!hideSaveButton && !readOnly && (
                        <button
                            onClick={handleSave}
                            disabled={isSaving}
                            className="px-4 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold flex items-center gap-1.5 shadow-sm transition-all disabled:opacity-50"
                        >
                            <Save className="w-3.5 h-3.5" />
                            {isSaving ? 'Saving...' : 'Save Glossary'}
                        </button>
                    )}
                </div>
            </div>

            {/* Type Categories & Term Actions Sub-Bar */}
            <div className="px-4 py-2.5 border-b border-gray-100 dark:border-gray-700 flex flex-wrap items-center justify-between gap-3 bg-white dark:bg-gray-800">
                <div className="flex items-center gap-1.5 flex-wrap">
                    <span className="text-[11px] font-bold text-gray-400 uppercase tracking-wider mr-1">Type:</span>
                    {['All', ...allTypes].map(type => (
                        <button
                            key={type}
                            onClick={() => setTypeFilter(type)}
                            className={`px-2.5 py-1 rounded-lg text-xs font-semibold transition-all ${
                                typeFilter === type
                                    ? 'bg-indigo-50 dark:bg-indigo-950/60 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800'
                                    : 'text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/50'
                            }`}
                        >
                            {type}
                        </button>
                    ))}
                </div>

                {!readOnly && (
                    <div className="flex items-center gap-2">
                        <button
                            onClick={handleAddTerm}
                            className="px-3 py-1.5 bg-indigo-50 dark:bg-indigo-950/50 hover:bg-indigo-100 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-800 rounded-xl text-xs font-bold flex items-center gap-1.5 transition-colors"
                        >
                            <Plus className="w-3.5 h-3.5" /> Add Term
                        </button>
                        {suppressedTerms.length > 0 && (
                            <button
                                onClick={() => setShowSuppressed(!showSuppressed)}
                                className={`px-2.5 py-1.5 rounded-xl text-xs font-semibold border flex items-center gap-1.5 transition-all ${
                                    showSuppressed
                                        ? 'bg-amber-100 border-amber-300 text-amber-800 dark:bg-amber-950/40 dark:border-amber-800 dark:text-amber-300'
                                        : 'bg-gray-50 dark:bg-gray-750 border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300'
                                }`}
                            >
                                <EyeOff className="w-3.5 h-3.5 text-amber-500" />
                                <span>Suppressed ({suppressedTerms.length})</span>
                            </button>
                        )}
                    </div>
                )}
            </div>

            {/* Suppressed Terms Drawer */}
            <AnimatePresence>
                {showSuppressed && suppressedTerms.length > 0 && (
                    <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: 'auto', opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        className="bg-amber-50/70 dark:bg-amber-950/20 border-b border-amber-200 dark:border-amber-900/50 p-4"
                    >
                        <div className="flex items-center justify-between mb-2">
                            <h4 className="text-xs font-bold text-amber-900 dark:text-amber-200 flex items-center gap-2">
                                <EyeOff className="w-4 h-4 text-amber-600" />
                                Suppressed Parent Universe Terms for this Project
                            </h4>
                            <span className="text-[11px] text-amber-700 dark:text-amber-400">
                                Click Restore on any term to inherit it again.
                            </span>
                        </div>
                        <div className="flex flex-wrap gap-2">
                            {suppressedTerms.map(tName => (
                                <div
                                    key={tName}
                                    className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white dark:bg-gray-800 border border-amber-200 dark:border-amber-900 text-xs font-semibold text-gray-700 dark:text-gray-300 shadow-sm"
                                >
                                    <span className="line-through text-gray-400">{tName}</span>
                                    <button
                                        onClick={() => handleUnsuppressTerm(tName)}
                                        className="text-indigo-600 dark:text-indigo-400 hover:underline text-[11px] font-bold"
                                    >
                                        Restore
                                    </button>
                                </div>
                            ))}
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Bulk Actions Floating Bar */}
            <AnimatePresence>
                {selectedTerms.size > 0 && !readOnly && (
                    <motion.div
                        initial={{ opacity: 0, y: -8 }}
                        animate={{ opacity: 1, y: 0 }}
                        exit={{ opacity: 0, y: -8 }}
                        className="p-3 bg-indigo-600 text-white flex items-center justify-between gap-4 shadow-lg"
                    >
                        <div className="flex items-center gap-3">
                            <span className="font-bold text-xs bg-indigo-800/80 px-2.5 py-1 rounded-lg">
                                {selectedTerms.size} selected
                            </span>
                            <button
                                onClick={() => setSelectedTerms(new Set())}
                                className="text-xs text-indigo-200 hover:text-white underline"
                            >
                                Deselect all
                            </button>
                        </div>

                        <div className="flex items-center gap-2">
                            {parentProject && (
                                <button
                                    onClick={handleBulkPromote}
                                    className="px-3 py-1.5 bg-white/10 hover:bg-white/20 text-white rounded-lg text-xs font-bold flex items-center gap-1.5 transition-colors"
                                >
                                    <Globe className="w-3.5 h-3.5 text-amber-300" />
                                    <span>Promote to Universe</span>
                                </button>
                            )}
                            <button
                                onClick={handleBulkRevert}
                                className="px-3 py-1.5 bg-white/10 hover:bg-white/20 text-white rounded-lg text-xs font-bold flex items-center gap-1.5 transition-colors"
                            >
                                <RotateCcw className="w-3.5 h-3.5 text-blue-300" />
                                <span>Revert Overrides</span>
                            </button>
                            <button
                                onClick={handleBulkSuppress}
                                className="px-3 py-1.5 bg-white/10 hover:bg-white/20 text-white rounded-lg text-xs font-bold flex items-center gap-1.5 transition-colors"
                            >
                                <EyeOff className="w-3.5 h-3.5 text-amber-300" />
                                <span>Suppress</span>
                            </button>
                            <button
                                onClick={handleBulkDelete}
                                className="px-3 py-1.5 bg-rose-500 hover:bg-rose-600 text-white rounded-lg text-xs font-bold flex items-center gap-1.5 transition-colors"
                            >
                                <Trash2 className="w-3.5 h-3.5" />
                                <span>Delete</span>
                            </button>
                        </div>
                    </motion.div>
                )}
            </AnimatePresence>

            {/* Glossary Table */}
            <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                    <thead>
                        <tr className="border-b border-gray-100 dark:border-gray-700 bg-gray-50/80 dark:bg-gray-850/80 text-[11px] font-bold text-gray-500 dark:text-gray-400 uppercase tracking-wider">
                            {!readOnly && (
                                <th className="p-3 w-10 text-center">
                                    <input
                                        type="checkbox"
                                        checked={filteredTerms.length > 0 && filteredTerms.every(t => selectedTerms.has(t.term._uid))}
                                        onChange={toggleSelectAllFiltered}
                                        className="rounded text-indigo-600 focus:ring-indigo-500 w-3.5 h-3.5 border-gray-300"
                                    />
                                </th>
                            )}
                            <th className="p-3 min-w-[180px]">Source Term</th>
                            <th className="p-3 min-w-[200px]">Translation</th>
                            <th className="p-3 w-32">Type</th>
                            <th className="p-3 w-28">Gender</th>
                            <th className="p-3 min-w-[200px]">Notes & Description</th>
                            <th className="p-3 w-28 text-center">Actions</th>
                        </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100 dark:divide-gray-750 text-xs">
                        {filteredTerms.length === 0 ? (
                            <tr>
                                <td colSpan={7} className="text-center py-12 text-gray-400">
                                    <Book className="w-8 h-8 mx-auto mb-2 opacity-40" />
                                    <p className="font-semibold text-gray-500 dark:text-gray-400">No glossary terms match your filter</p>
                                    <p className="text-[11px] mt-0.5">Try clearing your search or adding new terms.</p>
                                </td>
                            </tr>
                        ) : (
                            filteredTerms.map(({ term, index }) => {
                                const isSelected = selectedTerms.has(term._uid);
                                const isModifiedDiffOpen = expandedDiffUid === term._uid;
                                const parentDef = term.parent_term;

                                return (
                                    <React.Fragment key={term._uid}>
                                        <tr className={`transition-colors ${
                                            isSelected
                                                ? 'bg-indigo-50/40 dark:bg-indigo-950/20'
                                                : term.upstream_modified
                                                    ? 'bg-amber-50/30 dark:bg-amber-950/15 hover:bg-amber-50/50'
                                                    : 'hover:bg-gray-50/60 dark:hover:bg-gray-750/40'
                                        }`}>
                                            {!readOnly && (
                                                <td className="p-3 text-center">
                                                    <input
                                                        type="checkbox"
                                                        checked={isSelected}
                                                        onChange={() => toggleSelectTerm(term._uid)}
                                                        className="rounded text-indigo-600 focus:ring-indigo-500 w-3.5 h-3.5 border-gray-300"
                                                    />
                                                </td>
                                            )}

                                            {/* Term Name & Origin Badges */}
                                            <td className="p-3">
                                                <div className="flex flex-col gap-1">
                                                    <input
                                                        type="text"
                                                        value={term.term || ''}
                                                        disabled={readOnly}
                                                        onChange={(e) => handleTermChange(index, 'term', e.target.value)}
                                                        className="w-full font-bold text-gray-900 dark:text-white bg-transparent border-0 focus:ring-1 focus:ring-indigo-500 rounded px-1.5 py-0.5"
                                                        placeholder="English Term"
                                                    />
                                                    <div className="flex items-center gap-1.5 flex-wrap pl-1.5">
                                                        {term.inherited ? (
                                                            <span className="text-[10px] font-semibold px-1.5 py-0.2 rounded bg-purple-50 dark:bg-purple-950 text-purple-700 dark:text-purple-300 border border-purple-200 dark:border-purple-900/60 flex items-center gap-1">
                                                                <Globe className="w-2.5 h-2.5" />
                                                                {term.inherited_from || 'Universe'}
                                                            </span>
                                                        ) : term.is_override ? (
                                                            <span className="text-[10px] font-semibold px-1.5 py-0.2 rounded bg-amber-50 dark:bg-amber-950 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-900/60">
                                                                Local Override
                                                            </span>
                                                        ) : (
                                                            <span className="text-[10px] font-semibold px-1.5 py-0.2 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
                                                                Project Only
                                                            </span>
                                                        )}

                                                        {term.upstream_modified && (
                                                            <button
                                                                onClick={() => setExpandedDiffUid(isModifiedDiffOpen ? null : term._uid)}
                                                                className="text-[10px] font-bold px-1.5 py-0.2 rounded bg-amber-500 text-white flex items-center gap-1 animate-pulse shadow-sm"
                                                                title="Parent definition changed upstream. Click to compare."
                                                            >
                                                                <AlertCircle className="w-2.5 h-2.5" />
                                                                Upstream Changed
                                                                {isModifiedDiffOpen ? <ChevronUp className="w-2.5 h-2.5" /> : <ChevronDown className="w-2.5 h-2.5" />}
                                                            </button>
                                                        )}
                                                    </div>
                                                </div>
                                            </td>

                                            {/* Translation */}
                                            <td className="p-3">
                                                <input
                                                    type="text"
                                                    value={term.translation || ''}
                                                    disabled={readOnly}
                                                    onChange={(e) => handleTermChange(index, 'translation', e.target.value)}
                                                    className="w-full font-semibold text-indigo-600 dark:text-indigo-400 bg-transparent border-0 focus:ring-1 focus:ring-indigo-500 rounded px-1.5 py-0.5"
                                                    placeholder="Target Translation"
                                                />
                                            </td>

                                            {/* Type Dropdown */}
                                            <td className="p-3">
                                                <select
                                                    value={term.type || 'other'}
                                                    disabled={readOnly}
                                                    onChange={(e) => handleTermChange(index, 'type', e.target.value)}
                                                    className="w-full text-xs font-semibold rounded-lg border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-750 text-gray-800 dark:text-gray-200 py-1 px-2 focus:ring-1 focus:ring-indigo-500"
                                                >
                                                    {allTypes.map(t => (
                                                        <option key={t} value={t.toLowerCase()}>{t}</option>
                                                    ))}
                                                </select>
                                            </td>

                                            {/* Gender Dropdown */}
                                            <td className="p-3">
                                                <select
                                                    value={term.gender || 'neuter'}
                                                    disabled={readOnly}
                                                    onChange={(e) => handleTermChange(index, 'gender', e.target.value)}
                                                    className="w-full text-xs rounded-lg border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-750 text-gray-800 dark:text-gray-200 py-1 px-2 focus:ring-1 focus:ring-indigo-500"
                                                >
                                                    <option value="masculine">Masc (αρσ)</option>
                                                    <option value="feminine">Fem (θηλ)</option>
                                                    <option value="neuter">Neut (ουδ)</option>
                                                    <option value="n/a">N/A</option>
                                                </select>
                                            </td>

                                            {/* Description & Flags */}
                                            <td className="p-3">
                                                <div className="flex flex-col gap-1">
                                                    <input
                                                        type="text"
                                                        value={term.description || ''}
                                                        disabled={readOnly}
                                                        onChange={(e) => handleTermChange(index, 'description', e.target.value)}
                                                        className="w-full text-xs text-gray-600 dark:text-gray-300 bg-transparent border-0 focus:ring-1 focus:ring-indigo-500 rounded px-1.5 py-0.5"
                                                        placeholder="Context notes..."
                                                    />
                                                    <div className="flex items-center gap-3 pl-1.5 text-[11px] text-gray-400">
                                                        <label className="flex items-center gap-1 cursor-pointer select-none">
                                                            <input
                                                                type="checkbox"
                                                                checked={term.case_sensitive ?? false}
                                                                disabled={readOnly}
                                                                onChange={(e) => handleTermChange(index, 'case_sensitive', e.target.checked)}
                                                                className="rounded text-indigo-600 w-3 h-3"
                                                            />
                                                            <span>Case Sensitive</span>
                                                        </label>
                                                        <label className="flex items-center gap-1 cursor-pointer select-none">
                                                            <input
                                                                type="checkbox"
                                                                checked={term.keep_original ?? false}
                                                                disabled={readOnly}
                                                                onChange={(e) => handleTermChange(index, 'keep_original', e.target.checked)}
                                                                className="rounded text-indigo-600 w-3 h-3"
                                                            />
                                                            <span>Keep Original</span>
                                                        </label>
                                                    </div>
                                                </div>
                                            </td>

                                            {/* Actions */}
                                            <td className="p-3 text-center">
                                                <div className="flex items-center justify-center gap-1">
                                                    {/* Promote to Universe */}
                                                    {!term.inherited && parentProject && !readOnly && (
                                                        <button
                                                            onClick={() => handleInlinePromote(term)}
                                                            disabled={promotingTerm === term.term}
                                                            className="p-1.5 text-purple-600 hover:bg-purple-50 dark:hover:bg-purple-950/50 rounded-lg transition-colors"
                                                            title={`Promote to ${parentProject} universe`}
                                                        >
                                                            <ArrowUpCircle className={`w-4 h-4 ${promotingTerm === term.term ? 'animate-spin' : ''}`} />
                                                        </button>
                                                    )}

                                                    {/* Revert override */}
                                                    {term.is_override && !readOnly && (
                                                        <button
                                                            onClick={() => handleRevertToUniverse(index)}
                                                            className="p-1.5 text-blue-600 hover:bg-blue-50 dark:hover:bg-blue-950/50 rounded-lg transition-colors"
                                                            title="Revert override to parent universe definition"
                                                        >
                                                            <RotateCcw className="w-4 h-4" />
                                                        </button>
                                                    )}

                                                    {/* Delete / Suppress */}
                                                    {!readOnly && (
                                                        <button
                                                            onClick={() => handleRemoveTerm(index)}
                                                            className="p-1.5 text-gray-400 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950/50 rounded-lg transition-colors"
                                                            title={term.inherited ? "Suppress from this show" : "Delete term"}
                                                        >
                                                            {term.inherited ? <EyeOff className="w-4 h-4" /> : <Trash2 className="w-4 h-4" />}
                                                        </button>
                                                    )}
                                                </div>
                                            </td>
                                        </tr>

                                        {/* Upstream Diff Accordion Row */}
                                        {isModifiedDiffOpen && parentDef && (
                                            <tr className="bg-amber-50/60 dark:bg-amber-950/30 border-b border-amber-200 dark:border-amber-900/60">
                                                <td colSpan={readOnly ? 6 : 7} className="p-4">
                                                    <div className="flex flex-col md:flex-row items-start md:items-center justify-between gap-4">
                                                        <div className="space-y-1">
                                                            <div className="text-xs font-bold text-amber-900 dark:text-amber-200 flex items-center gap-1.5">
                                                                <AlertCircle className="w-4 h-4 text-amber-600" />
                                                                Upstream Parent Definition:
                                                            </div>
                                                            <div className="text-xs text-amber-800 dark:text-amber-300 font-mono bg-white/70 dark:bg-gray-850 p-2 rounded-lg border border-amber-200 dark:border-amber-900">
                                                                <strong>{parentDef.term}</strong> → <span className="text-indigo-600 dark:text-indigo-400 font-bold">{parentDef.translation}</span> | Type: {parentDef.type} | Gender: {parentDef.gender} | Notes: {parentDef.description || 'none'}
                                                            </div>
                                                        </div>

                                                        <div className="flex items-center gap-2">
                                                            <button
                                                                onClick={() => handleAdoptUpstream(index)}
                                                                className="px-3 py-1.5 bg-amber-600 hover:bg-amber-700 text-white rounded-lg text-xs font-bold flex items-center gap-1 shadow-sm transition-all"
                                                            >
                                                                <Check className="w-3.5 h-3.5" /> Adopt Upstream
                                                            </button>
                                                            <button
                                                                onClick={() => handleRevertToUniverse(index)}
                                                                className="px-3 py-1.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-bold flex items-center gap-1 shadow-sm transition-all"
                                                            >
                                                                <RotateCcw className="w-3.5 h-3.5" /> Revert to Parent
                                                            </button>
                                                            <button
                                                                onClick={() => handleSilenceUpstreamDiff(index)}
                                                                className="px-3 py-1.5 bg-white dark:bg-gray-800 hover:bg-gray-100 text-gray-700 dark:text-gray-300 border border-gray-200 dark:border-gray-700 rounded-lg text-xs font-semibold transition-colors"
                                                            >
                                                                Keep Local
                                                            </button>
                                                        </div>
                                                    </div>
                                                </td>
                                            </tr>
                                        )}
                                    </React.Fragment>
                                );
                            })
                        )}
                    </tbody>
                </table>
            </div>

            {/* Term Harvester Modal */}
            <TermHarvestModal
                isOpen={harvestModalOpen}
                onClose={() => setHarvestModalOpen(false)}
                projectName={projectName}
                parentProjectName={parentProject}
                onTermsAdded={handleHarvestedTermsAdded}
            />
        </div>
    );
};

export default GlossaryEditor;
