import React, { useState } from 'react';
import { Film, Wrench, FolderOpen, Loader2 } from 'lucide-react';
import { api } from '../../api';

const SubtitleSettings = ({ settings, onChange, toast }) => {
    const [browsing, setBrowsing] = useState(false);

    const handleBrowseExecutable = async () => {
        setBrowsing(true);
        try {
            const res = await api.browseExecutable();
            if (res.data?.path) {
                onChange('subtitle_edit_path', res.data.path);
                if (toast?.success) toast.success(`Selected: ${res.data.path}`);
            }
        } catch {
            if (toast?.error) toast.error("Could not launch file browser dialog.");
        } finally {
            setBrowsing(false);
        }
    };

    return (
        <div className="space-y-6">
            {/* Subtitle Formats & ASS Preferences */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-5">
                <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                    <Film className="text-purple-600 dark:text-purple-400" size={20} />
                    <div>
                        <h3 className="text-base font-bold text-gray-900 dark:text-white">Subtitle Formats &amp; Embedded ASS</h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                            Manage extraction and preservation of styled Advanced SubStation Alpha (.ass) tracks.
                        </p>
                    </div>
                </div>

                <div className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                            Embedded .ass Subtitle Preference
                        </label>
                        <select
                            value={settings.prefer_ass_format || 'auto'}
                            onChange={(e) => {
                                const v = e.target.value;
                                onChange('prefer_ass_format', v);
                                if (v === 'always') onChange('translate_all_source_formats', true);
                            }}
                            className="w-full px-4 py-2.5 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                        >
                            <option value="auto">Auto &mdash; Prioritize .ass for anime and stylized media</option>
                            <option value="always">Always &mdash; Prioritize embedded .ass streams across all media</option>
                            <option value="never">Never &mdash; Restrict to standalone subtitle files (.srt only)</option>
                        </select>
                        <p className="text-xs text-gray-400 mt-1">
                            When active, linked video files are probed to extract original ASS styling, typesetting, and colors.
                        </p>
                    </div>

                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                            Track Deprioritization Keywords (Comma-separated)
                        </label>
                        <input
                            type="text"
                            value={settings.embedded_deprioritize_keywords || ''}
                            onChange={(e) => onChange('embedded_deprioritize_keywords', e.target.value)}
                            placeholder="signs, songs, op, ed, kara, title, insert, preview"
                            className="w-full px-4 py-2.5 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white font-mono text-xs outline-none focus:ring-2 focus:ring-indigo-500"
                        />
                        <p className="text-xs text-gray-400 mt-1">
                            Stream title words that rank a track below main dialogue during automatic extraction.
                        </p>
                    </div>

                    <label className="flex items-start gap-3 p-3.5 rounded-xl border border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                        <input
                            type="checkbox"
                            checked={settings.translate_all_source_formats || false}
                            onChange={(e) => onChange('translate_all_source_formats', e.target.checked)}
                            className="mt-1 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                        />
                        <div>
                            <span className="text-sm font-semibold text-gray-900 dark:text-white">Translate All Source Subtitle Formats (Dual-format SRT &amp; ASS)</span>
                            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                                If both an SRT and an extracted ASS exist, translates both so client players with varying format support can choose.
                            </p>
                        </div>
                    </label>
                </div>
            </div>

            {/* SubtitleEdit CLI Integration */}
            <div className="bg-white dark:bg-gray-800 rounded-2xl p-6 border border-gray-200 dark:border-gray-700 shadow-sm space-y-5">
                <div className="flex items-center gap-2 border-b border-gray-100 dark:border-gray-700 pb-4">
                    <Wrench className="text-indigo-600 dark:text-indigo-400" size={20} />
                    <div>
                        <h3 className="text-base font-bold text-gray-900 dark:text-white">SubtitleEdit CLI Post-Processing</h3>
                        <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                            Automated line splitting, formatting checks, and common error correction via SubtitleEdit.
                        </p>
                    </div>
                </div>

                <div className="space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                            SubtitleEdit Executable Path
                        </label>
                        <div className="flex gap-2">
                            <input
                                type="text"
                                value={settings.subtitle_edit_path || ''}
                                onChange={(e) => onChange('subtitle_edit_path', e.target.value)}
                                placeholder="C:\Program Files\Subtitle Edit\SubtitleEdit.exe"
                                className="flex-1 px-4 py-2.5 rounded-xl border border-gray-300 dark:border-gray-600 dark:bg-gray-700 dark:text-white font-mono text-sm outline-none focus:ring-2 focus:ring-indigo-500"
                            />
                            <button
                                type="button"
                                onClick={handleBrowseExecutable}
                                disabled={browsing}
                                className="px-4 py-2.5 rounded-xl bg-gray-100 hover:bg-gray-200 dark:bg-gray-700 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200 text-sm font-semibold flex items-center gap-2 transition-colors disabled:opacity-50"
                            >
                                {browsing ? <Loader2 size={16} className="animate-spin" /> : <FolderOpen size={16} />}
                                <span>Browse</span>
                            </button>
                        </div>
                        <p className="text-xs text-gray-400 mt-1">
                            Leave empty if installed in PATH or standard Program Files location.
                        </p>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        <label className="flex items-start gap-3 p-3.5 rounded-xl border border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                            <input
                                type="checkbox"
                                checked={settings.apply_subtitle_edit_fixes || false}
                                onChange={(e) => onChange('apply_subtitle_edit_fixes', e.target.checked)}
                                className="mt-1 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                            />
                            <div>
                                <span className="text-sm font-semibold text-gray-900 dark:text-white">Auto-Apply "Fix Common Errors"</span>
                                <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Runs OCR repairs, quotes unnesting, and syntax corrections.</p>
                            </div>
                        </label>

                        <label className="flex items-start gap-3 p-3.5 rounded-xl border border-gray-100 dark:border-gray-700 hover:bg-gray-50 dark:hover:bg-gray-750/50 cursor-pointer transition-colors">
                            <input
                                type="checkbox"
                                checked={settings.split_long_lines || false}
                                onChange={(e) => onChange('split_long_lines', e.target.checked)}
                                className="mt-1 w-4 h-4 text-indigo-600 rounded focus:ring-indigo-500 border-gray-300"
                            />
                            <div>
                                <span className="text-sm font-semibold text-gray-900 dark:text-white">Auto-Split Long Lines</span>
                                <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">Splits translated lines exceeding standard 42 characters per line limit.</p>
                            </div>
                        </label>
                    </div>
                </div>
            </div>
        </div>
    );
};

export default SubtitleSettings;
