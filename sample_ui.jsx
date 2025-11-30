import React, { useState, useEffect, useRef } from 'react';

// --- Icons (Inline SVG for portability) ---
const IconUpload = () => <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="17 8 12 3 7 8" /><line x1="12" y1="3" x2="12" y2="15" /></svg>;
const IconFileText = () => <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M14.5 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7.5L14.5 2z" /><polyline points="14 2 14 8 20 8" /></svg>;
const IconSearch = () => <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>;
const IconBook = () => <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" /><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" /></svg>;
const IconCheck = () => <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12" /></svg>;
const IconSettings = () => <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" /></svg>;
const IconDownload = () => <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" /></svg>;
const IconSparkles = () => <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m12 3-1.912 5.813a2 2 0 0 1-1.275 1.275L3 12l5.813 1.912a2 2 0 0 1 1.275 1.275L12 21l1.912-5.813a2 2 0 0 1 1.275-1.275L12 3Z" /></svg>;
const IconEdit = () => <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" /><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" /></svg>;
const IconTrash = () => <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" /></svg>;
const IconRefresh = () => <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" /><path d="M21 3v5h-5" /><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" /><path d="M3 21v-5h5" /></svg>;
const IconEye = () => <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z" /><circle cx="12" cy="12" r="3" /></svg>;

// --- Initial Data Mocks ---
const INITIAL_GLOSSARY = [
    { id: 1, term: "Winterfell", translation: "Winterfell", context: "Location", type: "location", keepOriginal: true, gender: "n/a" },
    { id: 2, term: "Maester", translation: "Mestre", context: "Title, Male", type: "character", keepOriginal: false, gender: "male" },
    { id: 3, term: "Direwolf", translation: "Lobo Gigante", context: "Creature", type: "creature", keepOriginal: false, gender: "male" },
    { id: 4, term: "The Wall", translation: "A Muralha", context: "Location", type: "location", keepOriginal: false, gender: "female" },
];

const MOCK_FILES = [
    { id: 1, name: "got_s01e01.srt", size: "45KB", progress: 0, status: 'idle', translationSpeed: 1 },
    { id: 2, name: "got_s01e02.srt", size: "48KB", progress: 0, status: 'idle', translationSpeed: 1.2 },
];

const MOCK_EDITOR_LINES = [
    { id: 1, original: "We should start back.", translated: "Devemos voltar." },
    { id: 2, original: "The wildlings are dead.", translated: "Os selvagens estão mortos." },
    { id: 3, original: "Do the dead frighten you?", translated: "Os mortos te assustam?" },
    { id: 4, original: "Our orders were to track the wildlings.", translated: "Nossas ordens eram rastrear os selvagens." },
    { id: 5, original: "We tracked them.", translated: "Nós os rastreamos." },
    { id: 6, original: "They won't trouble us no more.", translated: "Eles não vão mais nos incomodar." },
    { id: 7, original: "You don't think he'll ask us how they died?", translated: "Você não acha que ele vai perguntar como eles morreram?" },
    { id: 8, original: "Get back on your horse.", translated: "Volte para o seu cavalo." },
    { id: 9, original: "Whatever did it, I think it's gone.", translated: "O que quer que tenha feito isso, acho que já foi." },
    { id: 10, original: "It's getting dark.", translated: "Está escurecendo." },
];

