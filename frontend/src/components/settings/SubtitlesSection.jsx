import { useState } from 'react';
import { FolderOpen } from 'lucide-react';
import { api } from '../../api';
import { useToast } from '../../context/ToastContext';
import { Button, Card, Field, INPUT, Toggle } from '../ui';
import { cx, Divider, FieldGrid, NumberInput } from './parts';

function FormatsCard({ settings, onChange }) {
    return (
        <Card title="Formats and embedded subtitles" subtitle="Where the English source comes from, and how styled ASS tracks are treated.">
            <div className="space-y-5">
                <Toggle
                    label="Extract subtitles from inside the video"
                    description="When there is no subtitle file next to a video, its embedded tracks are read with ffmpeg."
                    checked={settings.embedded_extraction_enabled ?? true}
                    onChange={(v) => onChange('embedded_extraction_enabled', v)}
                />
                <FieldGrid>
                    <Field label="Prefer ASS" htmlFor="prefer-ass" hint="ASS keeps styling and typesetting. Auto means anime only.">
                        <select
                            id="prefer-ass"
                            className={INPUT}
                            value={settings.prefer_ass_format || 'auto'}
                            onChange={(e) => {
                                onChange('prefer_ass_format', e.target.value);
                                if (e.target.value === 'always') onChange('translate_all_source_formats', true);
                            }}
                        >
                            <option value="auto">Auto: for anime and stylised media</option>
                            <option value="always">Always, for all media</option>
                            <option value="never">Never: subtitle files (SRT) only</option>
                        </select>
                    </Field>
                    <Field label="Extractions at once" htmlFor="extract-conc" hint="Extraction reads the whole video file, so more than one slows each down.">
                        <NumberInput id="extract-conc" min={1} max={8} value={settings.embedded_extraction_concurrency ?? 1} onChange={(v) => onChange('embedded_extraction_concurrency', v)} />
                    </Field>
                </FieldGrid>
                <Field label="Tracks to rank last" htmlFor="deprioritize" hint="Comma separated words in a track’s title. Such tracks are used only when no better one exists.">
                    <input
                        id="deprioritize"
                        className={cx(INPUT, 'font-mono text-xs')}
                        value={settings.embedded_deprioritize_keywords || ''}
                        onChange={(e) => onChange('embedded_deprioritize_keywords', e.target.value)}
                        placeholder="signs, songs, forced, commentary, karaoke"
                    />
                </Field>
                <Toggle
                    label="Translate both SRT and ASS"
                    description="When a video has both, translate both so players can use either."
                    checked={!!settings.translate_all_source_formats}
                    onChange={(v) => onChange('translate_all_source_formats', v)}
                />
                <Toggle
                    label="Translate song lyrics"
                    description="Openings, endings and inserts are translated and kept consistent between episodes. Projects can override this."
                    checked={settings.translate_lyrics ?? true}
                    onChange={(v) => onChange('translate_lyrics', v)}
                />
                <Field label="ffmpeg location" htmlFor="ffmpeg-path" hint="The ffmpeg executable or the folder that holds it. Empty looks on PATH.">
                    <input id="ffmpeg-path" className={cx(INPUT, 'font-mono text-xs')} value={settings.ffmpeg_path || ''} onChange={(e) => onChange('ffmpeg_path', e.target.value)} placeholder="C:\ffmpeg\bin" />
                </Field>
            </div>
        </Card>
    );
}

function FormattingCard({ settings, onChange }) {
    const on = !!settings.conformance_enabled;
    return (
        <Card title="Formatting" subtitle="Applied to the exported file only. The text you see in the editor is never changed by it.">
            <div className="space-y-5">
                <Toggle
                    label="Conform to reading limits"
                    description="Re-wraps lines that are too long or too fast to read."
                    checked={on}
                    onChange={(v) => onChange('conformance_enabled', v)}
                />
                <div className={cx('grid grid-cols-1 gap-4 sm:grid-cols-3', !on && 'opacity-60')}>
                    <Field label="Characters per line" htmlFor="max-chars">
                        <NumberInput id="max-chars" min={20} max={80} disabled={!on} value={settings.max_chars_per_line ?? 42} onChange={(v) => onChange('max_chars_per_line', v)} />
                    </Field>
                    <Field label="Lines per cue" htmlFor="max-lines">
                        <NumberInput id="max-lines" min={1} max={4} disabled={!on} value={settings.max_lines ?? 2} onChange={(v) => onChange('max_lines', v)} />
                    </Field>
                    <Field label="Characters per second" htmlFor="max-cps">
                        <NumberInput id="max-cps" min={8} max={40} step={0.5} disabled={!on} value={settings.max_cps ?? 17} onChange={(v) => onChange('max_cps', v)} />
                    </Field>
                </div>
            </div>
        </Card>
    );
}

function SubtitleEditCard({ settings, onChange }) {
    const toast = useToast();
    const [browsing, setBrowsing] = useState(false);
    const browse = async () => {
        setBrowsing(true);
        try {
            const res = await api.browseExecutable();
            if (res.data?.path) onChange('subtitle_edit_path', res.data.path);
        } catch (err) {
            toast.error(err?.response?.data?.detail || 'The file dialog could not be opened. Type the path instead.');
        } finally {
            setBrowsing(false);
        }
    };
    return (
        <Card title="SubtitleEdit" subtitle="Optional. Its command line cleans up exported subtitles.">
            <div className="space-y-5">
                <Field label="SubtitleEdit.exe" htmlFor="se-path" hint="Empty uses PATH or the standard Program Files folder.">
                    <div className="flex flex-col gap-2 sm:flex-row">
                        <input id="se-path" className={cx(INPUT, 'font-mono text-xs sm:flex-1')} value={settings.subtitle_edit_path || ''} onChange={(e) => onChange('subtitle_edit_path', e.target.value)} placeholder="C:\Program Files\Subtitle Edit\SubtitleEdit.exe" />
                        <Button icon={FolderOpen} onClick={browse} loading={browsing}>Browse</Button>
                    </div>
                </Field>
                <Divider />
                <Toggle label="Fix common errors" description="OCR repairs, nested quotes and tag syntax." checked={!!settings.apply_subtitle_edit_fixes} onChange={(v) => onChange('apply_subtitle_edit_fixes', v)} />
                <Toggle label="Split long lines" description="Splits lines over the characters-per-line limit." checked={!!settings.split_long_lines} onChange={(v) => onChange('split_long_lines', v)} />
            </div>
        </Card>
    );
}

export default function SubtitlesSection({ settings, onChange }) {
    return (
        <div className="space-y-5">
            <FormatsCard settings={settings} onChange={onChange} />
            <FormattingCard settings={settings} onChange={onChange} />
            <SubtitleEditCard settings={settings} onChange={onChange} />
        </div>
    );
}
