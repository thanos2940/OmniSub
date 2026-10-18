import React, { useState, useEffect, useCallback } from 'react';
import { useLocation } from 'react-router-dom';
import {
    Save, Settings as SettingsIcon, Sparkles, Tv, Film, Lock, Sliders, Play, Loader2, RefreshCw, Plane
} from 'lucide-react';
import { api } from '../api';
import { useToast } from '../context/ToastContext';
import GeneralSettings from './settings/GeneralSettings';
import TranslationEngineSettings from './settings/TranslationEngineSettings';
import GeminiSettings from './settings/GeminiSettings';
import MediaServerSettings from './settings/MediaServerSettings';
import SubtitleSettings from './settings/SubtitleSettings';
import SecuritySettings from './settings/SecuritySettings';
import PresetsSettings from './settings/PresetsSettings';
import TestTranslators from './settings/TestTranslators';
import SystemUpdatesSettings from './settings/SystemUpdatesSettings';
import AutopilotSettings from './settings/AutopilotSettings';

const SECRET_SENTINEL = '__SECRET_UNCHANGED__';

const TABS = [
    { id: 'gemini', label: 'Gemini AI', icon: Sparkles },
    { id: 'presets', label: 'Quality Presets', icon: Sliders },
    { id: 'general', label: 'Hyperparameters', icon: SettingsIcon },
    { id: 'media', label: 'Media Servers', icon: Tv },
    { id: 'subtitles', label: 'Subtitles & ASS', icon: Film },
    { id: 'autopilot', label: 'Autopilot', icon: Plane },
    { id: 'security', label: 'Security & Auth', icon: Lock },
    { id: 'updates', label: 'Updates & System', icon: RefreshCw },
    { id: 'tester', label: 'Sandbox Tester', icon: Play },
];

