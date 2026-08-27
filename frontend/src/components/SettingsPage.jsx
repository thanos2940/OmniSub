import React, { useState, useEffect } from 'react';
import { Save, Info, Sparkles, Settings as SettingsIcon, BookOpen, Zap, Globe, Play, RefreshCw, Database, ShieldAlert, ShieldCheck, Lock, Copy, Eye, EyeOff, List, Link as LinkIcon, Activity, Plus, Trash2, Tv, Film, FolderOpen, Key } from 'lucide-react';
import { api, AUTH_STORAGE_KEY } from '../api';
import ModelCombobox from './ModelCombobox';
import { useLocation } from 'react-router-dom';
import { QUALITY_PRESETS, activePresetFor } from './settings/presets';
import SyncOptionsModal from './SyncOptionsModal';

// GET /settings masks secret fields (sonarr/radarr api keys, webhook secret,
// discord webhook url) with this sentinel so the real value never round-trips
// to the browser. An untouched field still holds this string; strip it before
// using the value for anything other than an unmodified save (the backend
// already drops it there, falling back to the stored secret).
const SECRET_SENTINEL = '__SECRET_UNCHANGED__';
const unmask = (value) => (value === SECRET_SENTINEL ? undefined : value);

const SettingsPage = () => {
    const [testLines, setTestLines] = useState([
        "What does it matter? It's just a curse.",
        "You don't understand the weight of this identity.",
        "The cherry blossoms are falling early this year.",
        "Wait! Don't go into the forbidden forest alone!"
    ]);
    const [testModel, setTestModel] = useState('');
    const [isTesting, setIsTesting] = useState(false);
    const [testResult, setTestResult] = useState(null);

    const handleRunTest = async () => {
        setIsTesting(true);
        try {
            // We use the first project found as context for the test
            const projectsRes = await api.getProjects();
            const projectName = projectsRes.data[0]?.show_name;
            if (!projectName) {
                alert("Please create at least one project first to provide glossary/language context.");
                return;
            }

            const res = await api.testTranslation(projectName, {
                lines: testLines,
                temperature: settings.temperature,
                top_k: settings.top_k,
                top_p: settings.top_p,
                model_name: testModel || undefined
            });
            setTestResult(res.data);
        } catch (err) {
            console.error("Test failed", err);
        } finally {
            setIsTesting(false);
        }
    };
    const [settings, setSettings] = useState({
        default_target_language: 'English',
        default_scan_model: 'gemini-2.5-flash',
        default_translation_model: 'gemini-2.5-flash'
    });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [message, setMessage] = useState(null);
    const [arrStatus, setArrStatus] = useState(null);
    const [testingSonarr, setTestingSonarr] = useState(false);
    const [testingRadarr, setTestingRadarr] = useState(false);
    const [syncingArr, setSyncingArr] = useState(false);
    const [syncModalOpen, setSyncModalOpen] = useState(false);
    const [diagnosticPath, setDiagnosticPath] = useState('');
    const [isTestingPath, setIsTestingPath] = useState(false);
    const [diagnosticResult, setDiagnosticResult] = useState(null);

    // Media Probe Diagnostic Tool (Embedded ASS)
    const [probeTestPath, setProbeTestPath] = useState('');
    const [probeTestLoading, setProbeTestLoading] = useState(false);
    const [probeTestResult, setProbeTestResult] = useState(null);
    const [probeTestError, setProbeTestError] = useState(null);

    const handleRunMediaProbeTest = async () => {
        if (!probeTestPath.trim()) return;
        setProbeTestLoading(true);
        setProbeTestError(null);
        setProbeTestResult(null);
        try {
            const res = await api.testMediaProbe(probeTestPath.trim());
            setProbeTestResult(res.data);
        } catch (err) {
            console.error('Media probe test failed', err);
            setProbeTestError(err.response?.data?.detail || err.message || 'Media probe test failed');
        } finally {
            setProbeTestLoading(false);
        }
    };

    // Security section (docs/PLAN_auth_security.md) — a separate form from the
    // main settings object: auth fields aren't part of SettingsRequest and are
    // only ever written through /api/auth/*, never the generic POST /settings.
    const [authUsername, setAuthUsername] = useState('');
    const [authPassword, setAuthPassword] = useState('');
    const [authPasswordConfirm, setAuthPasswordConfirm] = useState('');
    const [savingAuth, setSavingAuth] = useState(false);
    const [authMessage, setAuthMessage] = useState(null);
    const [showApiKey, setShowApiKey] = useState(false);
    const [regenerateKeyOnSave, setRegenerateKeyOnSave] = useState(false);
    const [browsing, setBrowsing] = useState(false);
    const location = useLocation();
    const [testingLocalLLM, setTestingLocalLLM] = useState(false);
    const [hasGeminiKey, setHasGeminiKey] = useState(false);
    const [geminiKeyInput, setGeminiKeyInput] = useState('');
    const [updatingGeminiKey, setUpdatingGeminiKey] = useState(false);
    const [showGeminiKey, setShowGeminiKey] = useState(false);
    const [showSonarrKey, setShowSonarrKey] = useState(false);
    const [showRadarrKey, setShowRadarrKey] = useState(false);
    const [testingGeminiKey, setTestingGeminiKey] = useState(false);

    const checkApiKeyStatus = async () => {
        try {
            const res = await api.getApiKeyStatus();
            setHasGeminiKey(res.data.has_key);
        } catch (err) {
            console.error("Failed to check Gemini API Key status", err);
            setHasGeminiKey(false);
        }
    };

    const handleTestGeminiKey = async () => {
        setTestingGeminiKey(true);
        try {
            const keyToTest = geminiKeyInput.trim() || null;
            const res = await api.testGeminiKey(keyToTest);
            if (res.data?.valid) {
                setMessage({ type: 'success', text: res.data.message || 'Gemini API connection successful!' });
            } else {
                setMessage({ type: 'error', text: `Gemini API key verification failed: ${res.data?.error || 'Unknown error'}` });
            }
        } catch (err) {
            setMessage({ type: 'error', text: `Failed to test Gemini API key: ${err.response?.data?.detail || err.message}` });
        } finally {
            setTestingGeminiKey(false);
            setTimeout(() => setMessage(null), 5000);
        }
    };

    const handleSaveGeminiKey = async () => {
        if (!geminiKeyInput.trim()) return;
        setUpdatingGeminiKey(true);
        try {
            await api.setApiKey(geminiKeyInput.trim());
            setHasGeminiKey(true);
            setGeminiKeyInput('');
            setMessage({ type: 'success', text: 'Gemini API Key saved successfully!' });
            setTimeout(() => setMessage(null), 3000);
        } catch (err) {
            console.error("Failed to save Gemini API Key", err);
            setMessage({ type: 'error', text: 'Failed to save Gemini API Key.' });
            setTimeout(() => setMessage(null), 3000);
        } finally {
            setUpdatingGeminiKey(false);
        }
    };

    const handleDeleteGeminiKey = async () => {
        if (!window.confirm("Are you sure you want to remove the Gemini API key?")) return;
        setUpdatingGeminiKey(true);
        try {
            await api.deleteApiKey();
            setHasGeminiKey(false);
            setGeminiKeyInput('');
            setMessage({ type: 'success', text: 'Gemini API Key deleted successfully!' });
            setTimeout(() => setMessage(null), 3000);
        } catch (err) {
            console.error("Failed to delete Gemini API Key", err);
            setMessage({ type: 'error', text: 'Failed to delete Gemini API Key.' });
            setTimeout(() => setMessage(null), 3000);
        } finally {
            setUpdatingGeminiKey(false);
        }
    };

    const handleTestLocalLLM = async () => {
        setTestingLocalLLM(true);
        try {
            const res = await api.fetchAllModels(settings.local_llm_base_url);
            const models = res.data?.models || [];
            const names = models.map(m => m.id || m.name || m).filter(Boolean);
            setMessage({
                type: 'success',
                text: `Connected successfully! Discovered ${names.length} model(s): ${names.slice(0, 5).join(', ')}${names.length > 5 ? '...' : ''}`
            });
            setTimeout(() => setMessage(null), 8000);
        } catch (err) {
            console.error("Local LLM test failed", err);
            setMessage({
                type: 'error',
                text: `Failed to connect to local LLM endpoint: ${err.response?.data?.detail || err.message}`
            });
            setTimeout(() => setMessage(null), 8000);
        } finally {
            setTestingLocalLLM(false);
        }
    };

    const handleBrowseSubtitleEdit = async () => {
        setBrowsing(true);
        try {
            const res = await api.browseExecutable();
            const path = res.data?.path;
            if (path) {
                handleChange('subtitle_edit_path', path);
                setMessage({ type: 'success', text: `Selected: ${path}` });
                setTimeout(() => setMessage(null), 4000);
            }
        } catch (err) {
            const detail = err?.response?.data?.detail || 'Could not open the file picker.';
            setMessage({ type: 'error', text: detail });
            setTimeout(() => setMessage(null), 6000);
        } finally {
            setBrowsing(false);
        }
    };

    const handleTestPath = async () => {
        setIsTestingPath(true);
        setDiagnosticResult(null);
        try {
            const res = await api.testPath(diagnosticPath, settings.arr_path_mappings || []);
            setDiagnosticResult(res.data);
        } catch (err) {
            console.error("Path test failed", err);
            setDiagnosticResult({
                resolved_path: diagnosticPath,
                exists: false,
                is_file: false,
                readable: false,
                error: err.response?.data?.detail || err.message
            });
        } finally {
            setIsTestingPath(false);
        }
    };

    useEffect(() => {
        loadSettings();
        loadArrStatus();
        checkApiKeyStatus();
    }, []);

    // Prefill the username field once we know the current one, so "change
    // password" doesn't force retyping it. Only runs while the field is still
    // untouched (empty) so it doesn't clobber in-progress typing on reload.
    useEffect(() => {
        if (settings.auth_username && !authUsername) {
            setAuthUsername(settings.auth_username);
        }
    }, [settings.auth_username]);

    useEffect(() => {
        if (!loading) {
            const params = new URLSearchParams(location.search);
            const testPath = params.get('test_path');
            if (testPath) {
                setDiagnosticPath(testPath);
                const runPathTest = async () => {
                    setIsTestingPath(true);
                    setDiagnosticResult(null);
                    try {
                        const res = await api.testPath(testPath, settings.arr_path_mappings || []);
                        setDiagnosticResult(res.data);
                    } catch (err) {
                        console.error("Path test failed", err);
                        setDiagnosticResult({
                            resolved_path: testPath,
                            exists: false,
                            is_file: false,
                            readable: false,
                            error: err.response?.data?.detail || err.message
                        });
                    } finally {
                        setIsTestingPath(false);
                    }
                };
                runPathTest();
                setTimeout(() => {
                    const el = document.getElementById('path-diagnostics');
                    if (el) {
                        el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
                    }
                }, 100);
            }
        }
    }, [location.search, loading]);

    const loadArrStatus = async () => {
        try {
            const res = await api.getArrStatus();
            setArrStatus(res.data);
        } catch (e) {
            console.error("Failed to load Arr status", e);
        }
    };

    const handleTestSonarr = async () => {
        setTestingSonarr(true);
        try {
            const res = await api.testSonarr({
                url: settings.sonarr_url,
                api_key: unmask(settings.sonarr_api_key)
            });
            if (res.data.connected) {
                setMessage({ type: 'success', text: `Sonarr connection successful! Version: ${res.data.version || 'Unknown'}` });
            } else {
                setMessage({ type: 'error', text: `Connection failed: ${res.data.error}` });
            }
        } catch (err) {
            setMessage({ type: 'error', text: 'Failed to test Sonarr connection.' });
        } finally {
            setTestingSonarr(false);
            setTimeout(() => setMessage(null), 5000);
        }
    };

    const handleTestRadarr = async () => {
        setTestingRadarr(true);
        try {
            const res = await api.testRadarr({
                url: settings.radarr_url,
                api_key: unmask(settings.radarr_api_key)
            });
            if (res.data.connected) {
                setMessage({ type: 'success', text: `Radarr connection successful! Version: ${res.data.version || 'Unknown'}` });
            } else {
                setMessage({ type: 'error', text: `Connection failed: ${res.data.error}` });
            }
        } catch (err) {
            setMessage({ type: 'error', text: 'Failed to test Radarr connection.' });
        } finally {
            setTestingRadarr(false);
            setTimeout(() => setMessage(null), 5000);
        }
    };

    const handleOpenSyncModal = () => {
        setSyncModalOpen(true);
    };

    const handleStartSyncWithOptions = async (options) => {
        setSyncingArr(true);
        try {
            if (options.source === 'sonarr') {
                await api.syncSonarr(options);
            } else if (options.source === 'radarr') {
                await api.syncRadarr(options);
            } else {
                await api.syncArrAll(options);
            }
            setMessage({ type: 'success', text: 'Sonarr/Radarr library sync triggered successfully!' });
            loadArrStatus();
        } catch (err) {
            setMessage({ type: 'error', text: 'Failed to trigger library sync.' });
        } finally {
            setSyncingArr(false);
            setTimeout(() => setMessage(null), 5000);
        }
    };

    const loadSettings = async () => {
        try {
            const response = await api.getSettings();
            setSettings(response.data);
        } catch (error) {
            console.error('Failed to load settings:', error);
        } finally {
            setLoading(false);
        }
    };

    const handleSave = async () => {
        setSaving(true);
        setMessage(null);
        try {
            const payload = { ...settings };
            if (geminiKeyInput.trim()) {
                payload.api_key = geminiKeyInput.trim();
            }
            await api.updateSettings(payload);
            if (geminiKeyInput.trim()) {
                setHasGeminiKey(true);
                setGeminiKeyInput('');
            }
            setMessage({ type: 'success', text: 'Settings saved successfully!' });
            setTimeout(() => setMessage(null), 3000);
        } catch (error) {
            console.error('Failed to save settings:', error);
            setMessage({ type: 'error', text: 'Failed to save settings.' });
        } finally {
            setSaving(false);
        }
    };

    const handleChange = (field, value) => {
        setSettings(prev => ({ ...prev, [field]: value }));
    };

    // Single unified action: /api/auth/credentials always requires both username
    // and password together (there's no separate "just rotate the key" endpoint),
    // so offer one form with an explicit "also regenerate the API key" checkbox
    // rather than a second button that would silently reset the password as a
    // side effect of trying to rotate the key.
    const handleSetCredentials = async () => {
        setAuthMessage(null);
        if (!authUsername.trim()) {
            setAuthMessage({ type: 'error', text: 'Enter a username.' });
            return;
        }
        if (authPassword.length < 8) {
            setAuthMessage({ type: 'error', text: 'Password must be at least 8 characters.' });
            return;
        }
        if (authPassword !== authPasswordConfirm) {
            setAuthMessage({ type: 'error', text: 'Passwords do not match.' });
            return;
        }
        setSavingAuth(true);
        try {
            const res = await api.setCredentials(authUsername.trim(), authPassword, regenerateKeyOnSave);
            // Always adopt whatever key comes back — if regenerateKeyOnSave was
            // false this is just the existing key, so it's a harmless no-op;
            // if true, this keeps the current browser session logged in.
            localStorage.setItem(AUTH_STORAGE_KEY, res.data.api_key);
            setAuthPassword('');
            setAuthPasswordConfirm('');
            setRegenerateKeyOnSave(false);
            setAuthMessage({ type: 'success', text: 'Credentials saved.' });
            loadSettings();
        } catch (err) {
            setAuthMessage({ type: 'error', text: err.response?.data?.detail || 'Failed to save credentials.' });
        } finally {
            setSavingAuth(false);
        }
    };

    const copyApiKey = () => {
        const key = localStorage.getItem(AUTH_STORAGE_KEY);
        if (key) {
            navigator.clipboard.writeText(key);
            setAuthMessage({ type: 'success', text: 'API key copied to clipboard.' });
        }
    };

    // Quality presets (v2): set a coherent group of quality flags in one click so
    // users don't have to discover the right combination of ~10 individual toggles.
    const applyQualityPreset = (name) => {
        const preset = QUALITY_PRESETS[name];
        if (preset) setSettings(prev => ({ ...prev, ...preset.values }));
    };

    const activePreset = activePresetFor(settings);

    const handleAddMapping = () => {
        const mappings = [...(settings.arr_path_mappings || []), { remote: '', local: '' }];
        handleChange('arr_path_mappings', mappings);
    };

    const handleRemoveMapping = (index) => {
        const mappings = [...(settings.arr_path_mappings || [])];
        mappings.splice(index, 1);
        handleChange('arr_path_mappings', mappings);
    };

    const handleUpdateMapping = (index, field, value) => {
        const mappings = [...(settings.arr_path_mappings || [])];
        mappings[index] = { ...mappings[index], [field]: value };
        handleChange('arr_path_mappings', mappings);
    };

    // Setup wizard (docs/PLAN_onboarding_wizard.md): flip the flag back off and
    // reload — App.jsx's own setup-status check picks it up on next mount and
    // shows the wizard, same as a fresh install. Simpler than lifting wizard
    // state up through App.jsx just for this one re-entry path.
    const handleRerunWizard = async () => {
        try {
            await api.updateSettings({ setup_completed: false });
            window.location.reload();
        } catch (e) {
            console.error('Failed to restart setup wizard', e);
        }
    };

    if (loading) {
        return <div className="p-8 text-center text-gray-500">Loading settings...</div>;
    }

    return (
        <div className="max-w-4xl mx-auto p-8 pb-28 space-y-8">
            <header className="flex items-center gap-3 mb-8">
                <div className="w-10 h-10 bg-indigo-600 rounded-xl flex items-center justify-center shadow-lg shadow-indigo-200 dark:shadow-none">
                    <SettingsIcon className="text-white" size={24} />
                </div>
                <div className="flex-1">
                    <h1 className="text-2xl font-bold text-gray-900 dark:text-white">Global Settings</h1>
                    <p className="text-gray-500 dark:text-gray-400">Configure default behavior for all projects.</p>
                </div>
                <button
                    onClick={handleRerunWizard}
                    className="text-sm text-indigo-600 dark:text-indigo-400 hover:underline whitespace-nowrap"
                >
                    Run setup wizard again
                </button>
            </header>

            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <Lock className="w-5 h-5 text-rose-500" />
                        Security
                    </h2>
                </div>
                <div className="p-6 space-y-4">
                    {settings.auth_enabled ? (
                        <div className="flex items-start gap-2 text-sm text-emerald-700 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-200 dark:border-emerald-800 rounded-lg p-3">
                            <ShieldCheck className="w-4 h-4 flex-shrink-0 mt-0.5" />
                            <span>Secured — signed in as <strong>{settings.auth_username}</strong>. Every request except a small allowlist (health check, login, webhooks) requires the API key below.</span>
                        </div>
                    ) : (
                        <div className="flex items-start gap-2 text-sm text-amber-700 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20 border border-amber-200 dark:border-amber-800 rounded-lg p-3">
                            <ShieldAlert className="w-4 h-4 flex-shrink-0 mt-0.5" />
                            <span>This server is unsecured — anyone who can reach it can read settings and control translations. Set a username and password below if it's reachable beyond your own machine.</span>
                        </div>
                    )}

                    {settings.auth_enabled && (
                        <div>
                            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">API Key</label>
                            <div className="flex items-center gap-2">
                                <input
                                    type={showApiKey ? 'text' : 'password'}
                                    readOnly
                                    value={localStorage.getItem(AUTH_STORAGE_KEY) || ''}
                                    className="flex-1 px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 font-mono outline-none"
                                />
                                <button type="button" onClick={() => setShowApiKey(s => !s)} className="p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300" title={showApiKey ? 'Hide' : 'Show'}>
                                    {showApiKey ? <EyeOff size={16} /> : <Eye size={16} />}
                                </button>
                                <button type="button" onClick={copyApiKey} className="p-2 text-gray-500 hover:text-gray-700 dark:hover:text-gray-300" title="Copy">
                                    <Copy size={16} />
                                </button>
                            </div>
                            <p className="text-xs text-gray-500 mt-1">Sent as the <code>X-Api-Key</code> header. This is this browser's copy — scripts/integrations need their own.</p>
                        </div>
                    )}

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2 border-t border-gray-100 dark:border-gray-700">
                        <div>
                            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Username</label>
                            <input type="text" value={authUsername} onChange={(e) => setAuthUsername(e.target.value)} className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" autoComplete="username" />
                        </div>
                        <div />
                        <div>
                            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">{settings.auth_enabled ? 'New password' : 'Password'}</label>
                            <input type="password" value={authPassword} onChange={(e) => setAuthPassword(e.target.value)} placeholder="At least 8 characters" className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" autoComplete="new-password" />
                        </div>
                        <div>
                            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Confirm password</label>
                            <input type="password" value={authPasswordConfirm} onChange={(e) => setAuthPasswordConfirm(e.target.value)} className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" autoComplete="new-password" />
                        </div>
                    </div>

                    {settings.auth_enabled && (
                        <label className="flex items-center gap-2 text-sm text-gray-600 dark:text-gray-300">
                            <input type="checkbox" checked={regenerateKeyOnSave} onChange={(e) => setRegenerateKeyOnSave(e.target.checked)} />
                            Also regenerate the API key (signs out every other session/script using the current one)
                        </label>
                    )}

                    {authMessage && (
                        <p className={`text-sm ${authMessage.type === 'error' ? 'text-red-600 dark:text-red-400' : 'text-emerald-600 dark:text-emerald-400'}`}>{authMessage.text}</p>
                    )}

                    <button
                        type="button"
                        onClick={handleSetCredentials}
                        disabled={savingAuth}
                        className="px-4 py-2 bg-rose-600 hover:bg-rose-700 text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50"
                    >
                        {savingAuth ? 'Saving...' : settings.auth_enabled ? 'Update credentials' : 'Secure this server'}
                    </button>
                </div>
            </div>

            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <Play className="w-5 h-5 text-purple-500" />
                        AI Strategy & Local LLM
                    </h2>
                </div>

                <div className="p-6 space-y-8">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                        {/* Provider Toggle */}
                        <div className="space-y-4">
                            <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
                                Active AI Provider
                            </label>
                            <div className="flex p-1 bg-gray-100 dark:bg-gray-700/50 rounded-xl border border-gray-200 dark:border-gray-600">
                                {['cloud', 'local', 'hybrid'].map((p) => (
                                    <button
                                        key={p}
                                        onClick={() => handleChange('ai_provider', p)}
                                        className={`flex-1 py-2 text-xs font-bold rounded-lg transition-all capitalize ${settings.ai_provider === p
                                                ? 'bg-white dark:bg-gray-600 text-indigo-600 dark:text-indigo-400 shadow-sm'
                                                : 'text-gray-500 hover:text-gray-700 dark:text-gray-400'
                                            }`}
                                    >
                                        {p}
                                    </button>
                                ))}
                            </div>
                            <p className="text-xs text-gray-500 leading-relaxed">
                                {settings.ai_provider === 'cloud' && "All tasks use Google Gemini (Internet required). Best quality."}
                                {settings.ai_provider === 'local' && "All tasks use your local server (llama.cpp/Ollama). 100% private."}
                                {settings.ai_provider === 'hybrid' && "Translate locally to save costs; Review/Research on Cloud for maximum accuracy."}
                            </p>
                        </div>

                        {/* Google Gemini API Key Input */}
                        <div className="space-y-3 p-4 rounded-xl bg-gray-50/80 dark:bg-gray-700/40 border border-gray-200/80 dark:border-gray-600/80">
                            <div className="flex items-center justify-between">
                                <label className="text-xs font-bold text-gray-700 dark:text-gray-300 uppercase tracking-wider flex items-center gap-1.5">
                                    <Key className="w-3.5 h-3.5 text-indigo-500" />
                                    Google Gemini API Key
                                </label>
                                <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full flex items-center gap-1.5 ${
                                    hasGeminiKey
                                        ? 'bg-emerald-100 dark:bg-emerald-950/50 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800/60'
                                        : 'bg-amber-100 dark:bg-amber-950/50 text-amber-700 dark:text-amber-300 border border-amber-200 dark:border-amber-800/60'
                                }`}>
                                    <span className={`w-1.5 h-1.5 rounded-full ${hasGeminiKey ? 'bg-emerald-500' : 'bg-amber-500'}`} />
                                    {hasGeminiKey ? 'Active & Configured' : 'Not Configured'}
                                </span>
                            </div>
                            <div className="flex gap-2">
                                <div className="relative flex-1">
                                    <Key className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 w-4 h-4" />
                                    <input
                                        type={showGeminiKey ? "text" : "password"}
                                        value={geminiKeyInput}
                                        onChange={(e) => setGeminiKeyInput(e.target.value)}
                                        className="w-full pl-10 pr-10 py-2.5 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 text-gray-900 dark:text-white focus:ring-2 focus:ring-indigo-500 outline-none transition-all text-sm font-mono"
                                        placeholder={hasGeminiKey ? "•••••••••••••••• (Leave blank to keep current key, or enter new key)" : "Paste your Google Gemini API key (AIza...)"}
                                    />
                                    <button
                                        type="button"
                                        onClick={() => setShowGeminiKey(!showGeminiKey)}
                                        className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-1"
                                        title={showGeminiKey ? "Hide key" : "Show key"}
                                    >
                                        {showGeminiKey ? <EyeOff size={15} /> : <Eye size={15} />}
                                    </button>
                                </div>
                                <button
                                    type="button"
                                    onClick={handleTestGeminiKey}
                                    disabled={testingGeminiKey || (!geminiKeyInput.trim() && !hasGeminiKey)}
                                    className="shrink-0 px-3.5 py-2.5 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700 border border-gray-200 dark:border-gray-600 text-gray-700 dark:text-gray-200 rounded-xl font-medium transition-all text-xs flex items-center gap-1.5 disabled:opacity-50"
                                    title="Test Gemini API connection"
                                >
                                    <Zap className="w-3.5 h-3.5 text-amber-500" />
                                    {testingGeminiKey ? 'Testing...' : 'Test Key'}
                                </button>
                                <button
                                    type="button"
                                    onClick={handleSaveGeminiKey}
                                    disabled={updatingGeminiKey || !geminiKeyInput.trim()}
                                    className="shrink-0 px-4 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl font-medium transition-all text-xs disabled:opacity-50 shadow-sm"
                                >
                                    {updatingGeminiKey ? 'Saving...' : 'Save Key'}
                                </button>
                                {hasGeminiKey && (
                                    <button
                                        type="button"
                                        onClick={handleDeleteGeminiKey}
                                        disabled={updatingGeminiKey}
                                        className="shrink-0 px-3.5 py-2.5 bg-rose-50 hover:bg-rose-100 text-rose-700 dark:bg-rose-950/20 dark:hover:bg-rose-900/40 dark:text-rose-400 rounded-xl font-medium transition-all text-xs disabled:opacity-50"
                                        title="Remove configured key"
                                    >
                                        <Trash2 className="w-3.5 h-3.5" />
                                    </button>
                                )}
                            </div>
                            <div className="flex items-center justify-between text-xs text-gray-500 pt-1">
                                <span>Used by the translator, glossary extractor, and scene analyzer.</span>
                                <button
                                    type="button"
                                    onClick={() => window.dispatchEvent(new CustomEvent('omnisub:open-api-key-modal'))}
                                    className="text-indigo-600 dark:text-indigo-400 hover:underline font-medium"
                                >
                                    Open API Key Manager ↗
                                </button>
                            </div>
                        </div>

                        {/* Local Endpoint */}
                        <div className="space-y-4">
                            <label className="block text-sm font-bold text-gray-700 dark:text-gray-300 uppercase tracking-wider">
                                Local LLM Endpoint
                            </label>
                            <div className="flex gap-2">
                                <div className="relative flex-1">
                                    <LinkIcon className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 w-4 h-4" />
                                    <input
                                        type="text"
                                        value={settings.local_llm_base_url || ''}
                                        onChange={(e) => handleChange('local_llm_base_url', e.target.value)}
                                        className="w-full pl-10 pr-4 py-2.5 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all text-sm font-mono"
                                        placeholder="http://localhost:1234/v1"
                                    />
                                </div>
                                <button
                                    type="button"
                                    onClick={handleTestLocalLLM}
                                    disabled={testingLocalLLM || !settings.local_llm_base_url}
                                    className="shrink-0 px-4 py-2.5 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 dark:bg-indigo-950/20 dark:hover:bg-indigo-900/40 dark:text-indigo-400 rounded-xl font-medium transition-all text-sm disabled:opacity-50"
                                >
                                    {testingLocalLLM ? 'Testing...' : 'Test Connection'}
                                </button>
                            </div>
                            <p className="text-xs text-gray-500">Must include /v1 suffix for compatibility.</p>
                        </div>
                    </div>

                    {(settings.ai_provider === 'local' || settings.ai_provider === 'hybrid') && (
                        <div className="pt-6 border-t border-gray-100 dark:border-gray-700 animate-in fade-in slide-in-from-top-2 duration-300">
                            <h3 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider mb-6 flex items-center gap-2">
                                <Zap className="w-4 h-4 text-amber-500" />
                                {settings.ai_provider === 'hybrid' ? 'Hybrid Model Routing' : 'Local Model Overrides'}
                            </h3>

                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
                                <ModelCombobox
                                    label="Local Translation Model"
                                    value={settings.local_translation_model}
                                    onChange={(v) => handleChange('local_translation_model', v)}
                                    placeholder="Uses default if empty..."
                                />
                                <ModelCombobox
                                    label="Local Scan Model"
                                    value={settings.local_scan_model}
                                    onChange={(v) => handleChange('local_scan_model', v)}
                                    placeholder="Uses translator if empty..."
                                />
                                <ModelCombobox
                                    label="Local Review Model"
                                    value={settings.local_review_model}
                                    onChange={(v) => handleChange('local_review_model', v)}
                                    placeholder={settings.ai_provider === 'hybrid' ? "Defaults to Cloud..." : "Uses translator if empty..."}
                                />
                            </div>
                            {settings.ai_provider === 'hybrid' && (
                                <div className="mt-4 p-4 rounded-xl bg-indigo-50/50 dark:bg-indigo-900/10 border border-indigo-100 dark:border-indigo-900/30">
                                    <p className="text-xs text-indigo-700 dark:text-indigo-300 leading-relaxed italic">
                                        <b>Hybrid Logic:</b> Translation, Scan, and Consistency checks will use your local hardware. Research, Episode Summaries, and the Reviewer Agent will remain on Cloud for complex reasoning.
                                    </p>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>

            {/* Settings Form */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <Zap className="w-5 h-5 text-indigo-500" />
                        Defaults
                    </h2>
                </div>

                <div className="p-6 space-y-6">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                Default Target Language
                            </label>
                            <div className="relative">
                                <Globe className="absolute left-3 top-1/2 -translate-y-1/2 text-gray-400 w-4 h-4" />
                                <input
                                    type="text"
                                    value={settings.default_target_language}
                                    onChange={(e) => handleChange('default_target_language', e.target.value)}
                                    className="w-full pl-10 pr-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                    placeholder="e.g. Greek, Spanish"
                                />
                            </div>
                            <p className="text-xs text-gray-500 mt-1">Used when creating new projects.</p>
                        </div>

                        <ModelCombobox
                            label="Default Translation Model"
                            value={settings.default_translation_model}
                            onChange={(v) => handleChange('default_translation_model', v)}
                        />

                        <ModelCombobox
                            label="Fallback Translation Model"
                            value={settings.fallback_translation_model || ''}
                            onChange={(v) => handleChange('fallback_translation_model', v)}
                        />

                        <ModelCombobox
                            label="Default Context Model"
                            value={settings.default_context_model}
                            onChange={(v) => handleChange('default_context_model', v)}
                        />

                        <ModelCombobox
                            label="Default Glossary Model"
                            value={settings.default_glossary_model}
                            onChange={(v) => handleChange('default_glossary_model', v)}
                        />

                        <ModelCombobox
                            label="Default Scan Model"
                            value={settings.default_scan_model}
                            onChange={(v) => handleChange('default_scan_model', v)}
                        />

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                Concurrent Scenes (Parallel Limit)
                            </label>
                            <input
                                type="number"
                                min="1"
                                max="10"
                                value={settings.concurrent_scenes || 3}
                                onChange={(e) => handleChange('concurrent_scenes', parseInt(e.target.value, 10))}
                                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                            />
                            <p className="text-xs text-gray-500 mt-1">Lower this if using local LLMs to prevent GPU/RAM crashes (Default: 3).</p>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                Max Lines per Scene (Chunk Size)
                            </label>
                            <input
                                type="number"
                                min="10"
                                max="1000"
                                value={settings.max_lines_per_scene || 200}
                                onChange={(e) => handleChange('max_lines_per_scene', parseInt(e.target.value, 10))}
                                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                            />
                            <p className="text-xs text-gray-500 mt-1">Split large scenes if they exceed this limit. 200 is recommended for most models.</p>
                        </div>

                        <div>
                            <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                Scene Lookahead Lines
                            </label>
                            <input
                                type="number"
                                min="0"
                                max="10"
                                value={settings.scene_lookahead_lines ?? 2}
                                onChange={(e) => handleChange('scene_lookahead_lines', parseInt(e.target.value, 10))}
                                className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                            />
                            <p className="text-xs text-gray-500 mt-1">Number of lines from the next scene to include as context. (Default: 2).</p>
                        </div>

                        <div className="md:col-span-2 space-y-4 pt-4 border-t border-gray-100 dark:border-gray-700">
                            <h3 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">Post-Processing (SubtitleEdit)</h3>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                        SubtitleEdit Path
                                    </label>
                                    <div className="flex gap-2">
                                        <input
                                            type="text"
                                            value={settings.subtitle_edit_path || ''}
                                            onChange={(e) => handleChange('subtitle_edit_path', e.target.value)}
                                            className="flex-1 min-w-0 px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                            placeholder="P:\SubtitleEdit\SubtitleEdit.exe"
                                        />
                                        <button
                                            type="button"
                                            onClick={handleBrowseSubtitleEdit}
                                            disabled={browsing}
                                            className="shrink-0 flex items-center gap-2 px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-gray-50 dark:bg-gray-700 hover:bg-gray-100 dark:hover:bg-gray-600 text-sm font-medium text-gray-700 dark:text-gray-200 transition-colors disabled:opacity-50"
                                            title="Browse for SubtitleEdit.exe (Windows file picker)"
                                        >
                                            <FolderOpen size={16} />
                                            {browsing ? 'Browsing…' : 'Browse'}
                                        </button>
                                    </div>
                                    <p className="text-xs text-gray-500 mt-1">Path to the SubtitleEdit.exe for automated fixes. The Browse button opens a Windows file picker on the machine running Omnisub.</p>
                                </div>

                                <div className="flex items-center gap-3 pt-6">
                                    <label className="relative inline-flex items-center cursor-pointer">
                                        <input
                                            type="checkbox"
                                            className="sr-only peer"
                                            checked={settings.apply_subtitle_edit_fixes || false}
                                            onChange={(e) => handleChange('apply_subtitle_edit_fixes', e.target.checked)}
                                        />
                                        <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-indigo-300 dark:peer-focus:ring-indigo-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-indigo-600"></div>
                                        <span className="ml-3 text-sm font-medium text-gray-900 dark:text-gray-300">Auto-Apply Fixes & Split Long Lines</span>
                                    </label>
                                </div>
                            </div>
                        </div>

                        <div className="md:col-span-2 space-y-4 pt-4 border-t border-gray-100 dark:border-gray-700">
                            <h3 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">Embedded Subtitles (ffmpeg)</h3>
                            <p className="text-xs text-gray-500">
                                When a media file has no subtitle sitting next to it, look for an <span className="font-mono">.ass</span> track
                                muxed inside the container and extract it automatically. Extraction runs in the background queue — it reads the
                                whole media file, so it is slow on network shares and never blocks translation.
                            </p>

                            <div className="flex items-center gap-3">
                                <label className="relative inline-flex items-center cursor-pointer">
                                    <input
                                        type="checkbox"
                                        className="sr-only peer"
                                        checked={settings.embedded_extraction_enabled || false}
                                        onChange={(e) => handleChange('embedded_extraction_enabled', e.target.checked)}
                                    />
                                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-indigo-300 dark:peer-focus:ring-indigo-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-indigo-600"></div>
                                    <span className="ml-3 text-sm font-medium text-gray-900 dark:text-gray-300">Extract embedded .ass tracks</span>
                                </label>
                            </div>

                            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                        ffmpeg Path
                                    </label>
                                    <input
                                        type="text"
                                        value={settings.ffmpeg_path || ''}
                                        onChange={(e) => handleChange('ffmpeg_path', e.target.value)}
                                        className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                        placeholder="Leave empty to use ffmpeg from PATH"
                                    />
                                    <p className="text-xs text-gray-500 mt-1">Path to <span className="font-mono">ffmpeg.exe</span> or the folder containing it (<span className="font-mono">ffprobe</span> is found alongside it). Empty = look on PATH.</p>
                                </div>

                                <div>
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                        Concurrent Extractions
                                    </label>
                                    <input
                                        type="number"
                                        min="1"
                                        max="8"
                                        value={settings.embedded_extraction_concurrency ?? 1}
                                        onChange={(e) => handleChange('embedded_extraction_concurrency', parseInt(e.target.value, 10))}
                                        className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                    />
                                    <p className="text-xs text-gray-500 mt-1">Separate from the translation worker. Keep at 1 for network shares — parallel extractions compete for the same bandwidth. (Default: 1).</p>
                                </div>

                                <div className="md:col-span-2">
                                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                                        Lower-Priority Track Keywords
                                    </label>
                                    <input
                                        type="text"
                                        value={settings.embedded_deprioritize_keywords ?? ''}
                                        onChange={(e) => handleChange('embedded_deprioritize_keywords', e.target.value)}
                                        className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                        placeholder="signs, songs, s&s, signs &amp; songs, forced, commentary, karaoke"
                                    />
                                    <p className="text-xs text-gray-500 mt-1">
                                        Comma-separated. Tracks whose title contains one of these are picked <span className="font-semibold">last</span>, not skipped —
                                        so a release whose only track is labelled &quot;Signs &amp; Songs&quot; but also carries the dialogue still works.
                                        A full dialogue track always wins when one exists.
                                    </p>
                                </div>

                                {/* Interactive Video File Probe Test Tool */}
                                <div className="md:col-span-2 pt-4 border-t border-gray-100 dark:border-gray-700/80">
                                    <div className="p-4 bg-gray-50 dark:bg-gray-750 rounded-xl border border-gray-200 dark:border-gray-700 space-y-3">
                                        <div className="flex items-center justify-between">
                                            <div>
                                                <h4 className="text-xs font-bold text-gray-700 dark:text-gray-200 uppercase tracking-wider flex items-center gap-2">
                                                    <Film size={14} className="text-purple-500" />
                                                    Test Video File Subtitle Probe
                                                </h4>
                                                <p className="text-xs text-gray-400 mt-0.5">
                                                    Inspect all muxed subtitle streams and test suitability ranking on any local video container (MKV/MP4).
                                                </p>
                                            </div>
                                        </div>

                                        <div className="flex gap-2">
                                            <input
                                                type="text"
                                                value={probeTestPath}
                                                onChange={(e) => setProbeTestPath(e.target.value)}
                                                placeholder="e.g. D:\Anime\Show\Show.S01E01.mkv"
                                                className="flex-1 px-3 py-2 text-xs font-mono rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none"
                                            />
                                            <button
                                                type="button"
                                                onClick={handleRunMediaProbeTest}
                                                disabled={probeTestLoading || !probeTestPath.trim()}
                                                className="px-4 py-2 text-xs font-bold text-white bg-purple-600 hover:bg-purple-700 disabled:opacity-50 rounded-lg flex items-center gap-1.5 transition-colors shadow-sm"
                                            >
                                                <RefreshCw size={13} className={probeTestLoading ? 'animate-spin' : ''} />
                                                {probeTestLoading ? 'Probing...' : 'Probe Container'}
                                            </button>
                                        </div>

                                        {probeTestError && (
                                            <div className="p-3 bg-rose-50 dark:bg-rose-950/40 border border-rose-200 dark:border-rose-800 rounded-lg text-xs text-rose-700 dark:text-rose-300">
                                                {probeTestError}
                                            </div>
                                        )}

                                        {probeTestResult && (
                                            <div className="space-y-3 pt-2">
                                                <div className="flex items-center justify-between text-xs text-gray-500 bg-white dark:bg-gray-800 p-2.5 rounded-lg border border-gray-150 dark:border-gray-700">
                                                    <span className="font-semibold text-gray-700 dark:text-gray-300 truncate max-w-sm" title={probeTestResult.media_path}>
                                                        {probeTestResult.media_filename} ({probeTestResult.file_size_mb} MB)
                                                    </span>
                                                    <span className="font-mono">
                                                        {probeTestResult.ass_tracks_count} ASS track(s) / {probeTestResult.total_tracks} total
                                                    </span>
                                                </div>

                                                {probeTestResult.tracks?.length === 0 ? (
                                                    <p className="text-xs text-gray-400 italic">No subtitle streams found in file.</p>
                                                ) : (
                                                    <div className="space-y-1.5 max-h-56 overflow-y-auto">
                                                        {probeTestResult.tracks.map((t) => (
                                                            <div
                                                                key={t.index}
                                                                className={`p-2.5 rounded-lg border text-xs flex items-center justify-between gap-3 ${
                                                                    t.is_recommended
                                                                        ? 'border-emerald-400 bg-emerald-50/40 dark:bg-emerald-950/20'
                                                                        : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800'
                                                                }`}
                                                            >
                                                                <div className="flex items-center gap-2 flex-wrap min-w-0">
                                                                    <span className="font-mono font-bold bg-gray-100 dark:bg-gray-700 px-1.5 py-0.5 rounded text-[11px]">
                                                                        #{t.index}
                                                                    </span>
                                                                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded uppercase ${
                                                                        t.is_ass
                                                                            ? 'bg-purple-100 text-purple-700 dark:bg-purple-900/40 dark:text-purple-300'
                                                                            : t.is_image
                                                                                ? 'bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300'
                                                                                : 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300'
                                                                    }`}>
                                                                        {t.codec}
                                                                    </span>
                                                                    <span className="font-semibold uppercase text-gray-600 dark:text-gray-300">
                                                                        {t.language || 'und'}
                                                                    </span>
                                                                    <span className="truncate max-w-[200px] text-gray-700 dark:text-gray-200 font-medium">
                                                                        {t.title || 'Untitled'}
                                                                    </span>
                                                                    {t.frames > 0 && (
                                                                        <span className="text-gray-400">({t.frames} cues)</span>
                                                                    )}
                                                                </div>

                                                                <div>
                                                                    {t.is_recommended ? (
                                                                        <span className="text-[11px] font-bold text-emerald-700 dark:text-emerald-300 bg-emerald-100 dark:bg-emerald-900/50 px-2 py-0.5 rounded-full">
                                                                            ★ Recommended
                                                                        </span>
                                                                    ) : t.penalized ? (
                                                                        <span className="text-[11px] font-medium text-amber-600 bg-amber-50 dark:bg-amber-950/40 px-2 py-0.5 rounded-full">
                                                                            Signs / Deprioritized
                                                                        </span>
                                                                    ) : null}
                                                                </div>
                                                            </div>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Advanced Model Config */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <Sparkles className="w-5 h-5 text-amber-500" />
                        Advanced Model Config
                    </h2>
                </div>
                <div className="p-6 space-y-6">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-8">
                        {/* Temperature */}
                        <div className="space-y-4">
                            <div className="flex justify-between items-center">
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Temperature</label>
                                <span className="inline-block px-2 py-0.5 rounded-lg bg-indigo-100 text-indigo-700 text-xs font-bold font-mono">
                                    {(settings.temperature ?? 0.3).toFixed(2)}
                                </span>
                            </div>
                            <input
                                type="range"
                                min="0"
                                max="2"
                                step="0.05"
                                value={settings.temperature ?? 0.3}
                                onChange={(e) => handleChange('temperature', parseFloat(e.target.value))}
                                className="w-full h-1.5 bg-gray-200 dark:bg-gray-700 rounded-lg appearance-none cursor-pointer accent-indigo-600"
                            />
                            <p className="text-xs text-gray-500">
                                Controls creativity. **0.0 - 0.3** is best for consistent translations. **0.5+** adds variety but might ignore glossary terms.
                            </p>
                        </div>

                        {/* Top-P */}
                        <div className="space-y-4">
                            <div className="flex justify-between items-center">
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Top-P (Nucleus Sampling)</label>
                                <span className="inline-block px-2 py-0.5 rounded-lg bg-indigo-100 text-indigo-700 text-xs font-bold font-mono">
                                    {(settings.top_p ?? 1.0).toFixed(2)}
                                </span>
                            </div>
                            <input
                                type="range"
                                min="0.1"
                                max="1"
                                step="0.01"
                                value={settings.top_p ?? 1.0}
                                onChange={(e) => handleChange('top_p', parseFloat(e.target.value))}
                                className="w-full h-1.5 bg-gray-200 dark:bg-gray-700 rounded-lg appearance-none cursor-pointer accent-indigo-600"
                            />
                            <p className="text-xs text-gray-500">
                                Limits vocabulary to the most likely tokens. **0.9** is a safe choice for local models. **1.0** uses everything.
                            </p>
                        </div>

                        {/* Top-K */}
                        <div className="space-y-4">
                            <div className="flex justify-between items-center">
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Top-K</label>
                                <span className="inline-block px-2 py-0.5 rounded-lg bg-indigo-100 text-indigo-700 text-xs font-bold font-mono">
                                    {settings.top_k ?? 40}
                                </span>
                            </div>
                            <input
                                type="range"
                                min="1"
                                max="100"
                                step="1"
                                value={settings.top_k ?? 40}
                                onChange={(e) => handleChange('top_k', parseInt(e.target.value, 10))}
                                className="w-full h-1.5 bg-gray-200 dark:bg-gray-700 rounded-lg appearance-none cursor-pointer accent-indigo-600"
                            />
                            <p className="text-xs text-gray-500">
                                Filters the top K tokens. **40** is standard. Lowering this makes the model more predictable.
                            </p>
                        </div>

                        <div className="bg-amber-50 dark:bg-amber-900/10 p-4 rounded-xl border border-amber-100 dark:border-amber-900/30">
                            <div className="flex gap-3">
                                <Info className="w-5 h-5 text-amber-500 flex-shrink-0" />
                                <div className="space-y-1">
                                    <p className="text-xs font-bold text-amber-800 dark:text-amber-400 uppercase tracking-wider">Expert Mode Tip</p>
                                    <p className="text-xs text-amber-700 dark:text-amber-300 leading-relaxed">
                                        For Local Models (Gemma), a temperature of **0.5**, Top-P of **0.9**, and Top-K of **64** are usually optimal for the "Thinking" stage.
                                    </p>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Advanced AI Features */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <Database className="w-5 h-5 text-indigo-500" />
                        Advanced AI Features
                    </h2>
                </div>
                <div className="p-6 space-y-8">
                    {/* Translation Memory */}
                    <div className="space-y-4">
                        <div className="flex items-center justify-between">
                            <h3 className="text-md font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                <BookOpen className="w-4 h-4 text-indigo-400" /> Translation Memory (TM)
                            </h3>
                            <label className="relative inline-flex items-center cursor-pointer">
                                <input
                                    type="checkbox"
                                    className="sr-only peer"
                                    checked={settings.tm_enabled ?? true}
                                    onChange={(e) => handleChange('tm_enabled', e.target.checked)}
                                />
                                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-indigo-300 dark:peer-focus:ring-indigo-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-indigo-600"></div>
                            </label>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pl-6 border-l-2 border-indigo-100 dark:border-indigo-900/30">
                            <div className="space-y-2">
                                <div className="flex justify-between">
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Fuzzy Match Threshold</label>
                                    <span className="text-xs font-mono text-indigo-600">{(settings.tm_similarity_threshold ?? 0.80).toFixed(2)}</span>
                                </div>
                                <input type="range" min="0.5" max="0.99" step="0.01" value={settings.tm_similarity_threshold ?? 0.80} onChange={(e) => handleChange('tm_similarity_threshold', parseFloat(e.target.value))} className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-indigo-600" />
                                <p className="text-xs text-gray-500">Minimum similarity to use a TM entry as context.</p>
                            </div>
                            <div className="space-y-2">
                                <div className="flex justify-between">
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Exact Match Threshold</label>
                                    <span className="text-xs font-mono text-indigo-600">{(settings.tm_exact_match_threshold ?? 0.95).toFixed(2)}</span>
                                </div>
                                <input type="range" min="0.8" max="1.0" step="0.01" value={settings.tm_exact_match_threshold ?? 0.95} onChange={(e) => handleChange('tm_exact_match_threshold', parseFloat(e.target.value))} className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-indigo-600" />
                                <p className="text-xs text-gray-500">Threshold to auto-replace without translating.</p>
                            </div>
                            <div className="space-y-2">
                                <div className="flex justify-between">
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Max Words for Exact Reuse</label>
                                    <span className="text-xs font-mono text-indigo-600">{settings.tm_exact_reuse_max_words ?? 6}</span>
                                </div>
                                <input type="number" min="1" max="100" value={settings.tm_exact_reuse_max_words ?? 6} onChange={(e) => handleChange('tm_exact_reuse_max_words', parseInt(e.target.value, 10))} className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" />
                                <p className="text-xs text-gray-500">Only auto-reuse TM match if line has at most this many words.</p>
                            </div>
                            <div className="flex items-center gap-3 pt-6">
                                <label className="relative inline-flex items-center cursor-pointer">
                                    <input
                                        type="checkbox"
                                        className="sr-only peer"
                                        checked={settings.tm_exact_reuse_require_same_speaker ?? false}
                                        onChange={(e) => handleChange('tm_exact_reuse_require_same_speaker', e.target.checked)}
                                    />
                                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-indigo-300 dark:peer-focus:ring-indigo-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-indigo-600"></div>
                                    <span className="ml-3 text-sm font-medium text-gray-900 dark:text-gray-300">Require Same Speaker</span>
                                </label>
                            </div>
                        </div>
                    </div>

                    {/* AI Reviewer */}
                    <div className="space-y-4 pt-4 border-t border-gray-100 dark:border-gray-700">
                        <div className="flex items-center justify-between">
                            <h3 className="text-md font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                <ShieldAlert className="w-4 h-4 text-rose-400" /> AI Reviewer
                            </h3>
                            <label className="relative inline-flex items-center cursor-pointer">
                                <input
                                    type="checkbox"
                                    className="sr-only peer"
                                    checked={settings.enable_reviewer ?? false}
                                    onChange={(e) => handleChange('enable_reviewer', e.target.checked)}
                                />
                                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-rose-300 dark:peer-focus:ring-rose-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-rose-600"></div>
                            </label>
                        </div>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pl-6 border-l-2 border-rose-100 dark:border-rose-900/30">
                            <ModelCombobox label="Reviewer Model" value={settings.review_model} onChange={(v) => handleChange('review_model', v)} />
                            <div className="space-y-2">
                                <div className="flex justify-between">
                                    <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Max Review %</label>
                                    <span className="text-xs font-mono text-rose-600">{Math.round((settings.review_max_pct ?? 0.25) * 100)}%</span>
                                </div>
                                <input type="range" min="0.05" max="1.0" step="0.05" value={settings.review_max_pct ?? 0.25} onChange={(e) => handleChange('review_max_pct', parseFloat(e.target.value))} className="w-full h-1.5 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-rose-600" />
                                <p className="text-xs text-gray-500">Max % of lines to flag per episode.</p>
                            </div>
                        </div>
                    </div>

                    {/* Episode Summaries */}
                    <div className="space-y-4 pt-4 border-t border-gray-100 dark:border-gray-700">
                        <div className="flex items-center justify-between">
                            <h3 className="text-md font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                <List className="w-4 h-4 text-emerald-400" /> Episode Summaries
                            </h3>
                            <label className="relative inline-flex items-center cursor-pointer">
                                <input
                                    type="checkbox"
                                    className="sr-only peer"
                                    checked={settings.episode_summaries_enabled ?? true}
                                    onChange={(e) => handleChange('episode_summaries_enabled', e.target.checked)}
                                />
                                <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-emerald-300 dark:peer-focus:ring-emerald-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-emerald-600"></div>
                            </label>
                        </div>
                        <div className="pl-6 border-l-2 border-emerald-100 dark:border-emerald-900/30">
                            <div className="w-1/2 space-y-2">
                                <label className="text-sm font-medium text-gray-700 dark:text-gray-300">Summary Context Window</label>
                                <input type="number" min="1" max="10" value={settings.episode_summary_window ?? 3} onChange={(e) => handleChange('episode_summary_window', parseInt(e.target.value, 10))} className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-emerald-500 outline-none transition-all" />
                                <p className="text-xs text-gray-500">Number of previous episode summaries to include as context.</p>
                            </div>
                        </div>
                    </div>
                </div>
            </div>

            {/* Translation quality & cost (v2) */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Translation Quality &amp; Cost</h2>
                    <p className="text-sm text-gray-500 dark:text-gray-400">Pick a preset, or tune the individual passes. Whole-episode mode + thinking-off are the big Gemini cost savers.</p>
                </div>
                <div className="px-6 pt-5">
                    <div className="text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Quality preset</div>
                    <div className="flex flex-wrap gap-2">
                        {Object.entries(QUALITY_PRESETS).map(([name, preset]) => (
                            <button key={name} type="button" onClick={() => applyQualityPreset(name)} title={preset.desc}
                                className={`px-4 py-2 rounded-xl text-sm font-semibold border transition-all ${activePreset === name
                                    ? 'bg-indigo-600 text-white border-indigo-600'
                                    : 'bg-white dark:bg-gray-700 text-gray-700 dark:text-gray-200 border-gray-200 dark:border-gray-600 hover:border-indigo-400'}`}>
                                {preset.label}
                            </button>
                        ))}
                        {activePreset === null && <span className="self-center text-xs text-gray-400 italic">Custom</span>}
                    </div>
                    <p className="text-xs text-gray-500 mt-2">{activePreset ? QUALITY_PRESETS[activePreset].desc : 'Your toggles below don’t match a preset. Pick one for a known-good combination.'}</p>
                </div>
                <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                        <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Request mode</label>
                        <select value={settings.episode_request_mode ?? 'auto'} onChange={(e) => handleChange('episode_request_mode', e.target.value)}
                            className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none">
                            <option value="auto">Auto (whole-episode for Gemini, scenes for local)</option>
                            <option value="whole">Whole episode (one request)</option>
                            <option value="scenes">Scenes (chunked)</option>
                        </select>
                        <p className="text-xs text-gray-500 mt-1">Whole-episode keeps consistency by construction and cuts requests ~10× on Gemini.</p>
                    </div>
                    <div>
                        <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Daily budget (USD, 0 = off)</label>
                        <input type="number" min="0" step="0.5" value={settings.daily_budget_usd ?? 0} onChange={(e) => handleChange('daily_budget_usd', parseFloat(e.target.value))}
                            className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" />
                        <p className="text-xs text-gray-500 mt-1">Pauses the batch lane once today’s recorded spend crosses this cap.</p>
                    </div>
                    <div>
                        <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Reviewer episode sample %</label>
                        <input type="number" min="0" max="100" value={Math.round((settings.review_episode_sample_pct ?? 1) * 100)} onChange={(e) => handleChange('review_episode_sample_pct', Math.max(0, Math.min(100, parseInt(e.target.value || '0', 10))) / 100)}
                            className="w-full px-3 py-2 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" />
                        <p className="text-xs text-gray-500 mt-1">Fraction of episodes the LLM reviewer runs on (a quality trend signal, not a per-episode gate).</p>
                    </div>
                </div>
                <div className="px-6 pb-6 grid grid-cols-1 md:grid-cols-2 gap-5">
                    {[
                        ['glossary_enforce_enabled', 'Glossary enforcement', 'Zero-token pass that fixes glossary names left untranslated in the output.'],
                        ['repair_pass_enabled', 'QC repair pass', 'Validators (tags, untranslated lines, wrong alphabet) → one batched repair call per episode.'],
                        ['script_guard_enabled', 'Script guard', 'Zero-token pass that fixes look-alike letters from the wrong alphabet (Latin "v" for Greek "ν"). Ambiguous cases go to the repair pass.', true],
                        ['intra_episode_reconcile_enabled', 'Intra-episode consistency', 'Align recurring term renderings within an episode after translation.'],
                        ['new_terms_suggest_enabled', 'Harvest new glossary terms', 'Collect new proper nouns from the translation response (zero extra calls).'],
                        ['sequential_scenes', 'Sequential scenes', 'Scene mode only: translate scenes in order so each sees the previous one (slower).'],
                        ['source_clean_enabled', 'Source subtitle cleaning', 'Strip SDH, fix split cues and junk lines before translating.'],
                        ['condense_enabled', 'Semantic condensation', 'Shorten over-fast cues that exceed CPS limits (requires conformance).'],
                        ['autobalance_export_lines', 'Auto-balance exported lines', 'SubtitleEdit-style: re-wrap over-long cues into balanced lines; cues still needing 3+ lines are split into two cues sharing the duration (output file only, editor untouched).', true],
                    ].map(([key, label, desc, defOn]) => (
                        <div key={key} className="flex items-start justify-between gap-3 bg-gray-50/40 dark:bg-gray-800/40 p-4 rounded-xl border border-gray-100 dark:border-gray-700">
                            <div>
                                <div className="text-sm font-semibold text-gray-900 dark:text-white">{label}</div>
                                <div className="text-xs text-gray-500 dark:text-gray-400">{desc}</div>
                            </div>
                            <label className="relative inline-flex items-center cursor-pointer shrink-0">
                                <input type="checkbox" className="sr-only peer" checked={settings[key] ?? !!defOn} onChange={(e) => handleChange(key, e.target.checked)} />
                                <div className="w-11 h-6 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-indigo-600"></div>
                            </label>
                        </div>
                    ))}
                </div>
            </div>

            {/* Advanced & Experimental */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white">Advanced &amp; Experimental</h2>
                    <p className="text-sm text-gray-500 dark:text-gray-400">Queue throughput, cost optimization, scheduling, conformance and security.</p>
                </div>
                <div className="p-6 grid grid-cols-1 md:grid-cols-2 gap-5">
                    {[
                        ['batch_api_enabled', 'Batch API for backlog', 'Cheaper (50%), async bulk draft of the backlog — same glossary/context as live; flagged lines refined in the editor.'],
                        ['context_cache_enabled', 'Gemini context caching', 'Cache the stable glossary/context per project to cut token cost.'],
                        ['adaptive_concurrency_enabled', 'Adaptive concurrency', 'Auto-tune parallel episodes from observed rate limits.'],
                        ['conformance_enabled', 'Subtitle conformance', 'Enforce reading speed / line length; flag violations.'],
                        ['worker_schedule_enabled', 'Off-peak scheduling', 'Only drain sync/backlog inside a time window.'],
                        ['structured_output_enabled', 'Structured JSON output', 'Schema-constrained translation output to prevent line misalignments.'],
                        ['morphology_normalization', 'Morphology-aware matching', 'Normalize text (accents, cases) for glossary and consistency checks.'],
                        ['morphology_stemming', 'Greek stemming', 'Use rule-based stemming for Greek (helps match inflected words).'],
                    ].map(([key, label, desc]) => (
                        <div key={key} className="flex items-start justify-between gap-3 bg-gray-50/40 dark:bg-gray-800/40 p-4 rounded-xl border border-gray-100 dark:border-gray-700">
                            <div>
                                <div className="text-sm font-semibold text-gray-900 dark:text-white">{label}</div>
                                <div className="text-xs text-gray-500 dark:text-gray-400">{desc}</div>
                            </div>
                            <label className="relative inline-flex items-center cursor-pointer shrink-0">
                                <input type="checkbox" className="sr-only peer" checked={settings[key] ?? false} onChange={(e) => handleChange(key, e.target.checked)} />
                                <div className="w-11 h-6 bg-gray-200 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-indigo-600"></div>
                            </label>
                        </div>
                    ))}
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Parallel episodes</label>
                            <input type="number" min="1" max="8" value={settings.concurrent_episodes ?? 2} onChange={(e) => handleChange('concurrent_episodes', parseInt(e.target.value, 10))} className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" />
                        </div>
                        <div>
                            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Max concurrency</label>
                            <input type="number" min="1" max="16" value={settings.concurrency_max ?? 6} onChange={(e) => handleChange('concurrency_max', parseInt(e.target.value, 10))} className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" />
                        </div>
                    </div>
                    <div className="grid grid-cols-2 gap-3">
                        <div>
                            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Off-peak window</label>
                            <div className="flex items-center gap-1">
                                <input type="time" value={settings.worker_window_start ?? '01:00'} onChange={(e) => handleChange('worker_window_start', e.target.value)} className="w-full px-2 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" />
                                <span className="text-gray-400">–</span>
                                <input type="time" value={settings.worker_window_end ?? '08:00'} onChange={(e) => handleChange('worker_window_end', e.target.value)} className="w-full px-2 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" />
                            </div>
                        </div>
                        <div>
                            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Max CPS</label>
                            <input type="number" min="10" max="30" value={settings.max_cps ?? 17} onChange={(e) => handleChange('max_cps', parseFloat(e.target.value))} className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" />
                        </div>
                    </div>
                    <div className="md:col-span-2">
                        <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Webhook secret (token required on /api/webhook/*)</label>
                        <input type="password" value={settings.webhook_secret ?? ''} onChange={(e) => handleChange('webhook_secret', e.target.value)} placeholder="auto-generated — append ?token=... to your Sonarr/Radarr webhook URL" className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 outline-none" />
                        <p className="text-xs text-gray-500 mt-1">Required — a secret is generated automatically on first boot. If you clear and save this field, webhook calls are rejected until a new secret is set (they are never left open).</p>
                    </div>
                </div>
            </div>

            {/* Sonarr & Radarr Integration */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50 flex justify-between items-center">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <LinkIcon className="w-5 h-5 text-indigo-500" />
                        Media Managers Integration (Sonarr & Radarr)
                    </h2>
                </div>
                <div className="p-6 space-y-6">
                    {/* Connection Settings Grid */}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                        {/* Sonarr Card */}
                        <div className="bg-gray-50/40 dark:bg-gray-800/40 p-5 rounded-2xl border border-gray-100 dark:border-gray-700 space-y-4">
                            <div className="flex items-center justify-between">
                                <h3 className="text-sm font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                    <Tv className="w-4 h-4 text-blue-500" /> Sonarr (Series)
                                </h3>
                                <label className="relative inline-flex items-center cursor-pointer">
                                    <input
                                        type="checkbox"
                                        className="sr-only peer"
                                        checked={settings.sonarr_enabled ?? false}
                                        onChange={(e) => handleChange('sonarr_enabled', e.target.checked)}
                                    />
                                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-blue-300 dark:peer-focus:ring-blue-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-blue-600"></div>
                                </label>
                            </div>
                            <div className="space-y-3">
                                <div>
                                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Sonarr URL</label>
                                    <input
                                        type="text"
                                        value={settings.sonarr_url ?? "http://localhost:8989"}
                                        onChange={(e) => handleChange('sonarr_url', e.target.value)}
                                        className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-blue-500 outline-none transition-all"
                                        placeholder="http://localhost:8989"
                                    />
                                </div>
                                <div>
                                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">API Key</label>
                                    <div className="relative">
                                        <input
                                            type={showSonarrKey ? "text" : "password"}
                                            value={settings.sonarr_api_key ?? ""}
                                            onChange={(e) => handleChange('sonarr_api_key', e.target.value)}
                                            className="w-full pl-3 pr-9 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-blue-500 outline-none transition-all font-mono"
                                            placeholder="Enter Sonarr API Key"
                                        />
                                        <button
                                            type="button"
                                            onClick={() => setShowSonarrKey(!showSonarrKey)}
                                            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-0.5"
                                            title={showSonarrKey ? "Hide key" : "Show key"}
                                        >
                                            {showSonarrKey ? <EyeOff size={14} /> : <Eye size={14} />}
                                        </button>
                                    </div>
                                </div>
                                <button
                                    onClick={handleTestSonarr}
                                    disabled={testingSonarr}
                                    className="w-full px-3 py-1.5 bg-white dark:bg-gray-700 hover:bg-gray-50 dark:hover:bg-gray-600 border border-gray-200 dark:border-gray-600 text-xs font-semibold text-gray-700 dark:text-gray-200 rounded-lg flex items-center justify-center gap-1.5 transition-all disabled:opacity-50"
                                >
                                    <Activity className="w-3.5 h-3.5" />
                                    {testingSonarr ? 'Testing...' : 'Test Sonarr Connection'}
                                </button>
                            </div>
                        </div>

                        {/* Radarr Card */}
                        <div className="bg-gray-50/40 dark:bg-gray-800/40 p-5 rounded-2xl border border-gray-100 dark:border-gray-700 space-y-4">
                            <div className="flex items-center justify-between">
                                <h3 className="text-sm font-bold text-gray-900 dark:text-white flex items-center gap-2">
                                    <Film className="w-4 h-4 text-purple-500" /> Radarr (Movies)
                                </h3>
                                <label className="relative inline-flex items-center cursor-pointer">
                                    <input
                                        type="checkbox"
                                        className="sr-only peer"
                                        checked={settings.radarr_enabled ?? false}
                                        onChange={(e) => handleChange('radarr_enabled', e.target.checked)}
                                    />
                                    <div className="w-11 h-6 bg-gray-200 peer-focus:outline-none peer-focus:ring-4 peer-focus:ring-purple-300 dark:peer-focus:ring-purple-800 rounded-full peer dark:bg-gray-700 peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all dark:border-gray-600 peer-checked:bg-purple-600"></div>
                                </label>
                            </div>
                            <div className="space-y-3">
                                <div>
                                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Radarr URL</label>
                                    <input
                                        type="text"
                                        value={settings.radarr_url ?? "http://localhost:7878"}
                                        onChange={(e) => handleChange('radarr_url', e.target.value)}
                                        className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-purple-500 outline-none transition-all"
                                        placeholder="http://localhost:7878"
                                    />
                                </div>
                                <div>
                                    <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">API Key</label>
                                    <div className="relative">
                                        <input
                                            type={showRadarrKey ? "text" : "password"}
                                            value={settings.radarr_api_key ?? ""}
                                            onChange={(e) => handleChange('radarr_api_key', e.target.value)}
                                            className="w-full pl-3 pr-9 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-purple-500 outline-none transition-all font-mono"
                                            placeholder="Enter Radarr API Key"
                                        />
                                        <button
                                            type="button"
                                            onClick={() => setShowRadarrKey(!showRadarrKey)}
                                            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 dark:hover:text-gray-200 p-0.5"
                                            title={showRadarrKey ? "Hide key" : "Show key"}
                                        >
                                            {showRadarrKey ? <EyeOff size={14} /> : <Eye size={14} />}
                                        </button>
                                    </div>
                                </div>
                                <button
                                    onClick={handleTestRadarr}
                                    disabled={testingRadarr}
                                    className="w-full px-3 py-1.5 bg-white dark:bg-gray-700 hover:bg-gray-50 dark:hover:bg-gray-600 border border-gray-200 dark:border-gray-600 text-xs font-semibold text-gray-700 dark:text-gray-200 rounded-lg flex items-center justify-center gap-1.5 transition-all disabled:opacity-50"
                                >
                                    <Activity className="w-3.5 h-3.5" />
                                    {testingRadarr ? 'Testing...' : 'Test Radarr Connection'}
                                </button>
                            </div>
                        </div>
                    </div>

                    {/* Synchronization Policies */}
                    <div className="pt-4 border-t border-gray-100 dark:border-gray-700 space-y-4">
                        <h3 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">Sync & Translation Policies</h3>
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <div>
                                <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Auto-Sync Interval (Minutes)</label>
                                <input
                                    type="number"
                                    min="0" max="1440"
                                    value={settings.arr_sync_interval ?? 0}
                                    onChange={(e) => handleChange('arr_sync_interval', parseInt(e.target.value, 10))}
                                    className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                />
                                <p className="text-xs text-gray-500 mt-1">0 = manual only. Interval to poll Sonarr & Radarr for media and subtitle updates.</p>
                            </div>
                            <div className="flex flex-col justify-end pb-2">
                                <label className="flex items-center gap-3 cursor-pointer">
                                    <input
                                        type="checkbox"
                                        checked={settings.arr_auto_translate ?? false}
                                        onChange={(e) => handleChange('arr_auto_translate', e.target.checked)}
                                        className="rounded border-gray-300 text-indigo-600 focus:ring-indigo-500 h-4.5 w-4.5"
                                    />
                                    <div>
                                        <span className="text-sm font-medium text-gray-700 dark:text-gray-300">Auto-translate imported subtitles</span>
                                        <p className="text-xs text-gray-500">Automatically trigger translation pipeline immediately upon receiving a download webhook.</p>
                                    </div>
                                </label>
                            </div>
                        </div>
                        <div className="flex justify-end">
                            <button
                                onClick={handleOpenSyncModal}
                                disabled={syncingArr || (!settings.sonarr_enabled && !settings.radarr_enabled)}
                                className="px-5 py-2 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded-xl font-bold transition-all text-sm flex items-center gap-2 disabled:opacity-50"
                            >
                                <RefreshCw className={`w-4 h-4 ${syncingArr ? 'animate-spin' : ''}`} />
                                Sync Media Library Now
                            </button>
                        </div>
                    </div>

                    {/* Path Mappings */}
                    <div className="pt-6 border-t border-gray-100 dark:border-gray-700">
                        <div className="flex items-center justify-between mb-4">
                            <div>
                                <h3 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider">Path Mappings</h3>
                                <p className="text-xs text-gray-500">Map Sonarr/Radarr host filesystem paths to local network paths on this machine.</p>
                            </div>
                            <button
                                onClick={handleAddMapping}
                                className="flex items-center gap-1 text-xs font-medium text-indigo-600 hover:text-indigo-700 transition-colors"
                            >
                                <Plus size={14} /> Add Mapping
                            </button>
                        </div>

                        <div className="space-y-3">
                            {(settings.arr_path_mappings || []).map((mapping, index) => (
                                <div key={index} className="flex gap-3 items-start animate-in fade-in slide-in-from-top-2 duration-200">
                                    <div className="flex-1">
                                        <input
                                            type="text"
                                            value={mapping.remote}
                                            onChange={(e) => handleUpdateMapping(index, 'remote', e.target.value)}
                                            className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                            placeholder="Remote Path (e.g. /tv/Shows)"
                                        />
                                    </div>
                                    <div className="flex-shrink-0 pt-2 text-gray-400">→</div>
                                    <div className="flex-1">
                                        <input
                                            type="text"
                                            value={mapping.local}
                                            onChange={(e) => handleUpdateMapping(index, 'local', e.target.value)}
                                            className="w-full px-3 py-1.5 text-sm rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all"
                                            placeholder="Local Path (e.g. \\Server\TvShows)"
                                        />
                                    </div>
                                    <button
                                        onClick={() => handleRemoveMapping(index)}
                                        className="p-2 text-gray-400 hover:text-rose-500 transition-colors"
                                    >
                                        <Trash2 size={16} />
                                    </button>
                                </div>
                            ))}

                            {(settings.arr_path_mappings || []).length === 0 && (
                                <div className="text-center py-4 bg-gray-50 dark:bg-gray-800/50 rounded-xl border border-dashed border-gray-200 dark:border-gray-700">
                                    <p className="text-xs text-gray-400 font-medium italic">No path mappings configured.</p>
                                </div>
                            )}
                        </div>
                    </div>

                    {/* Webhook Configuration */}
                    <div className="pt-6 border-t border-gray-100 dark:border-gray-700">
                        <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider mb-2">Real-Time Webhook Configuration</h4>
                        <p className="text-xs text-gray-500 mb-3">To trigger translations instantly when a new subtitle is downloaded, configure webhooks in Sonarr and Radarr.</p>

                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                            {/* Sonarr Webhook */}
                            <div className="bg-gray-50 dark:bg-gray-800/30 border border-gray-100 dark:border-gray-700 rounded-xl p-4 space-y-2">
                                <span className="text-xs font-bold text-blue-600 dark:text-blue-400 uppercase">Sonarr Webhook URL</span>
                                <div className="flex gap-2">
                                    <input
                                        type="text"
                                        readOnly
                                        value={`${window.location.origin}/api/webhook/sonarr`}
                                        className="flex-1 px-2.5 py-1 text-xs font-mono rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 outline-none select-all"
                                    />
                                    <button
                                        onClick={() => {
                                            navigator.clipboard.writeText(`${window.location.origin}/api/webhook/sonarr`);
                                            setMessage({ type: 'success', text: 'Sonarr Webhook URL copied!' });
                                            setTimeout(() => setMessage(null), 3000);
                                        }}
                                        className="px-2.5 py-1 bg-white dark:bg-gray-700 hover:bg-gray-50 text-xs border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 transition-colors"
                                    >
                                        Copy
                                    </button>
                                </div>
                                <div className="text-[11px] text-gray-600 dark:text-gray-400 pt-1">
                                    In <b>Sonarr</b>: Go to Settings → Connect → Add Webhook. Enable "On Download" and "On Rename".
                                </div>
                            </div>

                            {/* Radarr Webhook */}
                            <div className="bg-gray-50 dark:bg-gray-800/30 border border-gray-100 dark:border-gray-700 rounded-xl p-4 space-y-2">
                                <span className="text-xs font-bold text-purple-600 dark:text-purple-400 uppercase">Radarr Webhook URL</span>
                                <div className="flex gap-2">
                                    <input
                                        type="text"
                                        readOnly
                                        value={`${window.location.origin}/api/webhook/radarr`}
                                        className="flex-1 px-2.5 py-1 text-xs font-mono rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 outline-none select-all"
                                    />
                                    <button
                                        onClick={() => {
                                            navigator.clipboard.writeText(`${window.location.origin}/api/webhook/radarr`);
                                            setMessage({ type: 'success', text: 'Radarr Webhook URL copied!' });
                                            setTimeout(() => setMessage(null), 3000);
                                        }}
                                        className="px-2.5 py-1 bg-white dark:bg-gray-700 hover:bg-gray-50 text-xs border border-gray-200 dark:border-gray-600 rounded-lg text-gray-700 dark:text-gray-300 transition-colors"
                                    >
                                        Copy
                                    </button>
                                </div>
                                <div className="text-[11px] text-gray-600 dark:text-gray-400 pt-1">
                                    In <b>Radarr</b>: Go to Settings → Connect → Add Webhook. Enable "On Download" and "On Rename".
                                </div>
                            </div>
                        </div>
                    </div>

                    {/* Path Diagnostics Validator */}
                    <div id="path-diagnostics" className="pt-6 border-t border-gray-100 dark:border-gray-700">
                        <h4 className="text-sm font-bold text-gray-900 dark:text-white uppercase tracking-wider mb-2">Path Diagnostics Tool</h4>
                        <p className="text-xs text-gray-500 mb-4">Validate that remote paths map correctly and resolved directories are accessible on the Omnisub server.</p>

                        <div className="flex gap-3">
                            <input
                                type="text"
                                value={diagnosticPath}
                                onChange={(e) => setDiagnosticPath(e.target.value)}
                                className="flex-1 px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all text-sm"
                                placeholder="Enter a remote *arr media file/folder path to test (e.g. /movies/Snow Queen (2002)/file.mkv)"
                            />
                            <button
                                onClick={handleTestPath}
                                disabled={isTestingPath || !diagnosticPath}
                                className="px-4 py-2 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 rounded-lg font-medium transition-all text-sm flex items-center gap-2 disabled:opacity-50"
                            >
                                {isTestingPath ? 'Checking...' : 'Verify Path'}
                            </button>
                        </div>

                        {diagnosticResult && (
                            <div className="mt-4 p-4 rounded-xl border text-sm animate-in fade-in slide-in-from-top-2 duration-300 bg-gray-50 dark:bg-gray-800/30 border-gray-100 dark:border-gray-700">
                                <div className="space-y-2">
                                    <div className="flex justify-between items-center pb-2 border-b border-gray-100 dark:border-gray-700">
                                        <span className="font-semibold text-gray-700 dark:text-gray-300">Resolved Path:</span>
                                        <code className="text-xs px-2 py-0.5 bg-gray-100 dark:bg-gray-700 rounded text-indigo-600 dark:text-indigo-400 font-mono select-all">{diagnosticResult.resolved_path}</code>
                                    </div>
                                    <div className="flex items-center justify-between">
                                        <span className="text-gray-600 dark:text-gray-400">Path Reachable:</span>
                                        {diagnosticResult.exists ? (
                                            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-green-50 text-green-700 dark:bg-green-900/20 dark:text-green-400">
                                                ● Reachable
                                            </span>
                                        ) : (
                                            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-50 text-red-700 dark:bg-red-900/20 dark:text-red-400">
                                                ● Unreachable
                                            </span>
                                        )}
                                    </div>
                                    <div className="flex items-center justify-between">
                                        <span className="text-gray-600 dark:text-gray-400">Readable / File:</span>
                                        <span className="text-xs text-gray-500 font-medium">
                                            {diagnosticResult.exists
                                                ? `${diagnosticResult.is_file ? 'File' : 'Directory'} (${diagnosticResult.readable ? 'Readable' : 'No Permission'})`
                                                : 'N/A'}
                                        </span>
                                    </div>
                                    {!diagnosticResult.exists && (
                                        <div className="text-xs text-amber-600 dark:text-amber-400 mt-2">
                                            ⚠️ Check that your local folder path exists, is shared, and Omnisub has file permission access.
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            </div>

            {/* Model Rate Limits Config */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <Activity className="w-5 h-5 text-indigo-500" />
                        Model Rate Limits (RPM / RPD)
                    </h2>
                </div>
                <div className="p-6 space-y-4">
                    <p className="text-xs text-gray-500">Configure Requests Per Minute (RPM) and Requests Per Day (RPD) limits for each model.</p>
                    <div className="overflow-x-auto">
                        <table className="w-full text-left border-collapse text-sm">
                            <thead>
                                <tr className="border-b border-gray-200 dark:border-gray-700 text-xs font-bold text-gray-400 uppercase tracking-wider">
                                    <th className="py-2.5">Model</th>
                                    <th className="py-2.5">RPM Limit</th>
                                    <th className="py-2.5">RPD Limit (0 = Unlimited)</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-gray-150 dark:divide-gray-700/50">
                                {['default', 'gemini-2.5-flash', 'gemini-2.5-pro', 'gemini-2.5-flash-lite'].map(modelKey => {
                                    const limits = settings.rate_limits?.[modelKey] || { rpm: 15, rpd: 500 };
                                    return (
                                        <tr key={modelKey}>
                                            <td className="py-3 font-semibold text-gray-700 dark:text-gray-300">
                                                {modelKey === 'default' ? <i>Default Fallback</i> : modelKey}
                                            </td>
                                            <td className="py-3">
                                                <input
                                                    type="number"
                                                    min="1"
                                                    value={limits.rpm}
                                                    onChange={(e) => {
                                                        const val = parseInt(e.target.value, 10) || 1;
                                                        const updatedLimits = { ...settings.rate_limits, [modelKey]: { ...limits, rpm: val } };
                                                        handleChange('rate_limits', updatedLimits);
                                                    }}
                                                    className="w-24 px-2 py-1 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-750 focus:ring-2 focus:ring-indigo-500 outline-none"
                                                />
                                            </td>
                                            <td className="py-3">
                                                <input
                                                    type="number"
                                                    min="0"
                                                    value={limits.rpd}
                                                    onChange={(e) => {
                                                        const val = parseInt(e.target.value, 10) >= 0 ? parseInt(e.target.value, 10) : 0;
                                                        const updatedLimits = { ...settings.rate_limits, [modelKey]: { ...limits, rpd: val } };
                                                        handleChange('rate_limits', updatedLimits);
                                                    }}
                                                    className="w-24 px-2 py-1 rounded-lg border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-750 focus:ring-2 focus:ring-indigo-500 outline-none"
                                                />
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            {/* Notifications Config */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <LinkIcon className="w-5 h-5 text-indigo-500" />
                        Notifications
                    </h2>
                </div>
                <div className="p-6 space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                            Discord Webhook URL
                        </label>
                        <input
                            type="password"
                            value={settings.discord_webhook_url || ''}
                            onChange={(e) => handleChange('discord_webhook_url', e.target.value)}
                            className="w-full px-4 py-2 rounded-xl border border-gray-200 dark:border-gray-600 bg-white dark:bg-gray-700 focus:ring-2 focus:ring-indigo-500 outline-none transition-all text-sm"
                            placeholder="https://discord.com/api/webhooks/..."
                        />
                        <p className="text-xs text-gray-500 mt-1">Receive translation completion, daily limit reached, and failure alerts directly in your Discord server.</p>
                    </div>
                </div>
            </div>

            <div className="bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 overflow-hidden">
                <div className="p-6 border-b border-gray-200 dark:border-gray-700 bg-gray-50/50 dark:bg-gray-800/50">
                    <h2 className="text-lg font-semibold text-gray-900 dark:text-white flex items-center gap-2">
                        <BookOpen className="w-5 h-5 text-emerald-500" />
                        Workflow Instructions
                    </h2>
                </div>
                <div className="p-6 space-y-4 text-gray-600 dark:text-gray-300">
                    <div className="flex gap-4">
                        <div className="w-8 h-8 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center font-bold flex-shrink-0">1</div>
                        <div>
                            <h3 className="font-semibold text-gray-900 dark:text-white">Create Project</h3>
                            <p className="text-sm">Start by creating a project for your show. Set the target language correctly.</p>
                        </div>
                    </div>

                    <div className="flex gap-4">
                        <div className="w-8 h-8 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center font-bold flex-shrink-0">2</div>
                        <div>
                            <h3 className="font-semibold text-gray-900 dark:text-white">Build Glossary</h3>
                            <p className="text-sm">Upload episodes/files or use "Research Mode" to extract character names and terms. Review and approve them.</p>
                        </div>
                    </div>

                    <div className="flex gap-4">
                        <div className="w-8 h-8 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center font-bold flex-shrink-0">3</div>
                        <div>
                            <h3 className="font-semibold text-gray-900 dark:text-white">Translate</h3>
                            <p className="text-sm">Run translation on episodes/files. The AI will use your glossary and context guide to ensure consistency.</p>
                        </div>
                    </div>

                    <div className="flex gap-4">
                        <div className="w-8 h-8 rounded-full bg-emerald-100 text-emerald-600 flex items-center justify-center font-bold flex-shrink-0">4</div>
                        <div>
                            <h3 className="font-semibold text-gray-900 dark:text-white">Review & Export</h3>
                            <p className="text-sm">Use the editor to tweak translations if needed, then export the final SRT file.</p>
                        </div>
                    </div>
                </div>
            </div>

            {/* Sticky Save Bar */}
            <div className="sticky bottom-8 z-10">
                <div className="bg-white/80 dark:bg-gray-800/80 backdrop-blur-md rounded-2xl shadow-2xl border border-gray-200 dark:border-gray-700 p-4 flex items-center justify-between gap-4">
                    <div className="flex-1">
                        {message && (
                            <div className={`px-4 py-2 rounded-xl text-sm font-medium animate-in fade-in slide-in-from-bottom-2 duration-300 ${message.type === 'success'
                                    ? 'bg-green-50 text-green-700 border border-green-100 dark:bg-green-900/20 dark:text-green-400 dark:border-green-900/30'
                                    : 'bg-red-50 text-red-700 border border-red-100 dark:bg-red-900/20 dark:text-red-400 dark:border-red-900/30'
                                }`}>
                                {message.text}
                            </div>
                        )}
                    </div>
                    <div className="flex items-center gap-3">
                        <button
                            onClick={loadSettings}
                            className="px-6 py-2.5 text-gray-700 dark:text-gray-300 font-medium hover:bg-gray-100 dark:hover:bg-gray-700 rounded-xl transition-all"
                        >
                            Reset
                        </button>
                        <button
                            onClick={handleSave}
                            disabled={saving}
                            className="px-8 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl font-semibold shadow-lg shadow-indigo-200 dark:shadow-none transition-all flex items-center gap-2 disabled:opacity-70 scale-100 active:scale-95"
                        >
                            {saving ? 'Saving...' : <><Save className="w-5 h-5" /> Save All Changes</>}
                        </button>
                    </div>
                </div>
            </div>
            <SyncOptionsModal
                isOpen={syncModalOpen}
                onClose={() => setSyncModalOpen(false)}
                onConfirm={handleStartSyncWithOptions}
            />
        </div>
    );
};

export default SettingsPage;
