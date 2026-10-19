import React from 'react';
import { AlertTriangle } from 'lucide-react';
import { Button, Notice } from '../ui';
import { formatDate } from './episodeUtils';

const asDate = (raw) => {
    const d = raw ? new Date(raw) : null;
    return d && !Number.isNaN(d.getTime()) ? d : null;
};

// "Built automatically on 12 Oct 2026" for the card subtitle (null when it was built by hand).
export function bootstrapCaption(project) {
    const d = asDate(project?.bootstrapped_at);
    return d ? `Built automatically on ${formatDate(d)}.` : null;
}

// The runner builds a new title's context guide and glossary before its first episode. When that failed,
// say so here (it is retried before the next translation) and offer to build it now.
const BootstrapNotice = ({ project, onBuild, building, buildLabel = 'Build now' }) => {
    if (!project?.bootstrap_error) return null;
    const when = asDate(project.bootstrap_error_at);
    return (
        <Notice tone="warning" icon={AlertTriangle} title="The automatic setup for this title did not finish"
            actions={onBuild && <Button size="sm" variant="primary" onClick={onBuild} loading={building}>{buildLabel}</Button>}>
            {String(project.bootstrap_error)}
            {when ? ` (${formatDate(when)})` : ''}. It is tried again before the next episode is translated.
        </Notice>
    );
};

export default BootstrapNotice;
