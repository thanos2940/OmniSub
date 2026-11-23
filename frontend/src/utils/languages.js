// Target languages offered in the UI. The English name is what the settings store and what the prompts
// say ("translating from English into Greek"); backend/utils/language_codes.py maps it to the file code.
// Keep the two lists in step.
export const LANGUAGES = [
    { name: 'Greek', code: 'el', native: 'Ελληνικά' },
    { name: 'Spanish', code: 'es', native: 'Español' },
    { name: 'French', code: 'fr', native: 'Français' },
    { name: 'German', code: 'de', native: 'Deutsch' },
    { name: 'Italian', code: 'it', native: 'Italiano' },
    { name: 'Portuguese', code: 'pt', native: 'Português' },
    { name: 'Dutch', code: 'nl', native: 'Nederlands' },
    { name: 'Polish', code: 'pl', native: 'Polski' },
    { name: 'Romanian', code: 'ro', native: 'Română' },
    { name: 'Czech', code: 'cs', native: 'Čeština' },
    { name: 'Slovak', code: 'sk', native: 'Slovenčina' },
    { name: 'Hungarian', code: 'hu', native: 'Magyar' },
    { name: 'Bulgarian', code: 'bg', native: 'Български' },
    { name: 'Croatian', code: 'hr', native: 'Hrvatski' },
    { name: 'Serbian', code: 'sr', native: 'Српски' },
    { name: 'Slovenian', code: 'sl', native: 'Slovenščina' },
    { name: 'Swedish', code: 'sv', native: 'Svenska' },
    { name: 'Danish', code: 'da', native: 'Dansk' },
    { name: 'Norwegian', code: 'no', native: 'Norsk' },
    { name: 'Finnish', code: 'fi', native: 'Suomi' },
    { name: 'Icelandic', code: 'is', native: 'Íslenska' },
    { name: 'Lithuanian', code: 'lt', native: 'Lietuvių' },
    { name: 'Latvian', code: 'lv', native: 'Latviešu' },
    { name: 'Estonian', code: 'et', native: 'Eesti' },
    { name: 'Albanian', code: 'sq', native: 'Shqip' },
    { name: 'Macedonian', code: 'mk', native: 'Македонски' },
    { name: 'Catalan', code: 'ca', native: 'Català' },
    { name: 'Russian', code: 'ru', native: 'Русский' },
    { name: 'Ukrainian', code: 'uk', native: 'Українська' },
    { name: 'Turkish', code: 'tr', native: 'Türkçe' },
    { name: 'Arabic', code: 'ar', native: 'العربية' },
    { name: 'Hebrew', code: 'he', native: 'עברית' },
    { name: 'Persian', code: 'fa', native: 'فارسی' },
    { name: 'Hindi', code: 'hi', native: 'हिन्दी' },
    { name: 'Bengali', code: 'bn', native: 'বাংলা' },
    { name: 'Thai', code: 'th', native: 'ไทย' },
    { name: 'Vietnamese', code: 'vi', native: 'Tiếng Việt' },
    { name: 'Indonesian', code: 'id', native: 'Bahasa Indonesia' },
    { name: 'Malay', code: 'ms', native: 'Bahasa Melayu' },
    { name: 'Filipino', code: 'tl', native: 'Filipino' },
    { name: 'Chinese', code: 'zh', native: '中文' },
    { name: 'Japanese', code: 'ja', native: '日本語' },
    { name: 'Korean', code: 'ko', native: '한국어' },
    { name: 'English', code: 'en', native: 'English' },
];

const BY_CODE = Object.fromEntries(LANGUAGES.map(l => [l.code, l]));

/** The first language the browser prefers that is not English (the source is English), or null. */
export function browserTargetLanguage(languages = (typeof navigator !== 'undefined' && (navigator.languages || [navigator.language])) || []) {
    for (const tag of languages) {
        const code = String(tag || '').toLowerCase().split(/[-_]/)[0];
        if (code && code !== 'en' && BY_CODE[code]) return BY_CODE[code].name;
    }
    return null;
}
