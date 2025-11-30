import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { CheckCircle, Loader2, AlertCircle, Download, Book } from 'lucide-react';
import GlossaryEditor from './GlossaryEditor';

const TranslationView = ({ originalLines, translatedLines, isTranslating, progress, glossary, onGlossarySave }) => {
    // originalLines: Array of { index, start, end, text }
    // translatedLines: Array of { index, translated } (or merged into originalLines)
    const [isGlossaryOpen, setIsGlossaryOpen] = useState(false);

    const handleDownloadSRT = () => {
        let srtContent = '';
        // Use translatedLines if available, otherwise fall back to originalLines (though translatedLines should contain everything after translation)
        const linesToExport = translatedLines.length > 0 ? translatedLines : originalLines;

        linesToExport.forEach((line) => {
            srtContent += `${line.id}\n`;
            srtContent += `${line.timecode}\n`;
            srtContent += `${line.translated || line.original}\n\n`;
        });

        const blob = new Blob([srtContent], { type: 'text/plain' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'translated_subtitles.srt';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const handleSaveGlossary = (updatedGlossary) => {
        onGlossarySave(updatedGlossary);
        setIsGlossaryOpen(false);
    };

    return (
        <>
            <div className="h-full flex flex-col bg-white/50 backdrop-blur-xl rounded-3xl shadow-xl border border-white/60 overflow-hidden relative z-0">
                {/* Header */}
                <div className="p-6 border-b border-gray-200/50 bg-white/40 flex justify-between items-center">
                    <div>
                        <h2 className="text-2xl font-bold text-gray-800 flex items-center gap-2">
                            {isTranslating ? <Loader2 className="w-6 h-6 animate-spin text-indigo-600" /> : <CheckCircle className="w-6 h-6 text-green-500" />}
                            Translation Workspace
                        </h2>
                        <p className="text-sm text-gray-500">
                            {isTranslating ? `Translating... ${progress}%` : 'Translation complete. Review your subtitles.'}
                        </p>
                    </div>
                    <div className="flex gap-3 items-center">
                        <button
                            onClick={() => setIsGlossaryOpen(true)}
                            className="px-4 py-2 bg-white border border-gray-200 text-gray-700 rounded-xl flex items-center gap-2 hover:bg-gray-50 transition-all shadow-sm font-medium"
                        >
                            <Book className="w-4 h-4" /> Glossary
                        </button>

                        {isTranslating && (
                            <div className="px-4 py-1 bg-indigo-100 text-indigo-700 rounded-full text-sm font-medium animate-pulse">
                                AI Processing
                            </div>
                        )}
                        {!isTranslating && (
                            <button
                                onClick={handleDownloadSRT}
                                className="px-4 py-2 bg-green-600 text-white rounded-xl flex items-center gap-2 hover:bg-green-700 transition-all shadow-lg font-medium"
                            >
                                <Download className="w-4 h-4" /> Download SRT
                            </button>
                        )}
                    </div>
                </div>

                {/* Content Area */}
                <div className="flex-1 overflow-y-auto p-6 custom-scrollbar">
                    <div className="space-y-4">
                        {originalLines.map((line, idx) => {
                            const translatedLine = translatedLines.find(l => l.id === line.id);
                            const isLoaded = !!translatedLine;

                            return (
                                <motion.div
                                    key={line.id}
                                    initial={{ opacity: 0, y: 10 }}
                                    animate={{ opacity: 1, y: 0 }}
                                    transition={{ delay: idx * 0.05 }}
                                    className="grid grid-cols-1 md:grid-cols-2 gap-4 p-4 bg-white rounded-xl border border-gray-100 shadow-sm hover:shadow-md transition-all"
                                >
                                    {/* Original */}
                                    <div className="text-gray-500 text-sm font-mono border-r border-gray-100 pr-4">
                                        <div className="mb-1 text-xs text-gray-400 select-none">{line.timecode}</div>
                                        <p>{line.original}</p>
                                    </div>

                                    {/* Translated */}
                                    <div className="text-gray-800 font-medium pl-2 relative min-h-[3rem] flex items-center">
                                        {isLoaded ? (
                                            <motion.p
                                                initial={{ opacity: 0 }}
                                                animate={{ opacity: 1 }}
                                            >
                                                {translatedLine.translated}
                                            </motion.p>
                                        ) : (
                                            <div className="flex items-center gap-2 text-indigo-400 text-sm italic">
                                                <Loader2 className="w-3 h-3 animate-spin" /> Translating...
                                            </div>
                                        )}
                                    </div>
                                </motion.div>
                            );
                        })}
                    </div>
                </div>
            </div>

            {/* Glossary Modal */}
            <AnimatePresence>
                {isGlossaryOpen && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4"
                    >
                        <motion.div
                            initial={{ scale: 0.9, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={{ scale: 0.9, opacity: 0 }}
                            className="w-full max-w-5xl h-[85vh] bg-white rounded-3xl shadow-2xl overflow-hidden"
                        >
                            <GlossaryEditor
                                glossary={glossary}
                                onSave={handleSaveGlossary}
                                onCancel={() => setIsGlossaryOpen(false)}
                                isSaving={false}
                            />
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>
        </>
    );
};

export default TranslationView;