const SettingsPage = () => {
    const toast = useToast();
    const location = useLocation();
    const [activeTab, setActiveTab] = useState(location.state?.tab || 'gemini');

    useEffect(() => {
        if (location.state?.tab) {
            setActiveTab(location.state.tab);
        }
    }, [location.state?.tab]);
    const [settings, setSettings] = useState({
        default_target_language: 'English',
        default_scan_model: 'gemini-2.5-flash',
        default_translation_model: 'gemini-2.5-flash',
        default_context_model: 'gemini-2.5-flash',
        default_glossary_model: 'gemini-2.5-flash',
        default_review_model: 'gemini-2.5-pro',
        fallback_translation_model: '',
        temperature: 0.3,
        top_k: 64,
        top_p: 0.95,
        chunk_size: 150,
        max_output_tokens: 8192,
        episode_request_mode: 'auto',
        ai_provider: 'cloud',
        prefer_ass_format: 'auto',
        translate_all_source_formats: false,
        embedded_deprioritize_keywords: '',
        apply_subtitle_edit_fixes: true,
        split_long_lines: false,
        subtitle_edit_path: '',
        sequential_scenes: false,
        prompt_caching_enabled: false,
        sonarr_enabled: false,
        sonarr_url: 'http://localhost:8989',
        sonarr_api_key: '',
        radarr_enabled: false,
        radarr_url: 'http://localhost:7878',
        radarr_api_key: '',
        arr_path_mappings: [],
        path_mappings: [],
        tm_enabled: true,
        glossary_enforce_enabled: true,
        repair_pass_enabled: false,
        intra_episode_reconcile_enabled: false,
        character_profiles_enabled: false,
        episode_summaries_enabled: false,
        conformance_enabled: false,
        enable_reviewer: false,
        new_terms_suggest_enabled: true,
        auto_update_enabled: true,
        auto_update_interval_hours: 6,
        github_webhook_enabled: true,
        github_webhook_secret: '',
    });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);

    const loadSettings = useCallback(async () => {
        setLoading(true);
        try {
            const res = await api.getSettings();
            if (res.data) {
                const mappings = res.data.arr_path_mappings || res.data.path_mappings || [];
                setSettings(prev => ({
                    ...prev,
                    ...res.data,
                    arr_path_mappings: mappings,
                    path_mappings: mappings,
                    // Force ai_provider to cloud
                    ai_provider: 'cloud'
                }));
            }
        } catch (e) {
            console.error("Failed to load settings:", e);
            if (toast?.error) toast.error("Failed to load application settings.");
        } finally {
            setLoading(false);
        }
    }, [toast]);

    useEffect(() => {
        loadSettings();
    }, [loadSettings]);

    const handleChange = (key, value) => {
        setSettings(prev => ({ ...prev, [key]: value }));
    };

    const handleApplyPreset = (presetValues) => {
        setSettings(prev => ({ ...prev, ...presetValues }));
        if (toast?.success) toast.success("Quality preset flags updated.");
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            // Strip secret sentinels before saving
            const payload = { ...settings, ai_provider: 'cloud' };
            if (payload.sonarr_api_key === SECRET_SENTINEL) delete payload.sonarr_api_key;
            if (payload.radarr_api_key === SECRET_SENTINEL) delete payload.radarr_api_key;
            if (payload.github_webhook_secret === SECRET_SENTINEL) delete payload.github_webhook_secret;
            if (payload.plex_token === SECRET_SENTINEL) delete payload.plex_token;
            delete payload.autopilot_enabled_at; // read-only: set by the server when Autopilot is switched on

            await api.updateSettings(payload);
            if (toast?.success) toast.success("Global settings saved successfully!");
            loadSettings();
        } catch (e) {
            console.error("Failed to save settings:", e);
            if (toast?.error) toast.error(`Failed to save settings: ${e.response?.data?.detail || e.message}`);
        } finally {
            setSaving(false);
        }
    };

    if (loading) {
        return (
            <div className="max-w-6xl mx-auto p-8 flex flex-col items-center justify-center min-h-[400px]">
                <Loader2 className="w-8 h-8 text-indigo-600 animate-spin mb-3" />
                <p className="text-gray-500">Loading settings...</p>
            </div>
        );
    }

    return (
        <div className="max-w-6xl mx-auto p-6 md:p-8 space-y-6">
            {/* Top Coordinator Header */}
            <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight text-gray-900 dark:text-white">Global Settings</h1>
                    <p className="text-sm text-gray-500 dark:text-gray-400">
                        Configure Google Gemini AI models, translation presets, media servers, and security.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={handleSave}
                    disabled={saving}
                    className="px-6 py-2.5 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl font-bold text-sm flex items-center gap-2 transition-all shadow-md disabled:opacity-50"
                >
                    {saving ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                    <span>{saving ? 'Saving...' : 'Save Settings'}</span>
                </button>
            </div>

            {/* Tab Navigation Navigation Bar */}
            <div className="flex items-center gap-1.5 overflow-x-auto pb-1 border-b border-gray-200 dark:border-gray-800">
                {TABS.map(tab => {
                    const Icon = tab.icon;
                    const isActive = activeTab === tab.id;
                    return (
                        <button
                            key={tab.id}
                            type="button"
                            onClick={() => setActiveTab(tab.id)}
                            className={`px-4 py-2.5 rounded-xl text-xs font-bold flex items-center gap-2 transition-all whitespace-nowrap ${
                                isActive
                                    ? 'bg-indigo-600 text-white shadow-sm'
                                    : 'text-gray-600 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-800'
                            }`}
                        >
                            <Icon size={16} />
                            <span>{tab.label}</span>
                        </button>
                    );
                })}
            </div>

            {/* Tab View Content */}
            <div className="pt-2">
                {activeTab === 'gemini' && (
                    <div className="space-y-6">
                        <TranslationEngineSettings settings={settings} onChange={handleChange} />
                        <GeminiSettings
                            settings={settings}
                            onChange={handleChange}
                            toast={toast}
                        />
                    </div>
                )}

                {activeTab === 'presets' && (
                    <PresetsSettings
                        settings={settings}
                        onChange={handleChange}
                        onApplyPreset={handleApplyPreset}
                    />
                )}

                {activeTab === 'general' && (
                    <GeneralSettings
                        settings={settings}
                        onChange={handleChange}
                    />
                )}

                {activeTab === 'media' && (
                    <MediaServerSettings
                        settings={settings}
                        onChange={handleChange}
                        toast={toast}
                    />
                )}

                {activeTab === 'subtitles' && (
                    <SubtitleSettings
                        settings={settings}
                        onChange={handleChange}
                        toast={toast}
                    />
                )}

                {activeTab === 'autopilot' && (
                    <AutopilotSettings
                        settings={settings}
                        onChange={handleChange}
                    />
                )}

                {activeTab === 'security' && (
                    <SecuritySettings
                        toast={toast}
                    />
                )}

                {activeTab === 'updates' && (
                    <SystemUpdatesSettings
                        settings={settings}
                        updateSetting={handleChange}
                    />
                )}

                {activeTab === 'tester' && (
                    <TestTranslators
                        settings={settings}
                        toast={toast}
                    />
                )}
            </div>
        </div>
    );
};

export default SettingsPage;
