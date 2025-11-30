import React, { useState } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { Upload, FileText, Settings, Play, CheckCircle, AlertCircle, ArrowRight, Film, Globe, Cpu, Layers, Home } from 'lucide-react';
import GlossaryEditor from './GlossaryEditor';
import TranslationView from './TranslationView';

const LandingPage = () => {
    const [files, setFiles] = useState([]);
    const [showName, setShowName] = useState('');
    const [season, setSeason] = useState('');
    const [episode, setEpisode] = useState('');
    const [targetLanguage, setTargetLanguage] = useState('');
    const [stage, setStage] = useState('setup'); // setup, scanning, glossary, translating, review
    const [scanModel, setScanModel] = useState('gemini-flash-lite-latest');
    const [translateModel, setTranslateModel] = useState('gemini-flash-latest');
    const [glossaryData, setGlossaryData] = useState(null);
    const [originalLines, setOriginalLines] = useState([]);
    const [translatedLines, setTranslatedLines] = useState([]);

    const handleFileChange = (e) => {
        const selectedFiles = Array.from(e.target.files);
        setFiles(selectedFiles);

        // Auto-detect show details from filename if possible
        if (selectedFiles.length > 0) {
            const filename = selectedFiles[0].name;
            // Simple regex for Show.Name.S01E01
            const match = filename.match(/(.+?)[._\s]S(\d+)E(\d+)/i);
            if (match) {
                setShowName(match[1].replace(/[._]/g, ' '));
                setSeason(match[2]);
                setEpisode(match[3]);
            }
        }
    };

    const startScan = async (e) => {
        e.preventDefault();
        setStage('scanning');

        const formData = new FormData();
        formData.append('file', files[0]); // Currently handling single file for MVP

        try {
            // 1. Upload & Parse
            const uploadRes = await fetch('http://localhost:8000/upload', {
                method: 'POST',
                body: formData
            });
            const uploadData = await uploadRes.json();
            setOriginalLines(uploadData.data); // Store parsed lines

            // 2. Scan for Glossary
            const scanRes = await fetch('http://localhost:8000/scan', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    show_name: showName,
                    model: scanModel,
                    target_language: targetLanguage
                })
            });
            const glossary = await scanRes.json();
            setGlossaryData(glossary);
            setStage('glossary');
        } catch (err) {
            console.error("Error during scan:", err);
            setStage('setup'); // Go back on error
            alert("Error scanning file. Check console.");
        }
    };

    const startTranslation = async (updatedGlossary) => {
        setGlossaryData(updatedGlossary);
        setStage('translating');

        try {
            const res = await fetch('http://localhost:8000/translate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    target_language: targetLanguage,
                    glossary: updatedGlossary,
                    model: translateModel
                })
            });
            const result = await res.json();
            setTranslatedLines(result);
            setStage('review');
        } catch (err) {
            console.error("Error during translation:", err);
            alert("Error translating file. Check console.");
            setStage('glossary');
        }
    };

    return (
        <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100 flex flex-col">
            {/* Navigation Bar */}
            <nav className="bg-white/80 backdrop-blur-md border-b border-gray-200 sticky top-0 z-50 px-6 py-4 flex justify-between items-center">
                <div className="flex items-center gap-3">
                    <div className="w-10 h-10 bg-gradient-to-br from-indigo-600 to-purple-600 rounded-xl flex items-center justify-center text-white shadow-lg">
                        <Film className="w-6 h-6" />
                    </div>
                    <h1 className="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-indigo-700 to-purple-700">
                        OmbiSub
                    </h1>
                </div>
                <div className="flex items-center gap-4">
                    <button
                        onClick={() => setStage('setup')}
                        className={`p-2 rounded-lg transition-all ${stage === 'setup' ? 'bg-indigo-50 text-indigo-600' : 'text-gray-400 hover:text-gray-600'}`}
                    >
                        <Home className="w-5 h-5" />
                    </button>
                    <div className="h-6 w-px bg-gray-200" />
                    <div className="flex items-center gap-2 text-sm font-medium text-gray-600">
                        <span className={stage === 'setup' ? 'text-indigo-600' : ''}>Setup</span>
                        <ArrowRight className="w-4 h-4 text-gray-300" />
                        <span className={stage === 'scanning' || stage === 'glossary' ? 'text-indigo-600' : ''}>Glossary</span>
                        <ArrowRight className="w-4 h-4 text-gray-300" />
                        <span className={stage === 'translating' || stage === 'review' ? 'text-indigo-600' : ''}>Translation</span>
                    </div>
                </div>
            </nav>

            {/* Main Content Area */}
            <main className="flex-1 p-4 md:p-8 max-w-7xl mx-auto w-full">
                <AnimatePresence mode="wait">
                    {stage === 'setup' && (
                        <motion.div
                            key="setup"
                            initial={{ opacity: 0, y: 20 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: -20 }}
                            className="w-full bg-white/60 backdrop-blur-2xl rounded-3xl shadow-xl border border-white/50 overflow-hidden flex flex-col md:flex-row"
                        >
                            {/* Left Side: Info & Branding */}
                            <div className="md:w-1/3 bg-gradient-to-br from-indigo-600/90 to-purple-700/90 p-8 text-white flex flex-col justify-between relative overflow-hidden">
                                <div className="relative z-10">
                                    <h2 className="text-3xl font-bold mb-2">New Project</h2>
                                    <p className="text-indigo-100 mb-8">Configure your context-aware translation.</p>

                                    <div className="space-y-6">
                                        <div className="flex items-start gap-3">
                                            <div className="p-2 bg-white/20 rounded-lg"><Film className="w-5 h-5" /></div>
                                            <div><h3 className="font-semibold">Contextual Understanding</h3><p className="text-sm text-indigo-100/80">Analyzes narrative consistency.</p></div>
                                        </div>
                                        <div className="flex items-start gap-3">
                                            <div className="p-2 bg-white/20 rounded-lg"><Globe className="w-5 h-5" /></div>
                                            <div><h3 className="font-semibold">Nuanced Translation</h3><p className="text-sm text-indigo-100/80">Adapts idioms and tone.</p></div>
                                        </div>
                                        <div className="flex items-start gap-3">
                                            <div className="p-2 bg-white/20 rounded-lg"><Cpu className="w-5 h-5" /></div>
                                            <div><h3 className="font-semibold">Powered by Gemini</h3><p className="text-sm text-indigo-100/80">Multimodal intelligence.</p></div>
                                        </div>
                                    </div>
                                </div>
                                {/* Decorative Circles */}
                                <div className="absolute top-[-50px] right-[-50px] w-40 h-40 bg-white/10 rounded-full blur-2xl" />
                                <div className="absolute bottom-[-20px] left-[-20px] w-32 h-32 bg-purple-500/30 rounded-full blur-xl" />
                            </div>

                            {/* Right Side: Form */}
                            <div className="md:w-2/3 p-8 md:p-12">
                                <form onSubmit={startScan} className="space-y-6">
                                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium text-gray-700">Series / Movie Name</label>
                                            <input type="text" required value={showName} onChange={(e) => setShowName(e.target.value)} placeholder="e.g. Game of Thrones" className="w-full px-4 py-2 bg-white/50 border border-gray-200 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none transition-all" />
                                        </div>
                                        <div className="grid grid-cols-2 gap-4">
                                            <div className="space-y-2">
                                                <label className="text-sm font-medium text-gray-700">Season</label>
                                                <input type="text" value={season} onChange={(e) => setSeason(e.target.value)} placeholder="01" className="w-full px-4 py-2 bg-white/50 border border-gray-200 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none transition-all" />
                                            </div>
                                            <div className="space-y-2">
                                                <label className="text-sm font-medium text-gray-700">Episode</label>
                                                <input type="text" value={episode} onChange={(e) => setEpisode(e.target.value)} placeholder="01" className="w-full px-4 py-2 bg-white/50 border border-gray-200 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none transition-all" />
                                            </div>
                                        </div>
                                    </div>

                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium text-gray-700">Target Language</label>
                                            <input type="text" value={targetLanguage} onChange={(e) => setTargetLanguage(e.target.value)} placeholder="e.g. Spanish" className="w-full px-4 py-2 bg-white/50 border border-gray-200 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none transition-all" />
                                        </div>
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium text-gray-700">Scan Model</label>
                                            <input type="text" value={scanModel} onChange={(e) => setScanModel(e.target.value)} placeholder="gemini-flash-lite-latest" className="w-full px-4 py-2 bg-white/50 border border-gray-200 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none transition-all" />
                                        </div>
                                        <div className="space-y-2">
                                            <label className="text-sm font-medium text-gray-700">Translate Model</label>
                                            <input type="text" value={translateModel} onChange={(e) => setTranslateModel(e.target.value)} placeholder="gemini-flash-latest" className="w-full px-4 py-2 bg-white/50 border border-gray-200 rounded-xl focus:ring-2 focus:ring-indigo-500 outline-none transition-all" />
                                        </div>
                                    </div>

                                    <div className="space-y-2">
                                        <label className="text-sm font-medium text-gray-700">Subtitle Files (.srt)</label>
                                        <div className="relative group">
                                            <input type="file" accept=".srt" multiple onChange={handleFileChange} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-10" />
                                            <div className={`border-2 border-dashed rounded-xl p-6 flex flex-col items-center justify-center transition-all ${files.length > 0 ? 'border-indigo-500 bg-indigo-50' : 'border-gray-300 hover:border-indigo-400 hover:bg-gray-50'}`}>
                                                {files.length > 0 ? (
                                                    <div className="flex flex-col items-center gap-1 text-indigo-700">
                                                        <Layers className="w-8 h-8 mb-1" />
                                                        <span className="font-medium">{files.length} file(s) selected</span>
                                                        <span className="text-xs text-indigo-500">{files[0].name} {files.length > 1 && `+ ${files.length - 1} more`}</span>
                                                    </div>
                                                ) : (
                                                    <div className="text-center text-gray-500">
                                                        <Upload className="w-8 h-8 mx-auto mb-2 text-gray-400" />
                                                        <p>Click or drag files to upload</p>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    </div>

                                    <div className="pt-4">
                                        <button type="submit" disabled={files.length === 0 || !showName} className={`w-full py-4 rounded-xl font-bold text-lg text-white shadow-lg flex items-center justify-center gap-2 transition-all ${files.length === 0 || !showName ? 'bg-gray-400 cursor-not-allowed' : 'bg-gradient-to-r from-indigo-600 to-purple-600 hover:scale-[1.02] hover:shadow-xl'}`}>
                                            Start Deep Scan <ArrowRight className="w-5 h-5" />
                                        </button>
                                    </div>
                                </form>
                            </div>
                        </motion.div>
                    )}

                    {stage === 'scanning' && (
                        <motion.div key="scanning" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="flex flex-col items-center justify-center h-[60vh]">
                            <div className="w-24 h-24 relative">
                                <div className="absolute inset-0 border-4 border-indigo-200 rounded-full animate-ping"></div>
                                <div className="absolute inset-0 border-4 border-indigo-600 rounded-full border-t-transparent animate-spin"></div>
                            </div>
                            <h2 className="mt-8 text-2xl font-bold text-gray-800">Scanning Universe...</h2>
                            <p className="text-gray-500 mt-2">Extracting terminology and context from {files.length} file(s).</p>
                        </motion.div>
                    )}

                    {stage === 'glossary' && (
                        <motion.div key="glossary" initial={{ opacity: 0, scale: 0.95 }} animate={{ opacity: 1, scale: 1 }} className="h-[80vh]">
                            <GlossaryEditor
                                glossary={glossaryData}
                                onSave={startTranslation}
                                onCancel={() => setStage('setup')}
                                isSaving={false}
                            />
                        </motion.div>
                    )}

                    {(stage === 'translating' || stage === 'review') && (
                        <motion.div key="translation" initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="h-[80vh]">
                            <TranslationView
                                originalLines={originalLines}
                                translatedLines={translatedLines}
                                isTranslating={stage === 'translating'}
                                progress={stage === 'review' ? 100 : 50} // Mock progress for now
                                glossary={glossaryData}
                                onGlossarySave={setGlossaryData}
                            />
                        </motion.div>
                    )}
                </AnimatePresence>
            </main>
        </div>
    );
};

export default LandingPage;
