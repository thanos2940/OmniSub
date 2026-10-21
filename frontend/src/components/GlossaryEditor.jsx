import { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Save, Sparkles, EyeOff, Globe, RotateCcw, Trash2 } from 'lucide-react';
import { Button } from './ui';
import { useToast } from '../context/ToastContext';
import TermHarvestModal from './TermHarvestModal';
import { useGlossaryState, stripUids } from './glossary/useGlossaryState';
import GlossaryFacetFilter from './glossary/GlossaryFacetFilter';
import GlossaryImportExport from './glossary/GlossaryImportExport';
import GlossaryTable from './glossary/GlossaryTable';

const GlossaryEditor = ({
    glossary,
    onSave,
    isSaving,
    readOnly = false,
    onChange,
    hideSaveButton = false,
    projectName = null,
    parentProject = null,
    suppressedTerms: initialSuppressed = [],
    embedded = false,
}) => {
    const toast = useToast();
    const [showSuppressed, setShowSuppressed] = useState(false);
    const [harvestModalOpen, setHarvestModalOpen] = useState(false);

    const {
        terms,
        setTerms,
        suppressedTerms,
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
    } = useGlossaryState({
        glossary,
        initialSuppressed,
        onChange,
        readOnly,
        projectName,
        parentProject,
        toast,
    });

    const defaultTypes = ['Person', 'Location', 'Item', 'Organization', 'Technique', 'Concept', 'Other'];
    const termTypes = [...new Set(terms.map(t => t.type || 'other'))].map(t => t.charAt(0).toUpperCase() + t.slice(1));
    const allTypes = [...new Set([...defaultTypes, ...termTypes])];

    const upstreamModifiedCount = terms.filter(t => t.upstream_modified).length;
    const inheritedCount = terms.filter(t => t.inherited).length;
    const overrideCount = terms.filter(t => t.is_override).length;

    const handleSave = () => {
        const cleanTerms = stripUids(terms);
        if (dirtyRef) dirtyRef.current = false;
        if (onSave) {
            onSave({ ...glossary, terms: cleanTerms }, false, suppressedTerms);
        }
    };

    return (
        <div className={embedded ? 'flex flex-col' : 'flex flex-col overflow-hidden rounded-xl border border-gray-200 bg-white dark:border-gray-750 dark:bg-gray-850'}>
            {/* Top Toolbar with Actions */}
            <div className="flex flex-wrap items-center justify-between gap-3 border-b border-gray-100 p-3 dark:border-gray-750 sm:px-4">
                <div className="flex items-center gap-2 flex-wrap">
                    {/* Harvest Dialogue Terms */}
                    <Button size="sm" icon={Sparkles} onClick={() => setHarvestModalOpen(true)} title="Scan translated dialogue for new names and terms">
                        Find terms in dialogue
                    </Button>

                    <GlossaryImportExport
                        glossary={glossary}
                        terms={terms}
                        setTerms={setTerms}
                        suppressedTerms={suppressedTerms}
                        projectName={projectName}
                        dirtyRef={dirtyRef}
                        toast={toast}
                    />
                </div>

                {!hideSaveButton && !readOnly && (
                    <Button variant="primary" size="sm" icon={Save} onClick={handleSave} loading={isSaving}>
                        {isSaving ? 'Saving…' : 'Save glossary'}
                    </Button>
                )}
            </div>

            {/* Facet Filter Bar */}
            <GlossaryFacetFilter
                searchFilter={searchFilter}
                setSearchFilter={setSearchFilter}
                sourceFilter={sourceFilter}
                setSourceFilter={setSourceFilter}
                typeFilter={typeFilter}
                setTypeFilter={setTypeFilter}
                allTypes={allTypes}
                totalTermsCount={terms.length}
                inheritedCount={inheritedCount}
                overrideCount={overrideCount}
                upstreamModifiedCount={upstreamModifiedCount}
                suppressedCount={suppressedTerms.length}
                showSuppressed={showSuppressed}
                setShowSuppressed={setShowSuppressed}
                onAddTerm={handleAddTerm}
                readOnly={readOnly}
            />

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
                                        type="button"
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
                                type="button"
                                onClick={() => setSelectedTerms(new Set())}
                                className="text-xs text-indigo-200 hover:text-white underline"
                            >
                                Deselect all
                            </button>
                        </div>

                        <div className="flex items-center gap-2">
                            {parentProject && (
                                <button
                                    type="button"
                                    onClick={handleBulkPromote}
                                    className="px-3 py-1.5 bg-white/10 hover:bg-white/20 text-white rounded-lg text-xs font-bold flex items-center gap-1.5 transition-colors"
                                >
                                    <Globe className="w-3.5 h-3.5 text-amber-300" />
                                    <span>Promote to Universe</span>
                                </button>
                            )}
                            <button
                                type="button"
                                onClick={handleBulkRevert}
                                className="px-3 py-1.5 bg-white/10 hover:bg-white/20 text-white rounded-lg text-xs font-bold flex items-center gap-1.5 transition-colors"
                            >
                                <RotateCcw className="w-3.5 h-3.5 text-blue-300" />
                                <span>Revert Overrides</span>
                            </button>
                            <button
                                type="button"
                                onClick={handleBulkSuppress}
                                className="px-3 py-1.5 bg-white/10 hover:bg-white/20 text-white rounded-lg text-xs font-bold flex items-center gap-1.5 transition-colors"
                            >
                                <EyeOff className="w-3.5 h-3.5 text-amber-300" />
                                <span>Suppress</span>
                            </button>
                            <button
                                type="button"
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
            <GlossaryTable
                filteredTerms={filteredTerms}
                selectedTerms={selectedTerms}
                toggleSelectTerm={toggleSelectTerm}
                toggleSelectAllFiltered={toggleSelectAllFiltered}
                expandedDiffUid={expandedDiffUid}
                setExpandedDiffUid={setExpandedDiffUid}
                handleTermChange={handleTermChange}
                handleAdoptUpstream={handleAdoptUpstream}
                handleRevertToUniverse={handleRevertToUniverse}
                handleSilenceUpstreamDiff={handleSilenceUpstreamDiff}
                handleInlinePromote={handleInlinePromote}
                handleRemoveTerm={handleRemoveTerm}
                promotingTerm={promotingTerm}
                parentProject={parentProject}
                readOnly={readOnly}
                allTypes={allTypes}
            />

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
