export const PRIORITY_OPTIONS = [
    { id: 'high', label: 'High: first' },
    { id: 'normal', label: 'Normal' },
    { id: 'low', label: 'Low: last' },
    { id: 'ignored', label: 'Ignored: never automatically' },
];

export function itemPriority(item) {
    return item.translation_priority || (item.arr_disabled ? 'ignored' : 'normal');
}

export function isMovieItem(item) {
    return item.arr_media_type === 'movie' || item.type === 'movie';
}

// "Queued 12 for translation, 3 for extraction; 2 without a usable subtitle" from the enqueue-missing answer.
export function describeEnqueueResult(data) {
    const translation = data.queued_for_translation ?? data.enqueued_count ?? 0;
    const extraction = data.queued_for_extraction ?? 0;
    const unusable = data.no_usable_source ?? 0;
    const parts = [];
    if (translation) parts.push(`${translation} for translation`);
    if (extraction) parts.push(`${extraction} for extraction`);
    let text = parts.length ? `Queued ${parts.join(', ')}` : 'Nothing to queue';
    if (unusable) text += `${parts.length ? '; ' : ': '}${unusable} without a usable subtitle`;
    else if (!parts.length && (data.already_available ?? 0) > 0) text += ': everything is already translated';
    return text + '.';
}
