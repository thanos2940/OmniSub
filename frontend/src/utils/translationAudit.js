/**
 * Shared helpers for the translation audit UI (project sync dialog + library check).
 * Location: frontend/src/utils/translationAudit.js
 */

// The audit reports broken text as a per-episode dict `broken: { kind: lines }`. The UI talks
// about it in three groups; any kind the audit adds later falls into "other".
//   garbled  - mojibake: text saved in the wrong encoding (repaired without losing anything)
//   leftover - prompt text that leaked into the translation (numbered lines, echoed context,
//              review notes or summaries); a repair cleans it up or clears the line
//   mixed    - foreign letters inside Greek words; a plain repair leaves them in place and only
//              "repair & queue" clears them so the agent retranslates them
const KIND_GROUP = {
    mojibake: 'garbled',
    numbered: 'leftover',
    context_echo: 'leftover',
    review_echo: 'leftover',
    summary_echo: 'leftover',
    mixed_script: 'mixed',
};

export const BROKEN_LABELS = {
    garbled: 'garbled encoding',
    leftover: 'leftover prompt text',
    mixed: 'mixed alphabets — needs retranslation',
    other: 'other broken text',
};

export const BROKEN_HINTS = {
    garbled: 'Text saved in the wrong encoding. A repair fixes it without losing anything.',
    leftover: 'Prompt text that leaked into the translation (numbered lines, echoed context, review notes or summaries). A repair cleans it up or clears the line.',
    mixed: 'Foreign letters inside Greek words. A plain repair leaves these lines in place; "Repair & queue fixes" clears them so the agent retranslates them.',
    other: 'Broken text of a kind this screen does not know yet.',
};

// A count that may arrive as a number or as a list of names/items.
export const toCount = (value) => (Array.isArray(value) ? value.length : Number(value) || 0);

// Splits one episode's `broken` dict into the groups above.
//   fixable = what a repair can deal with (everything except mixed alphabets)
//   total   = every broken line
export const brokenGroups = (broken) => {
    const groups = { garbled: 0, leftover: 0, mixed: 0, other: 0 };
    if (broken && typeof broken === 'object') {
        for (const [kind, lines] of Object.entries(broken)) {
            groups[KIND_GROUP[kind] || 'other'] += toCount(lines);
        }
    }
    return { ...groups, fixable: groups.garbled + groups.leftover + groups.other, total: groups.garbled + groups.leftover + groups.other + groups.mixed };
};

const capitalize = (text) => (text ? text.charAt(0).toUpperCase() + text.slice(1) : text);

// What a repair/requeue pass changed, as short lower-case phrases. Works on a single audit response
// and on the totals of a library run (counts or lists are both fine).
export const repairParts = (data) => {
    if (!data) return [];
    const parts = [];
    const repaired = toCount(data.repaired_lines);
    if (repaired) parts.push(`repaired ${repaired} line(s)`);
    const broken = toCount(data.broken_cleared);
    if (broken) parts.push(`cleared ${broken} broken line(s)`);
    const misplaced = toCount(data.cleared_lines);
    if (misplaced) parts.push(`cleared ${misplaced} misplaced line(s)`);
    const files = toCount(data.disk_repaired);
    if (files) parts.push(`fixed ${files} subtitle file(s) on disk`);
    const reexported = toCount(data.reexported);
    if (reexported) {
        parts.push(Array.isArray(data.reexported) && reexported <= 3
            ? `re-exported ${data.reexported.join(', ')}`
            : `re-exported ${reexported} episode(s)`);
    }
    return parts;
};

// "Repaired 12 line(s); cleared 3 misplaced line(s)" ('' when there is nothing to say).
export const joinParts = (parts) => capitalize(parts.join('; '));

export const repairSummary = (data) => joinParts(repairParts(data));