const App = () => {
    const [appState, setAppState] = useState('landing'); // landing, scanning, review, translating, editor, done
    const [files, setFiles] = useState([]);
    const [glossary, setGlossary] = useState([]);
    const [autoPilot, setAutoPilot] = useState(false);
    const [showGlossary, setShowGlossary] = useState(false);
    const [scanProgress, setScanProgress] = useState(0);
    const [currentLog, setCurrentLog] = useState("Ready to start...");
    const [systemInstructions, setSystemInstructions] = useState("Maintain a medieval fantasy tone. Use formal speech for nobility.");
    const [editorLines, setEditorLines] = useState([]);
    const [selectedFileId, setSelectedFileId] = useState(null);

    // --- Inject Tailwind CDN ---
    useEffect(() => {
        const script = document.createElement('script');
        script.src = "https://cdn.tailwindcss.com";
        script.async = true;
        document.body.appendChild(script);

        return () => {
            if (document.body.contains(script)) {
                document.body.removeChild(script);
            }
        };
    }, []);

    // Mock "Deep Scan" Effect
    useEffect(() => {
        if (appState === 'scanning') {
            setShowGlossary(false);
            let progress = 0;
            const interval = setInterval(() => {
                progress += 2;
                setScanProgress(progress);

                if (progress < 30) setCurrentLog("Parsing subtitles...");
                else if (progress < 60) setCurrentLog("Extracting named entities (NER)...");
                else if (progress < 90) setCurrentLog("Searching Wiki for context...");
                else setCurrentLog("Synthesizing Master Glossary...");

                if (progress >= 100) {
                    clearInterval(interval);
                    setGlossary(INITIAL_GLOSSARY);
                    if (autoPilot) {
                        setAppState('translating');
                    } else {
                        setAppState('review');
                        setShowGlossary(true);
                    }
                }
            }, 60); // Fast simulation
            return () => clearInterval(interval);
        }
    }, [appState, autoPilot]);

    // Mock "Translation" Effect
    useEffect(() => {
        if (appState === 'translating') {
            setCurrentLog("Translating with context...");
            const interval = setInterval(() => {
                setFiles(prevFiles => {
                    const allDone = prevFiles.every(f => f.progress >= 100);
                    if (allDone) {
                        clearInterval(interval);
                        // Instead of done, we go to a state where we can choose to edit
                        setTimeout(() => setAppState('done'), 800);
                        return prevFiles;
                    }

                    return prevFiles.map(file => {
                        if (file.progress >= 100) return file;
                        const increment = Math.random() * 3 * file.translationSpeed;
                        const newProgress = Math.min(file.progress + increment, 100);
                        return {
                            ...file,
                            progress: newProgress,
                            status: newProgress === 100 ? 'completed' : 'translating'
                        };
                    });
                });
            }, 100);
            return () => clearInterval(interval);
        }
    }, [appState]);

    const handleUpload = () => {
        setFiles(MOCK_FILES);
        setAppState('scanning');
    };

    const handleStartTranslation = () => {
        setAppState('translating');
    };

    const handleOpenEditor = (fileId) => {
        setSelectedFileId(fileId);
        setEditorLines(MOCK_EDITOR_LINES);
        setAppState('editor');
        setShowGlossary(true); // Show glossary during editing
    };

    const handleDeleteTerm = (id) => {
        setGlossary(prev => prev.filter(item => item.id !== id));
    };

    const handleAddTerm = () => {
        const newId = Math.max(...glossary.map(g => g.id), 0) + 1;
        setGlossary([...glossary, { id: newId, term: "New Term", translation: "", context: "User added", type: "term", keepOriginal: false, gender: "n/a" }]);
    };

    const updateTerm = (id, field, value) => {
        setGlossary(prev => prev.map(item => item.id === id ? { ...item, [field]: value } : item));
    };

    return (
        <div className="font-['Quicksand'] bg-gradient-to-br from-rose-50 via-slate-50 to-teal-50 h-screen w-screen overflow-hidden text-slate-700 flex">
            <style>{`
                @import url('https://fonts.googleapis.com/css2?family=Quicksand:wght@300;400;500;600&display=swap');
                ::-webkit-scrollbar { width: 6px; }
                ::-webkit-scrollbar-track { background: transparent; }
                ::-webkit-scrollbar-thumb { background: rgba(156, 163, 175, 0.3); border-radius: 20px; }
                .glass-panel {
                    background: rgba(255, 255, 255, 0.45);
                    backdrop-filter: blur(12px);
                    -webkit-backdrop-filter: blur(12px);
                    border: 1px solid rgba(255, 255, 255, 0.6);
                }
                .glass-card {
                    background: rgba(255, 255, 255, 0.7);
                    backdrop-filter: blur(8px);
                    border: 1px solid rgba(255, 255, 255, 0.8);
                }
                .fade-enter-active {
                    animation: fadeIn 0.5s ease-out forwards;
                }
                @keyframes fadeIn {
                    from { opacity: 0; transform: translateY(10px); }
                    to { opacity: 1; transform: translateY(0); }
                }
            `}</style>

            <div className="flex h-full w-full p-6 gap-6 relative">

                {/* --- LEFT MAIN AREA --- */}
                <div className={`flex-1 flex flex-col transition-all duration-700 ease-in-out ${showGlossary ? 'mr-[380px]' : 'mr-0'}`}>

                    {/* Header */}
                    <header className="flex items-center justify-between mb-6">
                        <div className="flex items-center gap-3">
                            <div className="w-10 h-10 bg-indigo-600 rounded-xl flex items-center justify-center text-white shadow-lg shadow-indigo-200">
                                <IconSparkles />
                            </div>
                            <div>
                                <h1 className="text-2xl font-bold text-slate-800 tracking-tight">Context Translator</h1>
                                <p className="text-sm text-slate-500 font-medium">
                                    {appState === 'editor' ? 'Editor Mode' : 'Agentic Workflow'}
                                </p>
                            </div>
                        </div>

                        {/* Global Controls */}
                        <div className="flex items-center gap-4">
                            <div className="flex items-center gap-2 bg-white/60 px-4 py-2 rounded-full shadow-sm border border-white">
                                <span className={`text-xs font-bold uppercase tracking-wider ${autoPilot ? 'text-indigo-600' : 'text-slate-400'}`}>Auto-Pilot</span>
                                <button
                                    onClick={() => setAutoPilot(!autoPilot)}
                                    className={`w-12 h-6 rounded-full p-1 transition-colors duration-300 ${autoPilot ? 'bg-indigo-500' : 'bg-slate-300'}`}
                                >
                                    <div className={`w-4 h-4 bg-white rounded-full shadow-md transform transition-transform duration-300 ${autoPilot ? 'translate-x-6' : 'translate-x-0'}`}></div>
                                </button>
                            </div>
                        </div>
                    </header>

                    {/* DYNAMIC CONTENT AREA */}
                    <main className="flex-1 relative overflow-hidden flex flex-col">

                        {/* 1. LANDING STATE */}
                        {appState === 'landing' && (
                            <div className="flex-1 flex flex-col items-center justify-center fade-enter-active">
                                <div
                                    onClick={handleUpload}
                                    className="glass-panel w-full max-w-2xl h-96 rounded-3xl border-2 border-dashed border-indigo-200 flex flex-col items-center justify-center cursor-pointer hover:border-indigo-400 hover:bg-white/60 transition-all duration-300 group"
                                >
                                    <div className="w-20 h-20 bg-indigo-50 rounded-full flex items-center justify-center text-indigo-400 group-hover:scale-110 transition-transform duration-500 mb-6">
                                        <IconUpload />
                                    </div>
                                    <h2 className="text-xl font-semibold text-slate-700">Drop .srt files</h2>
                                    <p className="text-slate-500 mt-2">Supports Batch Processing</p>
                                </div>
                            </div>
                        )}

                        {/* 2. SCANNING STATE */}
                        {appState === 'scanning' && (
                            <div className="flex-1 flex flex-col items-center justify-center fade-enter-active">
                                <div className="w-full max-w-xl text-center">
                                    <div className="relative w-32 h-32 mx-auto mb-8">
                                        <div className="absolute inset-0 border-4 border-indigo-100 rounded-full"></div>
                                        <div className="absolute inset-0 border-4 border-indigo-500 rounded-full border-t-transparent animate-spin"></div>
                                        <div className="absolute inset-0 flex items-center justify-center">
                                            <span className="text-2xl font-bold text-indigo-600">{scanProgress}%</span>
                                        </div>
                                    </div>
                                    <h2 className="text-2xl font-semibold text-slate-800 mb-2">Building Context...</h2>
                                    <p className="text-slate-500 font-medium animate-pulse">{currentLog}</p>
                                </div>
                            </div>
                        )}

                        {/* 3. REVIEW & TRANSLATING STATE */}
                        {(appState === 'review' || appState === 'translating' || appState === 'done') && (
                            <div className="glass-panel w-full h-full rounded-3xl p-8 overflow-y-auto fade-enter-active flex flex-col">
                                <div className="flex items-center justify-between mb-6">
                                    <h2 className="text-lg font-semibold text-slate-700">Project Files</h2>
                                    {appState === 'translating' && <span className="text-xs font-bold text-indigo-500 animate-pulse">TRANSLATING...</span>}
                                    {appState === 'done' && <span className="text-xs font-bold text-emerald-500">COMPLETED</span>}
                                </div>

                                <div className="space-y-4 flex-1">
                                    {files.map((file) => (
                                        <div key={file.id} className="glass-card p-4 rounded-2xl flex items-center gap-4 group hover:shadow-md transition-all duration-300">
                                            <div className={`w-12 h-12 rounded-xl flex items-center justify-center transition-colors duration-500 ${file.progress === 100 ? 'bg-emerald-100 text-emerald-600' : 'bg-white text-slate-400'
                                                }`}>
                                                {file.progress === 100 ? <IconCheck /> : <IconFileText />}
                                            </div>

                                            <div className="flex-1 min-w-0">
                                                <div className="flex justify-between mb-1">
                                                    <h3 className="font-semibold text-slate-700 truncate">{file.name}</h3>
                                                    <span className="text-xs text-slate-500">{file.size}</span>
                                                </div>
                                                <div className="h-2 bg-slate-100 rounded-full overflow-hidden">
                                                    <div className={`h-full rounded-full transition-all duration-300 ${file.progress === 100 ? 'bg-emerald-500' : 'bg-indigo-500'}`} style={{ width: `${file.progress}%` }}></div>
                                                </div>
                                            </div>

                                            {appState === 'done' && (
                                                <div className="flex gap-2">
                                                    <button
                                                        onClick={() => handleOpenEditor(file.id)}
                                                        className="p-2 text-indigo-600 hover:bg-indigo-50 rounded-lg transition-colors flex items-center gap-1 text-sm font-medium"
                                                    >
                                                        <IconEye /> Review
                                                    </button>
                                                    <button className="p-2 text-slate-600 hover:bg-slate-100 rounded-lg transition-colors">
                                                        <IconDownload />
                                                    </button>
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>

                                {appState === 'review' && (
                                    <div className="mt-6 pt-6 border-t border-white/50">
                                        <label className="block text-sm font-bold text-slate-700 mb-2">System Instructions (Series Context)</label>
                                        <textarea
                                            className="w-full p-3 rounded-xl bg-white/50 border border-white focus:ring-2 focus:ring-indigo-300 outline-none text-sm text-slate-700 mb-4"
                                            rows="3"
                                            value={systemInstructions}
                                            onChange={(e) => setSystemInstructions(e.target.value)}
                                        />
                                        <div className="flex justify-center">
                                            <button
                                                onClick={handleStartTranslation}
                                                className="px-8 py-3 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl shadow-lg shadow-indigo-200 font-semibold transition-all transform hover:scale-105 flex items-center gap-2"
                                            >
                                                <IconSparkles /> Start Translation
                                            </button>
                                        </div>
                                    </div>
                                )}
                            </div>
                        )}

                        {/* 4. EDITOR STATE (Split View) */}
                        {appState === 'editor' && (
                            <div className="glass-panel w-full h-full rounded-3xl overflow-hidden flex flex-col fade-enter-active">
                                <div className="p-4 border-b border-white/50 flex justify-between items-center bg-white/30">
                                    <div className="flex items-center gap-3">
                                        <button onClick={() => setAppState('done')} className="text-slate-500 hover:text-slate-800">← Back</button>
                                        <h2 className="font-bold text-slate-700">got_s01e01.srt</h2>
                                    </div>
                                    <div className="flex gap-2">
                                        <button className="px-4 py-2 bg-emerald-500 text-white rounded-lg text-sm font-medium hover:bg-emerald-600">Save Changes</button>
                                    </div>
                                </div>

                                <div className="flex-1 overflow-y-auto p-4">
                                    <div className="grid grid-cols-2 gap-6">
                                        {/* Headers */}
                                        <div className="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2 sticky top-0 bg-inherit z-10">Original</div>
                                        <div className="text-xs font-bold text-indigo-500 uppercase tracking-wider mb-2 sticky top-0 bg-inherit z-10">Translation</div>

                                        {editorLines.map((line) => (
                                            <React.Fragment key={line.id}>
                                                <div className="p-3 bg-white/40 rounded-lg border border-white/20 text-slate-600 text-sm">
                                                    {line.original}
                                                </div>
                                                <textarea
                                                    className="p-3 bg-white/80 rounded-lg border border-indigo-100 text-slate-800 text-sm focus:ring-2 focus:ring-indigo-300 outline-none resize-none"
                                                    rows="1"
                                                    defaultValue={line.translated}
                                                />
                                            </React.Fragment>
                                        ))}
                                    </div>
                                </div>
                            </div>
                        )}

                    </main>
                </div>

                {/* --- RIGHT SIDEBAR (PERSISTENT GLOSSARY) --- */}
                <aside
                    className={`fixed top-6 bottom-6 right-6 w-[350px] glass-panel rounded-3xl flex flex-col transition-transform duration-500 ease-out z-20 shadow-2xl
                        ${showGlossary ? 'translate-x-0' : 'translate-x-[120%]'}
                    `}
                >
                    <div className="p-6 border-b border-white/50 bg-white/30 backdrop-blur-md rounded-t-3xl">
                        <div className="flex items-center gap-2 text-slate-700 mb-1">
                            <IconBook />
                            <h2 className="font-bold text-lg">Master Glossary</h2>
                        </div>
                        <p className="text-xs text-slate-500">Context Rules & Terminology</p>
                    </div>

                    <div className="flex-1 overflow-y-auto p-4 space-y-3">
                        {glossary.map((item) => (
                            <div key={item.id} className="bg-white/50 border border-white/60 p-3 rounded-xl hover:bg-white/80 transition-all group">
                                <div className="flex justify-between items-start mb-2">
                                    <input
                                        className="font-bold text-slate-800 text-sm bg-transparent border-b border-transparent hover:border-slate-300 focus:border-indigo-500 outline-none w-full"
                                        value={item.term}
                                        onChange={(e) => updateTerm(item.id, 'term', e.target.value)}
                                    />
                                    <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                                        <button onClick={() => handleDeleteTerm(item.id)} className="p-1 hover:text-red-500"><IconTrash /></button>
                                    </div>
                                </div>

                                <div className="grid grid-cols-2 gap-2 mb-2">
                                    <select
                                        className="text-[10px] p-1 rounded bg-slate-100 text-slate-600 border-none outline-none"
                                        value={item.type}
                                        onChange={(e) => updateTerm(item.id, 'type', e.target.value)}
                                    >
                                        <option value="character">Character</option>
                                        <option value="location">Location</option>
                                        <option value="item">Item</option>
                                        <option value="term">Term</option>
                                    </select>

                                    <select
                                        className="text-[10px] p-1 rounded bg-slate-100 text-slate-600 border-none outline-none"
                                        value={item.gender}
                                        onChange={(e) => updateTerm(item.id, 'gender', e.target.value)}
                                    >
                                        <option value="n/a">Gender: N/A</option>
                                        <option value="male">Male</option>
                                        <option value="female">Female</option>
                                        <option value="neutral">Neutral</option>
                                    </select>
                                </div>

                                <div className="flex items-center gap-2 mb-2">
                                    <input
                                        className="text-indigo-700 font-medium text-sm bg-indigo-50/50 px-2 py-1 rounded w-full border-none outline-none placeholder-indigo-300"
                                        placeholder="Translation..."
                                        value={item.translation}
                                        onChange={(e) => updateTerm(item.id, 'translation', e.target.value)}
                                        disabled={item.keepOriginal}
                                    />
                                </div>

                                <div className="flex items-center gap-2">
                                    <label className="flex items-center gap-1 cursor-pointer">
                                        <input
                                            type="checkbox"
                                            checked={item.keepOriginal}
                                            onChange={(e) => updateTerm(item.id, 'keepOriginal', e.target.checked)}
                                            className="accent-indigo-600 w-3 h-3"
                                        />
                                        <span className="text-[10px] text-slate-500">Keep Original</span>
                                    </label>
                                </div>
                            </div>
                        ))}

                        <button
                            onClick={handleAddTerm}
                            className="w-full py-3 border-2 border-dashed border-slate-300 rounded-xl text-slate-400 text-sm font-medium hover:border-indigo-300 hover:text-indigo-500 transition-colors flex items-center justify-center gap-2"
                        >
                            + Add Term
                        </button>
                    </div>
                </aside>

                {/* Overlay for Auto-Pilot Notice */}
                {autoPilot && appState !== 'landing' && (
                    <div className="fixed top-10 left-1/2 -translate-x-1/2 bg-indigo-900/90 text-white px-6 py-2 rounded-full text-sm font-medium shadow-xl z-50 backdrop-blur animate-pulse flex items-center gap-2">
                        <IconSparkles /> Auto-Pilot Active
                    </div>
                )}

            </div>
        </div>
    );
};

export default App;