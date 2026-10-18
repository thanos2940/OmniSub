import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { api } from '../../api';

let _uidCounter = 0;
export const ensureUids = (arr) => (arr || []).map(t => (t && t._uid != null) ? t : { ...t, _uid: `t${Date.now()}_${_uidCounter++}` });
export const stripUids = (arr) => (arr || []).map(t => {
    if (!t || typeof t !== 'object') return t;
    const copy = { ...t };
    delete copy._uid;
    return copy;
});

export const SOURCE_FILTERS = {
    ALL: 'all',
    INHERITED: 'inherited',
    OVERRIDE: 'override',
    PROJECT_ONLY: 'project_only',
    UPSTREAM_MODIFIED: 'upstream_modified',
    SUPPRESSED: 'suppressed',
};

export const useGlossaryState = ({
    glossary,
    initialSuppressed = [],
    onChange,
    readOnly = false,
    projectName = null,
    parentProject = null,
    toast = null,
}) => {
    const [terms, setTerms] = useState(() => ensureUids(glossary?.terms || []));
    const [suppressedTerms, setSuppressedTerms] = useState(() => initialSuppressed || []);
    const [selectedTerms, setSelectedTerms] = useState(new Set());
    const [promotingTerm, setPromotingTerm] = useState(null);
    const [expandedDiffUid, setExpandedDiffUid] = useState(null);

    // Filters
    const [searchFilter, setSearchFilter] = useState('');
    const [sourceFilter, setSourceFilter] = useState(SOURCE_FILTERS.ALL);
    const [typeFilter, setTypeFilter] = useState('All');

    const isInternalUpdateRef = useRef(false);
    const dirtyRef = useRef(false);
    const debounceTimerRef = useRef(null);
    const originalTermsMapRef = useRef(new Map());

    useEffect(() => {
        const map = new Map();
        (glossary?.terms || []).forEach(t => {
            if (t.term) {
                map.set(t.term.toLowerCase().trim ? t.term.toLowerCase().trim() : t.term.toLowerCase(), t);
            }
        });
        originalTermsMapRef.current = map;
    }, [glossary?.terms]);

    const prevGlossaryTermsRef = useRef(glossary?.terms);
    useEffect(() => {
        if (glossary?.terms !== prevGlossaryTermsRef.current) {
            setTerms(ensureUids(glossary?.terms || []));
            dirtyRef.current = false;
            prevGlossaryTermsRef.current = glossary?.terms;
        }
    }, [glossary?.terms]);

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
    }, [terms, suppressedTerms, onChange, glossary]);

    const handleTermChange = useCallback((index, field, value) => {
        if (readOnly) return;
        isInternalUpdateRef.current = true;
        dirtyRef.current = true;
        setTerms(prev => {
            const next = [...prev];
            next[index] = { ...next[index], [field]: value };
            return next;
        });
    }, [readOnly]);

    const handleRemoveTerm = useCallback((index) => {
        if (readOnly) return;
        setTerms(prev => {
            const targetTerm = prev[index];
            if (!targetTerm) return prev;

            if (targetTerm.inherited) {
                if (window.confirm(`"${targetTerm.term}" is inherited from parent universe (${targetTerm.inherited_from || 'Universe'}).\n\nSuppress this term for this project? (It won't affect the universe or sibling shows).`)) {
                    isInternalUpdateRef.current = true;
                    dirtyRef.current = true;
                    const normName = (targetTerm.term || '').trim();
                    if (normName) {
                        setSuppressedTerms(sup => sup.includes(normName) ? sup : [...sup, normName]);
                    }
                    if (toast?.info) toast.info(`"${targetTerm.term}" suppressed for this project.`);
                    return prev.filter((_, i) => i !== index);
                }
                return prev;
            } else {
                if (window.confirm(`Are you sure you want to remove "${targetTerm.term || 'this term'}"?`)) {
                    isInternalUpdateRef.current = true;
                    dirtyRef.current = true;
                    return prev.filter((_, i) => i !== index);
                }
                return prev;
            }
        });
    }, [readOnly, toast]);

    const handleUnsuppressTerm = useCallback((termName) => {
        isInternalUpdateRef.current = true;
        dirtyRef.current = true;
        setSuppressedTerms(prev => prev.filter(t => t.toLowerCase() !== termName.toLowerCase()));
        if (toast?.success) toast.success(`"${termName}" restored. Click Save to apply.`);
    }, [toast]);

    const handleRevertToUniverse = useCallback((index) => {
        setTerms(prev => {
            const targetTerm = prev[index];
            if (!targetTerm || !targetTerm.term) return prev;
            const parentDef = targetTerm.parent_term || originalTermsMapRef.current.get(targetTerm.term.toLowerCase());
            if (!parentDef) return prev;

            isInternalUpdateRef.current = true;
            dirtyRef.current = true;
            const next = [...prev];
            next[index] = {
                ...next[index],
                translation: parentDef.translation || '',
                type: parentDef.type || 'other',
                gender: parentDef.gender || 'neuter',
                description: parentDef.description || '',
                keep_original: parentDef.keep_original || false,
                case_sensitive: parentDef.case_sensitive !== false,
                is_override: false,
                upstream_modified: false,
            };
            if (toast?.info) toast.info(`Reverted "${targetTerm.term}" to universe standard.`);
            return next;
        });
    }, [toast]);

    const handleAdoptUpstream = useCallback((index) => {
        setTerms(prev => {
            const targetTerm = prev[index];
            if (!targetTerm || !targetTerm.parent_term) return prev;
            const parentDef = targetTerm.parent_term;

            isInternalUpdateRef.current = true;
            dirtyRef.current = true;
            const next = [...prev];
            next[index] = {
                ...next[index],
                translation: parentDef.translation || targetTerm.translation,
                type: parentDef.type || targetTerm.type,
                gender: parentDef.gender || targetTerm.gender,
                description: parentDef.description || targetTerm.description,
                keep_original: parentDef.keep_original ?? targetTerm.keep_original,
                case_sensitive: parentDef.case_sensitive ?? targetTerm.case_sensitive,
                upstream_modified: false,
            };
            if (toast?.success) toast.success(`Adopted upstream definition for "${targetTerm.term}".`);
            return next;
        });
    }, [toast]);

    const handleSilenceUpstreamDiff = useCallback((index) => {
        setTerms(prev => {
            const next = [...prev];
            next[index] = { ...next[index], upstream_modified: false };
            return next;
        });
    }, []);

    const handleInlinePromote = useCallback(async (term) => {
        if (!projectName || !parentProject) {
            if (toast?.error) toast.error("No parent universe linked to promote to.");
            return;
        }
        if (!term.term || !term.translation) {
            if (toast?.warning) toast.warning("Term and translation are required before promoting.");
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
            if (toast?.success) toast.success(`"${term.term}" promoted to ${parentProject} universe!`);
            setTerms(prev => prev.map(t => (t.term.toLowerCase() === term.term.toLowerCase())
                ? { ...t, inherited: true, inherited_from: parentProject, is_override: false, upstream_modified: false }
                : t
            ));
        } catch (err) {
            console.error("Failed to promote term", err);
            if (toast?.error) toast.error("Failed to promote term to universe.");
        } finally {
            setPromotingTerm(null);
        }
    }, [projectName, parentProject, toast]);

    const handleAddTerm = useCallback(() => {
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
        setTerms(prev => [newTerm, ...prev]);
    }, [typeFilter]);

    // Selection handlers
    const toggleSelectTerm = useCallback((uid) => {
        setSelectedTerms(prev => {
            const next = new Set(prev);
            if (next.has(uid)) next.delete(uid);
            else next.add(uid);
            return next;
        });
    }, []);

    // Filter computation
    const filteredTerms = useMemo(() => {
        return terms
            .map((term, index) => ({ term, index }))
            .filter(({ term }) => {
                const q = searchFilter.toLowerCase();
                const matchesSearch = !q ||
                    (term.term && term.term.toLowerCase().includes(q)) ||
                    (term.translation && term.translation.toLowerCase().includes(q)) ||
                    (term.description && term.description.toLowerCase().includes(q));

                let matchesSource = true;
                if (sourceFilter === SOURCE_FILTERS.INHERITED) matchesSource = !!term.inherited;
                else if (sourceFilter === SOURCE_FILTERS.OVERRIDE) matchesSource = !!term.is_override;
                else if (sourceFilter === SOURCE_FILTERS.PROJECT_ONLY) matchesSource = !term.inherited && !term.is_override;
                else if (sourceFilter === SOURCE_FILTERS.UPSTREAM_MODIFIED) matchesSource = !!term.upstream_modified;

                const matchesType = typeFilter === 'All' ||
                    (term.type && term.type.toLowerCase() === typeFilter.toLowerCase());

                return matchesSearch && matchesSource && matchesType;
            });
    }, [terms, searchFilter, sourceFilter, typeFilter]);

    const toggleSelectAllFiltered = useCallback(() => {
        const visibleUids = filteredTerms.map(t => t.term._uid);
        const allSelected = visibleUids.length > 0 && visibleUids.every(uid => selectedTerms.has(uid));
        setSelectedTerms(prev => {
            const next = new Set(prev);
            if (allSelected) {
                visibleUids.forEach(uid => next.delete(uid));
            } else {
                visibleUids.forEach(uid => next.add(uid));
            }
            return next;
        });
    }, [filteredTerms, selectedTerms]);

    // Bulk actions
    const handleBulkPromote = useCallback(async () => {
        if (!parentProject || !projectName) return;
        const selectedList = terms.filter(t => selectedTerms.has(t._uid) && t.term);
        if (selectedList.length === 0) return;

        try {
            await api.promoteTermsBatch(projectName, selectedList);
            if (toast?.success) toast.success(`Promoted ${selectedList.length} terms to ${parentProject} universe!`);
            setTerms(prev => prev.map(t => selectedTerms.has(t._uid)
                ? { ...t, inherited: true, inherited_from: parentProject, is_override: false, upstream_modified: false }
                : t
            ));
            setSelectedTerms(new Set());
        } catch {
            if (toast?.error) toast.error("Failed to batch promote terms.");
        }
    }, [parentProject, projectName, terms, selectedTerms, toast]);

    const handleBulkSuppress = useCallback(async () => {
        const selectedList = terms.filter(t => selectedTerms.has(t._uid) && t.term);
        if (selectedList.length === 0) return;

        const termNames = selectedList.map(t => t.term.trim());
        try {
            await api.suppressTermsBatch(projectName, termNames);
            setSuppressedTerms(prev => [...new Set([...prev, ...termNames])]);
            setTerms(prev => prev.filter(t => !selectedTerms.has(t._uid)));
            setSelectedTerms(new Set());
            if (toast?.success) toast.success(`Suppressed ${selectedList.length} terms.`);
        } catch {
            if (toast?.error) toast.error("Failed to batch suppress terms.");
        }
    }, [projectName, terms, selectedTerms, toast]);

    const handleBulkRevert = useCallback(async () => {
        const selectedList = terms.filter(t => selectedTerms.has(t._uid) && t.term);
        if (selectedList.length === 0) return;

        const termNames = selectedList.map(t => t.term.trim());
        try {
            await api.revertTermsBatch(projectName, termNames);
            if (toast?.success) toast.success(`Reverted overrides for ${selectedList.length} terms.`);
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
        } catch {
            if (toast?.error) toast.error("Failed to batch revert terms.");
        }
    }, [projectName, terms, selectedTerms, toast]);

    const handleBulkDelete = useCallback(() => {
        const count = selectedTerms.size;
        if (!count) return;
        if (window.confirm(`Delete / remove ${count} selected term(s)?`)) {
            isInternalUpdateRef.current = true;
            dirtyRef.current = true;
            setTerms(prev => prev.filter(t => !selectedTerms.has(t._uid)));
            setSelectedTerms(new Set());
            if (toast?.info) toast.info(`Removed ${count} terms.`);
        }
    }, [selectedTerms, toast]);

    const handleHarvestedTermsAdded = useCallback((newHarvestedTerms, directToUniverse = false) => {
        if (!newHarvestedTerms || newHarvestedTerms.length === 0) return;
        if (directToUniverse) {
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
    }, [parentProject]);

    return {
        terms,
        setTerms,
        suppressedTerms,
        setSuppressedTerms,
        selectedTerms,
        setSelectedTerms,
        promotingTerm,
        expandedDiffUid,
        setExpandedDiffUid,
        searchFilter,
        setSearchFilter,
        sourceFilter,
        setSourceFilter,
        typeFilter,
        setTypeFilter,
        filteredTerms,
        toggleSelectTerm,
        toggleSelectAllFiltered,
        handleTermChange,
        handleRemoveTerm,
        handleUnsuppressTerm,
        handleRevertToUniverse,
        handleAdoptUpstream,
        handleSilenceUpstreamDiff,
        handleInlinePromote,
        handleAddTerm,
        handleBulkPromote,
        handleBulkSuppress,
        handleBulkRevert,
        handleBulkDelete,
        handleHarvestedTermsAdded,
        dirtyRef,
    };
};
