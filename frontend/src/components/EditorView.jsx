import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Download, Book, X } from 'lucide-react';
import GlossaryEditor from './GlossaryEditor';

const EditorView = ({ data, glossary, onRetranslate, onUpdateGlossary, onDataUpdate, isLoading, project, projectName, episodeName, originalFilename }) => {
    const [showGlossary, setShowGlossary] = useState(false);

    const handleTranslationChange = (id, newText) => {
        if (onDataUpdate) {
            const newData = data.map(item =>
                item.id === id ? { ...item, translated: newText } : item
            );
            onDataUpdate(newData);
        }
    };

    const handleDownload = () => {
        let srtContent = "";
        data.forEach((item, index) => {
            const indexNum = index + 1;
            const timecode = item.timecode;
            // Use translated text if available, otherwise fallback to original or empty string
            const text = item.translated || item.original || "";

            srtContent += `${indexNum}\n${timecode}\n${text}\n\n`;
        });

        // Language code mapping
        const LANGUAGE_CODES = {
            "Greek": "el",
            "English": "en",
            "Spanish": "es",
            "French": "fr",
            "German": "de",
            "Italian": "it",
            "Portuguese": "pt",
            "Russian": "ru",
            "Japanese": "ja",
            "Korean": "ko",
            "Chinese": "zh"
        };

        const targetLang = project?.target_language || "English";
        const langCode = LANGUAGE_CODES[targetLang] || "en";

        // Generate filename with language code
        let filename = originalFilename || episodeName || "subtitle";
        if (filename.endsWith(".en.srt")) {
            filename = filename.replace(".en.srt", `.${langCode}.srt`);
        } else if (filename.endsWith(".srt")) {
            filename = filename.replace(".srt", `.${langCode}.srt`);
        } else {
            filename = `${filename}.${langCode}.srt`;
        }

        const element = document.createElement("a");
        const file = new Blob([srtContent], { type: 'text/plain' });
        element.href = URL.createObjectURL(file);
        element.download = filename;
        document.body.appendChild(element);
        element.click();
        document.body.removeChild(element);
    };

    return (
        <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="h-screen flex flex-col bg-slate-50"
        >
            {/* Header */}
            <header className="bg-white border-b border-gray-200 px-6 py-4 flex justify-between items-center shadow-sm z-10">
                <div className="flex items-center gap-4">
                    <h1 className="text-xl font-bold text-gray-800">Translation Editor</h1>
                    <button
                        onClick={() => setShowGlossary(true)}
                        className="flex items-center gap-2 px-3 py-1.5 bg-indigo-50 text-indigo-700 rounded-lg hover:bg-indigo-100 transition-colors text-sm font-medium"
                    >
                        <Book className="w-4 h-4" />
                        Glossary
                    </button>
                </div>
                <div className="flex items-center gap-3">
                    <button
                        onClick={handleDownload}
                        className="flex items-center gap-2 px-4 py-2 bg-gray-900 text-white rounded-lg hover:bg-gray-800 transition-colors shadow-lg shadow-gray-900/20"
                    >
                        <Download className="w-4 h-4" />
                        Export
                    </button>
                </div>
            </header>

            {/* Main Content - Side by Side Grid */}
            <div className="flex-1 overflow-y-auto p-6">
                <div className="max-w-7xl mx-auto bg-white rounded-xl shadow-sm border border-gray-200 overflow-hidden">
                    {/* Table Header */}
                    <div className="grid grid-cols-[100px_1fr_1fr] bg-gray-50 border-b border-gray-200 font-semibold text-gray-500 text-sm sticky top-0 z-10">
                        <div className="p-4 border-r border-gray-200">Timecode</div>
                        <div className="p-4 border-r border-gray-200">Original</div>
                        <div className="p-4">Translated</div>
                    </div>

                    {/* Table Body */}
                    <div className="divide-y divide-gray-100">
                        {data.map((item) => (
                            <div key={item.id} className="grid grid-cols-[100px_1fr_1fr] hover:bg-gray-50/50 transition-colors group">
                                <div className="p-4 text-xs font-mono text-gray-400 border-r border-gray-100 flex items-center">
                                    {item.timecode}
                                </div>
                                <div className="p-4 text-gray-700 border-r border-gray-100 leading-relaxed">
                                    {item.original}
                                </div>
                                <div className="p-0 bg-indigo-50/10 group-hover:bg-indigo-50/30 transition-colors relative">
                                    <textarea
                                        value={item.translated || ''}
                                        onChange={(e) => handleTranslationChange(item.id, e.target.value)}
                                        placeholder={isLoading ? "Translating..." : "Not translated"}
                                        disabled={isLoading}
                                        className="w-full h-full min-h-[80px] p-4 bg-transparent border-none outline-none resize-none text-gray-900 font-medium leading-relaxed focus:bg-white/50 focus:ring-2 focus:ring-indigo-500/20 transition-all placeholder:italic placeholder:text-gray-300 disabled:opacity-50"
                                    />
                                    {isLoading && !item.translated && (
                                        <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
                                            <span className="text-indigo-400 italic animate-pulse">Translating...</span>
                                        </div>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </div>

            {/* Glossary Modal */}
            <AnimatePresence>
                {showGlossary && (
                    <motion.div
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0 }}
                        className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4"
                    >
                        <motion.div
                            initial={{ scale: 0.95, opacity: 0 }}
                            animate={{ scale: 1, opacity: 1 }}
                            exit={{ scale: 0.95, opacity: 0 }}
                            className="bg-white rounded-2xl shadow-2xl w-full max-w-5xl h-[90vh] overflow-hidden flex flex-col"
                        >
                            <div className="p-4 border-b border-gray-100 flex justify-between items-center bg-gray-50">
                                <h2 className="text-lg font-bold text-gray-800 flex items-center gap-2">
                                    <Book className="w-5 h-5 text-indigo-600" />
                                    Glossary & Context
                                </h2>
                                <button
                                    onClick={() => setShowGlossary(false)}
                                    className="p-2 hover:bg-gray-200 rounded-full transition-colors"
                                >
                                    <X className="w-5 h-5 text-gray-500" />
                                </button>
                            </div>

                            <div className="flex-1 overflow-hidden bg-gray-50">
                                <GlossaryEditor
                                    glossary={glossary}
                                    onSave={(updatedGlossary) => {
                                        if (onUpdateGlossary) {
                                            onUpdateGlossary(updatedGlossary);
                                        }
                                        setShowGlossary(false);
                                    }}
                                    onCancel={() => setShowGlossary(false)}
                                    isSaving={isLoading}
                                />
                            </div>
                        </motion.div>
                    </motion.div>
                )}
            </AnimatePresence>
        </motion.div>
    );
};

export default EditorView;
